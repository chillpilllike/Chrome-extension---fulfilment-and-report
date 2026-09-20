import sqlite3
import unittest
from app.services.delivery_issue_queue import DELIVERY_ISSUE_QUEUE_SQL


class DeliveryIssueQueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''CREATE TABLE after_order_cases (
            id INTEGER,store_id INTEGER,odoo_order_id INTEGER,tracking_code TEXT,
            case_type TEXT,current_decision TEXT,status TEXT,decision_updated_at TEXT);
            INSERT INTO after_order_cases VALUES(1,1,100,'P1','delivery_confirmation',
                'not_received','needs_attention','2026-09-20T01:00:00Z');''')

    def tearDown(self):
        self.db.close()

    def count(self):
        return self.db.execute('SELECT COUNT(*) FROM after_order_cases WHERE '+DELIVERY_ISSUE_QUEUE_SQL).fetchone()[0]

    def test_unresolved_customer_reports_only(self):
        self.assertEqual(1,self.count())
        for change in ["current_decision='received'", "current_decision=NULL", "status='resolved'", "case_type='tracking'"]:
            self.db.execute('SAVEPOINT scenario')
            self.db.execute('UPDATE after_order_cases SET '+change)
            self.assertEqual(0,self.count())
            self.db.execute('ROLLBACK TO scenario')

    def test_case_rollover_does_not_duplicate_or_hide_unanswered_report(self):
        self.db.execute("INSERT INTO after_order_cases SELECT 2,store_id,odoo_order_id,tracking_code,case_type,NULL,'needs_attention',NULL FROM after_order_cases WHERE id=1")
        self.assertEqual(1,self.count())
        self.db.execute("UPDATE after_order_cases SET current_decision='received',decision_updated_at='2026-09-20T02:00:00Z' WHERE id=2")
        self.assertEqual(0,self.count())
        self.db.execute("UPDATE after_order_cases SET current_decision='not_received' WHERE id=2")
        self.assertEqual(1,self.count())

    def test_other_parcel_or_store_does_not_clear_report(self):
        self.db.execute("INSERT INTO after_order_cases VALUES(2,2,100,'P1','delivery_confirmation','received','resolved','2026-09-21')")
        self.db.execute("INSERT INTO after_order_cases VALUES(3,1,100,'P2','delivery_confirmation','received','resolved','2026-09-21')")
        self.assertEqual(1,self.count())
