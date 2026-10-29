# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from unittest.mock import MagicMock

import requests

from odoo.tests import BaseCase, tagged

from ..tools import WhatsAppApiError, WhatsAppCloudClient
from ..tools.graph_client import DEFAULT_TIMEOUT


def _response(status=200, payload=None):
    response = MagicMock()
    response.status_code = status
    response.ok = status < 400
    response.reason = "Bad Request" if status >= 400 else "OK"
    response.json.return_value = payload if payload is not None else {}
    return response


@tagged("post_install", "-at_install")
class TestGraphClient(BaseCase):
    """The HTTP layer itself, with requests.Session mocked out."""

    def _client(self, *responses):
        session = MagicMock()
        session.request.side_effect = list(responses)
        client = WhatsAppCloudClient("tok", "PNID", waba_id="WABA", session=session)
        return client, session

    def test_send_text_payload_and_auth(self):
        client, session = self._client(_response(payload={"messages": [{"id": "wamid.1"}]}))
        self.assertEqual(client.send_text("919876543210", "Hello"), "wamid.1")
        method, url = session.request.call_args.args
        kwargs = session.request.call_args.kwargs
        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://graph.facebook.com/v21.0/PNID/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(kwargs["timeout"], DEFAULT_TIMEOUT)
        self.assertEqual(kwargs["json"]["to"], "919876543210")
        self.assertEqual(kwargs["json"]["text"]["body"], "Hello")

    def test_send_template_builds_body_parameters(self):
        client, session = self._client(_response(payload={"messages": [{"id": "wamid.2"}]}))
        client.send_template("91999", "order_ready", "en_US", ["Kavya", 1250.5])
        template = session.request.call_args.kwargs["json"]["template"]
        self.assertEqual(template["name"], "order_ready")
        self.assertEqual(template["language"], {"code": "en_US"})
        self.assertEqual(
            template["components"][0]["parameters"],
            [{"type": "text", "text": "Kavya"}, {"type": "text", "text": "1250.5"}],
        )

    def test_graph_error_is_raised_with_code(self):
        error = {
            "error": {
                "message": "Invalid parameter",
                "code": 100,
                "error_data": {"details": "to is invalid"},
            }
        }
        client, _session = self._client(_response(400, error))
        with self.assertRaises(WhatsAppApiError) as ctx:
            client.send_text("bad", "Hi")
        self.assertEqual(ctx.exception.code, 100)
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("to is invalid", str(ctx.exception))

    def test_network_error_is_wrapped(self):
        client, _session = self._client(requests.ConnectionError("boom"))
        with self.assertRaisesRegex(WhatsAppApiError, "Network error"):
            client.get_phone_number()

    def test_list_templates_follows_pagination(self):
        next_url = "https://graph.facebook.com/v21.0/WABA/message_templates?after=abc"
        client, session = self._client(
            _response(payload={"data": [{"name": "a"}], "paging": {"next": next_url}}),
            _response(payload={"data": [{"name": "b"}], "paging": {}}),
        )
        self.assertEqual([t["name"] for t in client.list_templates()], ["a", "b"])
        self.assertEqual(session.request.call_args_list[1].args[1], next_url)
        self.assertIsNone(session.request.call_args_list[1].kwargs["params"])

    def test_token_is_never_sent_to_a_foreign_host(self):
        client, session = self._client(
            _response(payload={"data": [], "paging": {"next": "https://evil.example/steal"}}),
        )
        with self.assertRaisesRegex(WhatsAppApiError, "non-Graph URL"):
            client.list_templates()
        self.assertEqual(session.request.call_count, 1)

    def test_upload_media_and_send_document(self):
        client, session = self._client(
            _response(payload={"id": "media-9"}),
            _response(payload={"messages": [{"id": "wamid.3"}]}),
        )
        media_id = client.upload_media("S00001.pdf", b"%PDF", "application/pdf")
        self.assertEqual(media_id, "media-9")
        upload_kwargs = session.request.call_args_list[0].kwargs
        self.assertEqual(upload_kwargs["files"]["file"][0], "S00001.pdf")
        self.assertEqual(upload_kwargs["data"]["messaging_product"], "whatsapp")
        client.send_document("91999", media_id, "S00001.pdf", caption="Your quote")
        document = session.request.call_args.kwargs["json"]["document"]
        self.assertEqual(
            document, {"id": "media-9", "filename": "S00001.pdf", "caption": "Your quote"}
        )

    def test_missing_credentials(self):
        with self.assertRaises(WhatsAppApiError):
            WhatsAppCloudClient("", "PNID")
        with self.assertRaises(WhatsAppApiError):
            WhatsAppCloudClient("tok", "PNID").list_templates()
