import ast
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.notification_worker import Worker
from app.services.refund_notices import Notices
from app.services.care_sms import SMS, template_kind, sms_customer_number
from app.services.after_order_email import render_after_order_email
from app.services.notification_i18n import language_inventory


class NotificationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.conn=sqlite3.connect(':memory:'); self.conn.row_factory=sqlite3.Row
        self.conn.executescript('''CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,
            case_type TEXT,status TEXT,current_decision TEXT,confirmed_at TEXT,updated_at TEXT);
            CREATE TABLE order_lines(store_id INTEGER,odoo_order_id INTEGER,odoo_order_date TEXT);
            CREATE TABLE after_order_case_checks(case_id INTEGER PRIMARY KEY,checked_at TEXT);
            CREATE TABLE after_order_case_events(id INTEGER PRIMARY KEY,case_id INTEGER,event_type TEXT,details_json TEXT);
            CREATE TABLE after_order_line_selections(case_id INTEGER,line_id INTEGER,test_mode INTEGER,status TEXT,deadline_at TEXT,
              version INTEGER,updated_at TEXT,last_error TEXT);
            CREATE TABLE after_order_line_removals(case_id INTEGER,test_mode INTEGER,origin TEXT,status TEXT);
            CREATE TABLE after_order_messages(id INTEGER,case_id INTEGER,template_kind TEXT,test_mode INTEGER);
        ''')
        @contextmanager
        def db():
            class Adapter:
                def execute(_,sql,args=()):
                    if 'pg_try_advisory_xact_lock' in sql:return SimpleNamespace(fetchone=lambda:{'locked':True})
                    return self.conn.execute(sql,args)
            with self.conn: yield Adapter()
        self.mode=False; self.settings={'after_order_notifications_enabled':'true'}; self.cases={}
        self.events=Mock();self.send=Mock()
        self.ns={'db':db,'get_service_settings':lambda:self.settings,'after_order_email_test_mode':lambda:self.mode,
            'utc_now':lambda:datetime.now(timezone.utc).isoformat(),'after_order_cutoff_date':lambda:'2026-09-20',
            'after_order_case_by_id':lambda i:self.cases[i],'after_order_case_is_in_scope':lambda c:c.get('in_scope',True),
            'record_after_order_event':self.events,'clean_error_message':str,'sync_after_order_cases':Mock(),
            'send_after_order_email':self.send,'after_order_unavailable_review':lambda *a,**k:{'blocked':False,'approved':False},
            'care_delivery':Mock(),'care_requests':Mock(),'care_reminders':Mock(),'care_sms':Mock(),
            'refund_notices':Mock(),'alternative_workflow':Mock()}
        self.worker=Worker(self.ns)

    def tearDown(self): self.conn.close()

    def add(self,ident,kind,risk='',date='2026-09-20'):
        self.conn.execute("INSERT INTO after_order_cases(id,store_id,odoo_order_id,case_type,status) VALUES(?,1,?,?,'needs_attention')",(ident,ident,kind))
        self.conn.execute('INSERT INTO order_lines VALUES(1,?,?)',(ident,date))
        self.cases[ident]={'id':ident,'case_type':kind,'context':{'risk_state':risk},'status':'needs_attention'}

    def test_only_current_movements_and_action_cases_are_prepared(self):
        self.add(1,'tracking','in_transit');self.add(2,'tracking','awaiting_first_scan')
        self.add(3,'tracking','carrier_exception');self.add(4,'tracking','suspected_lost')
        self.add(5,'item_unavailable');self.add(6,'delivery_confirmation')
        self.add(7,'tracking','in_transit','2026-08-01')
        self.worker.prepare(object())
        self.assertEqual([1,4,6],[x.args[0] for x in self.send.call_args_list])
        self.assertEqual(6,self.conn.execute('SELECT count(*) FROM after_order_case_checks').fetchone()[0])

    def test_preparation_error_does_not_starve_other_cases(self):
        self.add(1,'tracking','in_transit');self.add(2,'tracking','in_transit')
        self.send.side_effect=[ValueError('site unavailable'),{}]
        self.worker.prepare(object());self.assertEqual(2,self.send.call_count)
        self.events.assert_called_once()

    def test_stage_failure_is_isolated_and_no_financial_execution(self):
        self.ns['care_delivery'].reconcile.side_effect=ValueError('receipt failure')
        self.worker.prepare=Mock(); self.worker.selection_reviews=Mock();self.worker.financial_sms=Mock()
        self.worker.run(object())
        self.worker.prepare.assert_called_once()
        self.ns['care_requests'].run_due.assert_called_once_with(review_only=True)
        self.ns['alternative_workflow'].run_due.assert_not_called()
        self.ns['alternative_workflow'].process.assert_not_called()
        self.ns['alternative_workflow'].release.assert_not_called()
        self.assertEqual('receipt failure',self.worker.errors['delivery_receipts'])
        self.assertTrue(self.worker.last_check_at)

    def test_test_mode_and_disabled_worker_do_nothing(self):
        self.worker.prepare=Mock()
        self.mode=True;self.worker.run(object());self.mode=False
        self.settings['after_order_notifications_enabled']='false';self.worker.run(object())
        self.worker.prepare.assert_not_called();self.ns['care_sms'].recover_failed.assert_not_called()

    def test_selection_expiry_only_moves_to_review_once(self):
        self.add(1,'item_unavailable')
        old=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        self.conn.execute("INSERT INTO after_order_line_selections VALUES(1,11,0,'choosing',?,2,?,NULL)",(old,old))
        self.worker.selection_reviews();self.worker.selection_reviews()
        self.assertEqual('needs_review',self.conn.execute('SELECT status FROM after_order_line_selections').fetchone()[0])
        self.assertEqual('needs_confirmation',self.conn.execute('SELECT status FROM after_order_cases').fetchone()[0])
        self.events.assert_called_once();self.ns['alternative_workflow'].process.assert_not_called()

    def test_refund_ack_requires_actual_unresolved_request(self):
        notices=Notices({**self.ns,'require_after_order_case_in_scope':Mock()})
        case={'id':1,'current_decision':'refund'};notices.validate(case)
        for change in [{'confirmed_at':'today'},{'status':'resolved'},{'current_decision':'received'}]:
            with self.assertRaises(ValueError):notices.validate({**case,**change})
        self.conn.execute("INSERT INTO after_order_line_removals VALUES(1,0,'customer','needs_approval')")
        notices.validate({'id':1,'current_decision':'offer_alternatives'})


class FinancialSmsTests(unittest.TestCase):
    def test_known_landline_is_blocked_and_valid_mobile_fallback_is_used(self):
        with self.assertRaisesRegex(ValueError,'landline'):
            sms_customer_number(['+442079460018'],'GB')
        self.assertEqual('+447911123456',sms_customer_number(['+442079460018','+447911123456'],'GB'))
        self.assertEqual('+14155552671',sms_customer_number(['+14155552671'],'US'))
    def test_owner_test_endpoint_cannot_be_used_for_live_or_unknown_templates(self):
        from fastapi import HTTPException
        tree=ast.parse(Path('app/main.py').read_text())
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='api_after_order_test_notification')
        fn.decorator_list=[];send=Mock(return_value={'status':'sent_test'})
        ns={'Request':object,'HTTPException':HTTPException,'send_after_order_email':send}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'owner-test','exec'),ns)
        ns[fn.name](1,'refund_request_received',None)
        send.assert_called_once_with(1,None,force_test=True,showcase_kind='refund_request_received')
        with self.assertRaises(HTTPException):ns[fn.name](1,'execute_refund',None)
        self.assertEqual(1,send.call_count)

    def test_quote_sms_is_blocked_for_paid_or_changed_quotations(self):
        selection={'status':'waiting_payment','result':{'quote_id':99,'mail':{'id':55}}}
        workflow=Mock();workflow.rows.return_value=[{'selection':selection}]
        client=Mock();quote={'state':'sent','website_id':[7,'Website'],'amount_total':2,'is_expired':False,'transaction_ids':[],'invoice_ids':[]}
        client.read.side_effect=lambda model,*a:[quote] if model=='sale.order' else [{'state':'done'}]
        sms=SMS({'alternative_workflow':workflow,'OdooClient':lambda _:client,'get_store':lambda _:None})
        case={'id':1,'store_id':1,'website_id':7,'current_decision':'offer_alternatives'}
        sms.validate_financial(case,{'provider_message_id':'55'},'price_difference')
        for change in [{'state':'cancel'},{'website_id':[8,'Other']},{'is_expired':True},{'transaction_ids':[1]},{'invoice_ids':[1]}]:
            old=dict(quote);quote.update(change)
            with self.assertRaises(ValueError):sms.validate_financial(case,{'provider_message_id':'55'},'price_difference')
            quote.clear();quote.update(old)

    def test_template_aliases_reuse_approved_templates(self):
        self.assertEqual('refund_completed',template_kind('manual_refund_completed'))
        self.assertEqual('alternative_payment',template_kind('price_difference'))

    def test_refund_sms_never_infers_completion(self):
        manual=Mock();manual.validate_message.side_effect=ValueError('No completion record')
        sms=SMS({'manual_refunds':manual})
        with self.assertRaises(ValueError):sms.validate_financial({'id':1},{},'manual_refund_completed')
        manual.validate_message.assert_called_once()
        with self.assertRaises(ValueError):sms.validate_financial({'id':1},{},'refund_completed')

    def test_safe_recovery_requires_terminal_receipt_one_hour_and_attempt_limit(self):
        sms=SMS({});conn=Mock();conn.execute.return_value.fetchone.return_value={'exists':True}
        row={'provider':'msg91','test_mode':0,'status':'provider_failed','attempts':1,
            'updated_at':(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),'provider_id':'p','recipient':'+14155552671'}
        self.assertTrue(sms.recovery_allowed(conn,row))
        for change in [{'status':'rejected'},{'status':'delivery_unknown'},{'status':'accepted'}, {'test_mode':1},{'attempts':3}, {'updated_at':datetime.now(timezone.utc).isoformat()}]:
            self.assertFalse(sms.recovery_allowed(conn,{**row,**change}))
        conn.execute.return_value.fetchone.return_value=None
        self.assertFalse(sms.recovery_allowed(conn,row))

    def test_refund_email_renders_every_supported_locale_without_actions(self):
        for lang in language_inventory():
            with self.subTest(language=lang['code']):
                case={'odoo_order_name':'TEST-1','sender_domain':'example.com','context':{'requested_language':lang['code'],'website_name':'Test'}}
                subject,html,plain=render_after_order_email(case,'',actions=[],labels={},template_kind='refund_request_received')
                self.assertIn('TEST-1',subject);self.assertIn('24',plain);self.assertNotIn('after_order_token',html)

    def test_worker_never_calls_financial_execution(self):
        source=Path('app/services/notification_worker.py').read_text()
        self.assertNotIn('.process(',source);self.assertNotIn('.release(',source)
        tree=ast.parse(source)
        self.assertNotIn('execute_after_order_job',{n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)})
