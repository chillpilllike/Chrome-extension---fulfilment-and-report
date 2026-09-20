import ast
import json
import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock

from app.services.welcome_email import Monitor,delay_minutes


class WelcomeScheduleTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
        self.c.executescript('''
          CREATE TABLE stores(id INTEGER PRIMARY KEY,active INTEGER);
          INSERT INTO stores VALUES(1,1);
          CREATE TABLE pull_jobs(store_id INTEGER,status TEXT,created_at TEXT);
          CREATE TABLE order_lines(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,
            odoo_order_date TEXT,odoo_order_state TEXT,asin TEXT,product_name TEXT,quantity INTEGER);
          CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,case_key TEXT UNIQUE,store_id INTEGER,odoo_order_id INTEGER,
            odoo_order_name TEXT,case_type TEXT,status TEXT,severity TEXT,title TEXT,affected_items_json TEXT,
            context_json TEXT,created_at TEXT,updated_at TEXT);
          CREATE TABLE after_order_messages(id INTEGER PRIMARY KEY,case_id INTEGER,status TEXT,template_kind TEXT,test_mode INTEGER);
          CREATE TABLE after_order_email_attempts(message_id INTEGER,updated_at TEXT,provider_message_id TEXT);
          CREATE TABLE after_order_case_events(case_id INTEGER,event_type TEXT);
        ''')
        self.now=datetime.now(timezone.utc)
        self.settings={'after_order_welcome_started_at':(self.now-timedelta(days=2)).isoformat(),'autosync_interval_minutes':'5'}
        self.locked=True;self.sent=[]
        self.monitor=Monitor(dict(db=self.db,get_service_settings=lambda:self.settings,
          after_order_cutoff_date=lambda:(self.now-timedelta(days=1)).date().isoformat(),
          utc_now=lambda:self.now.isoformat(),send_after_order_email=self.send,clean_error_message=str,
          record_after_order_event=lambda c,cid,event,**kw:c.execute('INSERT INTO after_order_case_events VALUES(?,?)',(cid,event))))
        self.monitor.validate=Mock()

    @contextmanager
    def db(self):
        class Adapter:
            def execute(_,sql,params=()):
                if 'pg_try_advisory' in sql:return self.c.execute('SELECT ? AS locked',(self.locked,))
                return self.c.execute(sql,params)
        with self.c:yield Adapter()

    def add(self,oid,minutes,state='sale'):
        placed=(self.now-timedelta(minutes=minutes)).strftime('%Y-%m-%d %H:%M:%S')
        self.c.execute('INSERT INTO order_lines VALUES(?,1,?,?,?, ?,?,?,1)',(oid,oid,f'NC{oid}',placed,state,'ASIN','Item'))

    def send(self,cid,request):
        self.sent.append(cid)
        mid=self.c.execute("INSERT INTO after_order_messages(case_id,status,template_kind,test_mode) VALUES(?,'sent','new_order_welcome',0)",(cid,)).lastrowid
        self.c.execute('INSERT INTO after_order_email_attempts VALUES(?,?,?)',(mid,self.now.isoformat(),f'provider-{mid}'))

    def test_newest_first_and_no_duplicate_or_cancelled_replay(self):
        self.add(1,20);self.add(2,5)
        self.monitor.run_checks(None)
        self.assertEqual([r[0] for r in self.c.execute('SELECT odoo_order_id FROM after_order_cases ORDER BY id')],[2,1])
        self.monitor.run_checks(None);self.assertEqual(len(self.sent),2)
        self.c.execute("UPDATE after_order_messages SET status='cancelled'")
        self.monitor.run_checks(None);self.assertEqual(len(self.sent),2)

    def test_old_cutoff_and_inactive_store_never_sent(self):
        self.add(1,5000);self.monitor.run_checks(None);self.assertEqual(self.sent,[])
        self.add(2,5);self.c.execute('UPDATE stores SET active=0')
        self.monitor.run_checks(None);self.assertEqual(self.sent,[])

    def test_one_dispatcher_only(self):
        self.add(1,5);self.locked=False;self.monitor.run_checks(None)
        self.assertEqual(self.sent,[]);self.assertIsNone(self.monitor.last_check_at)

    def test_overdue_warning_and_late_sent_audit(self):
        self.add(1,20);self.add(2,5)
        pending=self.monitor.timing()
        self.assertEqual(pending['pending_overdue'],1)
        self.assertEqual(pending['orders'][0]['order_name'],'NC1')
        self.assertEqual(self.monitor.timing(99)['pending_overdue'],0)
        self.monitor.run_checks(None)
        after=self.monitor.timing()
        self.assertEqual(after['pending_overdue'],0);self.assertEqual(after['sent_late'],1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM after_order_case_events WHERE event_type='welcome_email_sla_exceeded'").fetchone()[0],1)
        self.monitor.run_checks(None)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM after_order_case_events WHERE event_type='welcome_email_sla_exceeded'").fetchone()[0],1)

    def test_test_delivery_does_not_count_as_customer_delivery(self):
        self.add(1,20);self.monitor.run_checks(None)
        self.c.execute('UPDATE after_order_messages SET test_mode=1')
        self.assertEqual(self.monitor.timing()['pending_overdue'],1)
        self.assertEqual(self.monitor.timing()['orders'][0]['status'],'test_only')

    def test_cancelled_notifications_not_reported_as_sendable(self):
        self.add(1,20);self.monitor.run_checks(None)
        self.c.execute('DELETE FROM after_order_email_attempts')
        self.c.execute("UPDATE after_order_messages SET status='cancelled'")
        self.assertEqual(self.monitor.timing()['pending_overdue'],0)

    def test_draft_orders_not_in_timing_target(self):
        self.add(1,20,'draft')
        self.assertEqual(self.monitor.timing()['pending_overdue'],0)

    def test_delay_boundary(self):
        self.assertEqual(delay_minutes('2026-09-21 00:00:00','2026-09-21T00:15:00+00:00'),15)

    def test_import_backlog_warning_includes_not_yet_imported_work(self):
        self.c.execute('INSERT INTO pull_jobs VALUES(1,?,?)',('running',(self.now-timedelta(minutes=20)).isoformat()))
        self.assertGreaterEqual(self.monitor.timing()['import_backlog_minutes'],20)
        self.assertEqual(self.monitor.timing(99)['import_backlog_minutes'],0)

    def test_schedulers_are_separate_from_slow_tracking_work(self):
        tree=ast.parse(Path('app/main.py').read_text())
        funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
        self.assertNotIn('queue_auto_pull_jobs',ast.unparse(funcs['autosync_loop']))
        self.assertNotIn('welcome_emails',ast.unparse(funcs['after_order_automation_loop']))
        self.assertNotIn('delivery_followups',ast.unparse(funcs['run_welcome_email_checks']))
        self.assertIn('time.sleep(30)',ast.unparse(funcs['welcome_email_loop']))
        self.assertIn('pg_try_advisory_xact_lock',ast.unparse(funcs['order_import_loop']))


if __name__=='__main__':unittest.main()
