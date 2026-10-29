# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import api, fields, models

from ..tools import normalize_phone


class ResPartner(models.Model):
    _name = "res.partner"
    _inherit = ["res.partner", "wa.thread.mixin"]

    wa_phone = fields.Char(
        string="WhatsApp Number",
        compute="_compute_wa_phone",
        store=True,
        index=True,
        help="Phone number normalised to E.164, used to match inbound WhatsApp messages.",
    )

    @api.depends("phone", "country_id", "company_id")
    def _compute_wa_phone(self):
        default_country = self.env.company.country_id
        for partner in self:
            country = partner.country_id or partner.company_id.country_id or default_country
            partner.wa_phone = normalize_phone(partner.phone, country.phone_code or None)

    @api.model
    def _wa_find_by_phone(self, e164_number):
        """Return the best contact for an E.164 number (individuals first)."""
        if not e164_number:
            return self.browse()
        partners = self.search([("wa_phone", "=", e164_number)], order="id")
        return partners.filtered(lambda p: not p.is_company)[:1] or partners[:1]

    def _wa_get_partner(self):
        self.ensure_one()
        return self

    def _wa_message_domain(self):
        self.ensure_one()
        return [
            "|",
            ("partner_id", "=", self.id),
            "&",
            ("res_model", "=", self._name),
            ("res_id", "=", self.id),
        ]
