import ast
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from fastapi import HTTPException
from app.services.email_approval import bypassed,Dispatcher,KEYS,require_current_action_notice
from app.services.welcome_email import permitted,TEST_RECIPIENT


class PolicyTests(unittest.TestCase):
    def row(self,**kw):
        return dict({'provider':'resend','template_kind':'item_unavailable','status':'awaiting_approval',
                     'attempt_count':0,'payload_json':'{}','created_at':datetime.now(timezone.utc).isoformat(),'test_mode':0},**kw)

    def settings(self,**kw):return dict({KEYS[0]:'true',KEYS[1]:'true',KEYS[2]:datetime.now(timezone.utc).isoformat()},**kw)

    def test_default_other_notices_need_approval(self):
        for kind in ('tracking','item_unavailable','expected_dispatch','delivery_confirmation','alternative_payment'):
            self.assertFalse(permitted(self.row(template_kind=kind),test_mode=False))
            self.assertTrue(permitted(self.row(template_kind=kind),test_mode=False,settings=self.settings()))

    def test_existing_vs_new_boundary_and_switch_off(self):
        old=self.row(created_at='2026-09-01T00:00:00+00:00')
        self.assertFalse(bypassed(old,self.settings(**{KEYS[1]:'false'})))
        self.assertTrue(bypassed(old,self.settings()))
        self.assertFalse(bypassed(old,self.settings(**{KEYS[0]:'false'})))
        self.assertFalse(bypassed(self.row(created_at='bad'),self.settings(**{KEYS[1]:'false'})))

    def test_test_mode_cannot_leak(self):
        self.assertFalse(permitted(self.row(),test_mode=True,settings=self.settings()))
        own=self.row(test_mode=1,recipient=TEST_RECIPIENT,payload_json=json.dumps({'to':[TEST_RECIPIENT]}))
        self.assertTrue(permitted(own,test_mode=True,settings={}))
        for value in [{'to':['customer@example.org']},{'to':[TEST_RECIPIENT],'bcc':['other@example.org']},[]]:
            self.assertFalse(permitted({**own,'payload_json':json.dumps(value)},test_mode=True,settings=self.settings()))

    def test_never_resend_attempted_or_unknown(self):
        for change in [{'attempt_count':1},{'provider_message_id':'accepted-id'},
                       {'status':'failed'},{'status':'delivery_unknown'},{'status':'sent'},
                       {'payload_json':'invalid'},{'template_kind':'relay_request'},{'template_kind':'refund_confirmed'}]:
            self.assertFalse(permitted(self.row(**change),test_mode=False,settings=self.settings()))

    def test_automatic_exceptions_survive_toggle_off(self):
        for kind in ('new_order_welcome','shopify_dispatch','trustpilot_review','manual_refund_completed'):
            self.assertTrue(permitted(self.row(template_kind=kind),test_mode=False,settings={KEYS[0]:'false'}))

    def test_quote_email_permission_does_not_enable_financial_work(self):
        self.assertTrue(permitted(self.row(provider='odoo',template_kind='alternative_payment',provider_message_id='odoo-mail'),test_mode=False,settings=self.settings()))
        tree=ast.parse(Path('app/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_set_email_approval_settings')
        fn.decorator_list=[];setter=Mock()
        scope={'Any':object,'HTTPException':HTTPException,'set_service_settings':setter,'utc_now':lambda:'NOW','api_email_approval_settings':lambda:{}}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<settings>','exec'),scope)
        for payload in ({'bypass_approval':True},{'bypass_approval':'true'},{'bypass_approval':True,'confirm_live_sends':'true'}):
            with self.assertRaises(HTTPException):scope[fn.name](payload)
        setter.assert_not_called()
        scope[fn.name]({'bypass_approval':True,'include_existing':True,'confirm_live_sends':True})
        self.assertEqual(set(KEYS),set(setter.call_args.args[0]))
        scope[fn.name]({'bypass_approval':False})
        self.assertEqual('false',setter.call_args.args[0][KEYS[0]])

    def test_answered_or_closed_action_notice_cannot_bypass(self):
        for case,actions in [({'current_decision':'refund'},['refund']),({},[])]:
            with self.assertRaises(ValueError):
                require_current_action_notice(case,{'template_kind':'item_unavailable'},
                    {'after_order_allowed_actions':lambda _:actions})

    def test_dispatch_eta_must_still_match_unfulfilled_lines(self):
        eta=(datetime.now(timezone.utc)+timedelta(days=10)).date().isoformat()
        case={'store_id':1,'odoo_order_id':1,'affected_items':[{'line_id':1}],
              'context':{'expected_dispatch_date':eta}}
        line={'id':1,'state':'ordered','amazon_order_id':'A1','tracking_payload':'present'}
        query=Mock();query.execute.return_value.fetchall.side_effect=lambda:[line]
        @contextmanager
        def db():yield query
        ns={'db':db,'after_order_allowed_actions':lambda _:['proceed'],'get_service_settings':lambda:{'after_order_dispatch_handling_days':'0'},
            'parse_tracking_packages':lambda _: [{'expected_delivery_date':eta}]}
        require_current_action_notice(case,{'template_kind':'expected_dispatch'},ns)
        for change in [{'state':'fulfilled'},{'order_engine':'third_party'},{'amazon_order_id':''}]:
            original=dict(line);line.update(change)
            with self.assertRaises(ValueError):require_current_action_notice(case,{'template_kind':'expected_dispatch'},ns)
            line.clear();line.update(original)
        case['context']['expected_dispatch_date']='2026-01-01'
        with self.assertRaises(ValueError):require_current_action_notice(case,{'template_kind':'expected_dispatch'},ns)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript('''CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,provider TEXT,
          template_kind TEXT,status TEXT,attempt_count INTEGER,payload_json TEXT,created_at TEXT,updated_at TEXT,last_error TEXT);
          CREATE TABLE after_order_email_attempts(message_id INTEGER);''')
        c=self.c
        class DB:
            def execute(self,sql,args=()):
                if 'pg_try_advisory_xact_lock' in sql:return SimpleNamespace(fetchone=lambda:{'locked':True})
                return c.execute(sql,args)
        @contextmanager
        def db():
            with c:yield DB()
        self.settings={};self.events=Mock()
        def send(ident,*args,**kw):
            c.execute("UPDATE after_order_messages SET status='sent',attempt_count=1 WHERE id=?",(ident,))
        self.send=Mock(side_effect=send)
        self.ns={'db':db,'get_email_approval_settings':lambda:self.settings,'after_order_email_test_mode':lambda:False,
                 'retry_after_order_email':self.send,'clean_error_message':str,'utc_now':lambda:datetime.now(timezone.utc).isoformat(),
                 'record_after_order_event':self.events}
        self.d=Dispatcher(self.ns)

    def tearDown(self):self.c.close()

    def add(self,ident,kind='new_order_welcome',age=0):
        self.c.execute("INSERT INTO after_order_messages VALUES(?,1,'resend',?,'awaiting_approval',0,'{}',?,NULL,NULL)",
                       (ident,kind,(datetime.now(timezone.utc)-timedelta(days=age)).isoformat()))

    @patch('app.services.email_approval.time.sleep')
    def test_stranded_welcome_recovers_once_without_approval(self,sleep):
        self.add(883);self.d.run(None);self.d.run(None)
        self.send.assert_called_once_with(883,None,policy_exception=True)

    @patch('app.services.email_approval.time.sleep')
    def test_bypass_releases_queue_but_stops_when_disabled(self,sleep):
        self.add(1,'tracking');self.d.run(None);self.send.assert_not_called()
        self.settings.update({KEYS[0]:'true',KEYS[1]:'true'})
        self.d.run(None);self.assertEqual(1,self.send.call_count)
        self.settings[KEYS[0]]='false';self.add(2,'tracking');self.d.run(None)
        self.assertEqual(1,self.send.call_count)

    @patch('app.services.email_approval.time.sleep')
    def test_attempt_evidence_unknown_and_stale_are_not_resent(self,sleep):
        self.add(1);self.c.execute('INSERT INTO after_order_email_attempts VALUES(1)')
        self.add(2);self.c.execute("UPDATE after_order_messages SET status='delivery_unknown' WHERE id=2")
        self.add(3,age=4);self.d.run(None);self.send.assert_not_called()
        self.assertIn('three days',self.c.execute('SELECT last_error FROM after_order_messages WHERE id=3').fetchone()[0])

    @patch('app.services.email_approval.time.sleep')
    def test_blocked_case_does_not_skip_later_emails_or_spam_events(self,sleep):
        self.add(1);self.add(2)
        self.send.side_effect=lambda ident,*args,**kw: (_ for _ in ()).throw(ValueError('Safety blocked')) if ident==1 else self.c.execute("UPDATE after_order_messages SET status='sent' WHERE id=?",(ident,))
        self.d.run(None);self.d.run(None)
        self.events.assert_called_once()
        self.assertEqual('sent',self.c.execute('SELECT status FROM after_order_messages WHERE id=2').fetchone()[0])
