import logging
from datetime import timedelta

from psycopg2 import OperationalError, errorcodes

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

INSTALLATION_KEY = "abandoned_checkout_quotation.installed_at"
BATCH_SIZE = 25
DELAY = timedelta(hours=6)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    abandoned_checkout_email_sent = fields.Boolean(copy=False, readonly=True)
    abandoned_checkout_email_sent_date = fields.Datetime(
        string="Abandoned quotation queued on", copy=False, readonly=True,
    )

    @api.model
    def _abandoned_quotation_domain(self, websites, start, end=None):
        deadline = fields.Datetime.now() - DELAY
        domain = [
            ("website_id", "in", websites.ids),
            ("partner_id", "not in", websites.partner_id.ids),
            ("partner_id.active", "=", True),
            ("partner_id.email_normalized", "!=", False),
            ("state", "=", "draft"),
            ("amount_total", ">", 0),
            "|", ("validity_date", "=", False), ("validity_date", ">=", fields.Date.context_today(self)),
            ("cart_recovery_email_sent", "=", False),
            ("abandoned_checkout_email_sent", "=", False),
            ("create_date", ">=", start),
            ("create_date", "<=", deadline),
            ("write_date", "<=", deadline),
            ("order_line", "any", [
                ("product_id", "!=", False),
                ("is_delivery", "=", False),
                ("product_uom_qty", ">", 0),
            ]),
            ("order_line", "not any", [("write_date", ">", deadline)]),
            ("transaction_ids", "not any", [
                "|",
                ("state", "in", ["pending", "authorized", "done"]),
                ("last_state_change", ">", deadline),
            ]),
        ]
        if end:
            domain.append(("create_date", "<", end))
        return domain

    @api.model
    def _cron_send_abandoned_checkout_quotations(self):
        installed_at = self.env["ir.config_parameter"].sudo().get_param(INSTALLATION_KEY)
        if not installed_at:
            return
        websites = self.env["website"].sudo().search([("abandoned_quotation_enabled", "=", True)])
        if not websites:
            return
        orders = self.sudo()
        domain = orders._abandoned_quotation_domain(websites, installed_at)
        candidates = orders.search(domain, order="create_date, id", limit=BATCH_SIZE)
        candidates._queue_abandoned_quotations(domain)

    def _queue_abandoned_quotations(self, domain):
        queued = self.browse()
        for order in self:
            try:
                with self.env.cr.savepoint():
                    # A concurrent cron or Settings request must claim the same order row.
                    self.env.cr.execute(
                        "SELECT id FROM sale_order WHERE id = %s FOR NO KEY UPDATE SKIP LOCKED",
                        [order.id],
                    )
                    if not self.env.cr.fetchone():
                        continue
                    if not self.search_count([("id", "=", order.id), *domain], limit=1):
                        continue
                    order.with_company(order.company_id)._queue_abandoned_quotation()
            except OperationalError as error:
                if error.pgcode != errorcodes.SERIALIZATION_FAILURE:
                    raise
                continue
            except UserError:
                _logger.warning("Could not queue abandoned quotation %s", order.id, exc_info=True)
                continue
            queued |= order
        return queued

    def _queue_abandoned_quotation(self):
        self.ensure_one()
        template = self._find_mail_template()
        if not template:
            raise UserError(_("The Sales quotation email template is missing."))
        self._portal_ensure_token()
        order = self.with_context(
            abandoned_quotation_recipient=self.partner_id.id,
            mail_notify_force_send=False,
            mail_post_autofollow=False,
            mark_so_as_sent=True,
        )
        message = order.message_post_with_source(
            template,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            email_layout_xmlid="mail.mail_notification_layout_with_responsible_signature",
            partner_ids=self.partner_id.ids,
            force_send=False,
        )
        if not self.env["mail.mail"].sudo().search_count([
            ("mail_message_id", "in", message.ids),
            ("state", "=", "outgoing"),
        ], limit=1):
            raise UserError(_("Odoo did not queue an email for the quotation recipient."))
        self.write({
            "abandoned_checkout_email_sent": True,
            "abandoned_checkout_email_sent_date": fields.Datetime.now(),
        })

    def get_base_url(self):
        if self.env.context.get("abandoned_quotation_recipient"):
            domain = self.website_id.domain_punycode
            if domain:
                return domain.rstrip("/")
        return super().get_base_url()

    def _notify_get_recipients(self, message, msg_vals, **kwargs):
        recipients = super()._notify_get_recipients(message, msg_vals, **kwargs)
        partner_id = self.env.context.get("abandoned_quotation_recipient")
        if partner_id:
            return [
                dict(recipient, notif="email")
                for recipient in recipients if recipient["id"] == partner_id
            ]
        return recipients

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        # Keep the manual-send record even if the quotation is later reset to draft.
        if self.env.context.get("mark_so_as_sent") and not self.env.context.get("proforma"):
            self.filtered(lambda order: (
                order.website_id and order.partner_id in message.partner_ids
            )).cart_recovery_email_sent = True
        return result

    def _message_mail_after_hook(self, mails):
        result = super()._message_mail_after_hook(mails)
        if self.env.context.get("abandoned_manual_quotation"):
            orders_by_id = {order.id: order for order in self if order.website_id}
            for mail in mails:
                order = orders_by_id.get(mail.res_id)
                if order and order.partner_id in mail.recipient_ids:
                    order.cart_recovery_email_sent = True
        return result

    def action_quotation_send(self):
        action = super().action_quotation_send()
        if self.website_id and not self.env.context.get("proforma"):
            action["context"]["abandoned_manual_quotation"] = True
        return action


class Website(models.Model):
    _inherit = "website"

    abandoned_quotation_enabled = fields.Boolean(
        string="Automatically send abandoned quotations", default=True,
    )

    @api.model
    def _send_abandoned_cart_email(self):
        # Let Odoo run its original campaign only for websites outside this addon.
        websites = self.with_context(abandoned_quotation_native=True)
        return super(Website, websites)._send_abandoned_cart_email()

    @api.model
    def _search(self, domain, offset=0, limit=None, order=None):
        if self.env.context.get("abandoned_quotation_native"):
            domain = [*domain, ("abandoned_quotation_enabled", "=", False)]
        return super()._search(domain, offset=offset, limit=limit, order=order)
