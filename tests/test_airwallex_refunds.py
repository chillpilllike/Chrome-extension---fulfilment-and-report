import json
import threading
import unittest
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services.airwallex_refunds import AirwallexRefunds, ReviewInput, amount_guard, refund_totals, daily_refund_usage


class MemoryDB:
    """Transaction lock fake; PostgreSQL concurrency is additionally checked in integration."""
    def __init__(self):
        self.rows = {}
        self.lock = threading.RLock()
    @contextmanager
    def __call__(self):
        with self.lock:
            yield self
    def execute(self, sql, params=()):
        self.result = None
        if 'INSERT INTO airwallex_refund_payouts' in sql:
            keys = ['request_id','order_key','store_id','order_id','order_name','account_key','amount','currency','recipient','edit_reason','payload_hash','created_at','updated_at','actor']
            row = dict(zip(keys, params)); row.update(status='SUBMITTING', transfer_id=None, last_error='')
            self.rows[row['request_id']] = row
        elif "SET status='UNKNOWN'" in sql:
            self.rows[params[-1]].update(status='UNKNOWN',last_error=params[0])
        elif 'SET status=?' in sql:
            keys = ['status','transfer_id','updated_at','last_error','fee_amount','fee_currency']
            vals = [*params[:3], '', *params[3:5]]
            self.rows[params[-1]].update(dict(zip(keys,vals)))
        elif 'WHERE request_id=?' in sql:
            self.result = self.rows.get(params[0])
        elif 'SELECT request_id,transfer_id,created_at' in sql:
            self.result = list(self.rows.values())
        elif 'WHERE order_key=?' in sql:
            self.result = [v for v in self.rows.values() if v['order_key']==params[0]]
        return self
    def fetchone(self):return self.result
    def fetchall(self):return self.result or []


class RefundGuardTests(unittest.TestCase):
    def test_decimal_cap_and_invalid_amounts(self):
        self.assertEqual(amount_guard('0.30','0.30','0.01'), Decimal('.30'))
        for value in ['100.01','0','-1','NaN','Infinity','1.001','garbage']:
            with self.subTest(value=value), self.assertRaises(ValueError):amount_guard(value,'100','0.01')
        with self.assertRaises(ValueError):amount_guard('1.5','100','1')

    def test_external_and_local_deduplicate_and_reserve_failed(self):
        t={'id':'t1','request_id':'r1','reference':'Refund NC100','transfer_amount':'30','transfer_currency':'CAD','status':'PAID'}
        local=[{'request_id':'r1','transfer_id':'t1','amount':'30','currency':'CAD','status':'PAID'},
               {'request_id':'r2','amount':'20','currency':'CAD','status':'UNKNOWN','order_name':'NC100','created_at':''}]
        used, rows=refund_totals('NC100','CAD',[t],local)
        self.assertEqual(used,50); self.assertEqual(len(rows),2)
        t['status']='FAILED'
        self.assertEqual(refund_totals('NC100','CAD',[t],[])[0],30)
        t['status']='CANCELLED'
        self.assertEqual(refund_totals('NC100','CAD',[t],[])[0],0)

    def test_partial_and_whole_reference_matching(self):
        transfers=[{'id':str(i),'reference':name,'transfer_amount':20,'transfer_currency':'USD','status':'PAID'}
                   for i,name in enumerate(['Partial Refund NC100','Refund NC1000','Refund XNC100'])]
        self.assertEqual(refund_totals('NC100','USD',transfers,[])[0],20)
        with self.assertRaises(ValueError):refund_totals('NC100','CAD',transfers,[])


class DailyLimitTests(unittest.TestCase):
    def test_india_midnight_boundary_and_deduplication(self):
        from datetime import datetime, timezone
        now = datetime(2026, 9, 20, 18, 31, tzinfo=timezone.utc)
        rows = [{'request_id':'r1','transfer_id':'t1','created_at':'2026-09-20T18:30:00+00:00'},
                {'request_id':'r2','created_at':'2026-09-20T18:29:59+00:00'}]
        transfers = [{'id':'t1','request_id':'r1','reference':'Refund NC1','created_at':rows[0]['created_at']},
                     {'id':'t2','reference':'Partial Refund NC2','created_at':'2026-09-20T18:30:00+0000'},
                     {'id':'t3','reference':'Supplier invoice','created_at':rows[0]['created_at']}]
        usage=daily_refund_usage(rows,transfers,now)
        self.assertEqual(usage['used'],2);self.assertEqual(usage['date'],'2026-09-21')
        self.assertEqual(usage['remaining'],3)
    def test_uncertain_failed_and_cancelled_attempts_still_count(self):
        from datetime import datetime, timezone
        now=datetime.now(timezone.utc)
        rows=[{'request_id':str(i),'created_at':now.isoformat(),'status':state} for i,state in enumerate(
            ['SUBMITTING','UNKNOWN','PROCESSING','FAILED','CANCELLED'])]
        usage=daily_refund_usage(rows,[],now)
        self.assertEqual(usage['remaining'],0)
        with self.assertRaisesRegex(ValueError,'daily limit'):
            AirwallexRefunds.daily_guard(usage)
    def test_bad_refund_timestamp_blocks(self):
        with self.assertRaises(ValueError):
            daily_refund_usage([], [{'id':'t1','reference':'Refund NC1','created_at':None}])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.database=MemoryDB()
        self.cfg=dict(client_id='test-client',api_key='test-only-secret',state='enabled',account_id='')
        self.service=AirwallexRefunds(db=self.database,get_store=Mock(),client_factory=Mock(),
            configuration=lambda:self.cfg,staff_check=lambda request:request.headers.get('x-admin-token')=='test-staff')
        self.snap={'store_id':1,'order_id':2,'order_key':'key','order_name':'NC100','currency':'CAD','remaining':'100','rounding':'.01'}
        self.service.snapshot=Mock(return_value=self.snap)
        self.service.schema=Mock(return_value={'fields':[{'path':p,'required':True,'field':{'default':v}} for p,v in [
            ('beneficiary.type','BANK_ACCOUNT'),('beneficiary.bank_details.account_currency','CAD'),
            ('beneficiary.bank_details.account_name','Test Customer'),('beneficiary.bank_details.account_number','123456789')]]})
        self.calls=[]
        def call(method,path,**kw):
            self.calls.append((method,path,kw))
            if path == '/api/v1/transfers':return {'items':[]}
            if path.endswith('/balances/current'):return [{'currency':'CAD','available_amount':200}]
            if path.endswith('/create'):return {**kw['data'],'id':'transfer-1','status':'PROCESSING'}
            return {}
        self.service.call=Mock(side_effect=call)
    def form(self,**kw):
        return ReviewInput(**dict({'store_id':1,'order_id':2,'amount':'100','fields':{
            'beneficiary.bank_details.bank_country_code':'CA','transfer_method':'LOCAL'},
            'recipient_confirmed':True,'other_refunds_checked':True},**kw))
    def test_review_never_creates_transfer_and_hides_bank_account(self):
        r=self.service.prepare(self.form())
        self.assertNotIn('123456789',json.dumps(r));self.assertFalse(any(p.endswith('/create') for _,p,_ in self.calls))
        self.assertIn('6789',r['destination'])
    def test_tampered_currency_is_overridden(self):
        f=self.form();f.fields['beneficiary.bank_details.account_currency']='USD'
        self.service.prepare(f)
        payload=next(k['data'] for _,p,k in self.calls if p.endswith('transfers/validate'))
        self.assertEqual(payload['transfer_currency'],'CAD');self.assertEqual(payload['beneficiary']['bank_details']['account_currency'],'CAD')
    def test_partial_requires_warning_and_reason(self):
        with self.assertRaises(ValueError):self.service.prepare(self.form(amount='50'))
        self.service.prepare(self.form(amount='50',edit_acknowledged=True,edit_reason='One item'))
    def test_recipient_and_external_refund_confirmation_required(self):
        for field in ['recipient_confirmed','other_refunds_checked']:
            with self.assertRaises(ValueError):self.service.prepare(self.form(**{field:False}))
    def test_submit_rechecks_limit(self):
        review=self.service.prepare(self.form())
        self.snap['remaining']='99'
        with self.assertRaises(ValueError):self.service.submit(review['review_token'])
        self.assertFalse(any(p.endswith('/create') for _,p,_ in self.calls))
    def test_daily_limit_rechecked_after_review_and_idempotent_retry(self):
        from datetime import datetime, timezone
        review=self.service.prepare(self.form())
        for i in range(5):
            self.database.rows[str(i)]={'request_id':str(i),'created_at':datetime.now(timezone.utc).isoformat()}
        with self.assertRaisesRegex(ValueError,'daily limit'):
            self.service.submit(review['review_token'])
        with self.assertRaisesRegex(ValueError,'daily limit'):
            self.service.prepare(self.form())
        self.assertFalse(any(p.endswith('/create') for _,p,_ in self.calls))

    def test_duplicate_submission_creates_once(self):
        review=self.service.prepare(self.form())
        a=self.service.submit(review['review_token']);b=self.service.submit(review['review_token'])
        self.assertEqual(a['request_id'],b['request_id']);self.assertEqual(a['status'],'PROCESSING')
        self.assertEqual(sum(p.endswith('/create') for _,p,_ in self.calls),1)
    def test_timeout_keeps_reservation_and_does_not_resend(self):
        review=self.service.prepare(self.form());previous=self.service.call.side_effect
        def timeout(method,path,**kw):
            if path.endswith('/create'):raise TimeoutError()
            return previous(method,path,**kw)
        self.service.call.side_effect=timeout
        result=self.service.submit(review['review_token']);self.assertEqual(result['status'],'UNKNOWN')
        self.service.submit(review['review_token']);self.assertEqual(self.service.call.call_count,len(self.calls)+1)
        self.assertEqual(next(iter(self.database.rows.values()))['amount'],'100')
    def test_tampered_or_expired_review_rejected(self):
        token=self.service.prepare(self.form())['review_token']
        with self.assertRaises(ValueError):self.service.submit(token[:-8]+'tampered')
        with patch('cryptography.fernet.time.time',return_value=9999999999),self.assertRaises(ValueError):self.service.submit(token)
    def test_account_change_invalidates_review(self):
        review=self.service.prepare(self.form());self.cfg['client_id']='other'
        with self.assertRaises(ValueError):self.service.submit(review['review_token'])
    def test_transfer_history_fetches_all_pages(self):
        self.service.call.side_effect=[{'items':[{'id':'old'}],'page_after':'next'},{'items':[{'id':'new'}]}]
        rows=self.service.transfers();self.assertEqual(len(rows),2)
        self.assertEqual(self.service.call.call_args_list[0].kwargs['params']['page'],'0')
        self.assertEqual(self.service.call.call_args_list[1].kwargs['params']['page'],'next')
    def test_incomplete_history_fails_closed(self):
        self.service.call.side_effect=None;self.service.call.return_value={'items':[],'page_after':'repeat'}
        with self.assertRaises(ValueError):self.service.transfers()
    def test_routes_require_staff_and_same_origin(self):
        app=FastAPI();app.include_router(self.service.router());client=TestClient(app)
        self.assertEqual(client.get('/api/airwallex/refunds/history').status_code,403)
        r=client.post('/api/airwallex/refunds/schema',json={},headers={'x-admin-token':'test-staff','origin':'https://evil.example'})
        self.assertEqual(r.status_code,403)
        r=client.post('/api/airwallex/refunds/submit',json={'review_token':'x','confirmed':False},headers={'x-admin-token':'test-staff'})
        self.assertEqual(r.status_code,400)
    def test_dedicated_webhook_rejects_invalid_and_stale_signatures(self):
        import time, hmac, hashlib
        self.database.execute=Mock(return_value=Mock(fetchone=lambda:{'secret':'test-hook'}))
        body=b'{"name":"payout.transfer.paid"}'
        timestamp=str(int(time.time()*1000))
        signature=hmac.new(b'test-hook',timestamp.encode()+body,hashlib.sha256).hexdigest()
        self.assertTrue(self.service.verify_transfer_webhook(timestamp,signature,body))
        self.assertFalse(self.service.verify_transfer_webhook(timestamp,'invalid',body))
        self.assertFalse(self.service.verify_transfer_webhook('1000',signature,body))

    def test_transfer_hook_fast_path_does_not_consume_deposit_events(self):
        import ast
        from pathlib import Path
        tree=ast.parse(Path('app/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='api_airwallex_webhook')
        fast=next(n for n in fn.body if isinstance(n,ast.If) and 'verify_transfer_webhook' in ast.unparse(n.test))
        self.assertFalse(any(isinstance(n,ast.Return) for n in fast.body))
        self.assertTrue(any(isinstance(n,ast.If) and 'payout.transfer.' in ast.unparse(n.test) for n in fast.body))

    def test_public_proxy_still_rejects_payouts(self):
        from app.services.airwallex_api import validate_operation
        with self.assertRaises(ValueError):validate_operation({'method':'POST','endpoint':'/api/v1/transfers/create'},self.cfg)



class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.order={'id':1,'name':'NC100','amount_total':100,'currency_id':[1,'CAD'],'partner_id':[7,'Test'],
                    'invoice_ids':[],'state':'sale','date_order':'2026-01-01'}
        self.tx={'id':10,'amount':100,'currency_id':[1,'CAD'],'state':'done','operation':'online_direct',
                 'sale_order_ids':[1],'provider_code':'test','provider_id':[1,'Card provider'],'partner_id':[7,'Test'],'reference':'NC100'}
        self.transactions=[self.tx];self.invoices=[];self.children=[];self.external=[]
        self.database=MemoryDB();self.client=Mock();self.client.execute.return_value={k:{} for k in ['source_transaction_id','airwallex_deposit_id','airwallex_payment_amount','airwallex_payment_currency_id']}
        def read(model,domain,fields,**kw):
            if model=='sale.order':return [self.order]
            if model=='payment.transaction' and domain[0][0]=='airwallex_deposit_id':return []
            if model=='payment.transaction':return self.children if domain[0][0]=='source_transaction_id' else self.transactions
            if model=='account.move':return self.invoices
            if model=='res.currency':return [{'rounding':.01}]
            if model=='res.partner':return [{'name':'Test','email':'test@example.com','street':'Road','street2':False,'city':'City','zip':'123','country_id':False,'state_id':False}]
            raise AssertionError(model)
        self.client.search_read.side_effect=read
        self.svc=AirwallexRefunds(db=self.database,get_store=lambda _:SimpleNamespace(odoo_url='https://example.com',odoo_db='test',website_id=None),client_factory=lambda _:self.client,
                                 configuration=lambda:dict(client_id='test',api_key='test',state='enabled'),staff_check=lambda _:True)
        self.svc.transfers=lambda:self.external
    def test_overpayment_is_capped_at_order_value(self):
        self.tx['amount']=150
        self.assertEqual(self.svc.snapshot(1,1)['remaining'],'100')
    def test_unpaid_pending_and_authorized_are_blocked(self):
        for state in ['pending','authorized','draft','error']:
            self.tx['state']=state
            with self.subTest(state=state),self.assertRaises(ValueError):self.svc.snapshot(1,1)
    def test_partial_original_payment_caps_refund(self):
        self.tx['amount']=40
        self.assertEqual(self.svc.snapshot(1,1)['remaining'],'40')
    def test_external_refund_is_deducted(self):
        self.external=[{'id':'t1','reference':'Partial Refund NC100','transfer_amount':30,'transfer_currency':'CAD','status':'PAID'}]
        self.assertEqual(self.svc.snapshot(1,1)['remaining'],'70')
    def test_refund_child_without_order_link_is_deducted(self):
        self.children=[{**self.tx,'id':11,'amount':20,'operation':'refund','sale_order_ids':[]}]
        self.assertEqual(self.svc.snapshot(1,1)['remaining'],'80')
    def test_shared_order_payment_is_blocked(self):
        self.tx['sale_order_ids']=[1,2]
        with self.assertRaises(ValueError):self.svc.snapshot(1,1)
    def test_locked_payment_conversion_and_settled_deposit(self):
        self.tx.update(provider_code='airwallex_transfer',airwallex_deposit_id='deposit-1',
                       airwallex_payment_amount=75,airwallex_payment_currency_id=[2,'USD'])
        self.svc.call=Mock(return_value={'reference':'NC100','status':'SETTLED','amount':75,'currency':'USD'})
        result=self.svc.snapshot(1,1);self.assertEqual(result['currency'],'USD');self.assertEqual(Decimal(result['remaining']),75)
        self.assertEqual(Decimal(result['order_equivalent']),75)
        self.assertEqual(Decimal(result['conversion_rate']),Decimal('.75'))
        self.svc.call.return_value['status']='PENDING'
        with self.assertRaises(ValueError):self.svc.snapshot(1,1)
    def test_customer_and_deposit_order_mismatch_blocked(self):
        self.tx['partner_id']=[8,'Someone else']
        with self.assertRaisesRegex(ValueError,'customer'):
            self.svc.snapshot(1,1)
        self.tx.update(partner_id=[7,'Test'],provider_code='airwallex_transfer',airwallex_deposit_id='d1')
        self.svc.call=Mock(return_value={'reference':'NC1000','status':'SETTLED','amount':100,'currency':'CAD'})
        with self.assertRaisesRegex(ValueError,'reference'):
            self.svc.snapshot(1,1)
    def test_invoice_contact_must_belong_to_same_customer(self):
        order={'partner_id':[7,'Customer'],'partner_invoice_id':[8,'Billing']}
        self.client.search_read=Mock(return_value=[{'commercial_partner_id':[7,'Customer']},{'commercial_partner_id':[7,'Customer']}])
        self.assertTrue(self.svc.customer_matches(self.client,order,[8,'Billing']))
        self.assertFalse(self.svc.customer_matches(self.client,order,[9,'Other']))
        self.client.search_read.return_value[1]['commercial_partner_id']=[9,'Other']
        self.assertFalse(self.svc.customer_matches(self.client,order,[8,'Billing']))

    def test_original_method_and_equivalent_displayed(self):
        self.tx.update(provider_code='razorpay',provider_id=[3,'Razorpay'],payment_method_id=[1,'VISA'])
        result=self.svc.snapshot(1,1)
        self.assertEqual(result['order_equivalent'],'100')
        self.assertEqual(result['payment_matches'][0]['provider'],'Razorpay')
        self.assertEqual(result['payment_matches'][0]['method'],'VISA')

    def test_cross_currency_overpayment_cannot_exceed_order_equivalent(self):
        self.tx.update(amount=150,provider_code='airwallex_transfer',airwallex_deposit_id='deposit-1',
                       airwallex_payment_amount=112.5,airwallex_payment_currency_id=[2,'USD'])
        self.svc.call=Mock(return_value={'reference':'NC100','status':'SETTLED','amount':112.5,'currency':'USD'})
        result=self.svc.snapshot(1,1)
        self.assertEqual(Decimal(result['remaining']),75)
        with self.assertRaises(ValueError):amount_guard('75.01',result['remaining'],result['rounding'])

    def test_duplicate_deposit_is_blocked(self):
        self.tx.update(provider_code='airwallex_transfer',airwallex_deposit_id='deposit-1')
        self.transactions.append({**self.tx,'id':11})
        self.svc.call=Mock(return_value={'reference':'NC100','status':'SETTLED','amount':100,'currency':'CAD'})
        with self.assertRaises(ValueError):self.svc.snapshot(1,1)
    def test_posted_credit_note_is_deducted(self):
        self.order['invoice_ids']=[20]
        self.invoices=[{'id':21,'state':'posted','currency_id':[1,'CAD'],'move_type':'out_refund','amount_total':25,'amount_residual':0,'payment_state':'paid'}]
        self.assertEqual(self.svc.snapshot(1,1)['remaining'],'75')

if __name__=='__main__':unittest.main()
