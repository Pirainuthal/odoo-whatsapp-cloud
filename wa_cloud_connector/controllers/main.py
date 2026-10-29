# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class WhatsAppWebhookController(http.Controller):
    """Public endpoint registered as the callback URL in the Meta app."""

    @http.route(
        "/wa_cloud/webhook", type="http", auth="public", methods=["GET", "POST"], csrf=False
    )
    def webhook(self, **params):
        if request.httprequest.method == "GET":
            return self._verify(params)
        return self._receive()

    def _verify(self, params):
        """Webhook subscription handshake: echo ``hub.challenge`` if the token matches."""
        Account = request.env["wa.account"].sudo()
        if params.get("hub.mode") == "subscribe" and Account._webhook_check_verify_token(
            params.get("hub.verify_token")
        ):
            return request.make_response(
                params.get("hub.challenge", ""), headers=[("Content-Type", "text/plain")]
            )
        return request.make_response("Forbidden", status=403)

    def _receive(self):
        """Inbound messages and status callbacks, authenticated by HMAC-SHA256."""
        raw_body = request.httprequest.get_data()
        Account = request.env["wa.account"].sudo()
        signature = request.httprequest.headers.get("X-Hub-Signature-256")
        signed_by = Account._webhook_check_signature(raw_body, signature)
        if not signed_by:
            _logger.warning("Rejected WhatsApp webhook call with an invalid signature")
            return request.make_json_response({"error": "invalid signature"}, status=403)
        try:
            payload = json.loads(raw_body or b"{}")
        except ValueError:
            return request.make_json_response({"error": "invalid JSON"}, status=400)
        if not isinstance(payload, dict):
            return request.make_json_response({"error": "invalid payload"}, status=400)
        Account._webhook_process(payload, allowed_accounts=signed_by)
        # Always acknowledge quickly; Meta retries anything that is not a 200.
        return request.make_json_response({"status": "ok"})
