# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Thin client for the WhatsApp Cloud API (Meta Graph API).

Every network call made by the module goes through :class:`WhatsAppCloudClient`.
It knows nothing about Odoo, which keeps it easy to reason about and lets the
test-suite replace it with a fake object.
"""

import logging

import requests

_logger = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
DEFAULT_TIMEOUT = 15  # seconds
MEDIA_UPLOAD_TIMEOUT = 60


class WhatsAppApiError(Exception):
    """Raised for any failed Graph API call (HTTP, network or payload error)."""

    def __init__(self, message, code=None, http_status=None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status

    def __str__(self):
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class WhatsAppCloudClient:
    """Minimal WhatsApp Cloud API client.

    :param str access_token: permanent / system-user access token.
    :param str phone_number_id: WhatsApp business phone number ID.
    :param str waba_id: WhatsApp Business Account ID (needed for templates).
    """

    def __init__(
        self,
        access_token,
        phone_number_id,
        waba_id=None,
        api_version=DEFAULT_API_VERSION,
        timeout=DEFAULT_TIMEOUT,
        session=None,
    ):
        if not access_token:
            raise WhatsAppApiError("An access token is required.")
        if not phone_number_id:
            raise WhatsAppApiError("A phone number ID is required.")
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.waba_id = waba_id
        self.api_version = api_version or DEFAULT_API_VERSION
        self.timeout = timeout
        self.session = session or requests.Session()

    # ------------------------------------------------------------------
    # Low level
    # ------------------------------------------------------------------
    def _url(self, path):
        if path.startswith(f"{GRAPH_URL}/"):
            return path  # absolute "paging.next" URLs
        if "://" in path:
            # Never send the bearer token anywhere but the Graph API host.
            raise WhatsAppApiError(f"Refusing to call a non-Graph URL: {path}")
        return f"{GRAPH_URL}/{self.api_version}/{path.lstrip('/')}"

    def _request(self, method, path, timeout=None, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        try:
            response = self.session.request(
                method,
                self._url(path),
                headers=headers,
                timeout=timeout or self.timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise WhatsAppApiError(f"Network error: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if not response.ok or "error" in payload:
            error = payload.get("error") or {}
            details = (error.get("error_data") or {}).get("details")
            message = error.get("message") or response.reason or "Unknown error"
            if details:
                message = f"{message} ({details})"
            _logger.info("WhatsApp API error %s on %s %s", response.status_code, method, path)
            raise WhatsAppApiError(
                message, code=error.get("code"), http_status=response.status_code
            )
        return payload

    def _send(self, to, message_type, content):
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": message_type,
            message_type: content,
        }
        result = self._request("POST", f"{self.phone_number_id}/messages", json=payload)
        messages = result.get("messages") or [{}]
        wamid = messages[0].get("id")
        if not wamid:
            raise WhatsAppApiError("The API accepted the request but returned no message ID.")
        return wamid

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_phone_number(self):
        """Return the business phone number details (used by "Test connection")."""
        return self._request(
            "GET",
            self.phone_number_id,
            params={"fields": "display_phone_number,verified_name,quality_rating"},
        )

    def list_templates(self, page_size=100):
        """Return every message template of the WABA, following pagination."""
        if not self.waba_id:
            raise WhatsAppApiError("A WhatsApp Business Account ID is required to sync templates.")
        templates = []
        path = f"{self.waba_id}/message_templates"
        params = {
            "fields": "id,name,language,status,category,components",
            "limit": page_size,
        }
        while path:
            result = self._request("GET", path, params=params)
            templates.extend(result.get("data") or [])
            path = (result.get("paging") or {}).get("next")
            params = None  # "next" already carries the query string
        return templates

    def send_text(self, to, body, preview_url=False):
        """Send a free-form text message. Returns the ``wamid``."""
        return self._send(to, "text", {"body": body, "preview_url": preview_url})

    def send_template(self, to, name, language, body_params=None):
        """Send an approved template, filling the body ``{{n}}`` placeholders."""
        content = {"name": name, "language": {"code": language}}
        if body_params:
            content["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(v)} for v in body_params],
                }
            ]
        return self._send(to, "template", content)

    def upload_media(self, filename, content, mimetype):
        """Upload a file to Meta and return the media ID."""
        result = self._request(
            "POST",
            f"{self.phone_number_id}/media",
            timeout=MEDIA_UPLOAD_TIMEOUT,
            data={"messaging_product": "whatsapp", "type": mimetype},
            files={"file": (filename, content, mimetype)},
        )
        media_id = result.get("id")
        if not media_id:
            raise WhatsAppApiError("Media upload returned no media ID.")
        return media_id

    def send_document(self, to, media_id, filename, caption=None):
        """Send a previously uploaded document. Returns the ``wamid``."""
        content = {"id": media_id, "filename": filename}
        if caption:
            content["caption"] = caption
        return self._send(to, "document", content)
