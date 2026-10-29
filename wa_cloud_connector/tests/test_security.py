# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Access-right and webhook-hardening regressions."""

import json

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, new_test_user, tagged

from .common import PHONE_NUMBER_ID, WhatsAppCase, sign, webhook_payload


@tagged("post_install", "-at_install")
class TestSecurity(WhatsAppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.wa_user = new_test_user(
            cls.env,
            login="wa_salesman",
            groups="base.group_user,sales_team.group_sale_salesman_all_leads,"
            "wa_cloud_connector.group_wa_user",
        )
        cls.wa_manager = new_test_user(
            cls.env,
            login="wa_manager",
            groups="base.group_user,wa_cloud_connector.group_wa_manager",
        )
        product = cls.env["product.product"].create({"name": "Desk", "list_price": 100.0})
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 1})],
            }
        )
        # An attachment that is not linked to any document is private to its creator.
        cls.private_attachment = cls.env["ir.attachment"].create(
            {"name": "payroll.pdf", "raw": b"%PDF-1.4 secret", "mimetype": "application/pdf"}
        )

    def _send_as(self, user, record, **values):
        action = record.with_user(user).action_wa_send()
        form = Form(self.env["wa.composer"].with_user(user).with_context(**action["context"]))
        for name, value in values.items():
            setattr(form, name, value)
        return form.save().action_send()

    # ------------------------------------------------------------------
    # Regular users
    # ------------------------------------------------------------------
    def test_user_can_send_text_and_pdf(self):
        result = self._send_as(self.wa_user, self.order, mode="text", body="Your quote")
        self.assertEqual(result["params"]["type"], "success")
        self.assertEqual([c[0] for c in self.fake_client.calls], ["upload", "document"])
        log = self.env["wa.message"].search([("res_model", "=", "sale.order")])
        self.assertEqual(log.state, "sent")
        self.assertTrue(log.mail_message_id)

    def test_user_cannot_edit_message_log(self):
        message = self.env["wa.message"].create(
            {"account_id": self.account.id, "phone": "+919876543210", "body": "Hi"}
        )
        with self.assertRaises(AccessError):
            message.with_user(self.wa_user).write({"state": "read"})

    def test_user_cannot_exfiltrate_unreadable_attachment(self):
        """A crafted log line must not upload a file the user cannot read."""
        message = (
            self.env["wa.message"]
            .with_user(self.wa_user)
            .create(
                {
                    "account_id": self.account.id,
                    "phone": "+919000000001",
                    "message_type": "document",
                    "state": "failed",
                    "attachment_id": self.private_attachment.id,
                    "res_model": "res.partner",
                    "res_id": self.partner.id,
                }
            )
        )
        with self.assertRaises(AccessError):
            message.action_retry()
        self.assertFalse(self.fake_client.calls)

    def test_report_must_match_document(self):
        composer = self.env["wa.composer"].create(
            {
                "res_model": "sale.order",
                "res_id": self.order.id,
                "mode": "text",
                "body": "Hi",
                "attach_report": True,
                "report_id": self.env.ref("account.account_invoices").id,
            }
        )
        with self.assertRaisesRegex(UserError, "does not apply"):
            composer.action_send()
        self.assertFalse(self.fake_client.calls)

    def test_composer_ignores_models_without_whatsapp(self):
        composer = self.env["wa.composer"].create(
            {"res_model": "res.users", "res_id": self.env.user.id, "mode": "text", "body": "Hi"}
        )
        self.assertFalse(composer.partner_id)
        with self.assertRaisesRegex(UserError, "no longer exists"):
            composer.action_send()

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------
    def test_verify_token_rotation_requires_settings_admin(self):
        with self.assertRaises(AccessError):
            self.account.with_user(self.wa_manager).action_regenerate_verify_token()
        old = self.account.webhook_verify_token
        self.account.action_regenerate_verify_token()
        self.assertNotEqual(self.account.webhook_verify_token, old)

    # ------------------------------------------------------------------
    # Webhook hardening
    # ------------------------------------------------------------------
    def test_non_ascii_tokens_and_signatures_are_rejected_cleanly(self):
        Account = self.env["wa.account"]
        self.assertFalse(Account._webhook_check_verify_token("tökén"))
        self.assertFalse(Account._webhook_check_signature(b"{}", "sha256=ünïcode"))

    def test_payload_signed_by_another_app_is_ignored(self):
        other = self.env["wa.account"].create(
            {
                "name": "Other App",
                "phone_number_id": "555000555",
                "waba_id": "555",
                "access_token": "other-token",
                "app_secret": "other-secret",
            }
        )
        body = webhook_payload(
            {
                "metadata": {"phone_number_id": other.phone_number_id},
                "messages": [
                    {
                        "from": "919876543210",
                        "id": "wamid.SPOOF",
                        "type": "text",
                        "text": {"body": "Forged"},
                    }
                ],
            }
        )
        signed_by = self.env["wa.account"]._webhook_check_signature(body, sign(body))
        self.assertEqual(signed_by, self.account)  # signed with the first app's secret
        self.env["wa.account"]._webhook_process(json.loads(body), allowed_accounts=signed_by)
        self.assertFalse(self.env["wa.message"].search([("wamid", "=", "wamid.SPOOF")]))

    def test_late_sent_does_not_hide_failure(self):
        message = self.env["wa.message"].create(
            {
                "account_id": self.account.id,
                "phone": "+919876543210",
                "body": "Hi",
                "state": "failed",
                "wamid": "wamid.F1",
            }
        )
        payload = json.loads(
            webhook_payload(
                {
                    "metadata": {"phone_number_id": PHONE_NUMBER_ID},
                    "statuses": [{"id": "wamid.F1", "status": "sent"}],
                }
            )
        )
        self.env["wa.account"]._webhook_process(payload)
        self.assertEqual(message.state, "failed")
