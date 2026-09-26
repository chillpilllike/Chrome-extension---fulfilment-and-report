import json
import unittest
from pathlib import Path
from unittest.mock import Mock
from app.services.airwallex_refunds import AirwallexRefunds
from app.services.refund_destination import destination_label
from tests import test_airwallex_refunds as fixtures

SCHEMAS = json.loads((Path(__file__).parent / 'fixtures/airwallex_bank_schemas.json').read_text())

class PayoutMethodTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WorkflowTests(); self.fixture.setUp()
        self.service = self.fixture.service

    def test_payid_dependent_schema_builds_email_phone_and_business_payload_without_account_number(self):
        for identifier, value, entity in [('email_address','refund@example.com','PERSONAL'),
                                          ('phone_number','+61-412345678','PERSONAL'),
                                          ('australian_business_number','12123456789','COMPANY')]:
            with self.subTest(identifier=identifier):
                final = SCHEMAS['direct-AU-'+identifier]
                base = final if entity == 'COMPANY' else SCHEMAS['schema-NPP']
                self.service.schema = Mock(side_effect=lambda p: final if p.get('account_routing_type1') == identifier else base)
                self.fixture.snap['currency'] = 'AUD'
                self.service.check_funding = Mock(return_value={})
                form = self.fixture.form()
                form.fields = {f['path']: str(f['field'].get('example') or f['field'].get('default') or 'Test value')
                               for f in final['fields']}
                form.fields.update({'transfer_method':'LOCAL','beneficiary.type':'BANK_ACCOUNT',
                    'beneficiary.entity_type':entity, 'beneficiary.bank_details.bank_country_code':'AU',
                    'beneficiary.bank_details.account_currency':'USD',
                    'beneficiary.bank_details.local_clearing_system':'NPP',
                    'beneficiary.bank_details.account_routing_type1':identifier,
                    'beneficiary.bank_details.account_routing_value1':value,
                    'beneficiary.bank_details.account_number':'stale-account'})
                review = self.service.prepare(form)
                transfer = json.loads(self.service.cipher(self.fixture.cfg).decrypt(review['review_token'].encode()))['transfer']
                bank = transfer['beneficiary']['bank_details']
                self.assertEqual(bank['account_routing_type1'],identifier)
                self.assertEqual(bank['account_routing_value1'],value)
                self.assertEqual(bank['account_currency'],'AUD')
                self.assertNotIn('account_number',bank)
                self.assertNotIn(value,review['destination'])
                self.assertFalse(any(p.endswith('/create') for _,p,_ in self.fixture.calls))

    def test_unsupported_identifier_cannot_bypass_account_schema(self):
        self.service.schema = Mock(return_value=SCHEMAS['schema-NPP'])
        form = self.fixture.form()
        form.fields['beneficiary.bank_details.account_routing_type1']='unsupported'
        with self.assertRaisesRegex(ValueError,'supported option'):self.service.prepare(form)

    def test_schema_passes_country_specific_condition_keys_and_filters_complex_inputs(self):
        self.service.call = Mock(return_value={'fields':[]})
        AirwallexRefunds.schema(self.service,{'account_routing_type1':'email_address','beneficiary_type':'BANK_ACCOUNT',
                                             'postcode':'3000','bad.key':'x','nested':{'url':'private'}})
        params=self.service.call.call_args.kwargs['data']
        self.assertEqual(params,{'account_routing_type1':'email_address','type':'BANK_ACCOUNT','postcode':'3000'})

    def test_destination_never_uses_contact_email_as_payout_account(self):
        from app.services.refund_emails import masked_destination
        details=masked_destination({'beneficiary':{'personal_email':'contact@example.com','bank_details':{
            'local_clearing_system':'INTERAC','account_routing_type1':'phone_number','account_routing_value1':'+14165551234'}}})
        self.assertEqual(details['account'],'phone ending 1234')
        self.assertNotIn('contact',details['account'])
        self.assertEqual(destination_label({'account_number':'00123456789'}),'Account ending 6789')
