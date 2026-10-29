# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import WhatsAppCase


@tagged("post_install", "-at_install")
class TestSendFlow(WhatsAppCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        product = cls.env["product.product"].create({"name": "Teak Chair", "list_price": 2500.0})
        cls.order = cls.env["sale.order"].create(
            {
                "partner_id": cls.partner.id,
                "order_line": [(0, 0, {"product_id": product.id, "product_uom_qty": 2})],
            }
        )
        cls.template = cls.env["wa.template"].create(
            {
                "account_id": cls.account.id,
                "name": "quote_ready",
                "language": "en_US",
                "status": "approved",
                "category": "utility",
                "body": "Hi {{1}}, your quotation {{2}} is ready.",
                "variable_ids": [
                    (0, 0, {"position": 1, "field_path": "partner_id.name"}),
                    (0, 0, {"position": 2, "field_path": "name"}),
                ],
            }
        )

    def _composer(self, record, **values):
        action = record.action_wa_send()
        form = Form(self.env["wa.composer"].with_context(**action["context"]))
        for name, value in values.items():
            setattr(form, name, value)
        return form.save()

    def _chatter_bodies(self, record):
        return "\n".join(record.message_ids.mapped("body"))

    # ------------------------------------------------------------------
    # Composer defaults
    # ------------------------------------------------------------------
    def test_composer_defaults_from_sale_order(self):
        composer = self._composer(self.order, template_id=self.template)
        self.assertEqual(composer.partner_id, self.partner)
        self.assertEqual(composer.phone, "+919876543210")
        self.assertEqual(composer.account_id, self.account)
        self.assertEqual(composer.report_id, self.env.ref("sale.action_report_saleorder"))
        self.assertTrue(composer.attach_report)
        self.assertEqual(
            composer.variable_ids.sorted("position").mapped("value"),
            ["Kavya Raman", self.order.name],
        )
        self.assertEqual(
            composer.preview, f"Hi Kavya Raman, your quotation {self.order.name} is ready."
        )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def test_send_template_creates_log_and_chatter_note(self):
        composer = self._composer(self.order, template_id=self.template, attach_report=False)
        action = composer.action_send()
        self.assertEqual(action["params"]["type"], "success")

        self.assertEqual(
            self.fake_client.calls,
            [
                (
                    "template",
                    "919876543210",
                    "quote_ready",
                    "en_US",
                    ["Kavya Raman", self.order.name],
                )
            ],
        )
        log = self.env["wa.message"].search(
            [("res_model", "=", "sale.order"), ("res_id", "=", self.order.id)]
        )
        self.assertEqual(len(log), 1)
        self.assertRecordValues(
            log,
            [
                {
                    "direction": "outgoing",
                    "state": "sent",
                    "message_type": "template",
                    "phone": "+919876543210",
                    "partner_id": self.partner.id,
                    "wamid": "wamid.TEST1",
                    "template_id": self.template.id,
                }
            ],
        )
        self.assertIn("your quotation", log.body)
        self.assertTrue(log.mail_message_id)
        self.assertIn(log.mail_message_id, self.order.message_ids)
        self.assertIn("WhatsApp sent to +919876543210", self._chatter_bodies(self.order))
        self.assertEqual(self.order.wa_message_count, 1)

    def test_send_template_with_pdf_uploads_document(self):
        composer = self._composer(self.order, template_id=self.template)
        composer.action_send()

        kinds = [call[0] for call in self.fake_client.calls]
        self.assertEqual(kinds, ["template", "upload", "document"])
        _kind, filename, mimetype = self.fake_client.calls[1]
        self.assertTrue(filename.endswith(".pdf"))
        self.assertEqual(mimetype, "application/pdf")

        logs = self.env["wa.message"].search(
            [("res_id", "=", self.order.id), ("res_model", "=", "sale.order")]
        )
        self.assertEqual(sorted(logs.mapped("message_type")), ["document", "template"])
        document_log = logs.filtered(lambda m: m.message_type == "document")
        self.assertEqual(document_log.attachment_id.res_model, "sale.order")
        self.assertEqual(document_log.attachment_id.res_id, self.order.id)
        self.assertIn(document_log.attachment_id, document_log.mail_message_id.attachment_ids)

    def test_send_free_text_from_partner(self):
        composer = self._composer(self.partner, mode="text", body="Hello!\nSee you soon.")
        self.assertFalse(composer.attach_report)  # contacts have no default report
        composer.action_send()

        self.assertEqual(
            self.fake_client.calls, [("text", "919876543210", "Hello!\nSee you soon.")]
        )
        log = self.env["wa.message"].search([("partner_id", "=", self.partner.id)])
        self.assertEqual(log.message_type, "text")
        self.assertEqual(log.state, "sent")
        self.assertIn("See you soon.", self._chatter_bodies(self.partner))
        self.assertEqual(self.partner.wa_message_count, 1)

    def test_free_text_with_pdf_uses_caption(self):
        composer = self._composer(self.order, mode="text", body="Your quote")
        composer.action_send()
        self.assertEqual([c[0] for c in self.fake_client.calls], ["upload", "document"])
        self.assertEqual(self.fake_client.calls[1][-1], "Your quote")

    def test_send_failure_is_logged_not_raised(self):
        self.fake_client.error = "Re-engagement message"
        composer = self._composer(self.order, template_id=self.template, attach_report=False)
        action = composer.action_send()

        self.assertEqual(action["params"]["type"], "danger")
        self.assertIn("Re-engagement message", action["params"]["message"])
        log = self.env["wa.message"].search(
            [("res_id", "=", self.order.id), ("res_model", "=", "sale.order")]
        )
        self.assertEqual(log.state, "failed")
        self.assertIn("131047", log.error_message)
        self.assertIn("WhatsApp failed", self._chatter_bodies(self.order))

        # Retry once the problem is fixed.
        self.fake_client.error = None
        log.action_retry()
        self.assertEqual(log.state, "sent")
        self.assertFalse(log.error_message)

    def test_send_validations(self):
        # The form view already makes the template required; the server must too.
        no_template = self.env["wa.composer"].create(
            {"res_model": "sale.order", "res_id": self.order.id, "attach_report": False}
        )
        with self.assertRaisesRegex(UserError, "Choose a template"):
            no_template.action_send()
        with self.assertRaisesRegex(UserError, "Write a message"):
            self._composer(self.partner, mode="text", body=" ").action_send()
        with self.assertRaisesRegex(UserError, "not a valid phone number"):
            self._composer(self.partner, mode="text", body="Hi", phone="123").action_send()
        composer = self._composer(self.order, template_id=self.template, attach_report=False)
        composer.variable_ids[0].value = ""
        with self.assertRaisesRegex(UserError, "placeholder"):
            composer.action_send()
        self.assertFalse(self.fake_client.calls)

    def test_send_without_account(self):
        self.account.active = False
        composer = self.env["wa.composer"].create(
            {"res_model": "res.partner", "res_id": self.partner.id, "mode": "text", "body": "Hi"}
        )
        self.assertFalse(composer.account_id)
        with self.assertRaisesRegex(UserError, "Configure a WhatsApp account"):
            composer.action_send()

    def test_service_window(self):
        composer = self._composer(self.partner, mode="text", body="Hi")
        self.assertFalse(composer.in_service_window)
        self.env["wa.message"].create(
            {
                "account_id": self.account.id,
                "direction": "incoming",
                "state": "received",
                "phone": "+919876543210",
                "body": "Is my order ready?",
            }
        )
        composer.invalidate_recordset(["in_service_window", "last_inbound_date"])
        self.assertTrue(composer.in_service_window)
        self.assertTrue(composer.last_inbound_date)

    def test_send_from_invoice(self):
        journal = self.env["account.journal"].search(
            [("type", "=", "sale"), ("company_id", "=", self.env.company.id)], limit=1
        )
        if not journal:
            self.skipTest("No sales journal (no chart of accounts installed)")
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                "journal_id": journal.id,
                "invoice_line_ids": [(0, 0, {"name": "Service", "quantity": 1, "price_unit": 999})],
            }
        )
        self.assertEqual(invoice._wa_get_report(), self.env.ref("account.account_invoices"))
        self._composer(
            invoice, mode="text", body="Invoice attached", attach_report=False
        ).action_send()
        log = self.env["wa.message"].search(
            [("res_model", "=", "account.move"), ("res_id", "=", invoice.id)]
        )
        self.assertEqual(log.state, "sent")
        self.assertIn(log.mail_message_id, invoice.message_ids)


@tagged("post_install", "-at_install")
class TestWebhookProcessing(WhatsAppCase):
    """Payload handling, independent of the HTTP layer."""

    def _payload(self, **value):
        value.setdefault("messaging_product", "whatsapp")
        value.setdefault("metadata", {"phone_number_id": self.account.phone_number_id})
        return {
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": value}]}],
        }

    def _outgoing(self, wamid="wamid.OUT1", state="sent"):
        return self.env["wa.message"].create(
            {
                "account_id": self.account.id,
                "phone": "+919876543210",
                "partner_id": self.partner.id,
                "res_model": "res.partner",
                "res_id": self.partner.id,
                "body": "Hello",
                "state": state,
                "wamid": wamid,
            }
        )

    def _inbound(self, wa_id="919876543210", wamid="wamid.IN1", text="Is my order ready?"):
        return self._payload(
            contacts=[{"wa_id": wa_id, "profile": {"name": "Kavya"}}],
            messages=[
                {
                    "from": wa_id,
                    "id": wamid,
                    "timestamp": "1761700000",
                    "type": "text",
                    "text": {"body": text},
                }
            ],
        )

    def test_inbound_text_posts_on_partner(self):
        self.env["wa.account"]._webhook_process(self._inbound())
        log = self.env["wa.message"].search([("wamid", "=", "wamid.IN1")])
        self.assertRecordValues(
            log,
            [
                {
                    "direction": "incoming",
                    "state": "received",
                    "partner_id": self.partner.id,
                    "phone": "+919876543210",
                    "body": "Is my order ready?",
                }
            ],
        )
        self.assertEqual(log.mail_message_id.author_id, self.partner)
        self.assertIn(log.mail_message_id, self.partner.message_ids)
        self.assertIn("Is my order ready?", log.mail_message_id.body)

    def test_inbound_duplicate_is_ignored(self):
        self.env["wa.account"]._webhook_process(self._inbound())
        self.env["wa.account"]._webhook_process(self._inbound())
        self.assertEqual(self.env["wa.message"].search_count([("wamid", "=", "wamid.IN1")]), 1)

    def test_inbound_unknown_number(self):
        self.env["wa.account"]._webhook_process(
            self._inbound(wa_id="447700900123", wamid="wamid.X")
        )
        log = self.env["wa.message"].search([("wamid", "=", "wamid.X")])
        self.assertEqual(log.phone, "+447700900123")
        self.assertFalse(log.partner_id)

        self.account.create_unknown_partner = True
        self.env["wa.account"]._webhook_process(
            self._inbound(wa_id="447700900124", wamid="wamid.Y")
        )
        log = self.env["wa.message"].search([("wamid", "=", "wamid.Y")])
        self.assertEqual(log.partner_id.name, "Kavya")
        self.assertEqual(log.partner_id.wa_phone, "+447700900124")

    def test_inbound_media_message_placeholder(self):
        payload = self._payload(
            messages=[
                {
                    "from": "919876543210",
                    "id": "wamid.IMG",
                    "type": "image",
                    "image": {"id": "m1", "caption": "Broken leg"},
                }
            ]
        )
        self.env["wa.account"]._webhook_process(payload)
        log = self.env["wa.message"].search([("wamid", "=", "wamid.IMG")])
        self.assertEqual(log.body, "[Image] Broken leg")
        self.assertEqual(log.message_type, "other")

    def test_status_updates_progress_and_never_regress(self):
        message = self._outgoing()
        Account = self.env["wa.account"]
        for status in ("delivered", "read"):
            Account._webhook_process(
                self._payload(
                    statuses=[{"id": "wamid.OUT1", "status": status, "timestamp": "1761700100"}]
                )
            )
            self.assertEqual(message.state, status)
        # Late "delivered" callback must not downgrade "read".
        Account._webhook_process(
            self._payload(statuses=[{"id": "wamid.OUT1", "status": "delivered"}])
        )
        self.assertEqual(message.state, "read")
        self.assertEqual(message.status_date, fields.Datetime.to_datetime("2025-10-29 01:08:20"))

    def test_status_failed_records_error(self):
        message = self._outgoing()
        self.env["wa.account"]._webhook_process(
            self._payload(
                statuses=[
                    {
                        "id": "wamid.OUT1",
                        "status": "failed",
                        "errors": [
                            {
                                "code": 131026,
                                "title": "Message undeliverable",
                                "error_data": {"details": "Receiver not on WhatsApp"},
                            }
                        ],
                    }
                ]
            )
        )
        self.assertEqual(message.state, "failed")
        self.assertEqual(
            message.error_message, "131026 - Message undeliverable - Receiver not on WhatsApp"
        )
        self.assertIn("WhatsApp failed", "\n".join(self.partner.message_ids.mapped("body")))

    def test_unknown_account_and_object_are_ignored(self):
        message = self._outgoing()
        payload = self._payload(statuses=[{"id": "wamid.OUT1", "status": "read"}])
        payload["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "nope"
        self.env["wa.account"]._webhook_process(payload)
        self.env["wa.account"]._webhook_process({"object": "page", "entry": []})
        self.assertEqual(message.state, "sent")

    def test_status_date_is_utc(self):
        message = self._outgoing()
        self.env["wa.account"]._webhook_process(
            self._payload(statuses=[{"id": "wamid.OUT1", "status": "delivered", "timestamp": "0"}])
        )
        self.assertEqual(message.status_date, fields.Datetime.to_datetime("1970-01-01 00:00:00"))
        self.assertLess(message.status_date, fields.Datetime.now() - timedelta(days=1))
