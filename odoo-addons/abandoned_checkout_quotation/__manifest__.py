{
    "name": "Abandoned Checkout Quotations",
    "summary": "Email unpaid website quotations once, with a date-based review for older orders",
    "version": "18.0.1.1.0",
    "category": "Website/Website",
    "license": "LGPL-3",
    "depends": ["website_sale"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/res_config_settings_views.xml",
    ],
    "installable": True,
}
