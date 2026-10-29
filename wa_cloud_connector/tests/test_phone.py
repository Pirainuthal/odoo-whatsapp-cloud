# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo.tests import tagged

from ..tools import normalize_phone, to_wa_id
from .common import WhatsAppCase


@tagged("post_install", "-at_install")
class TestPhoneNormalization(WhatsAppCase):
    def test_normalize_phone_formats(self):
        cases = [
            # (raw input, default country code, expected)
            ("+91 98765 43210", None, "+919876543210"),
            ("+91-98765-43210", "91", "+919876543210"),
            ("0091 98765 43210", "91", "+919876543210"),
            ("098765 43210", "91", "+919876543210"),
            ("98765 43210", "91", "+919876543210"),
            ("98765 43210", "+91", "+919876543210"),
            ("919876543210", "91", "+919876543210"),
            ("(415) 555-2671", 1, "+14155552671"),
            ("+44 20 7946 0958", "91", "+442079460958"),
        ]
        for raw, country, expected in cases:
            with self.subTest(raw=raw, country=country):
                self.assertEqual(normalize_phone(raw, country), expected)

    def test_normalize_phone_rejects_invalid(self):
        for raw, country in [
            (None, "91"),
            ("", "91"),
            ("abc", "91"),
            ("12345", "91"),  # too short
            ("98765 43210", None),  # national number without a country
            ("+1234567890123456", None),  # more than 15 digits
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_phone(raw, country))

    def test_to_wa_id(self):
        self.assertEqual(to_wa_id("+919876543210"), "919876543210")

    def test_partner_wa_phone_is_computed(self):
        self.assertEqual(self.partner.wa_phone, "+919876543210")
        self.partner.phone = "+1 415 555 2671"
        self.assertEqual(self.partner.wa_phone, "+14155552671")

    def test_partner_without_country_uses_company_country(self):
        partner = self.env["res.partner"].create({"name": "No Country", "phone": "90000 11111"})
        self.assertEqual(partner.wa_phone, "+919000011111")

    def test_find_partner_by_phone_prefers_individuals(self):
        Partner = self.env["res.partner"]
        company = Partner.create(
            {"name": "Raman Traders", "is_company": True, "phone": "+91 90000 22222"}
        )
        person = Partner.create({"name": "Arun", "phone": "90000 22222"})
        self.assertEqual(company.wa_phone, person.wa_phone)
        # The company was created first, but an individual is the better match.
        self.assertEqual(Partner._wa_find_by_phone("+919000022222"), person)
        self.assertEqual(Partner._wa_find_by_phone("+919876543210"), self.partner)
        self.assertFalse(Partner._wa_find_by_phone("+911111111111"))

    def test_account_normalize_uses_partner_country(self):
        us_partner = self.env["res.partner"].create(
            {"name": "US", "country_id": self.env.ref("base.us").id}
        )
        self.assertEqual(
            self.account._normalize_phone("(415) 555-2671", us_partner), "+14155552671"
        )
        self.assertEqual(self.account._normalize_phone("98765 43210"), "+919876543210")
