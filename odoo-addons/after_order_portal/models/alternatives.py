"""Privileged, idempotent accounting bridge. Public portal users cannot call it."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse

from odoo import fields, models
from odoo.exceptions import AccessError, UserError
from markupsafe import Markup


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    after_order_operation_key = fields.Char(copy=False, index=True, readonly=True)
    after_order_parent_id = fields.Many2one('sale.order', copy=False, readonly=True)
    after_order_mail_id = fields.Many2one('mail.mail', copy=False, readonly=True)
    after_order_pricing_signature = fields.Char(copy=False, readonly=True)
    after_order_resolution_log = fields.Text(copy=False, readonly=True, default='[]')

    _sql_constraints = [('after_order_operation_unique', 'unique(after_order_operation_key)', 'An adjustment quotation already exists for this request.')]

    def _after_order_guard(self, line_id, website_id):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Only an Odoo administrator may use the fulfilment accounting bridge.')
        if self.state != 'sale' or self.website_id.id != int(website_id) or self.after_order_parent_id:
            raise UserError('A confirmed order on the matching website is required.')
        if not self.date_order or self.date_order.date().isoformat() < '2026-08-01':
            raise UserError('This order is outside the after-order date scope.')
        line = self.order_line.filtered(lambda item: item.id == int(line_id))
        if not line or line.display_type or line.qty_delivered or line.product_uom_qty <= 0:
            raise UserError('This affected product line is missing or already delivered.')
        return line

    def _after_order_alternative_product(self, line_id, website_id, template_id=0, reference=''):
        line = self._after_order_guard(line_id, website_id)
        Product = self.env['product.product'].with_company(self.company_id)
        domain = [('active', '=', True), ('sale_ok', '=', True),
                  '|', ('company_id', '=', False), ('company_id', '=', self.company_id.id),
                  '|', ('website_id', '=', False), ('website_id', '=', self.website_id.id),
                  ('is_published', '=', True)]
        if reference:
            domain += [('default_code', '=', reference)]
        else:
            domain += [('product_tmpl_id', '=', int(template_id))]
        products = Product.search(domain, limit=2)
        if len(products) != 1:
            raise UserError('Use an exact, unique Internal Reference for a published product variant on this website.')
        product = products[0]
        if product == line.product_id or not product.default_code:
            raise UserError('Choose a different product with an Internal Reference.')
        return line, product

    def _after_order_priced_alternative(self, line, product):
        # Never infer money actually paid from the zeroed unavailable sale line.
        invoices = line.invoice_lines.filtered(lambda item: item.move_id.state == 'posted')
        paid = invoices.filtered(lambda item: item.move_id.move_type == 'out_invoice')
        credits = invoices.filtered(lambda item: item.move_id.move_type == 'out_refund')
        if not paid or any(item.move_id.payment_state != 'paid' for item in paid):
            raise UserError('A fully paid, posted invoice for this line is required for automatic price differences. Accounting must review unpaid or authorized-only orders.')
        if any(item.move_id.payment_state != 'paid' for item in credits):
            raise UserError('An existing credit note has not settled. Accounting review required.')
        if sum(paid.mapped('quantity')) != line.product_uom_qty or credits:
            raise UserError('Split quantities or prior refunds require accounting review before an alternative can be priced.')
        if any(item.move_id.currency_id != self.currency_id or len(item.sale_line_ids) != 1 for item in paid):
            raise UserError('Invoice currency or allocation is ambiguous. Accounting review required.')
        if any(item.price_total < 0 for invoice in paid.move_id for item in invoice.invoice_line_ids):
            raise UserError('An order-level discount or credit needs accounting allocation before an automatic price difference.')
        quantity = line.product_uom_qty
        if line.product_uom != product.uom_id:
            raise UserError('Different units of measure need team review before automatic substitution.')
        price = self.pricelist_id._get_product_price(product, quantity, uom=product.uom_id, currency=self.currency_id)
        taxes = self.fiscal_position_id.map_tax(product.taxes_id.filtered(lambda tax: tax.company_id == self.company_id))
        totals = taxes.compute_all(price, currency=self.currency_id, quantity=quantity, product=product, partner=self.partner_id)
        original = self.currency_id.round(sum(paid.mapped('price_total')))
        original_net = self.currency_id.round(sum(paid.mapped('price_subtotal')))
        alternative = self.currency_id.round(totals['total_included'])
        difference = self.currency_id.round(alternative-original)
        data = {'product_id': product.id, 'product_tmpl_id': product.product_tmpl_id.id,
                'name': product.display_name, 'default_code': product.default_code,
                'description': product.description or '', 'quantity': quantity,
                'original_total': original, 'alternative_total': alternative, 'difference': difference,
                'currency': self.currency_id.name, 'unit_price': price,
                'original_net': original_net, 'alternative_net': totals['total_excluded'],
                'tax_ids': taxes.ids, 'invoice_ids': paid.move_id.ids,
                'same_taxes': all(set(item.tax_ids.ids) == set(taxes.ids) for item in paid),
                'simple_taxes': all(tax.amount_type == 'percent' and not tax.include_base_amount for tax in taxes)}
        signature_data = {k: v for k, v in data.items() if k not in ('name','description')}
        data['pricing_signature'] = hashlib.sha256(json.dumps(signature_data, sort_keys=True).encode()).hexdigest()
        return data

    def after_order_alternative_info(self, line_id, website_id, template_id=0, reference=''):
        line, product = self._after_order_alternative_product(line_id,website_id,template_id,reference)
        try:
            return self._after_order_priced_alternative(line,product)
        except UserError as exc:
            # A valid recommendation is still useful when accounting cannot yet
            # establish a trustworthy price difference. Never substitute zero.
            return {'product_id':product.id,'product_tmpl_id':product.product_tmpl_id.id,
                'name':product.display_name,'default_code':product.default_code,
                'description':product.description or '', 'quantity':line.product_uom_qty,
                'currency':self.currency_id.name,'pricing_error':str(exc),'pricing_signature':''}

    def after_order_process_alternative(self, line_id, website_id, product_id, operation_key, deadline_at, pricing_signature):
        line = self._after_order_guard(line_id, website_id)
        if self.env['ir.config_parameter'].sudo().get_param('after_order_portal.live_alternatives_enabled') != 'true':
            raise UserError('Live alternative processing is disabled in Odoo. Complete test-mode verification first.')
        deadline = datetime.fromisoformat(deadline_at.replace('Z','+00:00'))
        if deadline.tzinfo is None or deadline > datetime.now(timezone.utc):
            raise UserError('The customer selection window has not expired.')
        if not operation_key.startswith('care:') or len(operation_key) > 160:
            raise UserError('Invalid alternative operation key.')
        self.env.cr.execute('SELECT id FROM sale_order WHERE id=%s FOR UPDATE', (self.id,))
        product = self.env['product.product'].browse(int(product_id)).exists()
        if not product:
            raise UserError('Selected product no longer exists.')
        info = self.after_order_alternative_info(line_id,website_id,reference=product.default_code)
        if info.get('pricing_error'):
            raise UserError(info['pricing_error'])
        if info['product_id'] != product.id or info['pricing_signature'] != pricing_signature:
            raise UserError('Alternative pricing changed; obtain a new customer agreement.')
        result = {'difference': info['difference'], 'currency': info['currency'], 'status': 'ready'}
        if info['difference'] <= 0:
            # Credit notes/refunds are deliberately left for the team's approval.
            result['refund_amount'] = abs(info['difference'])
            recorded = json.loads(self.after_order_resolution_log or '[]')
            if operation_key not in recorded:
                self.message_post(body=Markup('Customer alternative selected for line %s: <b>%s</b>. The 24-hour window has closed. Difference refund for team review: %s %s. No refund has been issued.') % (line.id,product.display_name,self.currency_id.name,result['refund_amount']),subtype_xmlid='mail.mt_note')
                self.after_order_resolution_log = json.dumps([*recorded,operation_key])
            return result
        if not info['same_taxes'] or not info['simple_taxes']:
            raise UserError('Different or compound tax treatments need accounting review; no quotation was created.')
        quote = self.search([('after_order_operation_key','=',operation_key)],limit=1)
        if quote and (quote.after_order_parent_id != self or quote.company_id != self.company_id):
            raise UserError('Quotation operation scope mismatch.')
        if not quote:
            adjustment_product = self.env.ref('after_order_portal.alternative_price_adjustment').with_company(self.company_id)
            taxes = self.env['account.tax'].browse(info['tax_ids'])
            net = info['alternative_net']-info['original_net']
            # Convert the net delta to the configured price-included convention.
            tax_multiplier = 1 + sum(t.amount/100 for t in taxes if t.price_include)
            quote = self.create({
                'partner_id':self.partner_id.id, 'partner_invoice_id':self.partner_invoice_id.id,
                'partner_shipping_id':self.partner_shipping_id.id, 'company_id':self.company_id.id,
                'website_id':self.website_id.id, 'pricelist_id':self.pricelist_id.id,
                'fiscal_position_id':self.fiscal_position_id.id, 'currency_id':self.currency_id.id,
                'origin':self.name, 'client_order_ref': '%s — alternative price difference' % self.name,
                'after_order_parent_id':self.id, 'after_order_operation_key':operation_key,
                'after_order_pricing_signature':pricing_signature,
                'require_payment':True, 'prepayment_percent':1.0, 'require_signature':False,
                'order_line':[(0,0,{'product_id':adjustment_product.product_variant_id.id,
                    'name':'Price difference for %s: %s → %s' % (self.name,line.name,product.display_name),
                    'product_uom_qty':1, 'price_unit':net*tax_multiplier, 'discount':0,
                    'tax_id':[(6,0,taxes.ids)]})],
            })
            if self.currency_id.compare_amounts(quote.amount_total,info['difference']):
                raise UserError('Quotation taxes do not match the agreed difference. Accounting review required.')
            # Queue in the same transaction: no forced SMTP send inside an RPC.
            template = self.env.ref('after_order_portal.alternative_quotation_email')
            host = urlparse(self.website_id.domain or '').hostname
            if not host or not self.partner_id.email:
                raise UserError('Website sender domain and customer email are required.')
            mail_id = template.with_context(lang=self.partner_id.lang).send_mail(quote.id, force_send=False, email_values={
                'subject':'%s — payment for your selected alternative' % self.name,
                'email_from':'notifications@%s' % host.removeprefix('www.'),
                'auto_delete':False,
            })
            quote.after_order_mail_id = mail_id
            quote.write({'state':'sent'})
            self.message_post(body=Markup('Alternative price-difference quotation <b>%s</b> created after the selection deadline; payment required.') % quote.name,subtype_xmlid='mail.mt_note')
        if quote.state == 'cancel':
            raise UserError('The price-difference quotation was cancelled. Review before fulfilment.')
        if (quote.partner_id != self.partner_id or quote.website_id != self.website_id
                or quote.currency_id != self.currency_id or quote.after_order_pricing_signature != pricing_signature
                or self.currency_id.compare_amounts(quote.amount_total,info['difference'])):
            raise UserError('The quotation changed after creation. Review it before releasing fulfilment.')
        transactions = quote.transaction_ids.filtered(lambda tx: tx.state == 'done' and tx.operation != 'refund')
        # Authorized is NOT paid. Ambiguous shared transactions/refunds must be reviewed.
        eligible = transactions.filtered(lambda tx: tx.currency_id == quote.currency_id and tx.sale_order_ids == quote)
        refunds = quote.transaction_ids.filtered(lambda tx: tx.operation == 'refund' and tx.state != 'cancel')
        refunds |= self.env['payment.transaction'].search([('source_transaction_id','in',eligible.ids),('operation','=','refund'),('state','!=','cancel')])
        paid = not refunds and self.currency_id.compare_amounts(sum(eligible.mapped('amount')),quote.amount_total) >= 0
        invoices = quote.invoice_ids.filtered(lambda move: move.move_type == 'out_invoice' and move.state == 'posted')
        if (not refunds and invoices and all(move.payment_state == 'paid' and move.currency_id == quote.currency_id for move in invoices)
                and self.currency_id.compare_amounts(sum(invoices.mapped('amount_total')),quote.amount_total) == 0):
            paid = True
        if quote.invoice_ids.filtered(lambda move: move.move_type == 'out_refund' and move.state != 'cancel'):
            paid = False
        result.update({'status':'ready' if paid else 'waiting_payment', 'quote_id':quote.id,
            'quote_name':quote.name, 'quote_url':quote.get_portal_url(),
            'payment_verified':bool(paid), 'email_status':quote.after_order_mail_id.state or 'unknown',
            'mail':{'id':quote.after_order_mail_id.id,'recipient':quote.after_order_mail_id.email_to or self.partner_id.email,
                'sender':quote.after_order_mail_id.email_from or '', 'subject':quote.after_order_mail_id.subject or '',
                'state':quote.after_order_mail_id.state or 'unknown','error':quote.after_order_mail_id.failure_reason or ''}})
        return result

    def after_order_retry_quote_email(self, quote_id):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise AccessError('Administrator access is required.')
        if self.env['ir.config_parameter'].sudo().get_param('after_order_portal.live_alternatives_enabled') != 'true':
            raise UserError('Live alternative processing is disabled.')
        quote = self.search([('id','=',int(quote_id)),('after_order_parent_id','=',self.id)],limit=1)
        if (not quote or self.state != 'sale' or quote.state not in ('draft','sent') or quote.is_expired
                or quote.partner_id != self.partner_id or quote.website_id != self.website_id or quote.company_id != self.company_id):
            raise UserError('The quotation is no longer eligible for a payment email.')
        mail = quote.after_order_mail_id
        self.env.cr.execute('SELECT id FROM mail_mail WHERE id=%s FOR UPDATE',(mail.id,))
        mail.invalidate_recordset(['state'])
        if mail.state != 'exception':
            raise UserError('Only a definitively failed quotation email may be retried.')
        mail.write({'state':'outgoing'})
        self.message_post(body=Markup('Quotation email retry queued for <b>%s</b>.') % quote.name,subtype_xmlid='mail.mt_note')
        return True
