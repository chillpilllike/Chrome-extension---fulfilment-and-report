import hashlib
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from app.services.refund_emails import RefundEmails, masked_destination
from app.services.after_order import EmailRejected

class RefundEmailTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE airwallex_refund_payouts(request_id TEXT PRIMARY KEY,store_id INTEGER,order_id INTEGER,order_name TEXT,status TEXT,amount TEXT,currency TEXT,transfer_id TEXT,email_destination TEXT,created_at TEXT);
        CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,case_key TEXT UNIQUE,store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,case_type TEXT,status TEXT,severity TEXT,title TEXT,customer_email TEXT,sender_domain TEXT,context_json TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT UNIQUE,payload_json TEXT,created_at TEXT,updated_at TEXT,test_mode INTEGER,template_kind TEXT,attempt_count INTEGER,provider_message_id TEXT,last_error TEXT);
        CREATE TABLE after_order_email_attempts(id INTEGER PRIMARY KEY,message_id INTEGER,attempt_number INTEGER,status TEXT,created_at TEXT,updated_at TEXT,provider_message_id TEXT,error TEXT);
        ''')
        self.site_id=22
        self.client=Mock();self.client.read.side_effect=self.read
        self.service=RefundEmails(db=self.db,get_store=lambda id:SimpleNamespace(website_id=None),client_factory=lambda store:self.client,test_mode=lambda:False,suppressed=lambda *a:False)
        self.service.refunds=Mock();self.service.init_db()
        self.conn.execute("INSERT INTO airwallex_refund_payouts VALUES('r1',1,20,'NC20','PAID','12.34','EUR','transfer1',?,'2026-09-20T12:00:00+00:00')",(json.dumps({'account':'Account ending 1234','holder':'Customer'}),))
        self.provider=Mock();self.provider.send.return_value={'id':'email1'}
    def read(self,model,ids,fields):
        return {'sale.order':[{'name':'NC20','website_id':[self.site_id,'Actual site'],'partner_id':[7,'Customer'],'state':'cancel','date_order':'2026-09-18 12:35:04'}],
                'website':[{'name':'Actual site','domain':'https://actual.example.com'}],
                'res.partner':[{'email':'customer@example.com'}]}[model]
    @contextmanager
    def db(self):
        conn=self.conn
        class Adapter:
            def execute(self,sql,params=()):
                if 'pg_try_advisory_xact_lock' in sql:return conn.execute('SELECT 1 AS locked')
                return conn.execute(sql.replace(' FOR UPDATE',''),params)
        yield Adapter();conn.commit()
    def enqueue(self,status='PAID',enabled=1):
        with self.db() as c:self.service.enqueue(c,{'request_id':'r1','notify_customer':enabled},{'status':status})
    def job(self):return dict(self.conn.execute('SELECT * FROM airwallex_refund_emails').fetchone())
    def cycle(self):
        with patch('app.services.refund_emails.create_email_provider',return_value=self.provider):self.service.cycle()
    def test_only_new_successful_app_refunds_are_queued_once(self):
        for status in ['PROCESSING','FAILED','UNKNOWN','CANCELLED']:self.enqueue(status)
        self.enqueue(enabled=0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM airwallex_refund_emails').fetchone()[0],0)
        self.enqueue();self.enqueue();self.cycle();self.cycle()
        self.assertEqual(self.provider.send.call_count,1)
        self.assertEqual(self.job()['state'],'sent')
    def test_actual_multisite_sender_and_log_allow_cancelled_order(self):
        self.enqueue();self.cycle()
        msg=dict(self.conn.execute('SELECT * FROM after_order_messages').fetchone())
        case=dict(self.conn.execute('SELECT * FROM after_order_cases').fetchone())
        self.assertEqual(case['website_id'],22)
        self.assertEqual(msg['sender'],'Actual site <notifications@actual.example.com>')
        self.assertEqual(msg['recipient'],'customer@example.com')
        self.assertIn('12.34 EUR',msg['html_preview']);self.assertIn('24–72 business hours',msg['html_preview']);self.assertNotIn('after the successful refund',msg['html_preview'])
        self.assertIn('Account ending 1234',msg['html_preview'])
    def test_timeout_retry_uses_identical_payload_and_key(self):
        self.provider.send.side_effect=[RuntimeError('timeout'),{'id':'email1'}]
        self.enqueue();self.cycle();self.cycle();self.cycle()
        self.assertEqual(self.provider.send.call_count,2)
        self.assertEqual(self.provider.send.call_args_list[0],self.provider.send.call_args_list[1])
    def test_changed_website_blocks_retry(self):
        self.provider.send.side_effect=RuntimeError('timeout')
        self.enqueue();self.cycle();self.site_id=33;self.cycle()
        self.assertEqual(self.provider.send.call_count,1);self.assertEqual(self.job()['state'],'held')
    def test_no_email_if_refund_not_paid_or_test_mode(self):
        self.enqueue();self.conn.execute("UPDATE airwallex_refund_payouts SET status='FAILED'");self.cycle()
        self.provider.send.assert_not_called()
        self.service.test_mode=lambda:True;self.cycle();self.provider.send.assert_not_called()
    def test_failure_does_not_auto_resend(self):
        self.provider.send.side_effect=EmailRejected('rejected');self.enqueue();self.cycle();self.cycle()
        self.assertEqual(self.provider.send.call_count,1);self.assertEqual(self.job()['state'],'failed')
    def test_expired_uncertain_send_not_retried(self):
        self.provider.send.side_effect=RuntimeError('timeout');self.enqueue();self.cycle()
        self.conn.execute('UPDATE airwallex_refund_emails SET attempted_at=?',((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),))
        self.cycle();self.assertEqual(self.provider.send.call_count,1)
    def test_confirmed_failure_retry_requires_approval_digest(self):
        self.provider.send.side_effect=[EmailRejected('rejected'),{'id':'email1'}]
        self.enqueue();self.cycle()
        message=dict(self.conn.execute('SELECT * FROM after_order_messages').fetchone())
        with self.assertRaises(ValueError):self.service.approve_retry(message,'wrong')
        self.service.approve_retry(message,hashlib.sha256(message['payload_json'].encode()).hexdigest())
        self.cycle();self.assertEqual(self.provider.send.call_count,2)
    def test_registered_website_connection_owns_the_log(self):
        self.service.get_store=lambda id:SimpleNamespace(website_id=None,odoo_db='db',odoo_url='https://odoo.example.com')
        self.service.list_stores=lambda:[{'id':9,'active':1,'website_id':22,'odoo_db':'db','odoo_url':'https://odoo.example.com'}]
        self.enqueue();self.cycle()
        self.assertEqual(self.conn.execute('SELECT store_id FROM after_order_cases').fetchone()[0],9)
    def test_masking_does_not_persist_full_bank_account(self):
        result=masked_destination({'beneficiary':{'bank_details':{'iban':'LT123456789012345678','account_name':'A'}}})
        self.assertEqual(result['account'],'Account ending 5678')
        self.assertNotIn('LT123456789012345678',json.dumps(result))

    def test_cancelled_queue_snapshot_cannot_send(self):
        self.enqueue();stale=self.job()
        self.conn.execute("UPDATE airwallex_refund_emails SET state='cancelled'")
        with patch('app.services.refund_emails.create_email_provider',return_value=self.provider):
            self.service.process(stale)
        self.provider.send.assert_not_called()

    def test_rollout_cutoff_uses_refund_date_not_order_date(self):
        self.service.cutoff_date=lambda:'2026-09-20'
        self.enqueue();self.cycle()
        self.assertEqual(self.provider.send.call_count,1)
        self.assertEqual(self.job()['state'],'sent')

    def test_refund_before_rollout_is_held(self):
        self.service.cutoff_date=lambda:'2026-09-20'
        self.conn.execute("UPDATE airwallex_refund_payouts SET created_at='2026-09-18T12:00:00+00:00'")
        self.enqueue();self.cycle()
        self.provider.send.assert_not_called()
        self.assertEqual(self.job()['state'],'held')
