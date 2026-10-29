# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo.tests import HttpCase, tagged

from .common import PHONE_NUMBER_ID, VERIFY_TOKEN, WhatsAppSetupMixin, sign, webhook_payload

WEBHOOK_URL = "/wa_cloud/webhook"


@tagged("post_install", "-at_install")
class TestWebhookController(WhatsAppSetupMixin, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_whatsapp()

    def _post(self, body, signature=None):
        headers = {"Content-Type": "application/json"}
        if signature:
            headers["X-Hub-Signature-256"] = signature
        response = self.url_open(WEBHOOK_URL, data=body, headers=headers)
        self.env.invalidate_all()  # the request ran in another environment
        return response

    def _inbound_body(self):
        return webhook_payload(
            {
                "messaging_product": "whatsapp",
                "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                "contacts": [{"wa_id": "919876543210", "profile": {"name": "Kavya"}}],
                "messages": [
                    {
                        "from": "919876543210",
                        "id": "wamid.HTTP1",
                        "timestamp": "1761700000",
                        "type": "text",
                        "text": {"body": "Can you send the invoice?"},
                    }
                ],
            }
        )

    # ------------------------------------------------------------------
    # GET: subscription handshake
    # ------------------------------------------------------------------
    def test_verification_returns_challenge(self):
        response = self.url_open(
            f"{WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token={VERIFY_TOKEN}&hub.challenge=1158201444"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "1158201444")

    def test_verification_rejects_wrong_token(self):
        response = self.url_open(
            f"{WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42"
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("42", response.text)

    # ------------------------------------------------------------------
    # POST: signed notifications
    # ------------------------------------------------------------------
    def test_valid_signature_is_accepted_and_processed(self):
        body = self._inbound_body()
        response = self._post(body, sign(body))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

        log = self.env["wa.message"].search([("wamid", "=", "wamid.HTTP1")])
        self.assertEqual(log.partner_id, self.partner)
        self.assertIn("Can you send the invoice?", log.mail_message_id.body)
        self.assertIn(log.mail_message_id, self.partner.message_ids)

    def test_invalid_signature_is_rejected(self):
        body = self._inbound_body()
        tampered = body.replace(b"invoice", b"refund!")
        for signature in (None, "sha256=deadbeef", sign(body).replace("sha256=", "sha1=")):
            with self.subTest(signature=signature):
                response = self._post(body, signature)
                self.assertEqual(response.status_code, 403)
        # A valid signature for a different body is also refused.
        response = self._post(tampered, sign(body))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.env["wa.message"].search([("wamid", "=", "wamid.HTTP1")]))

    def test_status_callback_updates_log(self):
        message = self.env["wa.message"].create(
            {
                "account_id": self.account.id,
                "phone": "+919876543210",
                "partner_id": self.partner.id,
                "body": "Your order shipped",
                "state": "sent",
                "wamid": "wamid.OUT42",
            }
        )
        body = webhook_payload(
            {
                "messaging_product": "whatsapp",
                "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                "statuses": [{"id": "wamid.OUT42", "status": "read", "timestamp": "1761700500"}],
            }
        )
        response = self._post(body, sign(body))
        self.assertEqual(response.status_code, 200)
        message.invalidate_recordset()
        self.assertEqual(message.state, "read")

    def test_malformed_json_with_valid_signature(self):
        for body in (b"{not json", b"[1, 2]"):
            with self.subTest(body=body):
                response = self._post(body, sign(body))
                self.assertEqual(response.status_code, 400)

    def test_verification_with_non_ascii_token_is_forbidden(self):
        response = self.url_open(
            f"{WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=t%C3%B6ken&hub.challenge=42"
        )
        self.assertEqual(response.status_code, 403)
