import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.care_sms import SMS, SCHEMA, TEST_NUMBER, Rejected, deliver, digest, number, order_link, recipient, validate_config, validate_target


class SMSTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY);
          CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,template_kind TEXT,status TEXT,
          test_mode INTEGER,html_preview TEXT);
          INSERT INTO after_order_cases VALUES(1);
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
        for row, mode in (({'test_mode':1,'recipient':'+14155552671'},False),
                          ({'test_mode':0,'recipient':'+14155552671'},True)):
            with self.assertRaises(ValueError): validate_target(row,mode)

    def test_links_stay_on_order_website(self):
        markup = '<a href="https://backend.example/my/orders/1">bad</a><a href="https://shop.example/my/orders/123?x=y">good</a>'
        self.assertEqual('https://shop.example/my/orders/123?x=y',order_link(markup,'shop.example'))
        self.assertEqual('https://shop.example/my/orders',order_link('<a href="https://shop.example.evil/my/orders/1">x</a>','shop.example'))

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
            deliver({'provider':'msg91','recipient':TEST_NUMBER,'body':'Test'},{'sender':'HEADER','template_id':'id'})
        post.assert_not_called()

    def test_companion_failure_does_not_escape_to_email(self):
        self.sms.prepare=Mock(side_effect=RuntimeError('failure'))
        self.ns['db']=Mock(side_effect=RuntimeError('db down'))
        self.sms.companion(1)

    @patch('app.services.care_sms.deliver',return_value=('1','accepted'))
    def test_live_welcome_validates_order_and_can_use_resolved_welcome_case(self, send):
        self.ns['after_order_email_test_mode']=lambda:False
        self.ns['welcome_emails']=Mock()
        self.conn.execute("UPDATE after_order_messages SET test_mode=0,template_kind='new_order_welcome',status='sent'")
        self.case.update(case_type='new_order_welcome',status='resolved')
        self.sms.phone=Mock(return_value='+14155552671')
        self.sms.companion(1)
        self.ns['welcome_emails'].validate.assert_called_once()
        send.assert_called_once()

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
        self.assertEqual('918800128087',post.call_args.kwargs['json']['recipients'][0]['mobiles'])


if __name__ == '__main__':
    unittest.main()
