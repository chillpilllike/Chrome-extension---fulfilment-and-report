import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
pkg=types.ModuleType('relay_bridge_test');pkg.__path__=[str(ROOT/'app/services')];sys.modules['relay_bridge_test']=pkg
from relay_bridge_test.relay_policy import match_capture, parse_receipt, payment_key
from relay_bridge_test.relay_payments import RelayPayments, DEFAULTS

SNAP={'request_id':'req-1','transaction_id':8,'order_number':'NC-22','invoice_number':'NC-22-R1','customer_name':'Amit Soni [Odoo-123-4-USD]','customer_email':'billing@example.test','amount_cents':2607,'state':'pending','initiated_at':'','source_hash':'frozen','website_id':1,'database_uuid':'db1','qbo_invoice_id':'qbo3','website_url':'https://shop.example.test','website_name':'Shop','original_total':'36.23','original_currency':'AUD','due_date':'2026-09-19','subtotal':'32.23','tax':'4.00','items':[{'name':'Product','quantity':1,'unit_price':'32.23','discount':0,'subtotal':'32.23','total':'36.23'}]}
CAPTURE={k:SNAP[k] for k in ('order_number','invoice_number','customer_name','customer_email','amount_cents')}
CAPTURE.update(currency='USD',relay_invoice_id='relay-id-123',payment_link='https://relay.cash/pay/test-token')

class PolicyTests(unittest.TestCase):
 def test_exact_match(self): self.assertTrue(match_capture(SNAP,CAPTURE))
 def test_every_identity_mismatch_blocks(self):
  for key in ('order_number','invoice_number','customer_name','customer_email','amount_cents','currency'):
   value=999 if key=='amount_cents' else 'other'
   with self.subTest(key=key), self.assertRaises(ValueError):match_capture(SNAP,{**CAPTURE,key:value})
 def test_no_fuzzy_or_missing_customer(self):
  for email in ('','billing+other@example.test'):
   with self.assertRaises(ValueError):match_capture(SNAP,{**CAPTURE,'customer_email':email})
 def test_paid_request_blocks(self):
  with self.assertRaises(ValueError):match_capture({**SNAP,'initiated_at':'now'},CAPTURE)
 def test_url_hosts(self):
  for url in ('https://relay.cash.evil/pay/a','http://relay.cash/pay/a','https://relay.cash/pay/a?target=b','https://relay.cash@evil/pay/a'):
   self.assertEqual('',payment_key(url))
 def message(self):
  return {'from':'am-it@outlook.com','to':['relay-payments@taloofalut.resend.app'],'headers':{'authentication-results':'receiver.test; dmarc=pass header.from=outlook.com;'},'subject':'Fwd: Payment is on the way for your invoice!', 'text':'Customer initiated a payment for your invoice. $26.07 is being processed. https://relay.cash/pay/test-token/receipt This payment request was created using Relay Financial. Have a question or need help? Footer insurance $3,000,000.00 and $250,000.00'}
 def test_verified_notice(self):
  result=parse_receipt(self.message(),{**DEFAULTS,'authserv_ids':'receiver.test'})
  self.assertEqual(2607,result['amount_cents']);self.assertEqual('test-token',result['payment_key'])
 def test_quoted_authentication_not_trusted(self):
  msg=self.message();msg['text']+=' From: am-it@outlook.com Authentication-Results: receiver.test; dmarc=pass header.from=outlook.com';msg['headers']={}
  with self.assertRaises(ValueError):parse_receipt(msg,{**DEFAULTS,'authserv_ids':'receiver.test'})
 def test_unconfigured_gateway_and_wrong_sender_block(self):
  for patch,settings in (({},DEFAULTS),({'from':'attacker@example.test'},{**DEFAULTS,'authserv_ids':'receiver.test'}),({'headers':{'authentication-results':'receiver.test; dmarc=fail header.from=outlook.com'}},{**DEFAULTS,'authserv_ids':'receiver.test'})):
   with self.assertRaises(ValueError):parse_receipt({**self.message(),**patch},settings)
 def test_ambiguous_amount_and_link(self):
  for extra in (' $55.00',' https://relay.cash/pay/other'):
   with self.assertRaises(ValueError):parse_receipt({**self.message(),'text':self.message()['text'].replace(' This payment request',extra+' This payment request')},{**DEFAULTS,'authserv_ids':'receiver.test'})

class Adapter:
 def __init__(self,c):self.c=c
 def execute(self,sql,args=()):
  return self.c.execute(sql.replace(' FOR UPDATE','').replace('INTEGER PRIMARY KEY AUTOINCREMENT','INTEGER PRIMARY KEY AUTOINCREMENT'),args)

class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
  @contextmanager
  def db():
   try:yield Adapter(self.c);self.c.commit()
   except: self.c.rollback();raise
  self.svc=RelayPayments(db=db,get_store=lambda _: {'website_id':1},client_factory=lambda _:None,get_settings=lambda:{'relay_payment_settings':json.dumps({**DEFAULTS,'store_ids':[1],'public_base_url':'https://app.example.test'})},set_settings=lambda _:None,staff_check=lambda _:True,email_test_mode=lambda:False)
  self.svc.ensure();self.svc.current=lambda row:dict(SNAP)
  self.svc.rpc=Mock(return_value=SNAP)
  self.c.execute('INSERT INTO relay_payments(store_id,request_id,snapshot_json,pay_token,created_at,updated_at) VALUES(1,?,?,?,?,?)',('req-1',json.dumps(SNAP),'token','now','now'));self.c.commit()
 def tearDown(self):self.c.close()
 def test_duplicate_upload_one_email_and_binding(self):
  self.svc.capture(CAPTURE);self.svc.capture(CAPTURE)
  self.assertEqual(1,self.c.execute('SELECT COUNT(*) FROM relay_email_outbox').fetchone()[0])
  self.assertEqual(CAPTURE['payment_link'],self.c.execute('SELECT payment_link FROM relay_payments').fetchone()[0])
 def test_wrong_customer_never_binds_or_queues(self):
  with self.assertRaises(ValueError):self.svc.capture({**CAPTURE,'customer_email':'wrong@example.test'})
  self.svc.rpc.assert_not_called();self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM relay_email_outbox').fetchone()[0])
 def test_current_odoo_drift_never_binds(self):
  self.svc.current=lambda row:{**SNAP,'amount_cents':2700}
  with self.assertRaises(ValueError):self.svc.capture(CAPTURE)
  self.svc.rpc.assert_not_called()
 def test_two_exact_candidates_never_guess(self):
  self.c.execute('INSERT INTO relay_payments(store_id,request_id,snapshot_json,pay_token,created_at,updated_at) VALUES(1,?,?,?,?,?)',('req-2',json.dumps(SNAP),'token2','now','now'));self.c.commit()
  with self.assertRaises(ValueError):self.svc.capture(CAPTURE)
 def test_odoo_failure_no_email(self):
  self.svc.rpc.side_effect=RuntimeError('Odoo unreachable')
  with self.assertRaises(RuntimeError):self.svc.capture(CAPTURE)
  self.assertEqual(0,self.c.execute('SELECT COUNT(*) FROM relay_email_outbox').fetchone()[0])
 def test_request_email_pay_url_and_billing_recipient(self):
  self.svc.capture(CAPTURE);row=self.c.execute('SELECT * FROM relay_payments').fetchone()
  payload=self.svc.email_payload(row,'request');self.assertEqual(['billing@example.test'],payload['to']);self.assertIn('Pay USD 26.07',payload['html']);self.assertIn('/api/relay/pay/token',payload['html']);self.assertIn('Product',payload['html'])
 def test_no_request_mail_after_payment(self):
  self.svc.capture(CAPTURE);self.svc.current=lambda row:{**SNAP,'initiated_at':'now'}
  with self.assertRaises(ValueError):self.svc.email_payload(self.c.execute('SELECT * FROM relay_payments').fetchone(),'request')
 def test_received_email_only_after_odoo_confirmation(self):
  self.svc.capture(CAPTURE)
  with self.assertRaises(ValueError):self.svc.email_payload(self.c.execute('SELECT * FROM relay_payments').fetchone(),'received')



class SyncScopeTests(WorkflowTests):
 def setUp(self):
  super().setUp()
  self.c.execute("CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,case_key TEXT UNIQUE,store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,case_type TEXT,status TEXT,severity TEXT,title TEXT,customer_email TEXT,affected_items_json TEXT,context_json TEXT,created_at TEXT,updated_at TEXT)")
 def test_real_store_object_and_single_website_inference(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=None)
  client=Mock();client.execute.side_effect=[[{'id':1}],{'records':[{**SNAP,'order_id':22}], 'more':False}]
  self.svc.client=lambda _:client
  self.svc.sync()
  self.assertEqual(('payment.transaction','relay_bridge_pending',[1,0]),client.execute.call_args.args)
 def test_multiple_websites_never_guesses(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=None)
  client=Mock();client.execute.return_value=[{'id':1},{'id':2}];self.svc.client=lambda _:client
  with self.assertRaises(ValueError):self.svc.sync()
  self.assertEqual(1,client.execute.call_count)
 def test_snapshot_wrong_website_rejected(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=1)
  client=Mock();client.execute.return_value={'records':[{**SNAP,'website_id':2}],'more':False};self.svc.client=lambda _:client
  with self.assertRaises(ValueError):self.svc.sync()

class ConfirmationTests(WorkflowTests):
 def seed_notice(self,amount=2607):
  self.c.execute('CREATE TABLE IF NOT EXISTS after_order_cases(id INTEGER PRIMARY KEY,status TEXT,updated_at TEXT)')
  self.c.execute("INSERT INTO relay_receipts(email_id,digest,status,evidence_json,created_at) VALUES('email1','digest1','pending',?,'now')",(json.dumps({'payment_key':'test-token','amount_cents':amount}),));self.c.commit()
 def test_receipt_before_capture_waits_then_confirms_once(self):
  self.seed_notice();self.svc.confirmations();self.svc.rpc.assert_not_called()
  self.svc.capture(CAPTURE);self.svc.rpc.reset_mock();self.svc.rpc.return_value={**SNAP,'initiated_at':'now'}
  self.svc.confirmations();self.svc.confirmations()
  self.assertEqual(1,self.svc.rpc.call_count)
  self.assertEqual(1,self.c.execute("SELECT COUNT(*) FROM relay_email_outbox WHERE kind='received'").fetchone()[0])
 def test_wrong_receipt_amount_does_not_confirm(self):
  self.svc.capture(CAPTURE);self.svc.rpc.reset_mock();self.seed_notice(999)
  self.svc.confirmations();self.svc.rpc.assert_not_called()
  self.assertEqual('review',self.c.execute('SELECT status FROM relay_receipts').fetchone()[0])
 def test_confirmation_transport_failure_retains_pending(self):
  self.svc.capture(CAPTURE);self.seed_notice();self.svc.rpc.side_effect=RuntimeError('lost acknowledgement')
  self.svc.confirmations();self.assertEqual('pending',self.c.execute('SELECT status FROM relay_receipts').fetchone()[0])
  self.assertEqual(0,self.c.execute("SELECT COUNT(*) FROM relay_email_outbox WHERE kind='received'").fetchone()[0])

class RouterTests(WorkflowTests):
 def test_extension_cannot_use_staff_routes(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.svc.staff_check=lambda request:False
  app=FastAPI();app.include_router(self.svc.router());client=TestClient(app)
  self.assertEqual(401,client.get('/api/relay/settings',headers={'X-Relay-Token':'anything'}).status_code)
  self.assertEqual(401,client.post('/api/relay/extension/capture',json=CAPTURE).status_code)

class EmailOutboxTests(unittest.TestCase):
 tearDown=WorkflowTests.tearDown
 def setUp(self):
  WorkflowTests.setUp(self)
  self.c.execute("CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT UNIQUE,payload_json TEXT,created_at TEXT,updated_at TEXT,test_mode INTEGER,template_kind TEXT,attempt_count INTEGER,provider_message_id TEXT,last_error TEXT)")
  self.c.execute("CREATE TABLE after_order_email_attempts(message_id INTEGER,attempt_number INTEGER,status TEXT,created_at TEXT,updated_at TEXT,provider_message_id TEXT,error TEXT)")
  self.svc.settings=lambda:{**DEFAULTS,'test_mode':False,'store_ids':[1],'public_base_url':'https://app.example.test'}
  self.svc.capture(CAPTURE)
 def test_lost_ack_retries_identical_key_and_payload(self):
  from unittest.mock import patch
  import requests
  response=Mock();response.json.return_value={'id':'resend1'}
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}), patch('relay_bridge_test.relay_payments.requests.post',side_effect=[requests.Timeout(),response]) as post:
   self.svc.emails()
   self.assertEqual('retry',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
   self.svc.emails();self.svc.emails()
   self.assertEqual(2,post.call_count)
   self.assertEqual(post.call_args_list[0],post.call_args_list[1])
  self.assertEqual('sent',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
  self.assertEqual(2,self.c.execute('SELECT COUNT(*) FROM after_order_email_attempts').fetchone()[0])
 def test_expired_idempotency_window_never_sends(self):
  from unittest.mock import patch
  self.c.execute("UPDATE relay_email_outbox SET state='retry',attempted_at='2020-01-01T00:00:00+00:00'")
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post') as post:
   self.svc.emails();post.assert_not_called()
  self.assertEqual('delivery_unknown',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
 def test_missing_key_retains_queued_email(self):
  from unittest.mock import patch
  with patch.dict('os.environ',{},clear=True),self.assertRaises(ValueError):self.svc.emails()
  self.assertEqual('queued',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])

if __name__=='__main__':unittest.main()
