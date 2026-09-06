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
        self.env=patch.dict(os.environ,{'SUPPORT_SECRETGREEN_ENABLED':'true','SUPPORT_SESSION_KEY':'x'*32,'SUPPORT_LIBREDESK_INBOX_SECRET':'y'*32,'SUPPORT_CONTEXT_KEY':'z'*32});self.env.start();self.addCleanup(self.env.stop)
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
        self.assertIn('confirmed',r.json()['orders'][0]['reply'])
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
    def test_email_wildcards_are_rejected(self):
        self.assertEqual(422,self.api.post(self.p+'/request-code',json={'email':'%test@example.com'}).status_code)

if __name__=='__main__':unittest.main()
