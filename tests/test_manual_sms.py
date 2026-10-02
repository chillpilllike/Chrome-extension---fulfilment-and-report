import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from app.services.manual_sms import ManualSMS,SCHEMA,validate_body

class ManualSMSTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row;self.conn.executescript(SCHEMA)
        @contextmanager
        def db():
            class Adapter:
                def execute(_,sql,args=()):
                    if 'pg_advisory_xact_lock' in sql:return None
                    return self.conn.execute(sql.replace(' FOR UPDATE',''),args)
            with self.conn:yield Adapter()
        self.mode=False
        self.r=SimpleNamespace(db=db,utc_now=lambda:datetime.now(timezone.utc).isoformat(),after_order_email_test_mode=lambda:self.mode)
        self.service=ManualSMS(SimpleNamespace(r=self.r))
        self.info={'store_id':1,'website_id':2,'odoo_order_id':123,'odoo_order_name':'NC123','sender_domain':'https://shop.example',
                   'recipient':'+14155552671','customer_name':'Test Person','order_total':'30.50','currency':'USD','sender':{'sender':'+12512999993'}}
        self.service.target=Mock(return_value=self.info)
        self.payload={'request_key':'11111111-1111-1111-1111-111111111111','store_id':1,'order_id':123,'body':'Your order update.','actor':'Test Operator','consent_confirmed':True}
    def tearDown(self):self.conn.close()

    @patch('app.services.manual_sms.deliver',return_value=('SM'+'1'*32,'accepted'))
    def test_preview_does_not_send_send_is_idempotent(self,send):
        row=self.service.preview(self.payload);send.assert_not_called()
        self.assertEqual(row['id'],self.service.preview(self.payload)['id'])
        self.assertEqual('30.50',row['order_total']);self.assertEqual('Test Person',row['customer_name'])
        self.assertEqual('accepted',self.service.send(row['id'])['status'])
        self.service.send(row['id']);send.assert_called_once()
        self.assertEqual('Test Operator',self.service.view(row['id'])['actor'])

    @patch('app.services.manual_sms.deliver')
    def test_changed_contact_or_expired_preview_never_sends(self,send):
        row=self.service.preview(self.payload)
        self.service.target.return_value={**self.info,'recipient':'+14155552672'}
        with self.assertRaisesRegex(ValueError,'changed'):self.service.send(row['id'])
        self.service.target.return_value=self.info
        self.conn.execute('UPDATE manual_sms SET created_at=?',((datetime.now(timezone.utc)-timedelta(minutes=11)).isoformat(),))
        with self.assertRaisesRegex(ValueError,'expired'):self.service.send(row['id'])
        send.assert_not_called()

    @patch('app.services.manual_sms.deliver',side_effect=TimeoutError)
    def test_uncertain_send_never_retries(self,send):
        row=self.service.preview(self.payload)
        self.assertEqual('delivery_unknown',self.service.send(row['id'])['status'])
        self.service.send(row['id']);send.assert_called_once()

    @patch('app.services.manual_sms.deliver',return_value=('SM'+'1'*32,'accepted'))
    def test_duplicate_content_across_request_ids_blocked(self,send):
        row=self.service.preview(self.payload);self.service.send(row['id'])
        other=self.service.preview({**self.payload,'request_key':'22222222-2222-2222-2222-222222222222'})
        with self.assertRaisesRegex(ValueError,'identical'):self.service.send(other['id'])
        send.assert_called_once()

    @patch('app.services.manual_sms.deliver')
    def test_test_mode_blocks_at_provider_boundary(self,send):
        row=self.service.preview(self.payload);self.mode=True
        self.assertEqual('failed',self.service.send(row['id'])['status']);send.assert_not_called()

    def test_payload_validation(self):
        for body in ['',None,'x'*1001,'hello\x00']:
            with self.assertRaises(ValueError):validate_body(body)
        with self.assertRaises(ValueError):self.service.preview({**self.payload,'consent_confirmed':False})
        self.service.preview(self.payload)
        with self.assertRaisesRegex(ValueError,'another message'):self.service.preview({**self.payload,'body':'Changed'})

    def real_target(self,blocked=False):
        self.client=Mock()
        self.client.existing_fields.return_value=['name','mobile','phone','phone_blacklisted','country_id']
        def read(model,ids,fields):
            return {'sale.order':[{'name':'NC123','website_id':[2,'Shop'],'partner_id':[3,'Test'],'amount_total':30.5,'currency_id':[1,'USD']}],
                    'res.partner':[{'name':'Test Person','mobile':'+14155552671','phone_blacklisted':blocked,'country_id':False}]}[model]
        self.client.read.side_effect=read;self.client.execute.return_value=[]
        self.r.OdooClient=lambda _:self.client;self.r.get_store=lambda _:{}
        self.service.site=Mock(return_value=(self.client,{'name':'Shop','domain':'https://shop.example'},{'sender':'+12512999993'}))
        return lambda p:ManualSMS.target(self.service,p)

    def test_order_live_details_and_cross_website_guard(self):
        target=self.real_target()
        row=target({'store_id':1,'order_id':123})
        self.assertEqual('Test Person',row['customer_name']);self.assertEqual('30.5',row['order_total'])
        with self.assertRaisesRegex(ValueError,'website changed'):target({'store_id':1,'website_id':8,'order_id':123})

    def test_order_and_manual_phone_suppression(self):
        target=self.real_target(True)
        with self.assertRaisesRegex(ValueError,'opted out'):target({'store_id':1,'order_id':123})
        self.client.execute.return_value=[1]
        with self.assertRaisesRegex(ValueError,'opt-out'):target({'store_id':1,'website_id':2,'recipient':'+14155552671'})
        with self.assertRaises(ValueError):target({'store_id':1,'website_id':2,'recipient':'4155552671'})

    def test_disabled_provider_site_and_testmode(self):
        service=ManualSMS(SimpleNamespace(r=self.r,config=lambda:{'enabled':False,'provider':'twilio'}))
        with self.assertRaisesRegex(ValueError,'Enable Twilio'):service.site(1,2)
        service.sms.config=lambda:{'enabled':True,'provider':'twilio','mappings':{}}
        with self.assertRaisesRegex(ValueError,'not enabled'):service.site(1,2)
        self.mode=True
        with self.assertRaisesRegex(ValueError,'test mode'):service.site(1,2)

    @patch('app.services.manual_sms.requests.get')
    @patch.dict('os.environ',{'TWILIO_ACCOUNT_SID':'AC'+'1'*32,'TWILIO_AUTH_TOKEN':'test'})
    def test_provider_refresh_without_send(self,get):
        row=self.service.preview(self.payload)
        self.conn.execute("UPDATE manual_sms SET status='accepted',provider_id=?",('SM'+'2'*32,))
        get.return_value.json.return_value={'status':'delivered'}
        self.assertEqual('delivered',self.service.refresh(row['id'])['status'])

    def test_manual_message_is_in_unified_log_with_filters(self):
        from app.services.care_sms import SMS,SCHEMA as SMS_SCHEMA
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.conn.executescript('''CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY);
          CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_name TEXT,sender_domain TEXT);''')
        self.conn.executescript(SMS_SCHEMA)
        row=self.service.preview(self.payload)
        router=SMS({'db':self.r.db}).router()
        endpoint=next(x.endpoint for x in router.routes if x.path=='/api/after-order/sms/log')
        result=endpoint(q='NC123')
        self.assertEqual(1,result['total']);self.assertEqual(row['id'],result['rows'][0]['manual_id'])
        self.assertEqual(0,endpoint(status='delivered')['total'])
        self.assertEqual(0,endpoint(store_id=9)['total'])

if __name__=='__main__':unittest.main()
