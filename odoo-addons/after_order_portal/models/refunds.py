"""Two-RPC refund outbox. Preparation commits before any provider request."""
import json
import uuid
import hashlib
from datetime import datetime, timedelta, timezone
from markupsafe import Markup
from odoo import fields, models
from odoo.exceptions import AccessError, UserError


class CareRefund(models.Model):
    _name = 'after.order.refund'
    _description = 'Approved replacement price refund'

    operation_key = fields.Char(required=True, readonly=True, index=True)
    order_id = fields.Many2one('sale.order', required=True, readonly=True)
    line_id = fields.Many2one('sale.order.line', required=True, readonly=True)
    product_id = fields.Many2one('product.product', required=True, readonly=True)
    pricing_signature = fields.Char(required=True, readonly=True)
    snapshot = fields.Text(required=True, readonly=True)
    credit_signature = fields.Char(readonly=True)
    provider_key = fields.Char(required=True, readonly=True, default=lambda self:'care-refund:'+uuid.uuid4().hex)
    transaction_id = fields.Many2one('payment.transaction', required=True, readonly=True)
    credit_id = fields.Many2one('account.move', required=True, readonly=True)
    state = fields.Selection([(s, s) for s in ('prepared','pending','completed','needs_review')], default='prepared', readonly=True)
    last_error = fields.Text(readonly=True)
    _sql_constraints = [('operation_unique','unique(operation_key)','This refund already exists.'),
                        ('line_unique','unique(order_id,line_id)','A replacement refund already exists for this line; review it instead of refunding again.')]

    def _result(self):
        self.ensure_one()
        return {'refund_id':self.id, 'refund_status':self.state,
                'refund_amount':abs(self.transaction_id.amount), 'credit_note':self.credit_id.name,
                'provider_refund_id':self.transaction_id.provider_reference or '',
                'refund_verified':self.state=='completed', 'error':self.last_error or ''}

    def _credit_fingerprint(self):
        credit=self.credit_id
        data=[credit.company_id.id,credit.partner_id.id,credit.currency_id.id,credit.journal_id.id,
              [(line.account_id.id,line.product_id.id,line.quantity,line.price_unit,line.discount,sorted(line.tax_ids.ids))
               for line in credit.invoice_line_ids.sorted('id')]]
        return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def _care_refund_guard(self, website_id):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Settings administrator approval is required.')
        if self.website_id.id != int(website_id) or self.after_order_parent_id:
            raise UserError('Refund website or order scope mismatch.')
        if self.env['ir.config_parameter'].sudo().get_param('after_order_portal.live_refunds_enabled') != 'true':
            raise UserError('Live refunds are disabled. Complete sandbox verification first.')
        if self.env['ir.config_parameter'].sudo().get_param('after_order_portal.live_alternatives_enabled') != 'true':
            raise UserError('Live replacement processing is disabled.')
        self.env.cr.execute('SELECT id FROM sale_order WHERE id=%s FOR UPDATE',(self.id,))

    def after_order_prepare_replacement_refund(self, line_id, website_id, product_id, operation_key, deadline_at, pricing_signature):
        self._care_refund_guard(website_id)
        if not operation_key.startswith('care:') or len(operation_key)>160:
            raise UserError('Invalid refund operation key.')
        existing=self.env['after.order.refund'].sudo().search([('operation_key','=',operation_key)],limit=1)
        if existing:
            if (existing.order_id!=self or existing.line_id.id!=int(line_id) or existing.product_id.id!=int(product_id)
                    or existing.pricing_signature!=pricing_signature):
                raise UserError('Refund request changed; do not reuse its operation key.')
            return existing._result()
        deadline=datetime.fromisoformat(deadline_at.replace('Z','+00:00'))
        if deadline.tzinfo is None or deadline>datetime.now(timezone.utc):
            raise UserError('The 24-hour selection window is still open.')
        line=self._after_order_guard(line_id,website_id)
        product=self.env['product.product'].browse(int(product_id)).exists()
        if not product:
            raise UserError('Replacement product no longer exists.')
        info=self.after_order_alternative_info(line_id,website_id,reference=product.default_code)
        if (info.get('pricing_error') or info.get('pricing_signature')!=pricing_signature or info.get('product_id')!=product.id
                or info['difference']>=0 or not info['same_taxes'] or not info['simple_taxes'] or len(info['invoice_ids'])!=1):
            raise UserError('Refund pricing, invoice allocation or tax treatment requires accounting review.')
        invoice=self.env['account.move'].browse(info['invoice_ids'])
        sources=self.transaction_ids.filtered(lambda t:t.state=='done' and t.operation!='refund' and t.sale_order_ids==self
            and t.currency_id==self.currency_id and t.company_id==self.company_id and t.partner_id.commercial_partner_id==self.partner_id.commercial_partner_id)
        if len(sources)!=1 or sources.provider_code!='stripe' or sources.provider_id.support_refund!='partial' or invoice not in sources.invoice_ids:
            raise UserError('Automatic refunds currently require one verified Stripe payment linked to this invoice. Other providers require review.')
        source=sources
        self.env.cr.execute('SELECT id FROM payment_transaction WHERE id=%s FOR UPDATE',(source.id,))
        source.invalidate_recordset()
        reserved=sum(abs(t.amount) for t in source.child_transaction_ids if t.operation=='refund' and t.state!='cancel')
        amount=abs(info['difference'])
        if source.currency_id.compare_amounts(amount,source.amount-reserved)>0:
            raise UserError('Refund exceeds the remaining unreserved payment balance.')
        taxes=self.env['account.tax'].browse(info['tax_ids'])
        original=line.invoice_lines.filtered(lambda l:l.move_id==invoice)
        if len(original)!=1:
            raise UserError('Multiple invoice allocations require review.')
        net=info['original_net']-info['alternative_net']
        credit=self.env['account.move'].with_company(self.company_id).create({
            'move_type':'out_refund','partner_id':invoice.partner_id.id,'company_id':self.company_id.id,
            'currency_id':self.currency_id.id,'journal_id':invoice.journal_id.id,'reversed_entry_id':invoice.id,
            'invoice_date':fields.Date.context_today(self),'ref':operation_key,
            'invoice_line_ids':[(0,0,{'name':'Replacement price difference: '+self.name,
                'account_id':original.account_id.id,'quantity':1,'price_unit':net*(1+sum(t.amount/100 for t in taxes if t.price_include)),
                'tax_ids':[(6,0,taxes.ids)]})]})
        if self.currency_id.compare_amounts(credit.amount_total,amount):
            raise UserError('Credit-note total differs from the approved refund.')
        # The source transaction links the order. Do not attach sale_order_ids to
        # a refund: sale post-processing may otherwise create another invoice.
        child=source._create_child_transaction(amount,is_refund=True,invoice_ids=[(6,0,credit.ids)],sale_order_ids=[(6,0,[])])
        job=self.env['after.order.refund'].sudo().create({'operation_key':operation_key,'order_id':self.id,'line_id':line.id,
            'product_id':product.id,'pricing_signature':pricing_signature,'snapshot':json.dumps(info),
            'transaction_id':child.id,'credit_id':credit.id})
        job.credit_signature=job._credit_fingerprint()
        self.message_post(body=Markup('Replacement refund approved: %s %s. Durable request %s prepared; no funds sent yet.') % (self.currency_id.name,amount,job.id))
        return job._result()

    def after_order_execute_replacement_refund(self, website_id, operation_key):
        self._care_refund_guard(website_id)
        job=self.env['after.order.refund'].sudo().search([('operation_key','=',operation_key),('order_id','=',self.id)],limit=1)
        if not job:
            raise UserError('Prepare and commit the approved refund before executing it.')
        self.env.cr.execute('SELECT id FROM after_order_refund WHERE id=%s FOR UPDATE',(job.id,))
        job.invalidate_recordset()
        tx=job.transaction_id; source=tx.source_transaction_id; credit=job.credit_id
        info=json.loads(job.snapshot)
        from odoo.addons.payment import utils as payment_utils
        from odoo.addons.payment_stripe import const
        amount=payment_utils.to_minor_currency_units(-tx.amount,tx.currency_id,arbitrary_decimal_number=const.CURRENCY_DECIMALS.get(tx.currency_id.name))
        try:
            if job.credit_signature!=job._credit_fingerprint():
                raise UserError('Credit-note accounts, taxes or allocation changed after approval. Review required.')
            if source.provider_code!='stripe' or source.state!='done' or source.company_id!=self.company_id or tx.currency_id!=self.currency_id:
                raise UserError('Original payment changed; accounting review required.')
            if credit.state=='cancel' or credit.currency_id!=self.currency_id or credit.partner_id.commercial_partner_id!=self.partner_invoice_id.commercial_partner_id or self.currency_id.compare_amounts(credit.amount_total,-tx.amount):
                raise UserError('Approved credit note changed; accounting review required.')
            if tx.provider_reference:
                data=source.provider_id._stripe_make_request('refunds/'+tx.provider_reference,method='GET')
            else:
                self._after_order_guard(job.line_id.id,website_id)
                if fields.Datetime.now()-job.create_date>=timedelta(hours=23):
                    raise UserError('Uncertain refund is outside the safe retry window. Reconcile with Stripe; do not resend.')
                current=self.after_order_alternative_info(job.line_id.id,website_id,reference=job.product_id.default_code)
                if current.get('pricing_signature')!=job.pricing_signature:
                    raise UserError('Pricing changed before refund submission; review required.')
                data=source.provider_id._stripe_make_request('refunds',payload={'payment_intent':source.provider_reference,
                    'amount':amount,'metadata[care_operation]':operation_key},idempotency_key=job.provider_key)
            if (not data.get('id') or data.get('payment_intent')!=source.provider_reference or data.get('amount')!=amount
                    or data.get('currency')!=self.currency_id.name.lower()):
                raise UserError('Provider refund response does not match the approved payment and amount.')
            tx.provider_reference=data['id']
            if data.get('status')!='succeeded':
                job.write({'state':'pending' if data.get('status') in ('pending','requires_action') else 'needs_review',
                           'last_error':'Provider refund status: '+str(data.get('status'))})
                self.message_post(body=Markup('Replacement refund %s: %s') % (job.id,job.last_error))
                return job._result()
            if tx.state!='done':
                tx._set_done()
            # Accounting is retried independently; a known provider refund is only retrieved, never resent.
            with self.env.cr.savepoint():
                if credit.state=='draft': credit.action_post()
                if not tx.payment_id: tx._create_payment()
                credit.invalidate_recordset()
                if (not tx.payment_id or tx.payment_id.move_id.state!='posted' or not self.currency_id.is_zero(credit.amount_residual)):
                    raise UserError('Provider refund succeeded; Odoo payment reconciliation is incomplete.')
                tx.is_post_processed=True
            changed=job.state!='completed'
            job.write({'state':'completed','last_error':False})
            if changed:
                self.message_post(body=Markup('Replacement refund verified and reconciled: %s %s; credit note %s; refund %s.') % (self.currency_id.name,-tx.amount,credit.name,tx.provider_reference))
        except Exception as exc:
            job.write({'state':'needs_review','last_error':str(exc)[:1000]})
            self.message_post(body=Markup('Replacement refund %s needs review: %s. Do not refund separately until the provider outcome is reconciled.') % (job.id,job.last_error))
        return job._result()
