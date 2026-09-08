"""Team-funded replacement uplift; never a payment receipt or refund waiver."""
import json
from markupsafe import Markup
from odoo import fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    after_order_cost_acceptances = fields.Text(default='{}', copy=False, readonly=True)

    def _after_order_accepted_cost(self, operation_key, line, product, info):
        approved=json.loads(self.after_order_cost_acceptances or '{}').get(operation_key)
        if not approved:
            return False
        if (approved['line_id']!=line.id or approved['product_id']!=product.id
                or approved['pricing_signature']!=info['pricing_signature']
                or approved['currency']!=self.currency_id.name
                or self.currency_id.compare_amounts(approved['amount'],info['difference'])):
            raise UserError('The team cost approval no longer matches this replacement and price.')
        if self.search_count([('after_order_operation_key','=',operation_key)]):
            raise UserError('A quotation already exists; resolve its payment status before any override.')
        return approved

    def after_order_accept_replacement_cost(self, line_id, website_id, product_id, operation_key, pricing_signature, amount, reason):
        line=self._after_order_guard(line_id,website_id)  # Includes Settings-admin access.
        if self.env['ir.config_parameter'].sudo().get_param('after_order_portal.live_alternatives_enabled')!='true':
            raise UserError('Live replacement processing is disabled.')
        if not operation_key.startswith('care:') or len(operation_key)>160 or not 3<=len(reason.strip())<=500:
            raise UserError('A valid operation and approval reason are required.')
        self.env.cr.execute('SELECT id FROM sale_order WHERE id=%s FOR UPDATE',(self.id,))
        self.invalidate_recordset()
        line=self._after_order_guard(line_id,website_id)
        product=self.env['product.product'].browse(int(product_id)).exists()
        if not product:
            raise UserError('Selected product no longer exists.')
        info=self.after_order_alternative_info(line_id,website_id,reference=product.default_code)
        if (info.get('pricing_error') or info.get('pricing_signature')!=pricing_signature or info.get('product_id')!=product.id
                or info['difference']<=0 or self.currency_id.compare_amounts(amount,info['difference'])):
            raise UserError('Approve the exact positive difference for the current selected product. Customer refunds cannot be waived here.')
        if self.search_count([('after_order_operation_key','=',operation_key)]):
            raise UserError('A quotation already exists. This approval cannot cancel it or bypass payment in progress.')
        if self.env['after.order.refund'].sudo().search_count([('order_id','=',self.id),('line_id','=',line.id)]):
            raise UserError('A refund operation already exists for this line. Finance review is required.')
        approved=self._after_order_accepted_cost(operation_key,line,product,info)
        if not approved:
            approved={'line_id':line.id,'product_id':product.id,'pricing_signature':pricing_signature,
                      'amount':info['difference'],'currency':self.currency_id.name,'reason':reason.strip(),
                      'approved_by':self.env.user.id,'approved_at':fields.Datetime.to_string(fields.Datetime.now())}
            all_approvals=json.loads(self.after_order_cost_acceptances or '{}')
            self.after_order_cost_acceptances=json.dumps({**all_approvals,operation_key:approved})
            self.message_post(body=Markup('Team accepted replacement extra cost for line %s: %s %s. No additional customer payment will be requested. Reason: %s. Approver: %s.') %
                              (line.id,self.currency_id.name,info['difference'],reason.strip(),self.env.user.display_name))
        return {'cost_absorbed':True,'absorbed_amount':approved['amount'],'currency':approved['currency'],
                'approval_reason':approved['reason'],'approved_by':approved['approved_by'],'approved_at':approved['approved_at']}
