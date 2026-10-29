# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import logging
from datetime import datetime, timezone

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.tools import html_escape

from ..tools import WhatsAppApiError, to_wa_id

_logger = logging.getLogger(__name__)

# Higher rank wins: Meta does not guarantee callback ordering, so a late
# "delivered" must never overwrite "read".
STATE_RANK = {"queued": 0, "failed": 1, "sent": 2, "delivered": 3, "read": 4, "received": 5}
INBOUND_PLACEHOLDERS = {
    "image": "Image",
    "video": "Video",
    "audio": "Audio message",
    "document": "Document",
    "sticker": "Sticker",
    "location": "Location",
    "contacts": "Contact card",
}


class WaMessage(models.Model):
    _name = "wa.message"
    _description = "WhatsApp Message"
    _order = "create_date desc, id desc"
    _rec_name = "phone"

    account_id = fields.Many2one("wa.account", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    direction = fields.Selection(
        [("outgoing", "Outgoing"), ("incoming", "Incoming")],
        required=True,
        default="outgoing",
        readonly=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("sent", "Sent"),
            ("delivered", "Delivered"),
            ("read", "Read"),
            ("failed", "Failed"),
            ("received", "Received"),
        ],
        required=True,
        default="queued",
        readonly=True,
        index=True,
    )
    message_type = fields.Selection(
        [
            ("text", "Text"),
            ("template", "Template"),
            ("document", "Document"),
            ("other", "Other"),
        ],
        required=True,
        default="text",
        readonly=True,
    )
    phone = fields.Char(required=True, readonly=True, help="Number in E.164 format.")
    partner_id = fields.Many2one("res.partner", index=True, readonly=True)
    body = fields.Text(readonly=True)
    template_id = fields.Many2one("wa.template", readonly=True, ondelete="set null")
    template_params = fields.Json(readonly=True)
    attachment_id = fields.Many2one("ir.attachment", readonly=True, ondelete="set null")
    wamid = fields.Char(string="WhatsApp Message ID", readonly=True, index=True, copy=False)
    error_message = fields.Text(readonly=True)
    status_date = fields.Datetime(readonly=True)
    res_model = fields.Char(string="Document Model", readonly=True, index=True)
    res_id = fields.Many2oneReference(
        string="Document ID", model_field="res_model", readonly=True, index=True
    )
    record_name = fields.Char(compute="_compute_record_name")
    mail_message_id = fields.Many2one("mail.message", readonly=True, ondelete="set null")

    _wamid_unique = models.Constraint("UNIQUE(wamid)", "This WhatsApp message is already logged.")

    def _compute_record_name(self):
        for message in self:
            record = message._get_record()
            message.record_name = record.display_name if record else False

    def _get_record(self):
        self.ensure_one()
        if not self.res_model or not self.res_id or self.res_model not in self.env:
            return None
        record = self.env[self.res_model].browse(self.res_id).exists()
        return record or None

    def action_open_record(self):
        self.ensure_one()
        record = self._get_record() or self.partner_id
        if not record:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": record._name,
            "res_id": record.id,
            "view_mode": "form",
        }

    def action_retry(self):
        failed = self.filtered(lambda m: m.direction == "outgoing" and m.state == "failed")
        failed._check_send_access()
        failed.sudo().write({"state": "queued", "error_message": False})
        failed._send()

    # ------------------------------------------------------------------
    # Outgoing
    # ------------------------------------------------------------------
    def _check_send_access(self):
        """Make sure the current user may read what is about to leave Odoo.

        ``wa.message`` rows can be created over RPC with any document and
        attachment, so before data is uploaded to Meta (and logged on a chatter,
        which bypasses access rights) we check the user can read both.
        """
        for message in self:
            record = message._get_record()
            if record:
                record.check_access("read")
            if message.attachment_id:
                message.attachment_id.check_access("read")

    def _send(self):
        """Send every queued outgoing message, logging success or failure."""
        to_send = self.filtered(lambda m: m.direction == "outgoing" and m.state == "queued")
        to_send._check_send_access()
        for message in to_send:
            try:
                wamid = message._send_one(message.account_id._get_client())
            except WhatsAppApiError as exc:
                vals = {"state": "failed", "error_message": str(exc)}
            else:
                vals = {"state": "sent", "wamid": wamid, "status_date": fields.Datetime.now()}
            # Delivery bookkeeping is not something users edit by hand.
            message.sudo().write(vals)
            message._log_on_record()

    def _send_one(self, client):
        to = to_wa_id(self.phone)
        if self.message_type == "template":
            template = self.template_id
            return client.send_template(
                to, template.name, template.language, self.template_params or []
            )
        if self.message_type == "document":
            attachment = self.attachment_id  # read with the user's rights
            media_id = client.upload_media(
                attachment.name, attachment.raw, attachment.mimetype or "application/pdf"
            )
            return client.send_document(to, media_id, attachment.name, caption=self.body or None)
        return client.send_text(to, self.body or "")

    def _log_on_record(self):
        """Mirror the message in the chatter of its document."""
        self.ensure_one()
        record = self._get_record()
        if not record or not hasattr(record, "_message_log"):
            return
        if self.state == "failed":
            status = Markup('<span class="text-danger">%s</span>') % _(
                "WhatsApp failed: %s", self.error_message
            )
        else:
            status = _("WhatsApp sent to %s", self.phone)
        body = Markup("<p><b>%s</b></p><p>%s</p>") % (
            status,
            Markup("<br/>").join(html_escape(self.body or "").split("\n")),
        )
        mail_message = record._message_log(
            body=body,
            attachment_ids=self.attachment_id.ids,
        )
        self.sudo().mail_message_id = mail_message

    # ------------------------------------------------------------------
    # Incoming (webhook)
    # ------------------------------------------------------------------
    @api.model
    def _process_inbound(self, account, data, profile_name=None):
        """Store an inbound message and post it on the matching contact."""
        wamid = data.get("id")
        if wamid and self.search_count([("wamid", "=", wamid)]):
            return self.browse()  # Meta retries deliveries: ignore duplicates
        phone = account._normalize_phone("+" + (data.get("from") or "").lstrip("+"))
        if not phone:
            _logger.warning("Ignoring WhatsApp message with invalid sender %r", data.get("from"))
            return self.browse()

        partner = self.env["res.partner"]._wa_find_by_phone(phone)
        if not partner and account.create_unknown_partner:
            partner = self.env["res.partner"].create(
                {"name": profile_name or phone, "phone": phone, "company_id": False}
            )

        body = self._inbound_body(data)
        message = self.create(
            {
                "account_id": account.id,
                "direction": "incoming",
                "state": "received",
                "message_type": "text" if data.get("type") == "text" else "other",
                "phone": phone,
                "partner_id": partner.id,
                "body": body,
                "wamid": wamid,
                "status_date": self._timestamp_to_datetime(data.get("timestamp")),
                "res_model": "res.partner" if partner else False,
                "res_id": partner.id,
            }
        )
        if partner:
            message.mail_message_id = partner.message_post(
                body=Markup("<p><b>%s</b></p><p>%s</p>")
                % (
                    _("WhatsApp from %s", phone),
                    Markup("<br/>").join(html_escape(body).split("\n")),
                ),
                author_id=partner.id,
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        return message

    @api.model
    def _inbound_body(self, data):
        msg_type = data.get("type")
        if msg_type == "text":
            return (data.get("text") or {}).get("body") or ""
        if msg_type in ("button", "interactive"):
            payload = data.get("button") or data.get("interactive") or {}
            reply = payload.get("button_reply") or payload.get("list_reply") or payload
            return reply.get("title") or reply.get("text") or ""
        label = INBOUND_PLACEHOLDERS.get(msg_type, msg_type or "Unsupported message")
        caption = (data.get(msg_type) or {}).get("caption") if msg_type else None
        return f"[{label}] {caption}" if caption else f"[{label}]"

    @api.model
    def _process_status(self, data):
        """Apply a ``sent/delivered/read/failed`` callback to the matching log line."""
        message = self.search([("wamid", "=", data.get("id"))], limit=1)
        new_state = data.get("status")
        if not message or new_state not in STATE_RANK or message.state == new_state:
            return message
        if new_state != "failed" and (
            STATE_RANK[new_state] <= STATE_RANK[message.state]
            # A late "sent" must not hide an earlier "failed" callback.
            or (message.state == "failed" and new_state == "sent")
        ):
            return message
        vals = {
            "state": new_state,
            "status_date": self._timestamp_to_datetime(data.get("timestamp")),
        }
        if new_state == "failed":
            errors = data.get("errors") or [{}]
            error = errors[0]
            details = (error.get("error_data") or {}).get("details")
            vals["error_message"] = " - ".join(
                str(p) for p in (error.get("code"), error.get("title"), details) if p
            ) or _("Delivery failed")
        message.write(vals)
        if new_state == "failed":
            message._log_on_record()
        return message

    @staticmethod
    def _timestamp_to_datetime(timestamp):
        try:
            return datetime.fromtimestamp(int(timestamp), timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            return fields.Datetime.now()
