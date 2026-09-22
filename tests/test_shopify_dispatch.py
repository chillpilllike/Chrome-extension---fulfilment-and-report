import copy
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app.services.shopify_dispatch import evidence,carrier_url,case_key,sms_link,Monitor,SETTING,ORDERS_QUERY
from app.services.after_order_email import render_after_order_email
from app.services.after_order import request_fingerprint
from app.services.welcome_email import permitted
from app.services.notification_i18n import catalog
from tests import test_care_sms as sms_tests


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(timezone.utc)
        self.started=(self.now-timedelta(minutes=10)).isoformat()
        self.node={'id':'gid://shopify/Fulfillment/8','status':'SUCCESS','createdAt':self.now.isoformat(),
                   'order':{'id':'gid://shopify/Order/9','cancelledAt':None,'tags':['SRC_ODOO_DB:test','SRC_ODOO_ORDER:NC9']},
                   'trackingInfo':[{'number':'TRACK123','company':'Carrier','url':'https://track.example/parcel/TRACK123'}]}
        self.source={'src_shop':'dtc.myshopify.com','dest_name':'DTC','src_order_id':'gid://shopify/Order/9','src_fulfillment_id':self.node['id']}

    def case(self):
        return {'id':1,'case_key':case_key(self.source),'case_type':'shopify_dispatch','tracking_provider':'shopify_dtc',
                'store_id':1,'website_id':2,'odoo_order_id':99,'odoo_order_name':'NC9','sender_domain':'shop.example',
                'tracking_code':'TRACK123','context':{'website_name':'Store','dispatch_source':self.source,
                  'dispatch_parcels':evidence(self.node,9,self.started),'dispatch_created_at':self.now.isoformat(),
                  'tracking_url':'https://shop.example/my/orders/99'}}

    def test_success_and_split_tracking(self):
        self.node['trackingInfo'].append({'number':'TRACK456','url':'https://track.example/parcel/TRACK456'})
        self.assertEqual(2,len(evidence(self.node,9,self.started)))
        self.assertNotIn('risk_state',self.case()['context'])

    def test_cancel_pending_old_future_wrong_order(self):
        for change in [{'status':'CANCELLED'},{'status':'PENDING'}, {'trackingInfo':[]},
                       {'createdAt':(self.now-timedelta(days=1)).isoformat()},
                       {'createdAt':(self.now+timedelta(days=1)).isoformat()},
                       {'order':{'id':'gid://shopify/Order/9','cancelledAt':'2026-09-22'}},
                       {'order':{'id':'gid://shopify/Order/10','cancelledAt':None}}]:
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                evidence({**self.node,**change},9,self.started)
        with self.assertRaises(ValueError):evidence(self.node,9,None)

    def test_no_admin_or_unsafe_urls(self):
        for url in ['http://track.example/1','https://admin.shopify.com/1','https://backend.shop.example/1',
                    'https://fulfilment.gofinch.com/x','https://localhost/1','https://127.0.0.1/x',
                    'https://192.168.1.1/x','https://shop.example/web#id=1','https://x:y@track.example/x',
                    'javascript:alert(1)','https://track.example:8080/x','https://dtc.myshopify.com/admin',
                    'https://track.example/hello world']:
            with self.subTest(url=url),self.assertRaises(ValueError):carrier_url(url)

    def test_tracking_changes_invalidate_saved_fingerprint(self):
        case=self.case(); modified=copy.deepcopy(case)
        modified['context']['dispatch_parcels'][0]['url']='https://track.example/changed'
        self.assertNotEqual(request_fingerprint(case),request_fingerprint(modified))

    def test_one_identity_per_fulfilment_not_per_retry(self):
        self.assertEqual(case_key(self.source),case_key({**self.source,'src_fulfillment_id':'8'}))
        self.assertNotEqual(case_key(self.source),case_key({**self.source,'src_fulfillment_id':'10'}))
        self.assertNotEqual(case_key(self.source),case_key({**self.source,'src_shop':'other.myshopify.com'}))

    def test_email_code_links_brand_and_french(self):
        case=self.case(); case['context']['requested_language']='fr_CA'
        subject,html,plain=render_after_order_email(case,'',actions=[],labels={})
        for value in ['NC9','TRACK123','https://track.example/parcel/TRACK123','https://shop.example/my/orders/99','Store']:
            self.assertIn(value,html+plain)
        self.assertIn('NC9',subject)
        self.assertNotIn('Your package has moved',subject)
        self.assertNotIn('admin.shopify',html)
        self.assertNotIn('fulfilment.gofinch.com',html)
        self.assertNotIn('Amazon',plain)
        self.assertNotIn('Follow your delivery',subject)

    def test_existing_translated_copy_has_all_keys(self):
        keys=['DISPATCH UPDATE','Follow your delivery','There’s a new update on your package. You can find the latest details below.',
              'See the full tracking history and the latest carrier updates.','Date and time','Track all details','View your order']
        for key in keys:self.assertIn(key,catalog('en_US')['messages'])

    def test_automatic_exception_stays_narrow(self):
        row={'provider':'resend','status':'awaiting_approval','attempt_count':0,'test_mode':0,
             'template_kind':'shopify_dispatch','payload_json':'{}'}
        self.assertTrue(permitted(row,test_mode=False))
        self.assertFalse(permitted(row,test_mode=True))
        self.assertFalse(permitted({**row,'attempt_count':1},test_mode=False))
        for kind in ['tracking','item_unavailable','alternative_payment']:
            self.assertFalse(permitted({**row,'template_kind':kind},test_mode=False))

    def test_validator_rechecks_original_mapping_and_tracking(self):
        sql=Mock();sql.execute.return_value.fetchone.return_value={'odoo_db':'test'}
        @contextmanager
        def db():yield sql
        client=Mock();client.graphql.return_value={'node':self.node}
        m=Monitor({'db':db,'get_service_settings':lambda:{SETTING:self.started}})
        self.assertTrue(m.validate(self.case(),{'dtc.myshopify.com':client}))
        client.graphql.return_value={'node':{**self.node,'status':'CANCELLED'}}
        with self.assertRaises(ValueError):m.validate(self.case(),{'dtc.myshopify.com':client})
        client.graphql.return_value={'node':self.node}
        sql.execute.return_value.fetchone.return_value=None
        with self.assertRaises(ValueError):m.validate(self.case(),{'dtc.myshopify.com':client})


class DispatchSMSTests(unittest.TestCase):
    setUp=sms_tests.SMSTests.setUp
    tearDown=sms_tests.SMSTests.tearDown
    row=sms_tests.SMSTests.row
    french_msg91_fixture=sms_tests.SMSTests.french_msg91_fixture

    def fixture(self):
        self.french_msg91_fixture()
        self.case['case_type']='shopify_dispatch';self.case['status']='resolved'
        self.case['context']['dispatch_parcels']=[{'number':'TRACK123','url':'https://track.example/TRACK123'}]
        self.ns['shopify_dispatch']=SimpleNamespace(validate=Mock(return_value=True))
        self.ns['after_order_tracking_updates_opted_out']=lambda *args:False
        self.settings['after_order_sms_mappings']=self.settings['after_order_sms_mappings'].replace('item_unavailable','package_movement')
        self.conn.execute("UPDATE after_order_messages SET template_kind='shopify_dispatch'")
        self.conn.execute("UPDATE after_order_sms_localizations SET template_kind='package_movement'")

    @patch('app.services.care_sms.deliver',return_value=('msg-id','accepted'))
    @patch('app.services.care_sms.verify_followup_template')
    def test_dispatch_uses_existing_translation_and_test_number(self, verify, deliver):
        self.fixture()
        row=self.sms.prepare(1)
        self.assertEqual('+19296526393',row['recipient'])
        self.assertIn('TRACK123 https://track.example/TRACK123',row['body'])
        self.assertEqual('french',json.loads(row['snapshot_json'])['mapping']['template_id'])
        self.sms.send(row['id'],automatic=True)
        with self.assertRaises(ValueError):self.sms.send(row['id'],automatic=True)
        self.assertEqual(1,deliver.call_count)

    @patch('app.services.care_sms.deliver',return_value=('msg-id','accepted'))
    @patch('app.services.care_sms.verify_followup_template')
    def test_live_resolved_dispatch_is_allowed_and_pending_translation_falls_back(self,verify,deliver):
        self.fixture();self.ns['after_order_email_test_mode']=lambda:False
        self.conn.execute('UPDATE after_order_messages SET test_mode=0')
        self.sms.phone=Mock(return_value='+14155552671')
        def approve(mapping):
            if mapping['template_id']=='french':raise ValueError('Pending')
        verify.side_effect=approve
        row=self.sms.prepare(1)
        self.assertEqual('english',json.loads(row['snapshot_json'])['mapping']['template_id'])
        self.sms.send(row['id'],automatic=True)
        self.assertTrue(self.ns['shopify_dispatch'].validate.called)
        self.assertEqual(1,deliver.call_count)

    @patch('app.services.care_sms.deliver')
    @patch('app.services.care_sms.verify_followup_template')
    def test_changed_tracking_cannot_send_old_preview(self,verify,deliver):
        self.fixture();self.case['context']['requested_language']='en_US'
        row=self.sms.prepare(1)
        self.case['context']['dispatch_parcels'][0]['url']='https://track.example/changed'
        with self.assertRaises(ValueError):self.sms.send(row['id'],automatic=True)
        deliver.assert_not_called()


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''
          CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT);
          CREATE TABLE stores(id INTEGER,odoo_db TEXT,active INTEGER);
          INSERT INTO stores VALUES(1,'test',1);
          CREATE TABLE order_lines(store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT);
          INSERT INTO order_lines VALUES(1,99,'NC9');
          CREATE TABLE shopify_export_order_map(state_scope TEXT,dest_name TEXT,src_order_key TEXT,dest_order_id TEXT);
          INSERT INTO shopify_export_order_map VALUES('dtc','DTC','test:NC9','9');
          CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY AUTOINCREMENT,case_key TEXT UNIQUE,
            store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,case_type TEXT,status TEXT,severity TEXT,
            title TEXT,tracking_provider TEXT,tracking_code TEXT,affected_items_json TEXT,context_json TEXT,
            created_at TEXT,updated_at TEXT);
          CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,template_kind TEXT,
            status TEXT,attempt_count INTEGER,created_at TEXT);
          CREATE TABLE after_order_sms(id INTEGER PRIMARY KEY,email_id INTEGER,status TEXT,attempts INTEGER);
        ''')
        fixture=DispatchTests();fixture.setUp();self.fixture=fixture
        conn=self.conn
        class DB:
            def execute(self,sql,args=()):
                if 'pg_try_advisory_xact_lock' in sql:
                    return SimpleNamespace(fetchone=lambda:{'locked':True})
                return conn.execute(sql,args)
        @contextmanager
        def db():
            with conn:yield DB()
        self.settings={SETTING:fixture.started}
        self.client=Mock(shop='dtc.myshopify.com',name='unused')
        self.client.name='DTC'
        remote={**fixture.node['order'],'fulfillments':[fixture.node]}
        self.client.graphql.return_value={'orders':{'nodes':[remote],'pageInfo':{'hasNextPage':False,'endCursor':None}}}
        def send(cid,request):
            conn.execute("INSERT INTO after_order_messages(case_id,template_kind,status,attempt_count,created_at) VALUES(?,'shopify_dispatch','sent',1,?)",(cid,datetime.now(timezone.utc).isoformat()))
            return {'status':'sent'}
        self.send=Mock(side_effect=send)
        self.m=Monitor({'db':db,'get_service_settings':lambda:self.settings,
            'shopify_clients_for_route':lambda route:[self.client],
            'utc_now':lambda:datetime.now(timezone.utc).isoformat(),
            'send_after_order_email':self.send,'record_after_order_event':Mock(),'clean_error_message':str})
        self.sms=Mock();self.retry=Mock()
        self.m.r.namespace['care_sms']=SimpleNamespace(companion=self.sms)
        self.m.r.namespace['retry_after_order_email']=self.retry

    def tearDown(self):self.conn.close()

    def test_reservation_and_repeated_scans_send_once(self):
        self.m.run_checks(None);self.m.run_checks(None)
        self.assertEqual(1,self.send.call_count)
        row=self.conn.execute('SELECT * FROM after_order_cases').fetchone()
        self.assertEqual('[]',row['affected_items_json'])  # No invented parcel-to-line mapping.
        self.assertEqual('shopify_dtc',row['tracking_provider'])
        self.assertIsNotNone(self.conn.execute("SELECT value FROM app_settings WHERE key LIKE 'shopify_dispatch_cursor:%'").fetchone())

    def test_resume_unattempted_email_and_missing_sms_only(self):
        self.m.run_checks(None)
        self.conn.execute("UPDATE after_order_messages SET status='awaiting_approval',attempt_count=0")
        self.m.resume_pending(None)
        self.sms.assert_called_once_with(1)
        self.retry.assert_called_once_with(1,None,policy_exception=True)
        self.retry.reset_mock();self.sms.reset_mock()
        self.conn.execute("UPDATE after_order_messages SET status='delivery_unknown',attempt_count=1")
        self.conn.execute("INSERT INTO after_order_sms VALUES(1,1,'delivery_unknown',1)")
        self.m.resume_pending(None)
        self.retry.assert_not_called();self.sms.assert_not_called()

    def test_missing_import_does_not_advance_cursor(self):
        self.conn.execute('DELETE FROM order_lines')
        self.m.run_checks(None)
        self.send.assert_not_called()
        self.assertIsNone(self.conn.execute("SELECT value FROM app_settings WHERE key LIKE 'shopify_dispatch_cursor:%'").fetchone())
        self.conn.execute("INSERT INTO order_lines VALUES(1,99,'NC9')")
        self.m.run_checks(None);self.assertEqual(1,self.send.call_count)

    def test_initial_activation_never_backfills(self):
        self.settings.clear();self.m.run_checks(None)
        self.send.assert_not_called();self.client.graphql.assert_not_called()
        self.assertIsNotNone(self.conn.execute('SELECT value FROM app_settings WHERE key=?',(SETTING,)).fetchone())

    def test_all_pages_processed_before_cursor_advances(self):
        final=self.client.graphql.return_value
        self.client.graphql.side_effect=[{'orders':{'nodes':[],'pageInfo':{'hasNextPage':True,'endCursor':'next'}}},final]
        self.m.run_checks(None)
        self.assertEqual(2,self.client.graphql.call_count)
        self.assertEqual('next',self.client.graphql.call_args.args[1]['after'])
        self.assertEqual(1,self.send.call_count)

    def test_cancelled_order_and_other_route_do_not_notify(self):
        remote=self.client.graphql.return_value['orders']['nodes'][0]
        remote['cancelledAt']=self.fixture.now.isoformat()
        self.m.run_checks(None);self.send.assert_not_called()
        remote['cancelledAt']=None
        self.conn.execute("UPDATE shopify_export_order_map SET state_scope='dtb'")
        self.m.run_checks(None);self.send.assert_not_called()

    def test_blocked_email_retains_case_and_retries_preparation(self):
        self.send.side_effect=ValueError('Provider configuration missing')
        self.m.run_checks(None)
        self.assertEqual('Provider configuration missing',self.m.last_error)
        self.assertIsNotNone(self.conn.execute('SELECT id FROM after_order_cases').fetchone())
        self.assertIsNone(self.conn.execute("SELECT value FROM app_settings WHERE key LIKE 'shopify_dispatch_cursor:%'").fetchone())
