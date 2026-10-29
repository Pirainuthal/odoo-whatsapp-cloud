# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "wa.thread.mixin"]

    def _wa_get_report(self):
        return (
            self.env.ref("sale.action_report_saleorder", raise_if_not_found=False)
            or super()._wa_get_report()
        )
