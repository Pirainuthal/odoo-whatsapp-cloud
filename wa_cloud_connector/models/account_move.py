# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo import models


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "wa.thread.mixin"]

    def _wa_get_report(self):
        if self.is_invoice(include_receipts=True):
            report = self.env.ref("account.account_invoices", raise_if_not_found=False)
            if report:
                return report
        return super()._wa_get_report()
