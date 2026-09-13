import unittest,os
from unittest.mock import patch
from fastapi import HTTPException
from app.support.post_order_chat import scope_key,issue_offer,read_offer,consent,explicit_resend

class PostOrderConsentTests(unittest.TestCase):
 def setUp(self):
  self.env=patch.dict(os.environ,{'SUPPORT_LIBREDESK_TOOL_KEY':'t'*40});self.env.start();self.addCleanup(self.env.stop)
  self.scope={'store':1,'website':63,'inbox':80,'uuid':'conversation','email':'test@example.com','order':12}
 def test_signed_offer_scope_and_expiration(self):
  token=issue_offer(self.scope,10,100,now=1000)
  self.assertEqual(10,read_offer(token,self.scope,now=1100)['message_id'])
  for scope in [dict(self.scope,order=13),dict(self.scope,email='other@example.com'),dict(self.scope,website=1),dict(self.scope,uuid='other')]:
   with self.assertRaises(HTTPException):read_offer(token,scope,now=1100)
  with self.assertRaises(HTTPException):read_offer(token,self.scope,now=1601)
  with self.assertRaises(HTTPException):read_offer(token+'x',self.scope,now=1100)
 def test_keys_do_not_cross_sites(self):
  self.assertNotEqual(scope_key(1,1,80),scope_key(1,2,80));self.assertNotEqual(scope_key(1,1,80),scope_key(1,1,81))
 def test_yes_requires_new_message_after_offer(self):
  offer={'incoming_id':100}
  incoming={'id':100,'type':'incoming','content':'yes'}
  with self.assertRaises(HTTPException):consent([incoming],offer)
  messages=[incoming,{'id':101,'type':'outgoing','content':'Would you like me to resend that email?'},{'id':102,'type':'incoming','content':'Yes please'}]
  self.assertEqual(102,consent(messages,offer))
  messages[1]['private']=True
  with self.assertRaises(HTTPException):consent(messages,offer)
 def test_complaints_refusals_and_quoted_instructions_are_not_consent(self):
  for text in ['hi','I did not receive it','no do not resend','My email says please resend','yes but change my address','resend it to someone@example.com']:
   with self.assertRaises(HTTPException):consent([{'id':100,'type':'incoming','content':text}],{'incoming_id':100})
 def test_direct_and_french_commands(self):
  for text in ['Please resend the email','resend it','send it again','oui, renvoyez le courriel','renvoyez-moi le mail svp']:
   self.assertTrue(explicit_resend(text),text)
  messages=[{'id':1,'type':'incoming','content':'email ?'},{'id':2,'type':'outgoing','content':'Voulez-vous que je renvoie le courriel ?'},{'id':3,'type':'incoming','content':'oui'}]
  self.assertEqual(3,consent(messages,{'incoming_id':1}))

class NativeBoundaryTests(unittest.TestCase):
 setUp=PostOrderConsentTests.setUp
 def test_native_auth_and_customer_scope_are_server_resolved(self):
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from unittest.mock import Mock
  from app.support.post_order_chat import create_router
  app=FastAPI();odoo=Mock();factory=Mock(return_value=odoo)
  app.include_router(create_router(None,lambda s:s,factory));client=TestClient(app)
  url='/api/public/support-post-order/1/63/80/resend'
  auth={'X-Postorder-Key':scope_key(1,63,80),'X-Libredesk-Inbox-Id':'80','X-Libredesk-Contact-Verified':'true','X-Libredesk-Conversation-UUID':'12345678-1234-1234-1234-123456789012','X-Libredesk-Contact-Email':'test@example.com'}
  for headers in [{},{**auth,'X-Libredesk-Inbox-Id':'81'},{**auth,'X-Libredesk-Contact-Verified':'false'},{**auth,'X-Postorder-Key':scope_key(1,64,80)}]:
   self.assertEqual(403,client.post(url,headers=headers,json={'offer_token':'x'*50}).status_code)
  factory.assert_not_called()
  with patch('app.support.post_order_chat.native',return_value={'inbox_id':80,'contact':{'email':'other@example.com'}}):
   self.assertEqual(403,client.post(url,headers=auth,json={'offer_token':'x'*50}).status_code)
  factory.assert_not_called()
  odoo.search_read.side_effect=[[{'order_id':[12,'Order']}],[]]
  with patch('app.support.post_order_chat.native',return_value={'inbox_id':80,'contact':{'email':'test@example.com'}}):
   self.assertEqual(403,client.post(url,headers=auth,json={'offer_token':'x'*50}).status_code)
  self.assertIn(('website_id','=',63),odoo.search_read.call_args.args[1])
  self.assertIn(('partner_id.email_normalized','=','test@example.com'),odoo.search_read.call_args.args[1])

class StatusTests(unittest.TestCase):
 setUp=PostOrderConsentTests.setUp
 def test_unreviewed_item_details_are_withheld_and_test_mail_is_excluded(self):
  import sqlite3,types,app
  from contextlib import contextmanager
  from unittest.mock import Mock
  from fastapi import FastAPI
  from fastapi.testclient import TestClient
  from app.support.post_order_chat import create_router
  c=sqlite3.connect(':memory:',check_same_thread=False);c.row_factory=sqlite3.Row
  c.executescript('''CREATE TABLE after_order_cases(id INTEGER,store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,customer_email TEXT,case_type TEXT,current_decision TEXT,decision_updated_at TEXT,confirmed_at TEXT,status TEXT,context_json TEXT,updated_at TEXT);
  CREATE TABLE after_order_messages(id INTEGER,case_id INTEGER,recipient TEXT,status TEXT,test_mode INTEGER,created_at TEXT,provider TEXT,provider_message_id TEXT,template_kind TEXT);
  INSERT INTO after_order_cases VALUES(1,1,63,12,'test@example.com','item_unavailable',NULL,NULL,NULL,'needs_attention','{}','2026-09-13');
  INSERT INTO after_order_messages VALUES(1,1,'test@example.com','sent_test',1,'2026-09-13','resend','x','item_unavailable');''')
  @contextmanager
  def db():yield c
  fake=types.ModuleType('app.main');fake.after_order_case_by_id=lambda _: {'id':1,'status':'needs_attention','case_type':'item_unavailable','affected_items':[{'product_name':'PRIVATE SOURCING ITEM'}]};fake.after_order_case_is_in_scope=lambda _:True;fake.after_order_unavailable_review=lambda _: {'approved':False,'blocked':False}
  odoo=Mock();odoo.search_read.side_effect=[[{'order_id':[12,'order']}],[{'id':12,'name':'NC12','state':'sale'}]]
  web=FastAPI();web.include_router(create_router(db,lambda s:s,lambda s:odoo))
  headers={'X-Postorder-Key':scope_key(1,63,80),'X-Libredesk-Inbox-Id':'80','X-Libredesk-Contact-Verified':'true','X-Libredesk-Conversation-UUID':'12345678-1234-1234-1234-123456789012','X-Libredesk-Contact-Email':'test@example.com'}
  with patch.object(app,'main',fake,create=True),patch('app.support.post_order_chat.native',return_value={'inbox_id':80,'contact':{'email':'test@example.com'}}):
   r=TestClient(web).post('/api/public/support-post-order/1/63/80/status',headers=headers)
  self.assertEqual(200,r.status_code,r.text);data=r.json()
  self.assertEqual('confirmed',data['order_status']);self.assertIsNone(data['resend_offer']);self.assertFalse(data['post_order'][0]['customer_details_available'])
  self.assertEqual([],data['email_history']['cases'][0]['emails']);self.assertNotIn('PRIVATE SOURCING ITEM',r.text)
