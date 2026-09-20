import unittest
from unittest.mock import patch
import requests
from app.services.airwallex_refund_errors import readable_error, transfer_failure, RefundProviderError
from app.services.airwallex_refunds import AirwallexRefunds


class ReadableErrorsTests(unittest.TestCase):
    def test_name_mismatch_is_specific_but_invalid_name_is_not_assumed_mismatch(self):
        self.assertIn('does not match',readable_error({'code':'90301'}))
        self.assertIn('name or number',readable_error({'code':'90101'}))
        self.assertNotIn('does not match',readable_error({'code':'validation_failed','details':{'errors':[
            {'source':'beneficiary.bank_details.account_name','code':'011'}]}}))

    def test_multiple_fields_and_iban_country_explanation(self):
        result=readable_error({'code':'validation_failed','details':{'errors':[
            {'source':'beneficiary.bank_details.iban','code':'036'},
            {'source':'beneficiary.address.postcode','code':'030'},
            {'source':'beneficiary.address.street_address','code':'018','params':{'length_min':5,'length_max':200}}]}})
        self.assertIn('first two letters',result);self.assertIn('residential country unchanged',result)
        self.assertIn('Postal code:',result);self.assertIn('5 and 200 characters',result)
        self.assertNotIn('beneficiary.',result);self.assertNotIn('036',result)

    def test_upstream_values_never_echoed(self):
        payload={'code':'unknown','message':'private IBAN SECRET123 balance 998877 api-key SECRET',
                 'source':'SECRET123','details':{'errors':[{'source':'SECRET123','code':'018',
                 'params':{'length_min':'SECRET123','length_max':998877,'value':'SECRET123'}}]}}
        result=readable_error(payload,400)
        self.assertNotIn('SECRET',result);self.assertNotIn('998877',result)

    def test_malformed_payloads_are_safe(self):
        for payload in [None,[],{'details':None},{'details':{'errors':'invalid'}},{'details':{'errors':[None,5]}}]:
            self.assertTrue(readable_error(payload))

    def test_http_transport_translates_error(self):
        response=requests.Response();response.status_code=400
        response._content=b'{"code":"balance_insufficient","message":"Balance: 998877"}'
        service=AirwallexRefunds(db=None,get_store=None,client_factory=None,
            configuration=lambda:{'client_id':'test','api_key':'test','state':'enabled'},staff_check=None)
        with patch('app.services.airwallex_refunds.server_request',side_effect=requests.HTTPError(response=response)):
            with self.assertRaisesRegex(RefundProviderError,'not enough funds') as caught:
                service.call('POST','/api/v1/transfers/validate',data={})
            self.assertNotIn('998877',str(caught.exception))

    def test_failed_transfer_formats(self):
        for payload in [{'failure':{'code':'90301'}},{'failure_reason':'90301: Beneficiary name mismatch'},
                        {'failure_reason':{'code':'beneficiary_name_mismatch'}}]:
            self.assertIn('does not match',transfer_failure(payload))
        self.assertIn('transfer failed',transfer_failure({'status':'FAILED'}))
        self.assertEqual(transfer_failure({'status':'PAID'}),'')

    def test_connection_and_duplicate_errors_are_actionable(self):
        self.assertIn('administrator',readable_error({},403))
        self.assertIn('Wait a moment',readable_error({},429))
        self.assertIn('already been submitted',readable_error({'code':'duplicate_request_id'}))
