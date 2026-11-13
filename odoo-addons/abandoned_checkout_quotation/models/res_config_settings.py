from datetime import datetime, time

import pytz

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError

from .sale_order import BATCH_SIZE, INSTALLATION_KEY


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    abandoned_quotation_enabled = fields.Boolean(
        related="website_id.abandoned_quotation_enabled", readonly=False,
    )

    def action_review_abandoned_quotations(self):
        self.ensure_one()
        wizard = self.env["abandoned.quotation.review"].create({
            "website_id": self.website_id.id,
        })
        return wizard._open()


class AbandonedQuotationReview(models.TransientModel):
    _name = "abandoned.quotation.review"
    _description = "Review older abandoned checkout quotations"

    website_id = fields.Many2one("website", required=True, readonly=True)
    from_date = fields.Date(string="Orders created on or after", default=fields.Date.context_today, required=True)
    reviewed_from_date = fields.Date(readonly=True)
    order_ids = fields.Many2many("sale.order", readonly=True, string="Eligible quotations")
    result = fields.Char(readonly=True)

    def _open(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Review older checkout quotations"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "abandoned_checkout_quotation.abandoned_quotation_review_form"
            ).id,
            "target": "new",
        }

    def _domain(self):
        self.ensure_one()
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only Settings administrators can review older quotations."))
        if self.website_id.company_id not in self.env.companies:
            raise AccessError(_("Select the website's company before reviewing quotations."))
        if not self.from_date:
            raise UserError(_("Select a start date."))
        timezone = pytz.timezone(self.env.context.get("tz") or self.env.user.tz or "UTC")
        midnight = timezone.localize(datetime.combine(self.from_date, time.min))
        start = midnight.astimezone(pytz.UTC).replace(tzinfo=None)
        installed_at = self.env["ir.config_parameter"].sudo().get_param(INSTALLATION_KEY)
        if not installed_at:
            raise UserError(_("The addon's installation date is missing."))
        return self.env["sale.order"]._abandoned_quotation_domain(
            self.website_id, start, end=installed_at,
        )

    def action_preview(self):
        orders = self.env["sale.order"].search(
            self._domain(), order="create_date, id", limit=BATCH_SIZE + 1,
        )
        result = _("No eligible quotations were found.")
        if len(orders) > BATCH_SIZE:
            result = _("Showing the first %(size)s eligible quotations. Check again for the next batch after queuing.", size=BATCH_SIZE)
        elif orders:
            result = _("%(count)s eligible quotations found.", count=len(orders))
        self.write({
            "reviewed_from_date": self.from_date,
            "order_ids": [fields.Command.set(orders[:BATCH_SIZE].ids)],
            "result": result,
        })
        return self._open()

    def action_queue(self):
        domain = self._domain()
        if self.reviewed_from_date != self.from_date or not self.order_ids:
            raise UserError(_("Check the eligible quotations for this date before queuing."))
        orders = self.order_ids[:BATCH_SIZE]
        queued = orders._queue_abandoned_quotations(domain)
        self.write({
            "order_ids": [fields.Command.clear()],
            "reviewed_from_date": False,
            "result": _(
                "%(queued)s emails queued. %(skipped)s quotations skipped or could not be queued.",
                queued=len(queued), skipped=len(orders) - len(queued),
            ),
        })
        return self._open()
