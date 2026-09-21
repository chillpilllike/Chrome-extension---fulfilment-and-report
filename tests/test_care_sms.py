import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.care_sms import SMS, SCHEMA, TEST_NUMBER, Rejected, deliver, digest, number, customer_number, order_link, recipient, validate_config, validate_target, preparation_reason


class SMSTests(unittest.TestCase):
    @patch('app.services.care_sms.deliver',return_value=('provider12345678','accepted'))
    @patch('app.services.care_sms.verify_followup_template')
    def test_live_automatic_welcome_falls_back_when_translation_returns_to_pending(self, verify, send):
        self.french_msg91_fixture()
        self.ns['after_order_email_test_mode']=lambda:False
        self.ns['welcome_emails']=SimpleNamespace(validate=Mock())
        self.sms.phone=Mock(return_value='+14155552671')
        self.conn.execute("UPDATE after_order_messages SET test_mode=0,template_kind='new_order_welcome'")
        self.conn.execute("UPDATE after_order_sms_localizations SET template_kind='new_order_welcome'")
        self.settings['after_order_sms_mappings']=self.settings['after_order_sms_mappings'].replace('item_unavailable','new_order_welcome')
        row=self.sms.prepare(1)
        def approved(mapping):
            if mapping['template_id']=='french':raise ValueError('Pending again')
        verify.side_effect=approved
        self.sms.send(row['id'],automatic=True)
        self.assertEqual('english',send.call_args.args[1]['template_id'])
        self.assertEqual(1,self.row()['attempts'])
        with self.assertRaises(ValueError):self.sms.send(row['id'],automatic=True)
        self.assertEqual(1,send.call_count)

    @patch('app.services.care_sms.verify_followup_template')
    def test_wrong_brand_translation_is_not_selected(self, verify):
        self.french_msg91_fixture()
        self.conn.execute("UPDATE after_order_sms_localizations SET mapping_json=?",(json.dumps({
            'sender':'WrongBrand','template_id':'bad','text':'WrongBrand ##order## ##url## - Support'}),))
        row=self.sms.prepare(1)
        self.assertEqual('english',json.loads(row['snapshot_json'])['mapping']['template_id'])
        self.assertNotIn('WrongBrand',row['body'])

    @patch('app.services.care_sms.deliver',return_value=('provider12345678','accepted'))
    @patch('app.services.care_sms.verify_followup_template')
    def test_revoked_translation_reselects_english_and_invalidates_manual_approval(self, verify, send):
        self.french_msg91_fixture()
        row = self.sms.prepare(1)
        def approved(mapping):
            if mapping['template_id'] == 'french':
                raise ValueError('Pending again')
        verify.side_effect = approved
        with self.assertRaisesRegex(ValueError,'current SMS preview'):
            self.sms.send(row['id'],approval=digest(row))
        self.assertEqual('en_US',json.loads(self.row()['snapshot_json'])['language']['sent_language'])
        send.assert_not_called()
        self.sms.send(row['id'],approval=digest(self.row()))
        self.assertEqual('english',send.call_args.args[1]['template_id'])

    @patch('app.services.care_sms.deliver',return_value=('provider12345678','accepted'))
    @patch('app.services.care_sms.verify_followup_template')
    def test_newly_approved_translation_selected_for_automatic_test(self, verify, send):
        self.french_msg91_fixture()
        verify.side_effect=lambda m: (_ for _ in ()).throw(ValueError('Pending')) if m['template_id']=='french' else None
        row=self.sms.prepare(1)
        verify.side_effect=None
        self.sms.send(row['id'],automatic=True)
        self.assertEqual('french',send.call_args.args[1]['template_id'])
        self.assertEqual('fr_CA',json.loads(self.row()['snapshot_json'])['language']['sent_language'])

    @patch('app.services.care_sms.deliver')
    @patch('app.services.care_sms.verify_followup_template')
    def test_both_templates_pending_blocks_without_attempt(self, verify, send):
        self.french_msg91_fixture()
        row=self.sms.prepare(1)
        verify.side_effect=ValueError('Pending')
        with self.assertRaises(ValueError):self.sms.send(row['id'],automatic=True)
        self.assertEqual(0,self.row()['attempts'])
        send.assert_not_called()

    @patch('app.services.care_sms.verify_followup_template')
    def test_unknown_or_delivered_sms_never_rewritten_for_language(self, verify):
        self.french_msg91_fixture()
        row=self.sms.prepare(1)
        for status in ('delivery_unknown','accepted','sending','delivered'):
            self.conn.execute('UPDATE after_order_sms SET status=?,attempts=1',(status,))
            before=self.row()
            verify.reset_mock()
            self.sms.refresh_language(row['id'])
            self.assertEqual(before,self.row())
            verify.assert_not_called()

    def french_msg91_fixture(self):
        self.case['context']['requested_language']='fr_CA'
        self.settings['after_order_sms_provider']='msg91'
        self.settings['after_order_sms_mappings']=json.dumps({'1:2':{'transactional_sms_enabled':True,'msg91':{'sender':'nutricity','templates':{'item_unavailable':{'template_id':'english','text':'Nutricity: Order ##order## needs your choice. ##url## - Support'}}}}})
        mapping={'sender':'nutricity','template_id':'french','text':'Nutricity : commande ##order##. Choisissez : ##url## - Assistance Nutricity'}
        self.conn.execute("INSERT INTO after_order_sms_localizations VALUES('msg91','nutricity','fr','item_unavailable',?)",(json.dumps(mapping),))

    @patch('app.services.care_sms.verify_followup_template')
    def test_approved_localized_sms_uses_french_mapping(self, verify):
        self.french_msg91_fixture()
        row=self.sms.prepare(1)
        self.assertIn('commande NC123',row['body'])
        self.assertEqual('fr_CA',json.loads(row['snapshot_json'])['language']['sent_language'])
        self.assertEqual('french',json.loads(row['snapshot_json'])['mapping']['template_id'])

    @patch('app.services.care_sms.verify_followup_template',side_effect=[ValueError('Pending'),None])
    def test_pending_localized_sms_falls_back_without_changing_english_template(self, verify):
        self.french_msg91_fixture()
        row=self.sms.prepare(1)
        self.assertIn('Order NC123',row['body'])
        snapshot=json.loads(row['snapshot_json'])
        self.assertEqual('fr_CA',snapshot['language']['requested_language'])
        self.assertEqual('en_US',snapshot['language']['sent_language'])
        self.assertEqual('english',snapshot['mapping']['template_id'])
        self.assertTrue(snapshot['language']['fallback_reason'])

    @patch('app.services.care_sms.deliver')
    def test_customer_language_change_invalidates_sms_approval(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.sms.phone=Mock(return_value='+14155552671')
        row=self.sms.prepare(1)
        self.case['context']['requested_language']='fr_CA'
        with self.assertRaisesRegex(ValueError,'language changed'):
            self.sms.send(row['id'],approval=digest(row))
        send.assert_not_called()

    def test_customer_phone_uses_contact_country_not_website(self):
        self.assertEqual('+14165551234',customer_number('(416) 555-1234','CA'))
        self.assertEqual('+442079460018',customer_number('020 7946 0018','GB'))
        self.assertEqual('+442079460018',customer_number('+44 20 7946 0018','CA'))
        self.assertEqual('+14165551234',customer_number('1 416 555 1234','CA'))
        for value,region in [('4165551234',None),('123','CA'),('4165551234 ext 9','CA'),('', 'CA')]:
            with self.subTest(value=value),self.assertRaises(ValueError):customer_number(value,region)

    def test_welcome_is_eligible(self):
        self.assertEqual('',preparation_reason({'template_kind':'new_order_welcome'}))

    @patch('app.services.care_sms.deliver',side_effect=[Rejected('HTTP 403'),('provider12345678','accepted')])
    def test_failed_retry_retains_attempts(self,send):
        row=self.sms.prepare(1)
        self.sms.send(row['id'],approval=digest(self.row()))
        self.assertEqual('failed',self.row()['status'])
        self.sms.send(row['id'],approval=digest(self.row()))
        states=[r['status'] for r in self.conn.execute('SELECT status FROM after_order_sms_attempts ORDER BY attempt_number')]
        self.assertEqual(['failed','accepted'],states)

    @patch('app.services.care_sms.deliver',return_value=('provider12345678','accepted'))
    def test_explicit_resend_only_after_delivered(self,send):
        row=self.sms.prepare(1)
        self.sms.send(row['id'],approval=digest(self.row()))
        with self.assertRaises(ValueError):self.sms.send(row['id'],approval=digest(self.row()),resend=True)
        self.conn.execute("UPDATE after_order_sms SET status='delivered'")
        self.sms.send(row['id'],approval=digest(self.row()),resend=True)
        self.assertEqual(2,send.call_count)

    def test_receipts_match_recipient_and_do_not_regress(self):
        row=self.sms.prepare(1)
        self.conn.execute("UPDATE after_order_sms SET provider='msg91',provider_id='provider12345678',status='accepted',attempts=1")
        payload={'requestId':'provider12345678','telNum':TEST_NUMBER[1:],'status':'1'}
        self.sms.receipt({**payload,'telNum':'14155552671'})
        self.assertEqual('accepted',self.row()['status'])
        self.sms.receipt(payload);self.sms.receipt(payload)
        self.sms.receipt({**payload,'status':'0'})
        self.assertEqual('delivered',self.row()['status'])

    def test_webhook_requires_private_header(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app=FastAPI();app.include_router(self.sms.webhook_router())
        with patch.dict('os.environ',{'MSG91_WEBHOOK_SECRET':'x'*32}):
            client=TestClient(app)
            self.assertEqual(401,client.post('/api/public/after-order-webhooks/msg91',json={}).status_code)
            self.assertEqual(400,client.post('/api/public/after-order-webhooks/msg91',json={},headers={'X-MSG91-Webhook-Secret':'x'*32}).status_code)

    def test_secretgreen_brand_is_not_a_credential(self):
        payload = {'enabled':False, 'provider':'msg91', 'mappings':{
            '8:1':{'transactional_sms_enabled':False,'msg91':{
                'sender':'SecretGreen','templates':{'expected_dispatch':{
                    'template_id':'example','text':'SecretGreen: Order ##order##. ##url##'}}}}}}
        self.assertIn('SecretGreen', validate_config(payload))
        for field in ('authkey', 'auth_key', 'auth_token', 'password', 'client_secret'):
            with self.subTest(field=field):
                payload['mappings']['8:1']['msg91'][field] = 'blocked'
                with self.assertRaises(ValueError):
                    validate_config(payload)
                del payload['mappings']['8:1']['msg91'][field]

    def test_many_websites_fit_without_relaxing_secret_validation(self):
        templates = {kind: {'template_id':'a'*24, 'text':'##brand##: Order ##order##. Review your order: ##url##'}
                     for kind in ('expected_dispatch','warehouse_dispatch_delay','item_unavailable','no_alternatives',
                                  'package_movement','delivery_confirmation','alternative_payment','refund_request_received','refund_completed')}
        payload = {'enabled':True,'provider':'msg91','mappings':{
            f'1:{i}':{'transactional_sms_enabled':False,'msg91':{'sender':'nutricity','templates':templates}}
            for i in range(1,81)}}
        self.assertGreater(len(validate_config(payload)),50000)
        payload['mappings']['1:1']['msg91']['authkey']='not-allowed'
        with self.assertRaises(ValueError): validate_config(payload)

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,store_id INTEGER,website_id INTEGER,tracking_code TEXT);
          CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,template_kind TEXT,status TEXT,
          test_mode INTEGER,html_preview TEXT);
          INSERT INTO after_order_cases VALUES(1,1,2,'EPG123');
          INSERT INTO after_order_messages VALUES(1,1,'item_unavailable','awaiting_approval',1,'');''')
        self.conn.executescript(SCHEMA)
        conn = self.conn
        class SQL:
            def execute(self, sql, args=()):
                return conn.execute(sql.replace(' FOR UPDATE',''), args)
        @contextmanager
        def db():
            with conn:
                yield SQL()
        self.settings = {'after_order_sms_enabled':'true','after_order_sms_provider':'odoo',
                         'after_order_sms_mappings':'{"1:2":{"transactional_sms_enabled":true}}'}
        self.case = {'id':1,'store_id':1,'website_id':2,'odoo_order_id':123,'odoo_order_name':'NC123',
                     'sender_domain':'shop.example','context':{},'status':'open'}
        self.ns = {'db':db,'get_service_settings':lambda:self.settings,'after_order_email_test_mode':lambda:True,
                   'after_order_case_by_id':lambda _:self.case,'require_after_order_case_in_scope':Mock(),
                   'hydrate_after_order_recipient_and_domain':lambda c,**kw:c,'request_fingerprint':lambda c:'fp',
                   'utc_now':lambda:datetime.now(timezone.utc).isoformat(),'record_after_order_event':Mock(),
                   'OdooClient':Mock(),'get_store':Mock(),'after_order_tracking_is_current':lambda c:True,
                   'after_order_unavailable_review':lambda c,**kw:{'blocked':False,'approved':True},
                   'alternative_workflow':SimpleNamespace(ready=lambda c:True),'delivery_checkin_case':Mock()}
        self.sms = SMS(self.ns)

    def tearDown(self):
        self.conn.close()

    def row(self):
        return dict(self.conn.execute('SELECT * FROM after_order_sms').fetchone())

    def test_numbers_require_country(self):
        self.assertEqual('+918800128087', number('+91 88001-28087'))
        for bad in ('8800128087','abc','+0123456789','+911'):
            with self.assertRaises(ValueError): number(bad)
        self.assertEqual(TEST_NUMBER,recipient('customer',True))

    def test_hard_test_guard(self):
        self.assertEqual('+19296526393', TEST_NUMBER)
        validate_target({'test_mode':1,'recipient':TEST_NUMBER},True)
        for row, mode in (({'test_mode':1,'recipient':'+14155552671'},False),
                          ({'test_mode':1,'recipient':'+918800128087'},True),
                          ({'test_mode':0,'recipient':'+14155552671'},True)):
            with self.assertRaises(ValueError): validate_target(row,mode)

    def test_links_stay_on_order_website(self):
        self.assertEqual('https://shop.example/my/orders/123',order_link('shop.example',123))
        for domain, oid in [('shop.example',None),('shop.example',0),('shop.example','123/other'),
                            ('shop.example',True),('https://shop.example',123),('shop.example@evil.test',123),
                            ('shop..example',123),('shop.example:443',123)]:
            with self.assertRaises(ValueError): order_link(domain,oid)

    def test_prepared_links_use_case_order_not_email_links(self):
        self.conn.execute('UPDATE after_order_messages SET html_preview=?',
                          ('<a href="https://backend.example/my/orders/999">wrong</a>',))
        self.assertTrue(self.sms.prepare(1)['body'].endswith('https://shop.example/my/orders/123'))

    @patch('app.services.care_sms.deliver')
    def test_old_general_order_link_cannot_be_sent(self, send):
        row=self.sms.prepare(1)
        self.conn.execute('UPDATE after_order_sms SET body=?',('Test https://shop.example/my/orders',))
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(self.row()))
        send.assert_not_called()

    def test_config_rejects_secrets_and_bad_types(self):
        for mapping in ({'1:2':{'twilio':{'auth_token':'secret'}}},{'x':{}},{'1:2':{'transactional_sms_enabled':'false'}},
                        {'1:2':{'msg91':{'templates':[]}}}):
            with self.assertRaises(ValueError): validate_config({'enabled':False,'provider':'odoo','mappings':mapping})

    @patch('app.services.care_sms.deliver',return_value=('1','queued'))
    def test_test_companion_auto_sends_once_only_to_owner(self, send):
        self.sms.phone = Mock(side_effect=AssertionError('Never read customer phone in test mode'))
        self.sms.companion(1); self.sms.companion(1)
        send.assert_called_once()
        self.assertEqual(TEST_NUMBER,send.call_args.args[0]['recipient'])
        self.assertEqual(1,self.row()['attempts'])

    @patch('app.services.care_sms.deliver')
    def test_disabled_never_prepares_or_sends(self, send):
        self.settings['after_order_sms_enabled']='false'
        self.sms.companion(1)
        self.assertIsNone(self.conn.execute('SELECT * FROM after_order_sms').fetchone())
        send.assert_not_called()

    @patch('app.services.care_sms.deliver',return_value=('1','accepted'))
    def test_live_requires_exact_preview_approval(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.sms.phone=Mock(return_value='+14155552671')
        row=self.sms.prepare(1)
        self.sms.companion(1)
        send.assert_not_called()
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval='bad')
        self.sms.send(row['id'],approval=digest(row))
        send.assert_called_once()
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))

    @patch('app.services.care_sms.deliver')
    def test_switch_to_test_blocks_queued_live(self, send):
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.sms.phone=Mock(return_value='+14155552671')
        row=self.sms.prepare(1)
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))
        send.assert_not_called()

    @patch('app.services.care_sms.deliver',side_effect=TimeoutError)
    def test_timeout_is_unknown_and_never_retried(self, send):
        self.sms.companion(1)
        row=self.row()
        self.assertEqual('delivery_unknown',row['status'])
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))
        send.assert_called_once()

    @patch('app.services.care_sms.deliver',side_effect=Rejected('Config missing'))
    def test_definite_failure_requires_fresh_manual_approval(self, send):
        initial=self.sms.prepare(1)
        self.sms.companion(1); self.sms.companion(1)
        self.assertEqual('failed',self.row()['status'])
        send.assert_called_once()
        with self.assertRaises(ValueError): self.sms.send(initial['id'],approval=digest(initial))

    @patch('app.services.care_sms.deliver')
    def test_changed_customer_decision_blocks_live(self, send):
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.ns['after_order_email_test_mode']=lambda:False
        self.sms.phone=Mock(return_value='+14155552671')
        row=self.sms.prepare(1); self.case['current_decision']='refund'
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))
        send.assert_not_called()

    def test_provider_is_pinned(self):
        row=self.sms.prepare(1)
        self.settings['after_order_sms_provider']='twilio'
        self.assertEqual('odoo',self.sms.prepare(1)['provider'])
        changed={**row,'body':'changed'}
        self.assertNotEqual(digest(row),digest(changed))

    def test_odoo_reuses_queue_uuid_without_second_send(self):
        client=Mock(); client.execute.side_effect=[[7]]
        self.assertEqual(('7','queued'),deliver({'id':1,'provider':'odoo','recipient':TEST_NUMBER,'body':'Test'}, {},client))
        client.execute.assert_called_once()

    @patch.dict('os.environ',{'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'test'})
    @patch('app.services.care_sms.requests.post')
    def test_twilio_payload(self, post):
        post.return_value.status_code=201; post.return_value.json.return_value={'sid':'SM'+'b'*32}
        result=deliver({'provider':'twilio','recipient':TEST_NUMBER,'body':'Test'},{'sender':'+14155552671'})
        self.assertEqual('accepted',result[1])
        self.assertEqual(TEST_NUMBER,post.call_args.kwargs['data']['To'])
        post.assert_called_once()

    @patch.dict('os.environ',{'MSG91_AUTH_KEY':'test'})
    @patch('app.services.care_sms.requests.post')
    def test_msg91_requires_dlt_for_india(self, post):
        with self.assertRaises(Rejected):
            deliver({'provider':'msg91','recipient':'+918800128087','body':'Test'},{'sender':'HEADER','template_id':'id'})
        post.assert_not_called()

    def test_companion_failure_does_not_escape_to_email(self):
        self.sms.prepare=Mock(side_effect=RuntimeError('failure'))
        self.ns['db']=Mock(side_effect=RuntimeError('db down'))
        self.sms.companion(1)

    @patch('app.services.care_sms.deliver',return_value=('1','accepted'))
    def test_live_welcome_auto_sends_once(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.ns['welcome_emails']=Mock()
        self.conn.execute("UPDATE after_order_messages SET test_mode=0,template_kind='new_order_welcome',status='sent'")
        self.case.update(case_type='new_order_welcome',status='resolved')
        self.sms.phone=Mock(return_value='+14155552671')
        self.sms.companion(1)
        self.sms.companion(1)
        self.ns['welcome_emails'].validate.assert_called_once_with(self.case)
        send.assert_called_once()
        self.assertEqual('accepted',self.row()['status'])
        self.assertEqual('+14155552671',self.row()['recipient'])
        self.conn.execute("INSERT INTO after_order_messages VALUES(2,1,'new_order_welcome','sent',0,'')")
        self.sms.companion(2)
        send.assert_called_once()

    def test_movement_once_per_parcel_and_mode(self):
        self.case.update(tracking_code='EPG123',context={'risk_state':'in_transit'})
        self.conn.execute("UPDATE after_order_messages SET template_kind='package_movement'")
        first=self.sms.prepare(1)
        self.assertIsNotNone(first)
        self.conn.execute("INSERT INTO after_order_messages VALUES(2,1,'tracking','awaiting_approval',1,'')")
        self.assertIsNone(self.sms.prepare(2))
        self.assertEqual(first['id'],self.sms.prepare(1)['id'])
        self.case['tracking_code']='EPG456'
        self.assertIsNotNone(self.sms.prepare(2))

    @patch('app.services.care_sms.deliver')
    def test_unconfirmed_welcome_cannot_send(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.ns['welcome_emails']=Mock()
        self.ns['welcome_emails'].validate.side_effect=ValueError('Order cancelled')
        self.conn.execute("UPDATE after_order_messages SET test_mode=0,template_kind='new_order_welcome'")
        self.sms.phone=Mock(return_value='+14155552671')
        self.sms.companion(1)
        send.assert_not_called()
        self.assertEqual(0,self.row()['attempts'])

    @patch('app.services.care_sms.deliver',return_value=('1','accepted'))
    def test_test_welcome_only_owner_and_no_duplicate_resend(self, send):
        self.conn.execute("UPDATE after_order_messages SET template_kind='new_order_welcome'")
        self.sms.companion(1)
        self.assertEqual(TEST_NUMBER,self.row()['recipient'])
        self.assertEqual('accepted',self.row()['status'])
        self.conn.execute("UPDATE after_order_sms SET status='delivered'")
        with self.assertRaises(ValueError):
            self.sms.send(self.row()['id'],approval=digest(self.row()),resend=True)
        send.assert_called_once()

    @patch('app.services.care_sms.verify_followup_template',side_effect=ValueError('Pending approval'))
    @patch('app.services.care_sms.deliver')
    def test_welcome_requires_dedicated_approved_template(self, send, verify):
        self.conn.execute("UPDATE after_order_messages SET template_kind='new_order_welcome'")
        self.settings['after_order_sms_provider']='msg91'
        self.settings['after_order_sms_mappings']=json.dumps({'1:2':{'msg91':{'sender':'nutricity','text':'wrong template'}}})
        with self.assertRaisesRegex(ValueError,'dedicated'):self.sms.prepare(1)
        verify.assert_not_called()
        self.settings['after_order_sms_mappings']=json.dumps({'1:2':{'msg91':{'sender':'nutricity','templates':{'new_order_welcome':{'template_id':'pending','text':'Order ##order## ##url##'}}}}})
        self.sms.companion(1)
        verify.assert_called_once()
        send.assert_not_called()

    def test_pending_welcomes_no_historical_or_test_replay(self):
        self.conn.execute('ALTER TABLE after_order_messages ADD COLUMN created_at TEXT')
        now=datetime.now(timezone.utc).isoformat()
        self.settings['after_order_welcome_sms_started_at']=now
        self.ns['after_order_email_test_mode']=lambda:False
        self.conn.execute("UPDATE after_order_messages SET template_kind='new_order_welcome',test_mode=0,created_at=?",(now,))
        self.conn.execute("INSERT INTO after_order_messages VALUES(2,1,'new_order_welcome','sent',0,'','2020-01-01')")
        self.conn.execute("INSERT INTO after_order_messages VALUES(3,1,'new_order_welcome','sent',1,'',?)",(now,))
        self.sms.companion=Mock()
        self.sms.pending_welcomes()
        self.sms.companion.assert_called_once_with(1)
        self.sms.companion.reset_mock()
        self.ns['after_order_email_test_mode']=lambda:True
        self.sms.pending_welcomes()
        self.sms.companion.assert_not_called()

    @patch('app.services.care_sms.deliver')
    def test_previously_queued_welcome_is_blocked(self, send):
        row=self.sms.prepare(1)
        self.conn.execute("UPDATE after_order_messages SET template_kind='new_order_welcome'")
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))
        send.assert_not_called()

    def test_reminder_and_lost_sms_excluded(self):
        from app.services.care_sms import eligible
        self.assertFalse(eligible({'template_kind':'package_lost'}))
        self.assertFalse(eligible({'template_kind':'item_unavailable','payload_json':'{"_care_reminder_parent":1}'}))
        self.assertTrue(eligible({'template_kind':'refund_completed'}))

    @patch('app.services.care_sms.deliver')
    def test_expired_sourcing_approval_blocks_send(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.ns['after_order_unavailable_review']=lambda c,**kw:{'blocked':False,'approved':False}
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.sms.phone=Mock(return_value='+14155552671')
        row=self.sms.prepare(1)
        with self.assertRaises(ValueError): self.sms.send(row['id'],approval=digest(row))
        send.assert_not_called()

    @patch.dict('os.environ',{'TWILIO_ACCOUNT_SID':'AC'+'a'*32,'TWILIO_AUTH_TOKEN':'test'})
    @patch('app.services.care_sms.requests.get')
    def test_delivery_refresh_does_not_resend(self, get):
        self.sms.prepare(1)
        self.conn.execute("UPDATE after_order_sms SET provider='twilio',provider_id=?,status='accepted'",('SM'+'b'*32,))
        get.return_value.json.return_value={'status':'delivered'}
        self.assertEqual('delivered',self.sms.refresh(1)['status'])
        self.assertEqual(0,self.row()['attempts'])

    @patch.dict('os.environ',{'MSG91_AUTH_KEY':'test'})
    @patch('app.services.care_sms.requests.post')
    def test_msg91_payload_and_acceptance(self, post):
        post.return_value.status_code=200
        post.return_value.json.return_value={'type':'success','message':'request-id'}
        result=deliver({'provider':'msg91','recipient':TEST_NUMBER,'body':'Test'},
                       {'sender':'HEADER','template_id':'approved','dlt_template_id':'123','variables':{'order':'NC123'}})
        self.assertEqual(('request-id','accepted'),result)
        self.assertEqual('19296526393',post.call_args.kwargs['json']['recipients'][0]['mobiles'])


if __name__ == '__main__':
    unittest.main()
