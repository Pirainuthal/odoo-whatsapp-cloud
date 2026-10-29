# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Shared fixtures: a fake Graph API client and a configured account."""

import hashlib
import hmac
import itertools
import json

from odoo.tests import TransactionCase

from ..tools import WhatsAppApiError

APP_SECRET = "test-app-secret"
VERIFY_TOKEN = "test-verify-token"
PHONE_NUMBER_ID = "100200300400"


class FakeWhatsAppClient:
    """Drop-in replacement for WhatsAppCloudClient that never touches the network."""

    def __init__(self):
        self.calls = []
        self.templates = []
        self.error = None
        self._ids = itertools.count(1)

    def _record(self, *call):
        if self.error:
            raise WhatsAppApiError(self.error, code=131047, http_status=400)
        self.calls.append(call)
        return f"wamid.TEST{next(self._ids)}"

    def get_phone_number(self):
        if self.error:
            raise WhatsAppApiError(self.error, code=190, http_status=401)
        return {"display_phone_number": "+91 90000 00000", "verified_name": "Demo Shop"}

    def list_templates(self):
        return list(self.templates)

    def send_text(self, to, body, preview_url=False):
        return self._record("text", to, body)

    def send_template(self, to, name, language, body_params=None):
        return self._record("template", to, name, language, list(body_params or []))

    def upload_media(self, filename, content, mimetype):
        self._record("upload", filename, mimetype)
        return "media-1"

    def send_document(self, to, media_id, filename, caption=None):
        return self._record("document", to, media_id, filename, caption)


def meta_template(name, body, status="APPROVED", language="en_US", category="UTILITY", **extra):
    """Build a template dict shaped like the Graph API response."""
    components = [{"type": "BODY", "text": body}]
    components += extra.pop("components", [])
    return {
        "id": extra.pop("id", f"tpl-{name}-{language}"),
        "name": name,
        "language": language,
        "status": status,
        "category": category,
        "components": components,
    }


def sign(body):
    digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def webhook_payload(value):
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [{"id": "waba", "changes": [{"field": "messages", "value": value}]}],
        }
    ).encode()


class WhatsAppSetupMixin:
    """Mixed into TransactionCase / HttpCase test classes."""

    @classmethod
    def _setup_whatsapp(cls):
        cls.env.company.country_id = cls.env.ref("base.in")
        cls.account = cls.env["wa.account"].create(
            {
                "name": "Test Account",
                "phone_number_id": PHONE_NUMBER_ID,
                "waba_id": "999888777",
                "access_token": "test-token",
                "app_secret": APP_SECRET,
                "webhook_verify_token": VERIFY_TOKEN,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Kavya Raman",
                "phone": "098765 43210",
                "country_id": cls.env.ref("base.in").id,
            }
        )

    def _patch_client(self):
        self.fake_client = FakeWhatsAppClient()
        self.patch(type(self.env["wa.account"]), "_get_client", lambda account: self.fake_client)
        return self.fake_client


class WhatsAppCase(WhatsAppSetupMixin, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_whatsapp()

    def setUp(self):
        super().setUp()
        self._patch_client()
