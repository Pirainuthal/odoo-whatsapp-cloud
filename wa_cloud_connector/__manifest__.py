# Copyright 2026 Pirainuthal S (https://github.com/Pirainuthal)
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    "name": "WhatsApp Cloud API Connector",
    "summary": "Send WhatsApp templates, texts and PDF documents from quotations, "
    "invoices and contacts using Meta's WhatsApp Cloud API",
    "version": "19.0.1.0.0",
    "category": "Marketing",
    "author": "Pirainuthal S",
    "website": "https://github.com/Pirainuthal/odoo-whatsapp-cloud",
    "license": "LGPL-3",
    "depends": ["mail", "sale_management", "account"],
    "external_dependencies": {"python": ["requests"]},
    "data": [
        "security/wa_security.xml",
        "security/ir.model.access.csv",
        "views/wa_account_views.xml",
        "views/wa_template_views.xml",
        "views/wa_message_views.xml",
        "views/document_views.xml",
        "wizard/wa_composer_views.xml",
        "views/wa_menus.xml",
    ],
    "images": ["static/description/icon.png"],
    "application": True,
    "installable": True,
}
