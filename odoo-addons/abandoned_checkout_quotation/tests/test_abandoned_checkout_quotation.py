from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.sale_order import INSTALLATION_KEY


@tagged("post_install", "-at_install")
class TestAbandonedCheckoutQuotation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env["website"].search([], limit=1)
        cls.website.domain = "https://checkout.example.com"
        cls.customer = cls.env["res.partner"].create({
            "name": "Checkout customer",
            "email": "checkout@example.com",
        })
        cls.product = cls.env["product.product"].create({
            "name": "Checkout product",
            "type": "service",
            "list_price": 100,
        })
        cls.orders = cls.env["sale.order"].with_company(cls.website.company_id)
        cls.now = fields.Datetime.now()
        cls.installed_at = cls.now - timedelta(days=1)
        cls.env["ir.config_parameter"].set_param(INSTALLATION_KEY, cls.installed_at)

    def _make_order(self, *, hours=8, partner=None):
        order = self.orders.create({
            "website_id": self.website.id,
            "date_order": self.now - timedelta(hours=hours),
            "partner_id": (partner or self.customer).id,
            "order_line": [Command.create({
                "product_id": self.product.id,
                "product_uom_qty": 1,
                "price_unit": 100,
            })],
        })
        date = self.now - timedelta(hours=hours)
        order.flush_recordset()
        order.order_line.flush_recordset()
        self.env.cr.execute(
            "UPDATE sale_order SET create_date = %s, write_date = %s WHERE id = %s",
            [date, date, order.id],
        )
        self.env.cr.execute(
            "UPDATE sale_order_line SET write_date = %s WHERE order_id = %s",
            [date, order.id],
        )
        order.invalidate_recordset()
        order.order_line.invalidate_recordset()
        return order

    def _outgoing(self, order):
        return self.env["mail.mail"].search([
            ("model", "=", "sale.order"),
            ("res_id", "=", order.id),
            ("state", "=", "outgoing"),
        ])

    def test_cron_sends_once_per_order_and_respects_installation_cutoff(self):
        first, second = self._make_order(), self._make_order()
        old = self._make_order(hours=30)
        fresh = self._make_order(hours=5)
        public = self._make_order(partner=self.website.partner_id)
        manual = self._make_order()
        manual.action_quotation_sent()
        self.orders._cron_send_abandoned_checkout_quotations()
        self.orders._cron_send_abandoned_checkout_quotations()

        for order in first | second:
            self.assertEqual(order.state, "sent")
            self.assertTrue(order.abandoned_checkout_email_sent_date)
            mail = self._outgoing(order)
            self.assertEqual(len(mail), 1)
            self.assertEqual(mail.recipient_ids, self.customer)
            self.assertIn(order.name, mail.subject)
            self.assertIn("https://checkout.example.com/mail/view?", mail.body_html)
            self.assertIn(order.access_token, mail.body_html)
            self.assertTrue(mail.attachment_ids)
        for order in old | fresh | public | manual:
            self.assertFalse(self._outgoing(order))

    def test_settings_preview_and_queue_rechecks_eligibility(self):
        older = self._make_order(hours=52)
        stale = self._make_order(hours=48)
        excluded = self._make_order(hours=100)
        recent = self._make_order()
        wizard = self.env["abandoned.quotation.review"].with_context(tz="UTC").create({
            "website_id": self.website.id,
            "from_date": (self.now - timedelta(days=3)).date(),
        })
        wizard.action_preview()
        self.assertEqual(wizard.order_ids, older | stale)
        self.assertFalse(self._outgoing(older))
        stale.action_quotation_sent()
        wizard.action_queue()
        self.assertEqual(len(self._outgoing(older)), 1)
        for order in stale | excluded | recent:
            self.assertFalse(self._outgoing(order))
        wizard.action_preview()
        self.assertFalse(wizard.order_ids)

    def test_settings_requires_a_new_preview_after_date_change(self):
        self._make_order(hours=48)
        wizard = self.env["abandoned.quotation.review"].create({
            "website_id": self.website.id,
            "from_date": (self.now - timedelta(days=3)).date(),
        })
        wizard.action_preview()
        wizard.from_date -= timedelta(days=1)
        with self.assertRaises(UserError):
            wizard.action_queue()

    def test_recent_cart_change_and_active_payment_are_skipped(self):
        edited, pending = self._make_order(), self._make_order()
        edited.order_line.product_uom_qty = 2
        provider = self.env.ref("payment.payment_provider_transfer")
        self.env["payment.transaction"].create({
            "provider_id": provider.id,
            "payment_method_id": self.env.ref("payment.payment_method_bank_transfer").id,
            "reference": "Checkout pending payment",
            "amount": pending.amount_total,
            "currency_id": pending.currency_id.id,
            "partner_id": pending.partner_id.id,
            "state": "pending",
            "sale_order_ids": [Command.link(pending.id)],
        })
        self.orders._cron_send_abandoned_checkout_quotations()
        self.assertFalse(self._outgoing(edited))
        self.assertFalse(self._outgoing(pending))

    def test_manual_quotation_stays_excluded_if_reset_to_draft(self):
        order = self._make_order()
        action = order.action_quotation_send()
        composer = self.env["mail.compose.message"].with_context(
            **action["context"], mail_notify_force_send=False,
        ).create({"force_send": False})
        composer._action_send_mail()
        self.assertEqual(len(self._outgoing(order)), 1)
        self.assertTrue(order.cart_recovery_email_sent)
        order.action_draft()
        self.assertFalse(self.orders.search_count([
            ("id", "=", order.id),
            *self.orders._abandoned_quotation_domain(self.website, self.installed_at),
        ]))

    def test_native_cart_cron_leaves_managed_website_to_the_addon(self):
        self.website.send_abandoned_cart_email = True
        order = self._make_order(hours=30)
        self.website._send_abandoned_cart_email()
        self.assertFalse(self._outgoing(order))
        self.assertFalse(order.cart_recovery_email_sent)

        self.website.abandoned_quotation_enabled = False
        self.website._send_abandoned_cart_email()
        self.assertTrue(order.cart_recovery_email_sent)
