import unittest
from decimal import Decimal
from unittest.mock import Mock
from tests import test_airwallex_refunds as fixtures
from app.services.airwallex_refunds import amount_guard

class ShortPaymentTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.SnapshotTests();f.setUp();self.f=f;self.s=f.svc
        f.tx.update(state='pending',provider_code='airwallex_transfer',airwallex_payment_amount=100,
                    airwallex_payment_currency_id=[1,'CAD'])
        self.events=[{'deposit_id':'deposit-1'},{'deposit_id':'deposit-1'}]
        original=f.database.execute
        def execute(sql,params=()):
            if 'SELECT DISTINCT deposit_id' in sql:
                f.database.result=self.events;return f.database
            return original(sql,params)
        f.database.execute=execute
        self.deposit={'id':'deposit-1','status':'SETTLED','reference':'Payment NC100','amount':99.93,'currency':'CAD'}
        self.s.call=Mock(side_effect=lambda *a,**kw:dict(self.deposit))
    def test_short_payment_warns_uses_received_value_and_does_not_modify_odoo(self):
        result=self.s.snapshot(1,1)
        self.assertEqual(Decimal(result['remaining']),Decimal('99.93'))
        self.assertEqual(Decimal(result['order_equivalent']),100)
        self.assertEqual(result['conversion_rate'],'1')
        self.assertIn('0.07 CAD short',result['warnings'][0])
        self.assertIn('pending',result['evidence'])
        self.assertEqual(result['payment_matches'][0]['odoo_payment_status'],'pending')
        self.assertEqual(self.f.tx['state'],'pending')
        self.f.client.write.assert_not_called()
        with self.assertRaises(ValueError):amount_guard('100',result['remaining'],'.01')
    def test_previous_refunds_and_credit_notes_are_deducted(self):
        self.f.external=[{'id':'old-refund','reference':'Refund NC100','transfer_amount':20,'transfer_currency':'CAD','status':'PAID'}]
        self.f.order['invoice_ids']=[20]
        self.f.invoices=[{'id':20,'state':'posted','currency_id':[1,'CAD'],'move_type':'out_refund','amount_total':10,'amount_residual':0,'payment_state':'paid'}]
        self.assertEqual(Decimal(self.s.snapshot(1,1)['remaining']),Decimal('69.93'))
    def test_missing_ambiguous_reversed_wrong_currency_and_other_order_receipts_block(self):
        for changes in [{'status':'PENDING'},{'status':'REVERSED'},{'currency':'USD'},
                        {'reference':'NC1000'},{'reference':'NC100 NC200'},{'amount':0},{'amount':101}]:
            with self.subTest(changes=changes):
                original=self.deposit.copy();self.deposit.update(changes)
                with self.assertRaises(ValueError):self.s.snapshot(1,1)
                self.deposit=original
        for events in [[],[{'deposit_id':'one'},{'deposit_id':'two'}]]:
            self.events=events
            with self.assertRaises(ValueError):self.s.snapshot(1,1)
    def test_receipt_status_is_rechecked_not_cached(self):
        self.s.snapshot(1,1);self.deposit['status']='REVERSED'
        with self.assertRaises(ValueError):self.s.snapshot(1,1)
    def test_customer_mismatch_and_multiple_active_payments_block(self):
        self.f.tx['partner_id']=[8,'Other']
        with self.assertRaises(ValueError):self.s.snapshot(1,1)
        self.f.tx['partner_id']=[7,'Test'];self.f.transactions.append({**self.f.tx,'id':12})
        with self.assertRaises(ValueError):self.s.snapshot(1,1)
    def test_reused_receipt_or_ambiguous_order_reference_blocks(self):
        original=self.f.client.search_read.side_effect
        for model_name in ['payment.transaction','sale.order']:
            def read(model,domain,fields,**kw):
                if model==model_name and domain[0][0] in ['airwallex_deposit_id','name']:
                    return [{'id':999}]
                return original(model,domain,fields,**kw)
            self.f.client.search_read.side_effect=read
            with self.assertRaises(ValueError):self.s.snapshot(1,1)
    def test_draft_and_authorized_payments_are_not_promoted(self):
        for state in ['draft','authorized','error','cancel']:
            self.f.tx['state']=state
            with self.assertRaises(ValueError):self.s.snapshot(1,1)
    def test_final_submission_rechecks_receipt_and_rejects_reversal_or_reduced_cap(self):
        for changed in [{'status':'REVERSED'},{'amount':90}]:
            with self.subTest(changed=changed):
                w=fixtures.WorkflowTests();w.setUp()
                self.deposit.update(status='SETTLED',amount=99.93)
                w.service.snapshot=lambda *args:self.s.snapshot(1,1)
                review=w.service.prepare(w.form(amount='99.93'))
                self.assertTrue(review['warnings'])
                self.deposit.update(changed)
                with self.assertRaises(ValueError):w.service.submit(review['review_token'])
                self.assertFalse(any(p.endswith('/create') for _,p,_ in w.calls))
    def test_same_order_name_in_another_database_blocks_receipt_allocation(self):
        from types import SimpleNamespace
        current=self.s.get_store(1)
        other=SimpleNamespace(odoo_url='https://other.example.com',odoo_db='other',website_id=None)
        self.s.get_store=lambda id:current if id==1 else other
        self.s.list_stores=lambda:[{'id':1},{'id':2}]
        with self.assertRaisesRegex(ValueError,'not unique'):self.s.snapshot(1,1)
