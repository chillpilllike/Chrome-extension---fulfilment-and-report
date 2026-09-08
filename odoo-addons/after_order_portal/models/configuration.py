from odoo import api, models
from odoo.exceptions import AccessError


class AfterOrderConfiguration(models.Model):
    _inherit = 'ir.config_parameter'

    @api.model
    def after_order_portal_apply_safe_defaults(self):
        """Run on install/upgrade; never distribute or replace bridge secrets."""
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError('Only Settings administrators can configure after-order care.')
        parameters = self.sudo()
        if not (parameters.get_param('after_order_portal.api_base_url', '') or '').strip():
            parameters.set_param('after_order_portal.api_base_url', 'https://fulfilment.gofinch.com')
        # Upgrades require a fresh readiness review before real quotations/line writes.
        parameters.set_param('after_order_portal.live_alternatives_enabled', 'false')
        parameters.set_param('after_order_portal.live_refunds_enabled', 'false')
        return True
