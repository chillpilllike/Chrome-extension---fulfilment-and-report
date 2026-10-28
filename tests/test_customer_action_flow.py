"""Synthetic email -> portal -> saved request; never import production main."""
import ast
import hashlib
import hmac
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import Mock
from urllib.parse import urlencode, urlsplit, parse_qs
from fastapi import HTTPException
from app.services.after_order import normalize_customer_decision, request_fingerprint
from app.services.after_order_email import render_after_order_email
import test_after_order_portal_security as portal_tests

ROOT = Path(__file__).parents[1]
TREE = ast.parse((ROOT / 'app/main.py').read_text())

def extract(name, scope):
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    scope.update(Any=Any, Optional=Optional, Request=object)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'isolated-action', 'exec'), scope)
    return scope[name]


class DecisionPersistence(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
        CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY, store_id INTEGER,website_id INTEGER,odoo_order_id INTEGER,
          case_type TEXT, affected_items_json TEXT,context_json TEXT,current_decision TEXT,previous_decision TEXT,
          selected_product_id INTEGER,decision_version INTEGER DEFAULT 0,confirmed_at TEXT,decision_locked_at TEXT,
          confirmed_by TEXT,decision_updated_at TEXT,status TEXT,updated_at TEXT,decision_fingerprint TEXT);
        CREATE TABLE after_order_action_links(id INTEGER PRIMARY KEY,case_id INTEGER,token_hash TEXT,
          invalidated_at TEXT,expires_at TEXT,request_fingerprint TEXT,allowed_actions_json TEXT,test_mode INTEGER);
        CREATE TABLE events(decision TEXT);
        INSERT INTO after_order_cases(id,store_id,website_id,odoo_order_id,case_type,affected_items_json,context_json)
          VALUES(1,1,4,32202,'tracking','[]','{"risk_state":"suspected_lost"}');
        ''')
        self.token = 'a'*64
        @contextmanager
        def db():
            class Adapter:
                def execute(_,sql,args=()): return self.conn.execute(sql.replace(' FOR UPDATE',''),args)
            with self.conn: yield Adapter()
        def case(_):
            row = dict(self.conn.execute('SELECT * FROM after_order_cases WHERE id=1').fetchone())
            return dict(row,affected_items=json.loads(row['affected_items_json']),context=json.loads(row['context_json']))
        self.case = case
        self.signature = request_fingerprint(case(1))
        self.conn.execute('INSERT INTO after_order_action_links VALUES(1,1,?,NULL,?,?,?,0)',
            (hashlib.sha256(self.token.encode()).hexdigest(),(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), self.signature,json.dumps(['replacement','refund'])))
        self.conn.commit()
        self.scope = dict(db=db,datetime=datetime,timezone=timezone,hashlib=hashlib,json=json,
            HTTPException=HTTPException,parse_any_datetime=lambda v: datetime.fromisoformat(v) if v else None,
            clean_text=lambda v:str(v or '').strip(),row_to_dict=lambda r:dict(r) if r else None,
            require_after_order_case_in_scope=lambda _:None, after_order_case_is_in_scope=lambda _:True,
            after_order_case_by_id=case,request_fingerprint=request_fingerprint,
            normalize_customer_decision=normalize_customer_decision,after_order_email_test_mode=lambda:False,
            after_order_allowed_actions=lambda _:['replacement','refund'],receipt_correction=lambda _:False,
            after_order_json_list=json.loads,utc_now=lambda:datetime.now(timezone.utc).isoformat(),
            record_after_order_event=lambda c,*args,**kw:c.execute('INSERT INTO events VALUES(?)',(kw['decision'],)))
        self.submit = extract('record_after_order_customer_decision',self.scope)
        self.lookup = extract('after_order_action_link',self.scope)

    def tearDown(self): self.conn.close()
    def submit_choice(self, choice):
        case,link=self.lookup(self.token)
        return self.submit(case,link,choice)

    def test_replacement_and_refund_persist_once_each_duplicate_is_noop(self):
        for choice in ['replacement','replacement','refund','refund']:
            self.submit_choice(choice)
        row=self.case(1)
        self.assertEqual((row['current_decision'],row['previous_decision'],row['decision_version']),('refund','replacement',2))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],2)
        self.assertEqual(row['status'],'needs_confirmation')

    def test_duplicate_acknowledgement_preserves_order_display_context(self):
        self.submit_choice('refund')
        case,link=self.lookup(self.token)
        case.update(title='Package needs attention',odoo_order_name='TEST-NC32202')
        recorded,refreshed,_=self.submit(case,link,'refund')
        self.assertFalse(recorded)
        self.assertEqual(refreshed['odoo_order_name'],'TEST-NC32202')
        self.assertEqual(refreshed['title'],'Package needs attention')

    def test_other_whole_order_choices_persist_without_remote_order_actions(self):
        self.conn.execute('CREATE TABLE after_order_line_removals(case_id INTEGER,test_mode INTEGER,approved_at TEXT,status TEXT)')
        self.scope.update(alternative_workflow=SimpleNamespace(rows=lambda *args:[]),
            after_order_unavailable_review=lambda _:dict(blocked=False,approved=True),
            after_order_removal_allowed=lambda _:True,
            delivery_followups=SimpleNamespace(cancel_pending=lambda *args:None),
            OdooClient=Mock(side_effect=AssertionError('No remote order action allowed')))
        for kind,actions in [('expected_dispatch',['proceed','cancel_order']),
                             ('delivery_confirmation',['received','not_received']),
                             ('item_unavailable',['exclude_item_and_proceed','cancel_affected_item','cancel_order'])]:
            self.conn.execute('UPDATE after_order_cases SET case_type=?,current_decision=NULL,confirmed_at=NULL',(kind,))
            self.scope['after_order_allowed_actions']=lambda _,choices=actions:choices
            signature=request_fingerprint(self.case(1))
            self.conn.execute('UPDATE after_order_action_links SET allowed_actions_json=?,request_fingerprint=?',(json.dumps(actions),signature))
            self.conn.commit()
            for choice in actions:
                self.assertTrue(self.submit_choice(choice)[0])
                self.assertFalse(self.submit_choice(choice)[0])
                self.assertEqual(self.case(1)['current_decision'],choice)
        self.scope['OdooClient'].assert_not_called()

    def test_event_failure_rolls_back_customer_choice(self):
        self.scope['record_after_order_event']=Mock(side_effect=RuntimeError('queue unavailable'))
        with self.assertRaises(RuntimeError): self.submit_choice('refund')
        self.assertIsNone(self.case(1)['current_decision'])
        self.assertEqual(self.case(1)['decision_version'],0)

    def test_expiry_is_rechecked_after_lookup_before_commit(self):
        case,link=self.lookup(self.token)
        self.conn.execute("UPDATE after_order_action_links SET expires_at='2020-01-01T00:00:00+00:00'");self.conn.commit()
        with self.assertRaises(HTTPException) as err: self.submit(case,link,'refund')
        self.assertEqual(err.exception.status_code,410)
        self.assertIsNone(self.case(1)['current_decision'])

    def test_revocation_and_order_change_during_submission(self):
        case,link=self.lookup(self.token)
        self.conn.execute("UPDATE after_order_action_links SET invalidated_at='revoked'");self.conn.commit()
        with self.assertRaises(HTTPException): self.submit(case,link,'refund')
        self.conn.execute('UPDATE after_order_action_links SET invalidated_at=NULL')
        self.conn.execute('UPDATE after_order_cases SET website_id=99');self.conn.commit()
        with self.assertRaises(HTTPException): self.submit(case,link,'refund')
        self.assertIsNone(self.case(1)['current_decision'])

    def test_invalid_expired_missing_expiry_and_unallowed_tokens(self):
        with self.assertRaises(HTTPException): self.lookup('b'*64)
        for expiry in [None,'2020-01-01T00:00:00+00:00']:
            self.conn.execute('UPDATE after_order_action_links SET expires_at=?',(expiry,)); self.conn.commit()
            with self.assertRaises(HTTPException) as err:self.lookup(self.token)
            self.assertEqual(err.exception.status_code,410)
            _,navigation=self.lookup(self.token,allow_stale_navigation=True)
            self.assertTrue(navigation['navigation_only'])
        case=self.case(1);link=dict(self.conn.execute('SELECT * FROM after_order_action_links').fetchone())
        with self.assertRaises(HTTPException):self.submit(case,link,'cancel_order')


class BridgeAuthorization(unittest.TestCase):
    def test_missing_wrong_and_valid_bridge_credentials(self):
        scope=dict(HTTPException=HTTPException,hmac=hmac,clean_text=lambda v:str(v or ''),
                   get_service_settings=lambda:{'after_order_bridge_key':'synthetic-secret'})
        guard=extract('require_after_order_bridge',scope)
        for key in ('','wrong'):
            with self.assertRaises(HTTPException) as err:
                guard(SimpleNamespace(headers={'x-after-order-bridge-key':key}))
            self.assertEqual(err.exception.status_code,401)
        guard(SimpleNamespace(headers={'x-after-order-bridge-key':'synthetic-secret'}))

    def test_website_boundary_rejects_other_domains(self):
        guard=extract('require_after_order_website_host',dict(HTTPException=HTTPException,hmac=hmac,clean_text=lambda v:str(v or '')))
        for host in ('', 'other.example.test','shop.example.test.attacker.test'):
            with self.assertRaises(HTTPException):guard(SimpleNamespace(headers={'x-website-host':host}),{'sender_domain':'shop.example.test'})
        guard(SimpleNamespace(headers={'x-website-host':'www.shop.example.test:443'}),{'sender_domain':'shop.example.test'})


@unittest.skipUnless(os.environ.get('ACTION_FLOW_TEST_PG_DSN'), 'isolated PostgreSQL DSN not supplied')
class ConcurrentDecisions(unittest.TestCase):
    setUp = DecisionPersistence.setUp
    tearDown = DecisionPersistence.tearDown
    def test_simultaneous_identical_clicks_create_one_event_and_version(self):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        dsn = os.environ['ACTION_FLOW_TEST_PG_DSN']
        conn = psycopg2.connect(dsn)
        schema = "action_flow_" + uuid.uuid4().hex
        try:
            with conn, conn.cursor() as cur:
                cur.execute("CREATE SCHEMA " + schema)
                cur.execute("SET search_path TO " + schema)
                for line in self.conn.iterdump():
                    if line.startswith(('BEGIN','COMMIT')): continue
                    cur.execute(line.replace('INTEGER PRIMARY KEY','INTEGER PRIMARY KEY'))
            barrier = threading.Barrier(2)
            @contextmanager
            def db():
                connection = psycopg2.connect(dsn)
                with connection.cursor() as cursor:
                    cursor.execute("SET search_path TO " + schema)
                class Adapter:
                    def execute(_,sql,args=()):
                        cursor = connection.cursor(cursor_factory=RealDictCursor)
                        cursor.execute(sql.replace('?', '%s'), args)
                        return cursor
                try:
                    with connection: yield Adapter()
                finally: connection.close()
            def read_case(_):
                with db() as c:
                    row = dict(c.execute('SELECT * FROM after_order_cases WHERE id=1').fetchone())
                    return dict(row,affected_items=json.loads(row['affected_items_json']),context=json.loads(row['context_json']))
            scope = dict(self.scope, db=db,after_order_case_by_id=read_case,
                record_after_order_event=lambda c,*args,**kw:c.execute('INSERT INTO events VALUES(?)',(kw['decision'],)))
            submit = extract('record_after_order_customer_decision',scope)
            case,link=self.lookup(self.token)
            def click():
                barrier.wait(timeout=5)
                return submit(case,link,'refund')[0]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:click(),range(2)))
            self.assertEqual(sorted(results),[False,True])
            with db() as c:
                self.assertEqual(c.execute('SELECT decision_version FROM after_order_cases').fetchone()['decision_version'],1)
                self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM events').fetchone()['n'],1)
        finally:
            conn.rollback()
            with conn, conn.cursor() as cur:
                cur.execute("DROP SCHEMA IF EXISTS " + schema + " CASCADE")
            conn.close()


@unittest.skipUnless(os.environ.get("AFTER_ORDER_PORTAL_TEST_ROOT"), "set authoritative portal source for cross-repository journey tests")
class EmailPortalJourney(unittest.TestCase):
    def setUp(self):
        portal_tests.PortalSecurityTests.setUp(self)
        self.controller.order_update.__func__.__globals__.update(urlencode=urlencode,_logger=logging.getLogger('test'))
        self.request.env.user._is_public=lambda:False
        self.request.redirect=lambda target,code=302:SimpleNamespace(location=target,status_code=code,headers={})
        self.controller._document_check_access=Mock(return_value=self.order)
        self.payload={'order':{'id':123},'test_mode':False,'allowed_actions':['replacement','refund'],
                      'case':{'type':'tracking'},'action_labels':{'replacement':'Request replacement','refund':'Request refund'}}
        self.controller._bridge=Mock(return_value=self.payload)

    def test_email_choices_survive_redirect_and_login_without_post(self):
        _,markup,plain=render_after_order_email({'odoo_order_name':'TEST-NC32202','case_type':'tracking',
            'context':{'risk_state':'suspected_lost'}},'https://shop.example.test/order-update/'+'a'*64,
            actions=['replacement','refund'],labels={})
        for choice in ['replacement','refund']:
            self.assertIn('choice='+choice,markup);self.assertIn('choice='+choice,plain)
            for public in [False,True]:
                self.request.env.user._is_public=lambda:public
                response=self.controller.order_update('a'*64,choice=choice)
                target=response.location
                if public:target=parse_qs(urlsplit(target).query)['redirect'][0]
                self.assertEqual(parse_qs(urlsplit(target).query)['choice'],[choice])
        for call in self.controller._bridge.call_args_list:
            self.assertEqual(call.args,('a'*64,))  # scanner visits never submit a decision

    def test_untrusted_choice_discarded_and_page_only_suggests_allowed_choice(self):
        target=self.controller.order_update('a'*64,choice='https://evil.invalid').location
        self.assertNotIn('choice=',target)
        self.request.params.update(after_order_token='a'*64,choice='refund')
        self.controller._alternative_products=Mock(return_value=[])
        values=self.controller._sale_order_get_page_view_values(self.order,'access',{},'history')
        self.assertIn('refund',values['after_order_panels'][0]['payload']['allowed_actions'])
        self.controller._bridge.assert_called_with('a'*64)

    def test_failed_order_access_never_forwards_a_customer_decision(self):
        class Denied(Exception): pass
        self.controller.order_update_decision.__func__.__globals__.update(AccessError=Denied,MissingError=Denied)
        self.controller._document_check_access=Mock(side_effect=Denied('Order ownership required'))
        self.request.render=lambda *args:SimpleNamespace(status_code=200,headers={})
        response=self.controller.order_update_decision('a'*64,decision='refund')
        self.assertEqual(response.status_code,400)
        self.controller._bridge.assert_called_once_with('a'*64)

    def test_post_checks_order_access_then_uses_prg_and_preserves_access_token(self):
        self.controller._document_check_access=Mock(return_value=self.order)
        self.controller._bridge=Mock(side_effect=[self.payload,{'message':'Saved for team review'}])
        response=self.controller.order_update_decision('a'*64,decision='refund',access_token='access')
        self.controller._document_check_access.assert_called_once_with('sale.order',123,access_token='access')
        self.assertEqual(response.status_code,303)
        self.assertIn('access_token=access',response.location)
        self.assertEqual(self.request.session['after_order_message_123'],'Saved for team review')
        self.assertEqual(self.controller._bridge.call_args.args[1],'POST')

if __name__=='__main__':unittest.main()
