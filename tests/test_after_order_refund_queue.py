import sqlite3
import unittest
from app.services.refund_queue import REFUND_QUEUE_SQL


class RefundQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        # SQLite JSON arrows have the same extraction semantics; adapt only the
        # PostgreSQL type check/cast, leaving the production predicate intact.
        self.sql = REFUND_QUEUE_SQL.replace('CAST(s.product_json AS JSONB)', 's.product_json').replace(
            "jsonb_typeof(s.product_json->'difference')='number'",
            "json_type(s.product_json,'$.difference') IN ('integer','real')")
        self.db.executescript('''
          CREATE TABLE after_order_cases(id INTEGER,status TEXT,current_decision TEXT,decision_version INTEGER);
          CREATE TABLE after_order_refund_requests(case_id INTEGER,status TEXT);
          CREATE TABLE after_order_execution_jobs(case_id INTEGER,status TEXT,decision_version INTEGER);
          CREATE TABLE after_order_line_removals(case_id INTEGER,test_mode INTEGER,status TEXT);
          CREATE TABLE after_order_line_selections(case_id INTEGER,test_mode INTEGER,status TEXT,refund_status TEXT,product_json TEXT);
          INSERT INTO after_order_cases VALUES(1,'needs_confirmation','refund',1);
        ''')

    def tearDown(self):
        self.db.close()

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM after_order_cases WHERE '+self.sql).fetchone()[0]

    def test_customer_request_and_completed_refund(self):
        self.assertEqual(self.count(),1)
        self.db.execute("INSERT INTO after_order_refund_requests VALUES(1,'completed')")
        self.assertEqual(self.count(),0)

    def test_latest_decision_and_execution(self):
        self.db.execute("UPDATE after_order_cases SET current_decision='proceed'")
        self.assertEqual(self.count(),0)
        self.db.execute("UPDATE after_order_cases SET current_decision='refund'")
        self.db.execute("INSERT INTO after_order_execution_jobs VALUES(1,'completed',0)")
        self.assertEqual(self.count(),1)
        self.db.execute('UPDATE after_order_execution_jobs SET decision_version=1')
        self.assertEqual(self.count(),0)

    def test_multiple_lines_count_once_and_test_withdrawn_excluded(self):
        self.db.execute("UPDATE after_order_cases SET current_decision='offer_alternatives'")
        for mode,status in [(1,'needs_approval'),(0,'withdrawn')]:
            self.db.execute('INSERT INTO after_order_line_removals VALUES(1,?,?)',(mode,status))
        self.assertEqual(self.count(),0)
        for status in ('finance_review','removed_refund_pending'):
            self.db.execute('INSERT INTO after_order_line_removals VALUES(1,0,?)',(status,))
        self.assertEqual(self.count(),1)
        self.db.execute("UPDATE after_order_cases SET status='resolved'")
        self.assertEqual(self.count(),1)  # Unfinished money must not be hidden.

    def test_replacement_difference(self):
        self.db.execute("UPDATE after_order_cases SET current_decision='offer_alternatives'")
        for difference,expected in [('-2',1),('2',0),('0',0),('null',0),('"unknown"',0)]:
            self.db.execute('DELETE FROM after_order_line_selections')
            self.db.execute("INSERT INTO after_order_line_selections VALUES(1,0,'choosing','',?)", ('{"difference":'+difference+'}',))
            self.assertEqual(self.count(),expected)
        self.db.execute("UPDATE after_order_line_selections SET product_json='{\"difference\":-2}',refund_status='completed'")
        self.assertEqual(self.count(),0)
