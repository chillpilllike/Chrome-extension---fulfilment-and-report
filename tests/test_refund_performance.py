import unittest
from unittest.mock import Mock, patch
from app.services.refund_cache import RefundMetadataCache
from tests import test_airwallex_refunds as fixtures

class CacheTests(unittest.TestCase):
    def test_metadata_expires_and_callers_cannot_mutate_cached_values(self):
        cache=RefundMetadataCache(ttl=5)
        loader=Mock(return_value={'items':[1]})
        with patch('app.services.refund_cache.monotonic',return_value=10):
            cache.get('a',loader)['items'].append(2)
            self.assertEqual(cache.get('a',loader),{'items':[1]})
            self.assertEqual(loader.call_count,1)
            cache.get('b',loader)
        with patch('app.services.refund_cache.monotonic',return_value=16):cache.get('a',loader)
        self.assertEqual(loader.call_count,3)
    def test_failed_metadata_is_not_cached(self):
        cache=RefundMetadataCache();loader=Mock(side_effect=[ValueError('unavailable'),{'ok':True}])
        with self.assertRaises(ValueError):cache.get('a',loader)
        self.assertEqual(cache.get('a',loader),{'ok':True})

class FreshChecksTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SnapshotTests();self.fixture.setUp();self.service=self.fixture.svc
    def test_metadata_reuse_never_reuses_refund_history(self):
        self.assertEqual(self.service.snapshot(1,1)['remaining'],'100')
        self.fixture.external.append({'id':'new','reference':'Refund NC100','transfer_amount':30,'transfer_currency':'CAD','status':'PAID'})
        self.assertEqual(self.service.snapshot(1,1)['remaining'],'70')
        fields=[c for c in self.fixture.client.execute.call_args_list if c.args[1]=='fields_get']
        self.assertEqual(len(fields),1)
    def test_metadata_reuse_never_reuses_payment_status(self):
        self.service.snapshot(1,1);self.fixture.tx['state']='pending'
        with self.assertRaisesRegex(ValueError,'No completed payment'):self.service.snapshot(1,1)
    def test_supplied_history_is_reused_and_not_fetched_again(self):
        self.service.transfers=Mock(side_effect=AssertionError('duplicate history read'))
        self.assertEqual(self.service.snapshot(1,1,[])['remaining'],'100')
    def test_airwallex_history_and_customer_reads_overlap(self):
        import threading
        started=threading.Event();customer=threading.Event()
        original=self.service.recipient_details
        def transfers():
            started.set()
            if not customer.wait(3):raise AssertionError('Customer read did not overlap history')
            return []
        def recipient(*args):
            if not started.wait(3):raise AssertionError('History read did not start')
            customer.set();return original(*args)
        self.service.transfers=transfers;self.service.recipient_details=recipient
        self.assertEqual(self.service.snapshot(1,1)['remaining'],'100')

class StatusAndErrorsTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.WorkflowTests();self.fixture.setUp();self.service=self.fixture.service
    def test_known_transfer_status_uses_direct_lookup(self):
        review=self.service.prepare(self.fixture.form())
        row=self.service.submit(review['review_token'])
        self.service.call.reset_mock();self.service.call.side_effect=None
        self.service.call.return_value={'request_id':row['request_id'],'id':'transfer-1','transfer_amount':100,'transfer_currency':'CAD','source_currency':'CAD','status':'PAID'}
        self.assertEqual(self.service.refresh(row['request_id'])['status'],'PAID')
        self.service.call.assert_called_once_with('GET','/api/v1/transfers/transfer-1')
    def test_odoo_fault_and_timeout_never_expose_technical_details(self):
        import xmlrpc.client
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI();app.include_router(self.service.router());client=TestClient(app)
        for exception,expected in [(xmlrpc.client.Fault(1,'private traceback'),'Odoo could not verify'),(TimeoutError('private host'),'took too long')]:
            self.service.snapshot.side_effect=exception
            response=client.get('/api/airwallex/refunds/orders/1/2',headers={'x-admin-token':'test-staff'})
            self.assertIn(expected,response.json()['detail']);self.assertNotIn('private',response.text)
