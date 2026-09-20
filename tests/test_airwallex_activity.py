import sqlite3
import unittest
from app.services.airwallex_hub import DEPOSIT_ACTIVITY_CTE

class DepositActivityTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.execute('CREATE TABLE airwallex_webhook_events(id INTEGER,event_id TEXT,deposit_id TEXT,event_name TEXT,received_at TEXT,processing_status TEXT)')
    def add(self,id,deposit,status,processing='matched'):
        self.db.execute('INSERT INTO airwallex_webhook_events VALUES(?,?,?,?,?,?)',(id,str(id),deposit,'deposit.'+status,f'2026-09-20T10:00:{id:02d}',processing))
    def rows(self,where=''):
        return self.db.execute(DEPOSIT_ACTIVITY_CTE+' SELECT * FROM current_deposits '+where+' ORDER BY id').fetchall()
    def test_delayed_pending_does_not_replace_settlement(self):
        self.add(1,'a','settled');self.add(2,'a','pending','review')
        rows=self.rows();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['id'],1)
        self.assertEqual(rows[0]['status_update_count'],2)
        self.assertEqual(len(self.rows("WHERE processing_status='review'")),0)
    def test_reversal_replaces_settlement(self):
        self.add(1,'a','pending');self.add(2,'a','settled');self.add(3,'a','reversed')
        self.assertEqual(self.rows()[0]['id'],3)
    def test_distinct_payments_and_missing_ids_are_not_merged(self):
        self.add(1,'a','settled');self.add(2,'b','settled');self.add(3,'','pending');self.add(4,None,'pending')
        self.assertEqual(len(self.rows()),4)
    def test_count_and_pagination_are_payments(self):
        for i in range(1,7):self.add(i,str((i-1)//2),'pending' if i%2 else 'settled')
        count=self.db.execute(DEPOSIT_ACTIVITY_CTE+' SELECT COUNT(*) FROM current_deposits').fetchone()[0]
        self.assertEqual(count,3)
        rows=self.db.execute(DEPOSIT_ACTIVITY_CTE+' SELECT * FROM current_deposits ORDER BY id LIMIT 2 OFFSET 2').fetchall()
        self.assertEqual([r['id'] for r in rows],[6])
