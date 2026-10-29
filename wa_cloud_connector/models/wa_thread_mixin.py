# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import _, fields, models


class WaThreadMixin(models.AbstractModel):
    """Adds "Send WhatsApp" and a WhatsApp history counter to a document."""

    _name = "wa.thread.mixin"
    _description = "WhatsApp-enabled Document"

    wa_message_count = fields.Integer(
        string="WhatsApp Messages",
        compute="_compute_wa_message_count",
        groups="wa_cloud_connector.group_wa_user",
    )

    def _compute_wa_message_count(self):
        Message = self.env["wa.message"]
        for record in self:
            record.wa_message_count = (
                Message.search_count(record._wa_message_domain()) if record.id else 0
            )

    # ------------------------------------------------------------------
    # Hooks overridden by each document model
    # ------------------------------------------------------------------
    def _wa_get_partner(self):
        """Return the contact that should receive the message."""
        self.ensure_one()
        return self["partner_id"] if "partner_id" in self._fields else self.env["res.partner"]

    def _wa_get_report(self):
        """Return the ``ir.actions.report`` attached by default, if any."""
        return self.env["ir.actions.report"]

    def _wa_message_domain(self):
        self.ensure_one()
        return [("res_model", "=", self._name), ("res_id", "=", self.id)]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_wa_send(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Send WhatsApp"),
            "res_model": "wa.composer",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }

    def action_wa_view_messages(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "wa_cloud_connector.wa_message_action"
        )
        action["domain"] = self._wa_message_domain()
        action["context"] = {"create": False}
        return action
