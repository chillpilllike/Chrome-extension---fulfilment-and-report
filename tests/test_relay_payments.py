import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

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
  for key in ('order_number','invoice_number','customer_email','amount_cents','currency'):
   value=999 if key=='amount_cents' else 'other'
   with self.subTest(key=key), self.assertRaises(ValueError):match_capture(SNAP,{**CAPTURE,key:value})
 def test_existing_contact_uses_email_not_name_or_phone(self):
  self.assertTrue(match_capture(SNAP,{**CAPTURE,'customer_name':'Existing Relay customer','phone':'different'}))
  with self.assertRaises(ValueError):match_capture(SNAP,{**CAPTURE,'customer_name':''})
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

class TrackingReceiptTests(PolicyTests):
 def forwarded(self):
  msg=self.message();msg['text']=msg['text'].replace('https://relay.cash/pay/test-token/receipt','')
  msg['html']='<a href="https://links.relayfi.com/s/c/sample">View details in Relay</a>'
  return msg
 def test_outlook_forwarded_tracking_receipt(self):
  resolver=Mock(return_value='https://relay.cash/pay/test-token/receipt')
  evidence=parse_receipt(self.forwarded(),{**DEFAULTS,'authserv_ids':'receiver.test'},resolver)
  self.assertEqual(2607,evidence['amount_cents']);self.assertEqual('test-token',evidence['payment_key'])
  resolver.assert_called_once_with('https://links.relayfi.com/s/c/sample')
 def test_unverified_sender_never_resolves(self):
  resolver=Mock()
  with self.assertRaises(ValueError):parse_receipt(self.forwarded(),DEFAULTS,resolver)
  resolver.assert_not_called()
 def test_multiple_receipt_buttons_are_held(self):
  msg=self.forwarded();msg['html']+='<a href="https://links.relayfi.com/s/c/other">View details in Relay</a>'
  resolver=Mock()
  with self.assertRaises(ValueError):parse_receipt(msg,{**DEFAULTS,'authserv_ids':'receiver.test'},resolver)
  resolver.assert_not_called()
 def test_tracking_redirect_never_fetches_payment_page(self):
  response=Mock(status_code=302,headers={'Location':'https://relay.cash/pay/test-token/receipt'})
  with patch('relay_bridge_test.relay_payments.requests.get',return_value=response) as get:
   self.assertEqual('https://relay.cash/pay/test-token/receipt',RelayPayments.resolve_receipt_tracking('https://links.relayfi.com/s/c/sample'))
   get.assert_called_once();self.assertFalse(get.call_args.kwargs['allow_redirects'])
 def test_tracking_redirect_private_or_other_hosts_blocked(self):
  for target in ('http://127.0.0.1/','https://evil.test/','https://links.relayfi.com.evil.test/s/c/a','https://links.relayfi.com:443/s/c/a'):
   response=Mock(status_code=302,headers={'Location':target})
   with patch('relay_bridge_test.relay_payments.requests.get',return_value=response) as get:
    with self.assertRaises(ValueError):RelayPayments.resolve_receipt_tracking('https://links.relayfi.com/s/c/sample')
    get.assert_called_once()

class Adapter:
 def __init__(self,c):self.c=c
 def execute(self,sql,args=()):
  return self.c.execute(sql.replace(' FOR UPDATE','').replace('INTEGER PRIMARY KEY AUTOINCREMENT','INTEGER PRIMARY KEY AUTOINCREMENT'),args)

class WorkflowTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:',check_same_thread=False);self.c.row_factory=sqlite3.Row
  @contextmanager
  def db():
   try:yield Adapter(self.c);self.c.commit()
   except: self.c.rollback();raise
  self.svc=RelayPayments(db=db,get_store=lambda _: {'website_id':1},client_factory=lambda _:None,get_settings=lambda:{'relay_payment_settings':json.dumps({**DEFAULTS,'store_ids':[1],'public_base_url':'https://app.example.test'})},set_settings=lambda _:None,staff_check=lambda _:True,email_test_mode=lambda:False)
  self.svc.ensure();self.svc.current=lambda row:dict(SNAP)
  self.svc.rpc=Mock(return_value=SNAP)
  self.c.execute('INSERT INTO relay_payments(store_id,request_id,snapshot_json,pay_token,created_at,updated_at) VALUES(1,?,?,?,?,?)',('req-1',json.dumps(SNAP),'token','now','now'));self.c.commit()
 def tearDown(self):self.c.close()
 def approve_alias(self):
  self.svc.approve_customer_alias(1,{**SNAP,'customer_name':'Amit','relay_invoice_id':CAPTURE['relay_invoice_id']})
 def test_approved_alias_binds_only_exact_order_invoice_email_and_amount(self):
  self.approve_alias()
  self.svc.capture({**CAPTURE,'customer_name':'A Amit'})
  self.assertEqual('ready',self.c.execute('SELECT status FROM relay_payments').fetchone()[0])
  audit=self.c.execute('SELECT * FROM relay_customer_aliases').fetchone()
  self.assertEqual('Amit',audit['customer_name']);self.assertTrue(audit['approved_at'])
 def test_alias_never_relaxes_email_amount_order_or_invoice(self):
  self.approve_alias()
  for change in ({'customer_email':'other@example.test'},{'amount_cents':1},{'order_number':'OTHER'},{'relay_invoice_id':'other-id'}):
   with self.subTest(change=change),self.assertRaises(ValueError):
    self.svc.capture({**CAPTURE,'customer_name':'Amit',**change})
  self.svc.rpc.assert_not_called()
 def test_alias_requires_current_identity_and_cannot_be_added_after_binding(self):
  with self.assertRaises(ValueError):self.svc.approve_customer_alias(1,{**SNAP,'order_number':'OTHER','customer_name':'Amit','relay_invoice_id':CAPTURE['relay_invoice_id']})
  self.svc.capture(CAPTURE)
  with self.assertRaises(ValueError):self.approve_alias()
 def test_alias_invalidated_by_current_source_change(self):
  self.approve_alias();self.svc.current=lambda row:{**SNAP,'source_hash':'changed'}
  with self.assertRaises(ValueError):self.svc.capture({**CAPTURE,'customer_name':'Amit'})
  self.svc.rpc.assert_not_called()
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
  self.svc.configure_notifications=Mock()
  self.c.execute("CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,case_key TEXT UNIQUE,store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,case_type TEXT,status TEXT,severity TEXT,title TEXT,customer_email TEXT,affected_items_json TEXT,context_json TEXT,created_at TEXT,updated_at TEXT)")
 def test_real_store_object_and_single_website_inference(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=None)
  client=Mock();client.execute.side_effect=[[{'id':1}],{'records':[{**SNAP,'request_id':'req-new','order_id':22}], 'more':False}]
  self.svc.client=lambda _:client
  self.svc.sync()
  self.assertEqual(('payment.transaction','relay_bridge_pending',[1,0]),client.execute.call_args.args)
  self.assertEqual(1,self.c.execute("SELECT COUNT(*) FROM after_order_cases WHERE case_type='relay_payment'").fetchone()[0])
 def test_multiple_websites_are_each_scoped(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=None)
  client=Mock();client.execute.side_effect=[[{'id':1},{'id':2}],{'records':[], 'more':False},{'records':[{**SNAP,'request_id':'site-two','website_id':2,'order_id':23}], 'more':False}]
  self.svc.client=lambda _:client
  self.svc.sync()
  self.assertEqual([('payment.transaction','relay_bridge_pending',[1,0]),('payment.transaction','relay_bridge_pending',[2,0])],[call.args for call in client.execute.call_args_list[1:]])
  self.assertEqual(2,self.c.execute("SELECT website_id FROM after_order_cases").fetchone()[0])
 def test_explicit_website_does_not_enumerate_other_websites(self):
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=9)
  client=Mock();client.execute.return_value={'records':[], 'more':False};self.svc.client=lambda _:client
  self.svc.sync()
  client.execute.assert_called_once_with('payment.transaction','relay_bridge_pending',[9,0])
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

 def test_connection_check_authenticates_without_enabling_or_mutating(self):
  import hashlib
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.svc.get_settings=lambda:{'relay_extension_token_hash':hashlib.sha256(b'valid-token').hexdigest()}
  self.svc.set_settings=Mock()
  app=FastAPI();app.include_router(self.svc.router());client=TestClient(app)
  self.assertEqual(401,client.post('/api/relay/extension/check',headers={'X-Relay-Token':'wrong'}).status_code)
  response=client.post('/api/relay/extension/check',headers={'X-Relay-Token':'valid-token'})
  self.assertEqual(200,response.status_code);self.assertEqual('relay-payment-bridge',response.json()['service'])
  self.assertFalse(response.json()['enabled']);self.assertTrue(response.json()['test_mode'])
  self.svc.set_settings.assert_not_called();self.svc.rpc.assert_not_called()

class ContactMiddlewareTests(WorkflowTests):
 def test_contact_uses_extension_token_through_production_middleware(self):
  import ast, hashlib, re
  from pathlib import Path
  from fastapi import FastAPI, Request, Response
  from fastapi.testclient import TestClient
  from typing import Any
  tree=ast.parse((Path(__file__).parents[1]/'app/main.py').read_text())
  fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='admin_access_middleware')
  fn.decorator_list=[]
  scope=dict(Request=Request,Response=Response,Any=Any,re=re,json=json,
             effective_admin_access_token=lambda:'staff-secret',
             request_requires_public_access=lambda request:True,
             request_has_public_access=lambda request:False)
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'production-middleware','exec'),scope)
  self.svc.get_settings=lambda:{'relay_extension_token_hash':hashlib.sha256(b'valid-token').hexdigest(),'relay_payment_settings':json.dumps({'enabled':True})}
  self.svc.contact=Mock(return_value={'ok':True})
  app=FastAPI();app.middleware('http')(scope['admin_access_middleware']);app.include_router(self.svc.router())
  client=TestClient(app)
  for headers in ({},{'X-Relay-Token':'wrong'}):
   self.assertEqual(401,client.post('/api/relay/extension/contact',json=CAPTURE,headers=headers).status_code)
  self.svc.contact.assert_not_called()
  response=client.post('/api/relay/extension/contact',json=CAPTURE,headers={'X-Relay-Token':'valid-token'})
  self.assertEqual(200,response.status_code);self.svc.contact.assert_called_once()
  self.svc.resend_api=Mock(return_value={'data':[]})
  login_body={'journey_id':'current-journey-123','email':'am-it@outlook.com'}
  self.assertEqual(401,client.post('/api/relay/extension/login-link',json=login_body).status_code)
  self.assertEqual(200,client.post('/api/relay/extension/login-link',json=login_body,headers={'X-Relay-Token':'valid-token'}).status_code)
  self.assertEqual(401,client.post('/api/relay/extension/pending').status_code)
  self.assertEqual(200,client.post('/api/relay/extension/pending',headers={'X-Relay-Token':'valid-token'}).status_code)
  self.assertEqual(401,client.get('/api/relay/extension/contact',headers={'X-Relay-Token':'valid-token'}).status_code)
  self.assertEqual(401,client.get('/api/relay/settings',headers={'X-Relay-Token':'valid-token'}).status_code)

 def test_login_lookup_uses_extension_auth_and_never_mutates_orders(self):
  import hashlib
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.svc.get_settings=lambda:{'relay_extension_token_hash':hashlib.sha256(b'valid-token').hexdigest(),'relay_payment_settings':json.dumps({'enabled':True})}
  self.svc.resend_api=Mock(return_value={'data':[]})
  app=FastAPI();app.include_router(self.svc.router());client=TestClient(app)
  body={'journey_id':'current-journey-123','email':'am-it@outlook.com'}
  self.assertEqual(401,client.post('/api/relay/extension/login-link',json=body).status_code)
  self.svc.resend_api.assert_not_called()
  response=client.post('/api/relay/extension/login-link',json=body,headers={'X-Relay-Token':'valid-token'})
  self.assertEqual(200,response.status_code);self.assertEqual({'ok':True,'link':None},response.json())
  self.assertEqual('no-store',response.headers['cache-control']);self.svc.rpc.assert_not_called()
  self.assertEqual(400,client.post('/api/relay/extension/login-link',json={**body,'email':'wrong@example.com'},headers={'X-Relay-Token':'valid-token'}).status_code)

 def test_pending_queue_auth_scope_and_completed_link_exclusion(self):
  import hashlib
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  settings={'enabled':True,'store_ids':[1]}
  self.svc.get_settings=lambda:{'relay_extension_token_hash':hashlib.sha256(b'valid-token').hexdigest(),'relay_payment_settings':json.dumps(settings)}
  app=FastAPI();app.include_router(self.svc.router());client=TestClient(app)
  path='/api/relay/extension/pending';headers={'X-Relay-Token':'valid-token'}
  self.assertEqual(401,client.post(path).status_code)
  response=client.post(path,headers=headers);self.assertEqual(200,response.status_code)
  self.assertEqual(2,response.json()['poll_seconds']);self.assertEqual('NC-22',response.json()['items'][0]['order_number'])
  self.assertNotIn('customer_email',response.json()['items'][0]);self.assertEqual('no-store',response.headers['cache-control'])
  settings['store_ids']=[2];self.assertEqual([],client.post(path,headers=headers).json()['items'])
  settings['store_ids']=[1]
  self.c.execute("UPDATE relay_payments SET payment_link='https://relay.cash/pay/test-token'");self.c.commit()
  self.assertEqual([],client.post(path,headers=headers).json()['items'])

class EmailOutboxTests(unittest.TestCase):
 tearDown=WorkflowTests.tearDown
 def setUp(self):
  WorkflowTests.setUp(self)
  self.c.execute("CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT UNIQUE,payload_json TEXT,created_at TEXT,updated_at TEXT,test_mode INTEGER,template_kind TEXT,attempt_count INTEGER,provider_message_id TEXT,last_error TEXT)")
  self.c.execute("CREATE TABLE after_order_email_attempts(message_id INTEGER,attempt_number INTEGER,status TEXT,created_at TEXT,updated_at TEXT,provider_message_id TEXT,error TEXT)")
  self.svc.settings=lambda:{**DEFAULTS,'test_mode':False,'store_ids':[1],'public_base_url':'https://app.example.test'}
  self.svc.capture(CAPTURE)
 def rejected403(self):
  import requests
  response=Mock(status_code=403)
  response.raise_for_status.side_effect=requests.HTTPError(response=response)
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post',return_value=response):
   self.svc.emails()
  return self.c.execute('SELECT id FROM after_order_messages').fetchone()[0]
 def test_403_retry_requires_current_payment_and_explicit_review(self):
  message_id=self.rejected403()
  self.assertEqual('failed',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
  job,message,payload,proof=self.svc.rejected_email(message_id)
  with self.assertRaises(ValueError):self.svc.retry_rejected_email(message_id,'wrong')
  self.svc.retry_rejected_email(message_id,proof)
  response=Mock();response.json.return_value={'id':'recovered'}
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post',return_value=response) as post:
   self.svc.emails()
   self.assertTrue(post.call_args.kwargs['headers']['Idempotency-Key'].endswith(':approved-retry:1'))
  with self.assertRaises(ValueError):self.svc.rejected_email(message_id)
 def test_403_paid_or_mixed_uncertain_attempts_block(self):
  message_id=self.rejected403()
  self.svc.current=lambda row:dict(SNAP,state='done',initiated_at='now')
  with self.assertRaises(ValueError):self.svc.rejected_email(message_id)
  self.svc.current=lambda row:dict(SNAP)
  self.c.execute("UPDATE after_order_email_attempts SET error='Timeout'")
  with self.assertRaises(ValueError):self.svc.rejected_email(message_id)
 def test_403_old_queue_reset_needs_separate_override(self):
  message_id=self.rejected403()
  self.c.execute('UPDATE after_order_messages SET payload_json=?',(json.dumps({'_care_rollout_cancelled_at':'old'}),))
  proof=self.svc.rejected_email(message_id)[3]
  with self.assertRaises(ValueError):self.svc.retry_rejected_email(message_id,proof)
  self.svc.retry_rejected_email(message_id,proof,True)
  self.assertEqual(1,self.c.execute('SELECT COUNT(*) FROM relay_email_retries').fetchone()[0])
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
 def test_relay_live_mode_is_independent_of_other_after_order_emails(self):
  from unittest.mock import patch
  self.svc.email_test_mode=lambda:True
  response=Mock();response.json.return_value={'id':'resend-relay'}
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post',return_value=response) as post:
   self.svc.emails();self.assertEqual(1,post.call_count)
  self.assertEqual('sent',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
 def test_relay_test_mode_still_holds_delivery(self):
  from unittest.mock import patch
  self.svc.settings=lambda:{**DEFAULTS,'test_mode':True}
  with patch('relay_bridge_test.relay_payments.requests.post') as post:
   self.svc.emails();post.assert_not_called()
  self.assertEqual('queued',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
 def test_expired_idempotency_window_never_sends(self):
  from unittest.mock import patch
  self.c.execute("UPDATE relay_email_outbox SET state='retry',attempted_at='2020-01-01T00:00:00+00:00'")
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post') as post:
   self.svc.emails();post.assert_not_called()
  self.assertEqual('delivery_unknown',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])
 def test_branding_follows_each_order_website_and_escapes_items(self):
  for host,site_id in [('shop.example.test',1),('second.example.test',42)]:
   self.svc.current=lambda row:dict(SNAP,website_url='https://'+host,website_id=site_id,website_name='Brand & Co',items=[{'name':'Product <special>','quantity':2.0,'total':'36.23'}])
   row=self.c.execute('SELECT * FROM relay_payments').fetchone()
   mail=self.svc.email_payload(row,'request')
   self.assertIn(f'https://{host}/web/image/website/{site_id}/logo',mail['html'])
   self.assertIn(f'https://{host}/contactus',mail['html'])
   self.assertIn('Product &lt;special&gt;',mail['html'])
   self.assertIn('Pay USD 26.07',mail['html'])
   self.assertIn('https://app.example.test/api/relay/pay/token',mail['html'])
   self.assertNotIn('choice=',mail['html'])
   self.assertEqual('support@'+host,mail['reply_to'])
   self.assertIn('max-width:600px',mail['html'])
 def test_received_email_has_branding_without_pay_button(self):
  self.svc.current=lambda row:dict(SNAP,initiated_at='2026-09-17',state='done')
  row=self.c.execute('SELECT * FROM relay_payments').fetchone()
  mail=self.svc.email_payload(row,'received')
  self.assertIn('ORDER CONFIRMED',mail['html'])
  self.assertIn('Bank settlement is still processing',mail['html'])
  self.assertNotIn('Pay USD',mail['html'])
  self.assertIn('/web/image/website/1/logo',mail['html'])
 def test_manual_branded_resend_preserves_original_and_is_idempotent(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  response=Mock();response.json.return_value={'id':'original'}
  with patch.dict('os.environ',{'RESEND_API_KEY':'fake'}),patch('relay_bridge_test.relay_payments.requests.post',return_value=response) as post:
   self.svc.emails()
   original=self.c.execute('SELECT payload_json FROM relay_email_outbox').fetchone()[0]
   app=FastAPI();app.include_router(self.svc.router());client=TestClient(app)
   for _ in range(2):self.assertEqual(200,client.post('/api/relay/payments/1/resend-branded').status_code)
   self.svc.emails();self.svc.emails()
   self.assertEqual(2,post.call_count)
   self.assertNotEqual(post.call_args_list[0].kwargs['headers']['Idempotency-Key'],post.call_args_list[1].kwargs['headers']['Idempotency-Key'])
   self.assertEqual(original,self.c.execute("SELECT payload_json FROM relay_email_outbox WHERE kind='request'").fetchone()[0])
   self.assertIn('Your invoice is ready',post.call_args.kwargs['json']['html'])
 def test_manual_branded_resend_rejects_paid_order(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.svc.current=lambda row:dict(SNAP,initiated_at='now',state='done')
  app=FastAPI();app.include_router(self.svc.router())
  self.assertEqual(409,TestClient(app).post('/api/relay/payments/1/resend-branded').status_code)
 def test_missing_key_retains_queued_email(self):
  from unittest.mock import patch
  with patch.dict('os.environ',{},clear=True),self.assertRaises(ValueError):self.svc.emails()
  self.assertEqual('queued',self.c.execute('SELECT state FROM relay_email_outbox').fetchone()[0])

if __name__=='__main__':unittest.main()

class ContactLookupTests(WorkflowTests):
 def test_contact_requires_exact_match_before_reading_partner(self):
  client=Mock();self.svc.client=lambda _:client
  with self.assertRaises(ValueError):self.svc.contact({**CAPTURE,'customer_email':'wrong@example.test'})
  client.execute.assert_not_called()
 def test_contact_reads_only_matched_order_billing_partner(self):
  self.svc.current=lambda row:{**SNAP,'order_id':123}
  client=Mock();self.svc.client=lambda _:client
  client.execute.side_effect=[[{'partner_invoice_id':[42,'Customer']}],[{'email':SNAP['customer_email'],'phone':'0479 046 169','country_id':[13,'Australia']}],[{'code':'AU','name':'Australia'}]]
  result=self.svc.contact(CAPTURE)
  self.assertEqual(result['contact']['national_number'],'479046169')
  self.assertEqual(result['invoice_number'],SNAP['invoice_number'])
  self.assertEqual(client.execute.call_args_list[0].args[2],[[123]])
  self.assertEqual(client.execute.call_args_list[1].args[2],[[42]])


class NotificationTests(WorkflowTests):
 def setUp(self):
  super().setUp()
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.key='notification-secret-for-this-test-only'
  self.values={'relay_notify_secret_1':self.key,'relay_payment_settings':json.dumps({**DEFAULTS,'enabled':True,'store_ids':[1],'public_base_url':'https://app.example.test'})}
  self.svc.get_settings=lambda:self.values
  self.svc.set_settings=lambda values:self.values.update(values)
  self.svc.get_store=lambda _:types.SimpleNamespace(website_id=1)
  self.svc.notification_sync=Mock()
  app=FastAPI();app.include_router(self.svc.router());self.http=TestClient(app)
 def notify(self,changes=None,key=None):
  import time,hmac,hashlib
  body=json.dumps({'store_id':1,'website_id':1,'timestamp':int(time.time()),**(changes or {})}).encode()
  signature=hmac.new((key or self.key).encode(),body,hashlib.sha256).hexdigest()
  return self.http.post('/api/relay/odoo/notify',content=body,headers={'X-Relay-Signature':signature})
 def test_signed_notice_imports_authenticated_odoo_scope(self):
  self.assertEqual(202,self.notify().status_code)
  self.svc.notification_sync.assert_called_once_with(1,1)
 def test_bad_signature_expired_or_wrong_website_cannot_import(self):
  self.assertEqual(401,self.notify(key='wrong').status_code)
  self.assertEqual(409,self.notify({'timestamp':1}).status_code)
  self.assertEqual(409,self.notify({'website_id':2}).status_code)
  self.svc.notification_sync.assert_not_called()
 def test_disabled_bridge_does_not_import(self):
  self.values['relay_payment_settings']=json.dumps({**DEFAULTS,'store_ids':[1]})
  self.assertEqual(409,self.notify().status_code)
  self.svc.notification_sync.assert_not_called()
 def test_configure_reuses_secret_and_old_addon_preserves_fallback(self):
  client=Mock()
  self.svc.configure_notifications(1,client,1)
  client.execute.assert_called_once_with('payment.transaction','relay_bridge_configure_notifications',[1,1,'https://app.example.test/api/relay/odoo/notify',self.key])
  client.execute.side_effect=RuntimeError('Old addon')
  self.svc.configure_notifications(1,client,1)
  self.assertIn('one-minute',self.values['relay_notify_1_error'])
 def test_completed_pending_order_is_reviewed_and_not_deleted(self):
  self.svc.current=lambda row:{**SNAP,'state':'done'}
  self.svc.review_pending()
  self.assertEqual('review',self.c.execute('SELECT status FROM relay_payments').fetchone()[0])
  self.svc.current=lambda row:dict(SNAP)
  self.svc.review_pending()
  self.assertEqual('waiting_link',self.c.execute('SELECT status FROM relay_payments').fetchone()[0])
 def test_transport_outage_does_not_deactivate_order(self):
  self.svc.current=Mock(side_effect=RuntimeError('offline'))
  self.svc.review_pending()
  self.assertEqual('waiting_link',self.c.execute('SELECT status FROM relay_payments').fetchone()[0])

 def test_odoo_snapshot_rejection_is_held_and_revalidated(self):
  from xmlrpc.client import Fault
  self.svc.current=Mock(side_effect=Fault(1,'Odoo rejected changed order'))
  self.svc.review_pending()
  row=self.c.execute('SELECT status,last_error FROM relay_payments').fetchone()
  self.assertEqual('review',row['status']);self.assertNotIn('Fault',row['last_error'])
  self.svc.current=lambda row:dict(SNAP)
  self.svc.review_pending()
  self.assertEqual('waiting_link',self.c.execute('SELECT status FROM relay_payments').fetchone()[0])

class ExtensionTokenSettingsTests(unittest.TestCase):
 def setUp(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  self.values={}
  self.svc=RelayPayments(db=None,get_store=None,client_factory=None,get_settings=lambda:self.values,set_settings=lambda v:self.values.update(v),staff_check=lambda req:req.headers.get('X-Test-Staff')=='yes',email_test_mode=lambda:False)
  self.svc.ensure=lambda:None
  app=FastAPI();app.include_router(self.svc.router());self.http=TestClient(app)
  self.env=patch.dict('os.environ',{'RELAY_TOKEN_ENCRYPTION_KEY':'unit-test-encryption-secret'})
  self.env.start();self.headers={'X-Test-Staff':'yes'};self.token='unit-test-upload-token-0123456789abcdef'
 def tearDown(self):self.env.stop();self.http.close()
 def test_save_reveal_and_authenticate_without_plaintext_storage(self):
  self.assertEqual(200,self.http.put('/api/relay/extension-token',json={'token':self.token},headers=self.headers).status_code)
  self.assertNotIn(self.token,json.dumps(self.values))
  r=self.http.get('/api/relay/extension-token',headers=self.headers)
  self.assertEqual(self.token,r.json()['token']);self.assertEqual('no-store',r.headers['cache-control'])
  self.assertEqual(200,self.http.post('/api/relay/extension/check',headers={'X-Relay-Token':self.token}).status_code)
  self.assertNotIn(self.token,self.http.get('/api/relay/settings',headers=self.headers).text)
 def test_staff_only_and_invalid_values_do_not_replace(self):
  for method in ['get','put','post']:
   self.assertEqual(401,getattr(self.http,method)('/api/relay/extension-token',**({'json':{'token':self.token}} if method=='put' else {})).status_code)
  self.svc.save_extension_token(self.token);before=dict(self.values)
  for value in ['',None,'short','x'*32+' ',123]:
   self.assertEqual(400,self.http.put('/api/relay/extension-token',json={'token':value},headers=self.headers).status_code)
   self.assertEqual(before,self.values)
 def test_replace_invalidates_old_token(self):
  self.svc.save_extension_token(self.token);new='new-unit-test-upload-token-0123456789abcdef'
  self.http.put('/api/relay/extension-token',json={'token':new},headers=self.headers)
  self.assertEqual(401,self.http.post('/api/relay/extension/check',headers={'X-Relay-Token':self.token}).status_code)
  self.assertEqual(new,self.svc.reveal_extension_token())
 def test_legacy_hash_reveals_nothing_and_generated_tokens_are_saved(self):
  self.assertEqual('',self.http.get('/api/relay/extension-token',headers=self.headers).json()['token'])
  r=self.http.post('/api/relay/extension-token',headers=self.headers)
  self.assertEqual(r.json()['token'],self.svc.reveal_extension_token())
