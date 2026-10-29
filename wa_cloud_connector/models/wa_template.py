# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
import re

from odoo import api, fields, models

PLACEHOLDER_RE = re.compile(r"\{\{\s*(\d+)\s*\}\}")

TEMPLATE_STATUSES = [
    ("approved", "Approved"),
    ("pending", "Pending"),
    ("rejected", "Rejected"),
    ("paused", "Paused"),
    ("disabled", "Disabled"),
    ("other", "Other"),
]
TEMPLATE_CATEGORIES = [
    ("marketing", "Marketing"),
    ("utility", "Utility"),
    ("authentication", "Authentication"),
]


def placeholder_numbers(text):
    """Return the sorted, unique ``{{n}}`` indexes used in ``text``."""
    return sorted({int(n) for n in PLACEHOLDER_RE.findall(text or "")})


def render_placeholders(text, values):
    """Replace ``{{n}}`` with ``values[n]`` (missing values are left as-is)."""

    def _sub(match):
        return str(values.get(int(match.group(1)), match.group(0)))

    return PLACEHOLDER_RE.sub(_sub, text or "")


class WaTemplate(models.Model):
    _name = "wa.template"
    _description = "WhatsApp Message Template"
    _order = "name, language"

    name = fields.Char(required=True, readonly=True)
    language = fields.Char(required=True, readonly=True)
    active = fields.Boolean(default=True)
    account_id = fields.Many2one("wa.account", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="account_id.company_id", store=True)
    meta_id = fields.Char(string="Meta Template ID", readonly=True)
    status = fields.Selection(TEMPLATE_STATUSES, default="pending", readonly=True)
    category = fields.Selection(TEMPLATE_CATEGORIES, readonly=True)
    header_text = fields.Char(readonly=True)
    body = fields.Text(readonly=True)
    footer_text = fields.Char(readonly=True)
    placeholder_count = fields.Integer(compute="_compute_placeholder_count", store=True)
    model_id = fields.Many2one(
        "ir.model",
        string="Applies To",
        ondelete="cascade",
        domain=[("model", "in", ("sale.order", "account.move", "res.partner"))],
        help="Restrict this template to one kind of document. Leave empty to use it everywhere.",
    )
    model = fields.Char(related="model_id.model")
    variable_ids = fields.One2many("wa.template.variable", "template_id", string="Placeholders")

    _name_lang_unique = models.Constraint(
        "UNIQUE(account_id, name, language)",
        "A template with this name and language already exists for this account.",
    )

    @api.depends("body")
    def _compute_placeholder_count(self):
        for template in self:
            numbers = placeholder_numbers(template.body)
            template.placeholder_count = max(numbers) if numbers else 0

    @api.depends("name", "language")
    def _compute_display_name(self):
        for template in self:
            template.display_name = f"{template.name} ({template.language})"

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _get_placeholder_values(self, record):
        """Resolve each placeholder for ``record`` using the configured field paths."""
        self.ensure_one()
        mapping = {v.position: v for v in self.variable_ids}
        values = {}
        for position in range(1, self.placeholder_count + 1):
            variable = mapping.get(position)
            values[position] = variable._resolve(record) if variable else ""
        return values

    # ------------------------------------------------------------------
    # Synchronisation
    # ------------------------------------------------------------------
    @api.model
    def _parse_meta_template(self, data):
        """Convert one Graph API template dict into ``wa.template`` values."""
        components = {(c.get("type") or "").upper(): c for c in data.get("components") or []}
        header = components.get("HEADER") or {}
        status = (data.get("status") or "").lower()
        category = (data.get("category") or "").lower()
        return {
            "meta_id": data.get("id"),
            "name": data.get("name"),
            "language": data.get("language"),
            "status": status if status in dict(TEMPLATE_STATUSES) else "other",
            "category": category if category in dict(TEMPLATE_CATEGORIES) else False,
            "header_text": header.get("text") if header.get("format") == "TEXT" else False,
            "body": (components.get("BODY") or {}).get("text") or "",
            "footer_text": (components.get("FOOTER") or {}).get("text") or False,
        }

    @api.model
    def _sync_from_meta(self, account):
        """Create / update / archive templates from the Graph API.

        :returns: dict with ``created``, ``updated`` and ``archived`` counters.
        """
        remote = account._get_client().list_templates()
        existing = {
            (t.name, t.language): t
            for t in self.with_context(active_test=False).search([("account_id", "=", account.id)])
        }
        stats = {"created": 0, "updated": 0, "archived": 0}
        seen = set()
        to_create = []
        for data in remote:
            vals = self._parse_meta_template(data)
            if not vals["name"] or not vals["language"]:
                continue
            key = (vals["name"], vals["language"])
            seen.add(key)
            template = existing.get(key)
            if template:
                template.write({**vals, "active": True})
                stats["updated"] += 1
            else:
                to_create.append({**vals, "account_id": account.id})
        if to_create:
            self.create(to_create)
            stats["created"] = len(to_create)
        stale = self.browse([t.id for key, t in existing.items() if key not in seen and t.active])
        stale.active = False
        stats["archived"] = len(stale)
        return stats


class WaTemplateVariable(models.Model):
    _name = "wa.template.variable"
    _description = "WhatsApp Template Placeholder"
    _order = "template_id, position"

    template_id = fields.Many2one("wa.template", required=True, ondelete="cascade", index=True)
    position = fields.Integer(required=True, help="The n of the {{n}} placeholder.")
    field_path = fields.Char(
        help="Dotted field path evaluated on the document, e.g. partner_id.name, "
        "name or amount_total. Leave empty to always use the default value.",
    )
    default_value = fields.Char(help="Used when the field path is empty or resolves to nothing.")

    _position_unique = models.Constraint(
        "UNIQUE(template_id, position)",
        "Each placeholder can only be mapped once.",
    )

    def _resolve(self, record):
        """Follow ``field_path`` on ``record`` and return a display string."""
        self.ensure_one()
        value = record
        for name in (self.field_path or "").split("."):
            if not name:
                value = None
                break
            if not isinstance(value, models.BaseModel) or name not in value._fields:
                value = None
                break
            value = value[:1][name]
        if isinstance(value, models.BaseModel):
            value = value[:1].display_name
        elif isinstance(value, float):
            value = f"{value:.2f}"
        if value in (None, False, ""):
            return self.default_value or ""
        return str(value)
