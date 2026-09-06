import base64
from contextlib import contextmanager
import json
import os
import sqlite3
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.support.portal import create_portal_router, digest, code_digest, sign_jwt


class DB:
    def __init__(self):
        self.c=sqlite3.connect(':memory:',check_same_thread=False);self.c.row_factory=sqlite3.Row
    @contextmanager
    def connect(self):
        try:yield self;self.c.commit()
        except:self.c.rollback();raise
    def execute(self,sql,params=()):
        if 'pg_advisory' in sql:return self.c.execute('SELECT 1')
        return self.c.execute(sql.replace(' FOR UPDATE',''),params)

class Odoo:
    def __init__(self):self.calls=[];self.fail=False
    def execute(self,model,method,args):
        self.calls.append((model,method,args))
        if self.fail:raise ValueError('secret source error')
        return 12
    def read(self,*args):return [{'state':'sent'}]
    def fields_get(self,model):return {'website_id':{}}
    def existing_fields(self,model,fields):return fields
    def order_url(self,ident):return 'https://secretgreen.com.au/web#id='+str(ident)
    def search_read(self,model,domain,fields,**kwargs):
        self.calls.append((model,domain,kwargs))
        if model=='res.partner':return [{'id':7,'email':'test@example.com'}]
        if model=='sale.order':
            if ['id','=',99] in domain:return []
            return [{'id':4,'name':'SG1234','state':'sale','amount_total':20,'currency_id':[1,'AUD'],'date_order':'2026-09-06'}]
        return []

class PortalTests(unittest.TestCase):
    def setUp(self):
        self.upstream=patch("app.support.portal.libre",return_value={});self.upstream.start();self.addCleanup(self.upstream.stop)
        self.env=patch.dict(os.environ,{'SUPPORT_SECRETGREEN_ENABLED':'true','SUPPORT_SESSION_KEY':'x'*32,'SUPPORT_LIBREDESK_INBOX_SECRET':'y'*32,'SUPPORT_CONTEXT_KEY':'z'*32,'SUPPORT_LIBREDESK_TOOL_KEY':'k'*32});self.env.start();self.addCleanup(self.env.stop)
        self.db=DB();self.odoo=Odoo();app=FastAPI();app.include_router(create_portal_router(db=self.db.connect,get_store=lambda _:None,client_factory=lambda _:self.odoo));self.api=TestClient(app);self.p='/api/public/secretgreen-support'
    def issue(self):return self.api.post(self.p+'/request-code',json={'email':'test@example.com'}).json()['challenge']
    def authenticate(self):
        challenge=self.issue()
        self.db.c.execute('UPDATE support_portal_challenges SET code_hash=? WHERE id=?',(code_digest(challenge,'123456'),challenge));self.db.c.commit()
        r=self.api.post(self.p+'/verify',json={'challenge':challenge,'code':'123456'})
        return {'Authorization':'Bearer '+r.json()['token']}
    def test_no_orders_before_verified_email(self):
        r=self.api.get(self.p+'/orders');self.assertEqual(401,r.status_code);self.assertEqual([],self.odoo.calls)
    def test_request_does_not_enumerate_customers(self):
        self.issue();self.assertFalse(any(c[0]=='res.partner' for c in self.odoo.calls))
    def test_code_is_hashed_one_time_and_bounded(self):
        ch=self.issue();self.db.c.execute('UPDATE support_portal_challenges SET code_hash=? WHERE id=?',(code_digest(ch,'123456'),ch));self.db.c.commit()
        self.assertEqual(200,self.api.post(self.p+'/verify',json={'challenge':ch,'code':'123456'}).status_code)
        self.assertEqual(401,self.api.post(self.p+'/verify',json={'challenge':ch,'code':'123456'}).status_code)
    def test_invalid_code_attempts_commit_and_lock(self):
        ch=self.issue()
        self.db.c.execute('UPDATE support_portal_challenges SET code_hash=? WHERE id=?',(code_digest(ch,'123456'),ch));self.db.c.commit()
        for _ in range(5):self.api.post(self.p+'/verify',json={'challenge':ch,'code':'000000'})
        self.assertEqual(5,self.db.c.execute('SELECT attempts FROM support_portal_challenges').fetchone()[0])
    def test_rate_limit_and_failed_mail_are_not_success(self):
        for _ in range(3):self.issue()
        self.assertEqual(429,self.api.post(self.p+'/request-code',json={'email':'test@example.com'}).status_code)
    def test_upstream_mail_failure_is_generic(self):
        self.odoo.fail=True;r=self.api.post(self.p+'/request-code',json={'email':'test@example.com'})
        self.assertEqual(503,r.status_code);self.assertNotIn('secret source',r.text)
    def test_send_rpc_fault_after_delivery_still_returns_challenge(self):
        execute=self.odoo.execute
        def send_fault(model,method,args):
            if method=='send':raise ValueError('cannot marshal None')
            return execute(model,method,args)
        with patch.object(self.odoo,'execute',side_effect=send_fault):
            r=self.api.post(self.p+'/request-code',json={'email':'test@example.com'})
        self.assertEqual(200,r.status_code)
        self.assertEqual('sent',self.db.c.execute('SELECT delivery_state FROM support_portal_challenges').fetchone()[0])
    def test_send_rpc_fault_without_delivery_is_failure(self):
        with patch.object(self.odoo,'read',return_value=[{'state':'exception'}]):
            r=self.api.post(self.p+'/request-code',json={'email':'test@example.com'})
        self.assertEqual(503,r.status_code)
    def test_orders_are_scoped_and_safe(self):
        h=self.authenticate();r=self.api.get(self.p+'/orders',headers=h);self.assertEqual(200,r.status_code)
        self.assertEqual('no-store',r.headers['cache-control']);self.assertNotIn('partner_id',r.text)
        call=[x for x in self.odoo.calls if x[0]=='sale.order'][-1]
        self.assertIn(['website_id','=',1],call[1]);self.assertIn(['partner_id','in',[7]],call[1])
        self.assertIsNone(r.json()['orders'][0]['estimated_dispatch'])
    def test_foreign_order_cannot_start_conversation(self):
        h=self.authenticate()
        with patch('app.support.portal.libre') as upstream:
            r=self.api.post(self.p+'/start',headers=h,json={'order_id':99,'message':'Hello'})
            self.assertEqual(404,r.status_code);upstream.assert_not_called()
    def test_start_is_idempotent_and_cannot_relink(self):
        h=self.authenticate()
        def upstream(path,**kwargs):
            if 'exchange' in path:return {'session_token':'widget-session'}
            if 'init' in path:return {'conversation':{'uuid':'test-conversation'}}
            self.assertEqual('agent',kwargs['data']['sender_type'])
            self.assertFalse(kwargs['data']['private'])
            return {}
        with patch('app.support.portal.libre',side_effect=upstream) as call:
            body={'order_id':4,'message':'Please check my order'}
            a=self.api.post(self.p+'/start',headers=h,json=body);b=self.api.post(self.p+'/start',headers=h,json=body)
            self.assertEqual(200,a.status_code);self.assertEqual(a.json()['conversation_uuid'],b.json()['conversation_uuid']);self.assertEqual(3,call.call_count)
            self.assertEqual(409,self.api.post(self.p+'/start',headers=h,json={'message':'Other'}).status_code)
    def test_uncertain_start_does_not_retry(self):
        h=self.authenticate()
        with patch('app.support.portal.libre',side_effect=RuntimeError('timeout')) as call:
            body={'message':'Hello'}
            self.assertEqual(503,self.api.post(self.p+'/start',headers=h,json=body).status_code)
            self.assertEqual(409,self.api.post(self.p+'/start',headers=h,json=body).status_code)
            self.assertEqual(1,call.call_count)
    def test_plain_agent_claims_do_not_authorize(self):
        r=self.api.get(self.p+'/agent-context',headers={'X-Support-Context':base64.b64encode(b'{"agent_id":1}').decode()})
        self.assertEqual(403,r.status_code)
    def test_customer_and_staff_html_do_not_include_secrets(self):
        for path in ['/public/secretgreen-support','/public/secretgreen-support/agent']:
            r=self.api.get(path);self.assertEqual(200,r.status_code);self.assertNotIn('x'*32,r.text)
    def test_storefront_order_reference_is_preserved_without_source_text(self):
        from app.support.policy import safe_reference
        self.assertEqual('#5381',safe_reference('#5381'))
        self.assertEqual('Your order',safe_reference('Amazon #5381'))
    def native_headers(self,verified=False):
        return {'X-Secretgreen-Tool-Key':'k'*32,'X-Libredesk-Inbox-Id':'1',
                'X-Libredesk-Conversation-UUID':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                'X-Libredesk-Contact-Email':'test@example.com',
                'X-Libredesk-Contact-Verified':'true' if verified else 'false'}
    def test_native_tools_require_server_auth(self):
        for path in ['/customer-match','/orders','/link-order']:
            r=self.api.post(self.p+'/tools'+path,json={'order_id':4})
            self.assertEqual(403,r.status_code)
        self.assertEqual([],self.odoo.calls)
    def test_native_match_reveals_only_existence(self):
        r=self.api.post(self.p+'/tools/customer-match',headers=self.native_headers(),json={'email':'victim@example.com'})
        self.assertEqual(200,r.status_code);self.assertTrue(r.json()['has_orders'])
        self.assertNotIn('SG1234',r.text);self.assertNotIn('amount_total',r.text)
        partner=[c for c in self.odoo.calls if c[0]=='res.partner'][0]
        self.assertIn(['email_normalized','=','test@example.com'],partner[1])
    def test_native_unverified_cannot_list_or_link(self):
        for path in ['/orders','/link-order']:
            self.assertEqual(403,self.api.post(self.p+'/tools'+path,headers=self.native_headers(),json={'order_id':4}).status_code)
        self.assertEqual([],self.odoo.calls)
    def test_native_wrong_inbox_is_rejected(self):
        h=self.native_headers(True);h['X-Libredesk-Inbox-Id']='2'
        self.assertEqual(403,self.api.post(self.p+'/tools/orders',headers=h,json={}).status_code)
    def test_native_verified_order_binding(self):
        h=self.native_headers(True)
        self.assertEqual(404,self.api.post(self.p+'/tools/link-order',headers=h,json={'order_id':99}).status_code)
        r=self.api.post(self.p+'/tools/link-order',headers=h,json={'order_id':4})
        self.assertEqual(200,r.status_code);self.assertTrue(r.json()['linked'])
        row=self.db.c.execute('SELECT order_id,email FROM support_portal_sessions').fetchone()
        self.assertEqual(4,row['order_id']);self.assertEqual('test@example.com',row['email'])
    def test_status_requires_verified_matching_bound_contact(self):
        self.assertEqual(403,self.api.post(self.p+'/tools/order-status',headers=self.native_headers()).status_code)
        self.assertEqual(409,self.api.post(self.p+'/tools/order-status',headers=self.native_headers(True)).status_code)
        self.api.post(self.p+'/tools/link-order',headers=self.native_headers(True),json={'order_id':4})
        response=self.api.post(self.p+'/tools/order-status',headers=self.native_headers(True))
        self.assertEqual(200,response.status_code)
        self.assertNotIn('customer',response.json())
        h=self.native_headers(True);h['X-Libredesk-Contact-Email']='different@example.com'
        self.assertEqual(409,self.api.post(self.p+'/tools/order-status',headers=h).status_code)

    def test_review_requires_verified_order_and_confirmed_assignment(self):
        route=self.p+'/tools/request-cancellation-review'
        self.assertEqual(403,self.api.post(route,headers=self.native_headers()).status_code)
        h=self.native_headers(True)
        self.assertEqual(409,self.api.post(route,headers=h).status_code)
        self.api.post(self.p+'/tools/link-order',headers=h,json={'order_id':4})
        self.assertEqual(503,self.api.post(route,headers=h).status_code)
        with patch('app.support.portal.libre',return_value={'assigned_team_id':1,'assigned_user_id':2,'reference_number':123}):
            self.assertTrue(self.api.post(route,headers=h).json()['forwarded'])
            self.assertTrue(self.api.post(route,headers=h).json()['forwarded'])
        self.assertEqual(1,self.db.c.execute('SELECT count(*) FROM support_review_requests').fetchone()[0])
        h['X-Libredesk-Contact-Email']='other@example.com'
        self.assertEqual(409,self.api.post(route,headers=h).status_code)

    def test_product_context_needs_no_email_or_otp(self):
        h=self.native_headers();h.pop('X-Libredesk-Contact-Email')
        with patch('app.support.portal.libre',return_value=[]):
            r=self.api.post(self.p+'/tools/product-context',headers=h,json={})
        self.assertEqual(200,r.status_code);self.assertFalse(r.json()['found'])
        self.assertEqual(403,self.api.post(self.p+'/tools/product-context',json={}).status_code)

    def test_native_no_match_continues_general_without_code(self):
        with patch.object(self.odoo,'search_read',return_value=[]):
            r=self.api.post(self.p+'/tools/customer-match',headers=self.native_headers(),json={})
        self.assertEqual(200,r.status_code);self.assertFalse(r.json()['has_orders'])
        self.assertEqual('continue_general_chat',r.json()['next_step'])
        self.assertEqual([],self.odoo.calls)
    def test_native_link_preserves_other_conversation_attributes(self):
        with patch('app.support.portal.libre',side_effect=[{'custom_attributes':{'existing':'keep'}},True,{}]) as upstream:
            r=self.api.post(self.p+'/tools/link-order',headers=self.native_headers(True),json={'order_id':4})
        self.assertEqual(200,r.status_code)
        saved=upstream.call_args_list[1].kwargs['data']
        self.assertEqual('keep',saved['existing'])
        self.assertEqual('SG1234',saved['sg_order_reference'])
    def test_support_hours_sunday_requires_no_email_or_order(self):
        from datetime import datetime
        h=self.native_headers();h.pop('X-Libredesk-Contact-Email')
        with patch('app.support.portal.libre',side_effect=[{'app.timezone':'Asia/Kolkata','app.business_hours_id':'2'},{'hours':{'Monday':{'open':'09:00','close':'17:00'}},'holidays':[]}]),patch('app.support.portal.datetime') as clock:
            clock.now.return_value=datetime.fromisoformat('2026-09-06T12:00:00+05:30')
            r=self.api.post(self.p+'/tools/support-hours',headers=h,json={})
        self.assertEqual(200,r.status_code)
        self.assertFalse(r.json()['team_online_hours'])
        self.assertIn('Monday',r.json()['message'])
        self.assertEqual([],self.odoo.calls)
    def test_email_wildcards_are_rejected(self):
        self.assertEqual(422,self.api.post(self.p+'/request-code',json={'email':'%test@example.com'}).status_code)

if __name__=='__main__':unittest.main()
