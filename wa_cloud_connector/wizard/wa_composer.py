# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Command

from ..models.wa_template import render_placeholders

SERVICE_WINDOW = timedelta(hours=24)


class WaComposer(models.TransientModel):
    _name = "wa.composer"
    _description = "Send WhatsApp Message"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    # ``required`` lives in the form view: stored computed fields are only filled
    # after the INSERT, so a NOT NULL column would break programmatic create().
    account_id = fields.Many2one(
        "wa.account",
        compute="_compute_account_id",
        store=True,
        readonly=False,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Recipient", compute="_compute_partner_id", store=True, readonly=False
    )
    phone = fields.Char(compute="_compute_phone", store=True, readonly=False)
    mode = fields.Selection(
        [("template", "Template"), ("text", "Free text")],
        required=True,
        default="template",
    )
    template_id = fields.Many2one(
        "wa.template",
        domain="[('account_id', '=', account_id), ('status', '=', 'approved'),"
        " '|', ('model', '=', False), ('model', '=', res_model)]",
    )
    variable_ids = fields.One2many(
        "wa.composer.variable",
        "composer_id",
        string="Placeholder Values",
        compute="_compute_variable_ids",
        store=True,
        readonly=False,
    )
    body = fields.Text(string="Message")
    preview = fields.Text(compute="_compute_preview")
    attach_report = fields.Boolean(
        string="Attach PDF", compute="_compute_attach_report", store=True, readonly=False
    )
    report_id = fields.Many2one(
        "ir.actions.report",
        string="Report",
        compute="_compute_attach_report",
        store=True,
        readonly=False,
        domain="[('model', '=', res_model), ('report_type', '=', 'qweb-pdf')]",
    )
    last_inbound_date = fields.Datetime(compute="_compute_service_window")
    in_service_window = fields.Boolean(compute="_compute_service_window")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _get_record(self):
        """Return the target document, or ``None`` for a model without WhatsApp support."""
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return None
        Model = self.env[self.res_model]
        if not isinstance(Model, self.env.registry["wa.thread.mixin"]):
            return None
        return Model.browse(self.res_id).exists() or None

    @api.depends("res_model", "res_id")
    def _compute_account_id(self):
        for wizard in self:
            record = wizard._get_record()
            company = (
                record.company_id
                if record and "company_id" in record._fields and record.company_id
                else self.env.company
            )
            wizard.account_id = self.env["wa.account"]._get_default_account(company)

    @api.depends("res_model", "res_id")
    def _compute_partner_id(self):
        for wizard in self:
            record = wizard._get_record()
            wizard.partner_id = record._wa_get_partner() if record else False

    @api.depends("partner_id", "account_id")
    def _compute_phone(self):
        for wizard in self:
            partner = wizard.partner_id
            wizard.phone = (
                wizard.account_id._normalize_phone(partner.phone, partner) or partner.phone
                if partner.phone and wizard.account_id
                else partner.phone
            )

    @api.depends("template_id")
    def _compute_variable_ids(self):
        for wizard in self:
            commands = [Command.clear()]
            record = wizard._get_record()
            if wizard.template_id and record:
                values = wizard.template_id._get_placeholder_values(record)
                commands += [
                    Command.create({"position": position, "value": value})
                    for position, value in sorted(values.items())
                ]
            wizard.variable_ids = commands

    @api.depends("mode", "template_id", "variable_ids.value", "body")
    def _compute_preview(self):
        for wizard in self:
            if wizard.mode == "template":
                values = {v.position: v.value or "" for v in wizard.variable_ids}
                wizard.preview = render_placeholders(wizard.template_id.body, values)
            else:
                wizard.preview = wizard.body

    @api.depends("res_model", "res_id")
    def _compute_attach_report(self):
        for wizard in self:
            record = wizard._get_record()
            report = record._wa_get_report() if record else False
            wizard.report_id = report
            wizard.attach_report = bool(report)

    @api.depends("phone", "account_id")
    def _compute_service_window(self):
        now = fields.Datetime.now()
        for wizard in self:
            phone = wizard.account_id._normalize_phone(wizard.phone, wizard.partner_id)
            last = (
                self.env["wa.message"].search(
                    [("direction", "=", "incoming"), ("phone", "=", phone)],
                    order="create_date desc",
                    limit=1,
                )
                if phone
                else False
            )
            wizard.last_inbound_date = last.create_date if last else False
            wizard.in_service_window = bool(last and now - last.create_date <= SERVICE_WINDOW)

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def _render_report_attachment(self, record):
        """Render the selected PDF report and store it as an attachment of ``record``."""
        self.ensure_one()
        content, _report_type = self.env["ir.actions.report"]._render_qweb_pdf(
            self.report_id, [record.id]
        )
        if hasattr(record, "_get_report_base_filename"):
            base_name = record._get_report_base_filename()
        else:
            base_name = record.display_name
        filename = f"{base_name}.pdf".replace("/", "_")
        return self.env["ir.attachment"].create(
            {
                "name": filename,
                "raw": content,
                "mimetype": "application/pdf",
                "res_model": record._name,
                "res_id": record.id,
            }
        )

    def _prepare_message_vals(self, record, phone):
        """Return the list of ``wa.message`` values to create for this wizard."""
        self.ensure_one()
        common = {
            "account_id": self.account_id.id,
            "direction": "outgoing",
            "state": "queued",
            "phone": phone,
            "partner_id": self.partner_id.id,
            "res_model": record._name,
            "res_id": record.id,
        }
        vals_list = []
        caption = False
        if self.mode == "template":
            params = [v.value or "" for v in self.variable_ids.sorted("position")]
            vals_list.append(
                {
                    **common,
                    "message_type": "template",
                    "template_id": self.template_id.id,
                    "template_params": params,
                    "body": self.preview,
                }
            )
        elif not self.attach_report:
            vals_list.append({**common, "message_type": "text", "body": self.body})
        else:
            caption = self.body  # free text travels as the document caption
        if self.attach_report and self.report_id:
            attachment = self._render_report_attachment(record)
            vals_list.append(
                {
                    **common,
                    "message_type": "document",
                    "attachment_id": attachment.id,
                    "body": caption or False,
                }
            )
        return vals_list

    def action_send(self):
        self.ensure_one()
        record = self._get_record()
        if not record:
            raise UserError(_("The document no longer exists."))
        if not self.account_id:
            raise UserError(_("Configure a WhatsApp account first."))
        if self.mode == "template" and not self.template_id:
            raise UserError(_("Choose a template."))
        if self.mode == "text" and not (self.body or "").strip():
            raise UserError(_("Write a message."))
        if self.attach_report and not self.report_id:
            raise UserError(_("Choose the report to attach."))
        if self.attach_report and self.report_id.model != record._name:
            raise UserError(_("The selected report does not apply to this document."))
        record.check_access("read")
        if self.mode == "template" and any(not (v.value or "").strip() for v in self.variable_ids):
            raise UserError(_("Fill in every template placeholder."))
        phone = self.account_id._normalize_phone(self.phone, self.partner_id)
        if not phone:
            raise UserError(_("%s is not a valid phone number.", self.phone or "-"))

        messages = self.env["wa.message"].create(self._prepare_message_vals(record, phone))
        messages._send()

        failed = messages.filtered(lambda m: m.state == "failed")
        if failed:
            return self._notification(
                _("WhatsApp not sent"),
                "\n".join(failed.mapped("error_message")),
                "danger",
            )
        return self._notification(
            _("WhatsApp sent"),
            _("Message sent to %s.", phone),
            "success",
        )

    @staticmethod
    def _notification(title, message, kind):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": kind,
                "sticky": kind == "danger",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class WaComposerVariable(models.TransientModel):
    _name = "wa.composer.variable"
    _description = "WhatsApp Composer Placeholder Value"
    _order = "position"

    composer_id = fields.Many2one("wa.composer", required=True, ondelete="cascade")
    position = fields.Integer(required=True, readonly=True)
    value = fields.Char()
