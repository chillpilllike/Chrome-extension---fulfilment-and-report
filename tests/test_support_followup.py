import unittest,sqlite3
from datetime import datetime,timezone,timedelta
from app.support.followup import change_eligibility
from app.support.waiting import pending_customer,ACK
NOW=datetime.now(timezone.utc)
class FollowupTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
  self.c.execute('CREATE TABLE shopify_order_status_cache(store_id INTEGER,odoo_order_name TEXT,fulfillment_status TEXT,cancelled_at TEXT,synced_at TEXT,shopify_order_id TEXT)')
 def test_only_fresh_explicit_unfulfilled_order_can_collect_changes(self):
  o={'name':'#1'};self.assertFalse(change_eligibility(self.c,8,o,NOW)['can_collect_change'])
  self.c.execute('INSERT INTO shopify_order_status_cache VALUES(8,?, ?,NULL,?,?)',('#1','UNFULFILLED',NOW.isoformat(),'42'))
  self.assertTrue(change_eligibility(self.c,8,o,NOW)['can_collect_change'])
  self.assertFalse(change_eligibility(self.c,8,o,NOW+timedelta(minutes=6))['can_collect_change'])
  for status in ['FULFILLED','PARTIALLY_FULFILLED','UNKNOWN']:
   self.c.execute('UPDATE shopify_order_status_cache SET fulfillment_status=?',(status,));self.assertFalse(change_eligibility(self.c,8,o,NOW)['can_collect_change'])
 def test_cancelled_order_and_other_store_are_not_eligible(self):
  self.c.execute("INSERT INTO shopify_order_status_cache VALUES(8,'#1','UNFULFILLED','2026-09-01',?,'42')",(NOW.isoformat(),))
  self.assertFalse(change_eligibility(self.c,8,{'name':'#1'},NOW)['can_collect_change'])
  self.assertFalse(change_eligibility(self.c,9,{'name':'#1'},NOW)['can_collect_change'])
 def test_human_reply_suppresses_wait_notice_but_bot_and_private_notes_do_not(self):
  c={'uuid':'c','created_at':NOW.isoformat(),'sender_type':'contact','type':'incoming'}
  a={'created_at':(NOW+timedelta(seconds=1)).isoformat(),'sender_type':'agent','type':'outgoing','sender_id':2,'text_content':'I am checking this for you.'}
  self.assertIsNone(pending_customer([c,a]))
  for override in [{'private':True},{'sender_id':5},{'text_content':ACK},{'meta':{'ai_assistant_id':1}}]:
   self.assertEqual('c',pending_customer([c,{**a,**override}])['uuid'])
 def test_old_human_reply_does_not_hide_new_customer_request(self):
  c={'uuid':'c','created_at':NOW.isoformat(),'sender_type':'contact','type':'incoming'}
  a={'created_at':(NOW-timedelta(seconds=1)).isoformat(),'sender_type':'agent','type':'outgoing','sender_id':2}
  self.assertEqual('c',pending_customer([c,a])['uuid'])

class ResendTests(unittest.TestCase):
 def setUp(self):
  import types,app,json,hashlib
  from contextlib import contextmanager
  from unittest.mock import patch
  from app.services.after_order import request_fingerprint
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
  self.c.executescript("""CREATE TABLE after_order_cases(id INTEGER,store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,customer_email TEXT);
CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,recipient TEXT,sender TEXT,subject TEXT,html_preview TEXT,status TEXT,idempotency_key TEXT,payload_json TEXT,created_at TEXT,updated_at TEXT,test_mode INTEGER,request_fingerprint TEXT,template_kind TEXT,provider_message_id TEXT);
CREATE TABLE after_order_action_links(case_id INTEGER,token_hash TEXT,invalidated_at TEXT,test_mode INTEGER,expires_at TEXT,request_fingerprint TEXT);""")
  case={'id':1,'case_type':'tracking','store_id':8,'website_id':1,'odoo_order_id':4,'customer_email':'test@example.com','context':{'risk_state':'suspected_lost'}}
  self.fingerprint=request_fingerprint(case)
  fake=types.ModuleType('app.main');fake.after_order_case_by_id=lambda _:case;fake.after_order_email_test_mode=lambda:False;fake.after_order_allowed_actions=lambda _:['refund','replacement'];fake.after_order_tracking_is_current=lambda _:True
  self.patch=patch.object(app,'main',fake,create=True);self.patch.start();self.addCleanup(self.patch.stop)
  token='a'*64
  self.c.execute("INSERT INTO after_order_cases VALUES(1,8,1,4,'test@example.com')")
  payload={'from':'support@secretgreen.com.au','to':['test@example.com'],'html':'<a href="https://secretgreen.com.au/order-update/'+token+'">Choose</a>'}
  self.c.execute("INSERT INTO after_order_messages(id,case_id,provider,recipient,sender,subject,status,idempotency_key,payload_json,test_mode,request_fingerprint,template_kind) VALUES(1,1,'resend','test@example.com','support@secretgreen.com.au','Order update','sent','original',?,0,?,'tracking')",(json.dumps(payload),self.fingerprint))
  self.c.execute('INSERT INTO after_order_action_links VALUES(1,?,NULL,0,?,?)',(hashlib.sha256(token.encode()).hexdigest(),(NOW+timedelta(days=1)).isoformat(),self.fingerprint))
  class Conn:
   def execute(inner,sql,args=()):return self.c.execute('SELECT 1') if 'pg_advisory' in sql else self.c.execute(sql,args)
  @contextmanager
  def db():
   try:yield Conn();self.c.commit()
   except:self.c.rollback();raise
  self.db=db
 def run_resend(self):
  from app.support.followup import resend_action_email
  return resend_action_email(self.db,8,1,{'id':4},'test@example.com','conversation',1)
 def test_customer_approved_resend_logs_success_and_deduplicates(self):
  from unittest.mock import patch,Mock
  provider=Mock();provider.send.return_value={'id':'provider-id'}
  with patch('app.services.after_order.create_email_provider',return_value=provider):
   self.assertTrue(self.run_resend()['sent']);self.assertTrue(self.run_resend()['already_sent']);self.assertEqual(1,provider.send.call_count)
  self.assertEqual(2,self.c.execute("SELECT count(*) FROM after_order_messages WHERE status='sent'").fetchone()[0])
 def test_movement_and_expired_choices_cannot_be_resent(self):
  from fastapi import HTTPException
  self.c.execute("UPDATE after_order_messages SET template_kind='package_movement'")
  with self.assertRaises(HTTPException):self.run_resend()
  self.c.execute("UPDATE after_order_messages SET template_kind='tracking'");self.c.execute("UPDATE after_order_action_links SET invalidated_at='now'")
  with self.assertRaises(HTTPException):self.run_resend()
 def test_uncertain_send_does_not_claim_success_or_repeat(self):
  from unittest.mock import patch,Mock
  from fastapi import HTTPException
  provider=Mock();provider.send.side_effect=TimeoutError()
  with patch('app.services.after_order.create_email_provider',return_value=provider):
   with self.assertRaises(HTTPException):self.run_resend()
   with self.assertRaises(HTTPException):self.run_resend()
   self.assertEqual(1,provider.send.call_count)
 def test_other_recipient_and_test_email_cannot_be_resent(self):
  from fastapi import HTTPException
  self.c.execute('UPDATE after_order_messages SET test_mode=1')
  with self.assertRaises(HTTPException):self.run_resend()
  self.c.execute("UPDATE after_order_messages SET test_mode=0,recipient='other@example.com'")
  with self.assertRaises(HTTPException):self.run_resend()
