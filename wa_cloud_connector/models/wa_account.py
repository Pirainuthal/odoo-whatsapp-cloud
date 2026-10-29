# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import hashlib
import hmac
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import WhatsAppApiError, WhatsAppCloudClient, normalize_phone
from ..tools.graph_client import DEFAULT_API_VERSION

_logger = logging.getLogger(__name__)


class WaAccount(models.Model):
    _name = "wa.account"
    _description = "WhatsApp Cloud API Account"
    _order = "sequence, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    phone_number_id = fields.Char(
        string="Phone Number ID",
        required=True,
        help="Meta > WhatsApp > API Setup > Phone number ID.",
    )
    waba_id = fields.Char(
        string="WhatsApp Business Account ID",
        required=True,
        help="Needed to synchronise message templates.",
    )
    api_version = fields.Char(
        string="Graph API Version", default=DEFAULT_API_VERSION, required=True
    )
    access_token = fields.Char(
        groups="base.group_system",
        copy=False,
        help="Permanent System User access token with whatsapp_business_messaging "
        "and whatsapp_business_management permissions.",
    )
    app_secret = fields.Char(
        groups="base.group_system",
        copy=False,
        help="Meta app secret, used to verify the X-Hub-Signature-256 header of webhook calls.",
    )
    webhook_verify_token = fields.Char(
        groups="base.group_system",
        copy=False,
        default=lambda self: secrets.token_urlsafe(24),
        help="Any random string; paste the same value in the Meta webhook configuration.",
    )
    webhook_url = fields.Char(compute="_compute_webhook_url")
    create_unknown_partner = fields.Boolean(
        string="Create Contacts for Unknown Numbers",
        help="When an inbound message comes from a number that matches no contact, "
        "create one using the WhatsApp profile name.",
    )
    display_phone_number = fields.Char(readonly=True, copy=False)
    verified_name = fields.Char(readonly=True, copy=False)
    connection_state = fields.Selection(
        [("unknown", "Not tested"), ("ok", "Connected"), ("error", "Error")],
        default="unknown",
        readonly=True,
        copy=False,
    )
    template_ids = fields.One2many("wa.template", "account_id", string="Templates")
    template_count = fields.Integer(compute="_compute_template_count")

    _phone_number_unique = models.Constraint(
        "UNIQUE(phone_number_id)",
        "This phone number ID is already configured on another account.",
    )

    def _compute_webhook_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        for account in self:
            account.webhook_url = f"{base_url.rstrip('/')}/wa_cloud/webhook"

    @api.depends("template_ids")
    def _compute_template_count(self):
        counts = dict(
            self.env["wa.template"]._read_group(
                [("account_id", "in", self.ids)], ["account_id"], ["__count"]
            )
        )
        for account in self:
            account.template_count = counts.get(account, 0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _get_default_account(self, company=None):
        """Account of ``company`` (current company by default), else any visible one."""
        company = company or self.env.company
        return self.search([("company_id", "=", company.id)], limit=1) or self.search([], limit=1)

    def _get_client(self):
        """Return the Graph API client. Tests patch this single entry point."""
        self.ensure_one()
        account = self.sudo()
        if not account.access_token:
            raise UserError(_("Set an access token on the WhatsApp account %s.", self.name))
        return WhatsAppCloudClient(
            access_token=account.access_token,
            phone_number_id=account.phone_number_id,
            waba_id=account.waba_id,
            api_version=account.api_version,
        )

    def _normalize_phone(self, number, partner=None):
        """Normalise ``number`` to E.164 using the partner or company country."""
        country = (partner and partner.country_id) or (
            self.company_id or self.env.company
        ).country_id
        return normalize_phone(number, country.phone_code if country else None)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        try:
            info = self._get_client().get_phone_number()
        except WhatsAppApiError as exc:
            self.connection_state = "error"
            return self._notify(_("Connection failed"), str(exc), "danger")
        self.write(
            {
                "connection_state": "ok",
                "display_phone_number": info.get("display_phone_number"),
                "verified_name": info.get("verified_name"),
            }
        )
        return self._notify(
            _("Connection successful"),
            _(
                "Connected to %(name)s (%(number)s).",
                name=info.get("verified_name") or "-",
                number=info.get("display_phone_number") or "-",
            ),
            "success",
        )

    def action_sync_templates(self):
        self.ensure_one()
        try:
            stats = self.env["wa.template"]._sync_from_meta(self)
        except WhatsAppApiError as exc:
            raise UserError(_("Template synchronisation failed: %s", exc)) from exc
        return self._notify(
            _("Templates synchronised"),
            _(
                "%(created)s created, %(updated)s updated, %(archived)s archived.",
                **stats,
            ),
            "success",
        )

    def action_view_templates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "wa_cloud_connector.wa_template_action"
        )
        action["domain"] = [("account_id", "=", self.id)]
        action["context"] = {"default_account_id": self.id}
        return action

    def action_regenerate_verify_token(self):
        # No sudo: the field is restricted to base.group_system, so the ORM
        # refuses this write for anyone else (the method is reachable over RPC).
        for account in self:
            account.webhook_verify_token = secrets.token_urlsafe(24)

    @staticmethod
    def _notify(title, message, kind):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": kind,
                "sticky": kind == "danger",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    # ------------------------------------------------------------------
    # Webhook
    # ------------------------------------------------------------------
    @api.model
    def _webhook_check_verify_token(self, token):
        if not token:
            return False
        token = token.encode()
        tokens = self.sudo().search([]).mapped("webhook_verify_token")
        return any(t and hmac.compare_digest(t.encode(), token) for t in tokens)

    @api.model
    def _webhook_check_signature(self, raw_body, signature_header):
        """Validate ``X-Hub-Signature-256`` against the configured app secrets.

        :returns: the active accounts whose app secret produced the signature
            (an empty recordset when the signature is missing or invalid).
        """
        accounts = self.sudo().browse()
        if not signature_header or not signature_header.startswith("sha256="):
            return accounts
        received = signature_header.split("=", 1)[1].strip().encode()
        for account in self.sudo().search([("app_secret", "!=", False)]):
            expected = hmac.new(account.app_secret.encode(), raw_body, hashlib.sha256)
            if hmac.compare_digest(expected.hexdigest().encode(), received):
                accounts |= account
        return accounts

    @api.model
    def _webhook_process(self, payload, allowed_accounts=None):
        """Dispatch a (signature-checked) webhook payload.

        Payload shape: ``{"object": "whatsapp_business_account", "entry": [
        {"changes": [{"field": "messages", "value": {...}}]}]}``

        :param allowed_accounts: accounts whose app secret signed the payload.
            Changes addressed to any other phone number ID are ignored, so a
            payload signed with one Meta app cannot inject messages into an
            account that belongs to another app.
        """
        if payload.get("object") != "whatsapp_business_account":
            return
        Message = self.env["wa.message"].sudo()
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                if change.get("field") != "messages":
                    continue
                value = change.get("value") or {}
                phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
                account = self.sudo().search([("phone_number_id", "=", phone_number_id)], limit=1)
                if not account:
                    _logger.warning(
                        "WhatsApp webhook for unknown phone number ID %s", phone_number_id
                    )
                    continue
                if allowed_accounts is not None and account not in allowed_accounts:
                    _logger.warning(
                        "WhatsApp webhook for phone number ID %s was not signed with its "
                        "app secret; ignored",
                        phone_number_id,
                    )
                    continue
                contacts = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name")
                    for c in value.get("contacts") or []
                }
                for message in value.get("messages") or []:
                    Message._process_inbound(account, message, contacts.get(message.get("from")))
                for status in value.get("statuses") or []:
                    Message._process_status(status)
