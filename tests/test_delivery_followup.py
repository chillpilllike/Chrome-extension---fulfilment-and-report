import json
import ast
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from pathlib import Path
from typing import Any, Optional
from fastapi import HTTPException

from app.services.delivery_followup import Monitor, SCHEMA, REVIEW, ISSUE, eligible_kind, receipt_correction
from app.services.after_order_email import render_after_order_email
from app.services.welcome_email import permitted
from app.services.care_sms import eligible, render, verify_followup_template


class DeliveryFollowupTests(unittest.TestCase):
    def test_only_non_delivery_can_be_corrected_after_team_confirmation(self):
        self.assertTrue(receipt_correction({'case_type':'delivery_confirmation','current_decision':'not_received','confirmed_at':'now'}))
        for kind, decision in [('item_unavailable','not_received'),('delivery_confirmation','received'),('tracking','refund')]:
            self.assertFalse(receipt_correction({'case_type':kind,'current_decision':decision}))

    @patch('app.services.care_sms.requests.post')
    def test_template_approval_sender_and_text_are_verified(self, post):
        mapping = {'sender':'nutricity','template_id':'example','text':'Nutricity: review ##url## - Nutricity Support'}
        active = {'active_status':'1','status':'1','sender_id':mapping['sender'],'template_data':mapping['text']}
        post.return_value.json.return_value = {'data':[active]}
        verify_followup_template(mapping)
        for changes in ({'status':'0'}, {'status':'2'}, {'sender_id':'wrong'}, {'template_data':'changed'}, {'active_status':'0'}):
            post.return_value.json.return_value = {'data':[{**active, **changes}]}
            with self.assertRaises(ValueError):
                verify_followup_template(mapping)

    def test_deadlines(self):
        now = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)
        self.assertEqual(REVIEW, eligible_kind('received', now.isoformat(), '', now))
        self.assertIsNone(eligible_kind('received', (now + timedelta(seconds=1)).isoformat(), '', now))
        self.assertEqual(REVIEW, eligible_kind('', None, (now-timedelta(days=5)).isoformat(), now))
        self.assertIsNone(eligible_kind('', None, (now-timedelta(days=5)+timedelta(seconds=1)).isoformat(), now))
        self.assertEqual(ISSUE, eligible_kind('not_received', None, '2020-01-01', now))
        for value in ['', 'Not provided by carrier', 'invalid']:
            self.assertIsNone(eligible_kind('', None, value, now))
        self.assertIsNone(eligible_kind('refund', None, '2020-01-01', now))

    def test_copy_and_narrow_auto_exception(self):
        case = {'odoo_order_name':'NC123', 'context':{'website_name':'Nutricity Australia'}}
        _, html, plain = render_after_order_email(case, '', actions=[], labels={}, template_kind=REVIEW,
                                                  review_url='https://www.trustpilot.com/review/nutricity.com.au')
        self.assertNotIn('confirming delivery', plain)
        self.assertNotIn('glad your order arrived', plain)
        self.assertIn('https://www.trustpilot.com/review/nutricity.com.au', html)
        _, _, plain = render_after_order_email(case, '', actions=[], labels={}, template_kind=ISSUE)
        self.assertIn('Our team will investigate the delivery and contact you shortly.', plain)
        for kind in (REVIEW, ISSUE):
            row = {'provider':'resend', 'status':'awaiting_approval','template_kind':kind,
                   'payload_json':json.dumps({'to':['customer@example.com']})}
            self.assertTrue(permitted(row, test_mode=False))
            self.assertFalse(permitted(row, test_mode=True))
            self.assertTrue(eligible(row))
            self.assertFalse(eligible({**row, 'payload_json':'{"_care_reminder_parent":1}'}))
        self.assertIn('investigate', render(ISSUE, 'NC123', 'Nutricity', 'https://nutricity.com.au/my/orders/1'))

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA + '''
            CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY, store_id INTEGER, odoo_order_id INTEGER);
            CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,template_kind TEXT,test_mode INTEGER,
                status TEXT,attempt_count INTEGER,updated_at TEXT);
            CREATE TABLE after_order_sms(id INTEGER PRIMARY KEY,email_id INTEGER,status TEXT,attempts INTEGER,updated_at TEXT);
            INSERT INTO after_order_cases VALUES(1,1,100);
            INSERT INTO after_order_cases VALUES(2,1,100);
            INSERT INTO after_order_messages VALUES(10,1,'trustpilot_review',0,'awaiting_approval',0,'');
            INSERT INTO after_order_messages VALUES(11,2,'trustpilot_review',0,'awaiting_approval',0,'');
            INSERT INTO after_order_sms VALUES(20,10,'awaiting_approval',0,'');
        ''')
        connection = self.conn
        class Adapter:
            def execute(self, sql, values=()):
                return connection.execute(sql.replace(' FOR UPDATE',''), values)
        self.adapter = Adapter()
        self.monitor = Monitor({'utc_now':lambda:'2026-09-20T12:00:00+00:00', 'record_after_order_event':Mock()})
        self.monitor.validate = Mock()
        self.case = {'id':1, 'store_id':1, 'odoo_order_id':100}

    def tearDown(self):
        self.conn.close()

    def test_cancel_then_confirm_transfers_only_unsent_reservation(self):
        self.monitor.reserve(self.adapter, self.case, REVIEW, 'email', 10)
        with self.assertRaises(ValueError):
            self.monitor.reserve(self.adapter, self.case, REVIEW, 'email', 11)
        self.monitor.cancel_pending(self.adapter, self.case)
        self.assertEqual('cancelled', self.conn.execute('SELECT status FROM after_order_sms WHERE id=20').fetchone()[0])
        self.monitor.reserve(self.adapter, self.case, REVIEW, 'email', 11)
        self.conn.execute("UPDATE after_order_messages SET attempt_count=1,status='sent' WHERE id=11")
        with self.assertRaises(ValueError):
            self.monitor.reserve(self.adapter, self.case, REVIEW, 'email', 10)

    def test_legacy_attempt_and_unknown_delivery_never_duplicate(self):
        self.conn.execute("UPDATE after_order_messages SET attempt_count=1,status='delivery_unknown' WHERE id=10")
        with self.assertRaises(ValueError):
            self.monitor.reserve(self.adapter, self.case, REVIEW, 'email', 11)
        self.monitor.cancel_pending(self.adapter, self.case)
        self.assertEqual('delivery_unknown', self.conn.execute('SELECT status FROM after_order_messages WHERE id=10').fetchone()[0])

    def test_test_mode_monitor_does_not_scan_or_send(self):
        monitor = Monitor({'after_order_email_test_mode':lambda:True})
        monitor.run_checks(None)

    def test_record_later_receipt_preserves_expired_link_guard(self):
        tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'record_after_order_customer_decision')
        for definition in ('case_type TEXT', 'current_decision TEXT', 'confirmed_at TEXT', 'affected_items_json TEXT',
                           'context_json TEXT', 'previous_decision TEXT', 'selected_product_id INTEGER',
                           'decision_version INTEGER DEFAULT 0', 'decision_updated_at TEXT', 'status TEXT',
                           'updated_at TEXT', 'decision_fingerprint TEXT', 'decision_locked_at TEXT', 'confirmed_by TEXT'):
            self.conn.execute('ALTER TABLE after_order_cases ADD COLUMN '+definition)
        self.conn.executescript("""CREATE TABLE after_order_action_links(id INTEGER,invalidated_at TEXT);
            INSERT INTO after_order_action_links VALUES(1,NULL);
            CREATE TABLE after_order_execution_jobs(case_id INTEGER,decision TEXT,status TEXT,updated_at TEXT);
            INSERT INTO after_order_execution_jobs VALUES(1,'not_received','pending','');
            UPDATE after_order_cases SET case_type='delivery_confirmation', current_decision='not_received',
                confirmed_at='2026-09-19',affected_items_json='[]',context_json='{}' WHERE id=1;""")
        @contextmanager
        def db():
            yield self.adapter
        def get_case(cid):
            return dict(self.conn.execute('SELECT * FROM after_order_cases WHERE id=?', (cid,)).fetchone())
        scope = {'Any':Any,'Optional':Optional,'HTTPException':HTTPException,'json':json,'receipt_correction':receipt_correction,
                 'normalize_customer_decision':lambda x:x,'after_order_email_test_mode':lambda:False,
                 'request_fingerprint':lambda x:'fp','after_order_allowed_actions':lambda x:['received'],
                 'utc_now':lambda:'2026-09-20T00:00:00+00:00','db':db,'after_order_json_list':json.loads,
                 'clean_text':lambda x:x or '', 'record_after_order_event':Mock(), 'after_order_case_by_id':get_case}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'receipt_test','exec'),scope)
        link = {'id':1,'request_fingerprint':'fp','allowed_actions_json':'["received"]'}
        with self.assertRaises(HTTPException):
            scope[node.name](get_case(1),{**link,'invalidated_at':'expired'},'received')
        self.assertEqual('not_received',get_case(1)['current_decision'])
        recorded, refreshed, _ = scope[node.name](get_case(1),link,'received')
        self.assertTrue(recorded)
        self.assertEqual('received', refreshed['current_decision'])
        self.assertIsNone(refreshed['confirmed_at'])
        self.assertEqual('cancelled', self.conn.execute('SELECT status FROM after_order_execution_jobs').fetchone()[0])

    def test_new_carrier_case_does_not_erase_an_unresolved_non_delivery_report(self):
        old = {**self.case,'case_type':'delivery_confirmation','tracking_code':'P1',
               'current_decision':'not_received','decision_updated_at':'2000-01-01T00:00:00+00:00',
               'context_json':'{"risk_state":"delivered"}'}
        new = {**old,'id':2,'current_decision':None,'decision_updated_at':None}
        rows = [old,new]
        class Connection:
            def execute(self, sql, values=()):
                result = Mock()
                result.fetchall.return_value = rows
                result.fetchone.return_value = None
                return result
        @contextmanager
        def db():
            yield Connection()
        monitor = Monitor({'db':db})
        kind, answer = monitor.state(new)
        self.assertEqual(ISSUE, kind)
        self.assertEqual(1, answer['id'])
        rows[:] = [{**new,'current_decision':'received','decision_updated_at':'2001-01-01T00:00:00+00:00'},old]
        kind, answer = monitor.state(rows[0])
        self.assertEqual(REVIEW, kind)
        self.assertEqual(2, answer['id'])


if __name__ == '__main__':
    unittest.main()
