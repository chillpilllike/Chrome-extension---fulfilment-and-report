import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.support.policy import Evidence, public_order, public_status
from app.support.odoo import SupportOrders, ScopeError
from app.support.routes import create_router

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


class PolicyTests(unittest.TestCase):
    def status(self, **kwargs):
        return public_status('sale', Evidence(observed_at=NOW, **kwargs), NOW)

    def test_latest_required_package_plus_calendar_day(self):
        self.assertEqual('2026-09-11', self.status(expected_dates=(date(2026,9,8), date(2026,9,10)))['estimated_dispatch'])

    def test_unknown_or_overdue_package_withdraws_entire_estimate(self):
        for dates in [(None, date(2026,9,10)), (date(2026,9,5), date(2026,9,10))]:
            self.assertIsNone(self.status(expected_dates=dates)['estimated_dispatch'])

    def test_inbound_delivery_never_means_customer_delivery(self):
        value = self.status(inbound_delivered=True)
        self.assertEqual('processing', value['status'])
        self.assertNotIn('delivered', value['reply'])

    def test_receipt_precedes_expected_date(self):
        self.assertEqual('2026-09-07', self.status(received_dates=(date(2026,9,6),), expected_dates=(date(2026,9,20),))['estimated_dispatch'])

    def test_unconfirmed_cancelled_and_stale_never_promise_dispatch(self):
        evidence = Evidence(observed_at=NOW-timedelta(minutes=6), outbound_state='moving')
        for state in ['draft', 'sent', 'cancel', 'sale']:
            value=public_status(state, evidence, NOW)
            self.assertIsNone(value['estimated_dispatch'])
            self.assertNotEqual('dispatched',value['status'])

    def test_label_and_partial_do_not_claim_full_dispatch(self):
        self.assertEqual('awaiting_collection', self.status(outbound_state='label')['status'])
        self.assertEqual('partially_dispatched', self.status(outbound_state='partial')['status'])

    def test_raw_internal_fields_never_enter_public_contract(self):
        source={'name':'SG12345','state':'sale','amount_total':12.5,'currency_id':[1,'AUD'],
                'notes':'Amazon dropshipping ASIN B012345678','items':[{'name':'Amazon supplier invoice'}],
                'amazon_order_id':'123-4567890-1234567','tracking_url':'https://amazon.com/test'}
        result=public_order(source,Evidence(observed_at=datetime.now(timezone.utc)))
        raw=json.dumps(result)
        for forbidden in ['Amazon','dropshipping','ASIN','B012345678','amazon.com','123-4567890']:
            self.assertNotIn(forbidden,raw)
        self.assertEqual('12.5',result['total'])
        source['name']='<img src=https://amazon.com>Amazon123';source['currency_id']=[1,'Amazon']
        result=public_order(source,Evidence(observed_at=datetime.now(timezone.utc)))
        self.assertEqual('Your order',result['reference']); self.assertIsNone(result['currency'])


class Client:
    def __init__(self): self.calls=[]; self.fail=False; self.found=True
    def fields_get(self, model): return {'website_id':{}}
    def existing_fields(self, model, fields): return fields
    def search_read(self, model, domain, fields, **kw):
        self.calls.append((model,domain,kw))
        if self.fail: raise RuntimeError('SECRET upstream credentials')
        if model=='sale.order':
            return [{'id':4,'name':'SG123','state':'draft','amount_total':10,'currency_id':[1,'AUD']}] if self.found else []
        return []
    def order_url(self, ident): return 'https://secretgreen.com.au/web#id='+str(ident)


class ScopeTests(unittest.TestCase):
    def test_requires_explicit_website(self):
        for value in [None, 0, '', True]:
            with self.assertRaises(ScopeError): SupportOrders(Client(), value)
    def test_listing_has_website_and_customer_scope_and_keeps_drafts(self):
        c=Client(); SupportOrders(c,1).list(partner_id=7,page=2,per_page=10)
        _,domain,options=c.calls[0]
        self.assertIn(['website_id','=',1],domain);self.assertIn(['partner_id','=',7],domain)
        self.assertEqual(10,options['offset']);self.assertEqual(11,options['limit'])
        self.assertFalse(any(term[0]=='state' for term in domain))
    def test_timeline_cannot_bypass_order_scope(self):
        c=Client();c.found=False
        with self.assertRaises(LookupError): SupportOrders(c,1).timeline(4)
        self.assertEqual(1,len(c.calls))
    def test_missing_website_field_fails_closed(self):
        c=Client();c.fields_get=lambda _: {}
        with self.assertRaises(ScopeError): SupportOrders(c,1).list()


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.odoo=Client();self.token='staff-secret'
        @contextmanager
        def db():
            class Connection:
                def execute(self,*args): return self
                def fetchall(self): return []
            yield Connection()
        app=FastAPI();app.include_router(create_router(get_store=lambda _: object(),list_stores=lambda:[],
            client_factory=lambda _: self.odoo,db=db,admin_token=lambda:self.token))
        self.client=TestClient(app); self.headers={'X-Admin-Token':self.token}
    def test_staff_auth_required_even_without_legacy_token(self):
        for path in ['/api/support/status','/api/support/orders?store_id=8&website_id=1','/api/support/orders/4?store_id=8&website_id=1']:
            self.assertEqual(401,self.client.get(path).status_code)
        self.token='';self.assertEqual(401,self.client.get('/api/support/status').status_code)
    def test_query_token_and_claimed_customer_verification_do_not_grant_access(self):
        r=self.client.get('/api/support/status?admin_token=staff-secret',headers={'X-Verified':'true'})
        self.assertEqual(401,r.status_code)
    def test_no_store_secrets_in_status(self):
        r=self.client.get('/api/support/status',headers=self.headers)
        self.assertFalse(r.json()['customer_delivery_enabled']);self.assertEqual('no-store',r.headers['Cache-Control'])
    def test_source_outage_is_not_empty_results_or_secret_error(self):
        self.odoo.fail=True
        r=self.client.get('/api/support/orders?store_id=8&website_id=1',headers=self.headers)
        self.assertEqual(503,r.status_code);self.assertNotIn('SECRET',r.text)
    def test_detail_does_not_enable_sending(self):
        r=self.client.get('/api/support/orders/4?store_id=8&website_id=1',headers=self.headers)
        self.assertEqual(200,r.status_code);self.assertFalse(r.json()['automation']['can_send'])
    def test_missing_website_and_unbounded_page_rejected(self):
        for query in ['store_id=8','store_id=8&website_id=1&per_page=1000']:
            self.assertEqual(422,self.client.get('/api/support/orders?'+query,headers=self.headers).status_code)

if __name__=='__main__': unittest.main()
