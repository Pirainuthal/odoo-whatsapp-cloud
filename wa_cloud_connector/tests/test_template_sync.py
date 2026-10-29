# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models.wa_template import placeholder_numbers, render_placeholders
from ..tools import WhatsAppApiError
from .common import WhatsAppCase, meta_template


@tagged("post_install", "-at_install")
class TestTemplateSync(WhatsAppCase):
    def test_placeholder_helpers(self):
        body = "Hi {{1}}, order {{2}} total {{ 3 }}. Thanks {{1}}!"
        self.assertEqual(placeholder_numbers(body), [1, 2, 3])
        self.assertEqual(
            render_placeholders(body, {1: "Kavya", 2: "S001"}),
            "Hi Kavya, order S001 total {{ 3 }}. Thanks Kavya!",
        )

    def test_parse_meta_template(self):
        data = meta_template(
            "order_update",
            "Hello {{1}}, your order {{2}} is ready.",
            components=[
                {"type": "HEADER", "format": "TEXT", "text": "Order update"},
                {"type": "FOOTER", "text": "Reply STOP to opt out"},
                {"type": "BUTTONS", "buttons": []},
            ],
        )
        vals = self.env["wa.template"]._parse_meta_template(data)
        self.assertEqual(vals["name"], "order_update")
        self.assertEqual(vals["language"], "en_US")
        self.assertEqual(vals["status"], "approved")
        self.assertEqual(vals["category"], "utility")
        self.assertEqual(vals["header_text"], "Order update")
        self.assertEqual(vals["footer_text"], "Reply STOP to opt out")
        self.assertEqual(vals["body"], "Hello {{1}}, your order {{2}} is ready.")

    def test_parse_unknown_status_and_media_header(self):
        data = meta_template(
            "promo",
            "Sale!",
            status="IN_APPEAL",
            category="SOMETHING_NEW",
            components=[{"type": "HEADER", "format": "IMAGE"}],
        )
        vals = self.env["wa.template"]._parse_meta_template(data)
        self.assertEqual(vals["status"], "other")
        self.assertFalse(vals["category"])
        self.assertFalse(vals["header_text"])

    def test_sync_creates_updates_and_archives(self):
        Template = self.env["wa.template"].with_context(active_test=False)
        self.fake_client.templates = [
            meta_template("order_ready", "Hi {{1}}, order {{2}} is ready."),
            meta_template("welcome", "Welcome!", status="PENDING", language="ta"),
        ]
        stats = Template._sync_from_meta(self.account)
        self.assertEqual(stats, {"created": 2, "updated": 0, "archived": 0})
        order_ready = Template.search([("name", "=", "order_ready")])
        self.assertEqual(order_ready.placeholder_count, 2)
        self.assertEqual(order_ready.display_name, "order_ready (en_US)")
        self.assertEqual(self.account.template_count, 2)

        # Second run: one template changed, one removed in Meta.
        self.fake_client.templates = [
            meta_template("order_ready", "Hi {{1}}, order {{2}} for {{3}} is ready.")
        ]
        stats = Template._sync_from_meta(self.account)
        self.assertEqual(stats, {"created": 0, "updated": 1, "archived": 1})
        self.assertEqual(order_ready.placeholder_count, 3)
        self.assertFalse(Template.search([("name", "=", "welcome")]).active)

    def test_sync_action_reports_api_errors(self):
        def broken(*_args, **_kwargs):
            raise WhatsAppApiError("Token expired")

        self.fake_client.list_templates = broken
        with self.assertRaisesRegex(UserError, "Token expired"):
            self.account.action_sync_templates()

    def test_test_connection_updates_account(self):
        action = self.account.action_test_connection()
        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual(self.account.connection_state, "ok")
        self.assertEqual(self.account.verified_name, "Demo Shop")

        self.fake_client.error = "Invalid OAuth access token"
        action = self.account.action_test_connection()
        self.assertEqual(action["params"]["type"], "danger")
        self.assertEqual(self.account.connection_state, "error")

    def test_variable_field_path_resolution(self):
        template = self.env["wa.template"].create(
            {
                "account_id": self.account.id,
                "name": "greeting",
                "language": "en",
                "body": "Hi {{1}} from {{2}} ({{3}})",
                "variable_ids": [
                    (0, 0, {"position": 1, "field_path": "name"}),
                    (0, 0, {"position": 2, "field_path": "country_id"}),
                    (0, 0, {"position": 3, "field_path": "email", "default_value": "n/a"}),
                ],
            }
        )
        values = template._get_placeholder_values(self.partner)
        self.assertEqual(values, {1: "Kavya Raman", 2: "India", 3: "n/a"})
