import unittest
from unittest.mock import patch
import requests
from app.services.airwallex_refunds import refund_totals, daily_refund_usage
from app.services.airwallex_refund_errors import RefundProviderError, readable_error
from tests import test_airwallex_refunds as fixtures

class SubmissionStatusTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.WorkflowTests();self.f.setUp();self.s=self.f.service
    def test_explicit_create_validation_rejection_releases_reservation_and_never_resends_token(self):
        review=self.s.prepare(self.f.form());original=self.s.call.side_effect
        def call(method,path,**kw):
            if path.endswith('/create'):raise RefundProviderError('PayID is not registered.',creation_rejected=True)
            return original(method,path,**kw)
        self.s.call.side_effect=call
        row=self.s.submit(review['review_token'])
        self.assertEqual(row['status'],'REJECTED');self.assertIn('No refund was sent',row['last_error'])
        self.assertEqual(refund_totals('NC100','CAD',[],[row])[0],0)
        self.assertEqual(daily_refund_usage([row],[])['used'],0)
        count=self.s.call.call_count
        self.assertEqual(self.s.submit(review['review_token'])['request_id'],row['request_id'])
        self.assertEqual(self.s.call.call_count,count)
    def test_fast_downstream_failure_is_returned_to_submitter(self):
        review=self.s.prepare(self.f.form());original=self.s.call.side_effect;created={}
        def call(method,path,**kw):
            if path.endswith('/create'):
                created.update(original(method,path,**kw));return created
            if path=='/api/v1/transfers/transfer-1':
                return {**created,'status':'FAILED','failure':{'code':'90301'}}
            return original(method,path,**kw)
        self.s.call.side_effect=call
        row=self.s.submit(review['review_token'])
        self.assertEqual(row['status'],'FAILED');self.assertIn('does not match',row['last_error'])
        self.assertEqual(refund_totals('NC100','CAD',[],[row])[0],100)
    def test_failed_status_read_does_not_reclassify_accepted_transfer_as_paid(self):
        review=self.s.prepare(self.f.form());original=self.s.call.side_effect
        def call(method,path,**kw):
            if path=='/api/v1/transfers/transfer-1':raise TimeoutError()
            return original(method,path,**kw)
        self.s.call.side_effect=call
        self.assertEqual(self.s.submit(review['review_token'])['status'],'PROCESSING')
    def test_only_known_http_creation_validation_error_proves_rejection(self):
        from app.services.airwallex_refunds import AirwallexRefunds
        for code,status,endpoint,expected in [('validation_failed',400,'create',True),('validation_failed',500,'create',False),('duplicate_request_id',400,'create',False),('validation_failed',400,'validate',False)]:
            r=requests.Response();r.status_code=status;r._content=('{"code":"'+code+'"}').encode()
            with self.subTest(code=code,status=status,endpoint=endpoint),patch('app.services.airwallex_refunds.server_request',side_effect=requests.HTTPError(response=r)):
                with self.assertRaises(RefundProviderError) as e:AirwallexRefunds.call(self.s,'POST','/api/v1/transfers/'+endpoint,data={})
                self.assertEqual(e.exception.creation_rejected,expected)
    def test_absent_historical_transfer_remains_reserved_with_explicit_warning(self):
        review=self.s.prepare(self.f.form());original=self.s.call.side_effect
        def call(method,path,**kw):
            if path.endswith('/create'):raise TimeoutError()
            return original(method,path,**kw)
        self.s.call.side_effect=call;row=self.s.submit(review['review_token'])
        row=self.s.refresh(row['request_id'])
        self.assertEqual(row['status'],'UNKNOWN');self.assertIn('no matching transfer',row['last_error'])
        self.assertEqual(refund_totals('NC100','CAD',[],[row])[0],100)
    def test_payid_errors_are_actionable_and_do_not_expose_code(self):
        text=readable_error({'code':'validation_failed','details':{'errors':[{'source':'beneficiary.bank_details.account_routing_value1','code':'102'}]}})
        self.assertIn('not registered',text);self.assertNotIn('102',text)
