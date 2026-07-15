from odoo import fields, models


class Website(models.Model):
    _inherit = "website"

    final_mile_tracking_enabled = fields.Boolean(
        string="Final Mile Tracking Enabled",
        default=True,
    )
    final_mile_tracking_app_url = fields.Char(
        string="Fulfilment App URL",
        default="http://127.0.0.1:8000",
        help="Base URL of the FastAPI fulfilment app that exposes /api/public/track-order.",
    )
    final_mile_tracking_slug = fields.Char(
        string="Tracking Slug",
        help="Slug used by the fulfilment app, for example nutricity-com-au.",
    )
    final_mile_tracking_store_id = fields.Integer(
        string="Fulfilment App Store ID",
        help="Optional store_id to send to the fulfilment app for this website.",
    )
