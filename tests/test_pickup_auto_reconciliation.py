import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from app.main import ensure_package_pickup_scan_history_table, reconcile_package_pickup_scans, reset_package_pickup_scan_event


class TestPickupAutoReconciliation(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.enterContext = stack.enter_context
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row

        class Connection:
            def execute(self, sql, params=()):
                return raw.execute(sql.replace("FOR UPDATE SKIP LOCKED", "").replace("FOR UPDATE", "").replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN"), params)

        self.conn = Connection()
        self.addCleanup(raw.close)
        ensure_package_pickup_scan_history_table(self.conn)
        self.conn.execute("""CREATE TABLE amazon_dispatch_packages (
            id INTEGER PRIMARY KEY, store_id INTEGER, scan_code TEXT, canonical_scan_code TEXT,
            odoo_order_name TEXT, amazon_order_id TEXT, package_status TEXT, promise TEXT,
            updated_at TEXT, received_at TEXT, not_received_at TEXT, scanned_codes_json TEXT)""")
        self.conn.execute("""CREATE TABLE package_pickup_delivery_records (
            package_id INTEGER PRIMARY KEY, delivered_at TEXT, scanned_at TEXT, last_scanned_at TEXT,
            scanned_code TEXT, scan_count INTEGER, updated_at TEXT, created_at TEXT)""")
        self.conn.execute("""CREATE TABLE package_pickup_checks (
            store_id INTEGER, pickup_date TEXT, amazon_picked_up INTEGER, non_amazon_picked_up INTEGER,
            created_at TEXT, updated_at TEXT, PRIMARY KEY(store_id,pickup_date))""")
        self.conn.execute("""INSERT INTO amazon_dispatch_packages
            (id,store_id,scan_code,canonical_scan_code,odoo_order_name,amazon_order_id,package_status,updated_at)
            VALUES (50622,1,'TBA334200206835','TBA334200206835','NC24335','111-4064278-0332216','Delivered September 1','2026-09-02T18:52:47+00:00')""")
        self.conn.execute("""INSERT INTO package_pickup_scan_events
            (id,store_id,package_id,scan_code,result_status,message,scanned_at)
            VALUES (194,1,50622,'TBA334200206835','not_delivered','Old failure','2026-09-02T15:05:51+00:00')""")
        self.payment = self.enterContext(patch("app.main.amazon_order_has_open_payment_failure", return_value=False))
        self.matches = self.enterContext(patch("app.main.package_pickup_strict_scan_matches", side_effect=self.lookup))
        self.enterContext(patch("app.main.fast_page_cache_clear_matching"))
        self.enterContext(patch("app.main.package_pickup_order_readiness", return_value={
            "ready_to_ship": True, "total_packages": 1, "received_packages": 1,
            "remaining_packages": 0, "message": "All received", "pending_packages": [],
        }))

    def lookup(self, conn, code, store):
        return [dict(x) for x in conn.execute("SELECT * FROM amazon_dispatch_packages WHERE scan_code=? AND (? IS NULL OR store_id=?)", (code,store,store)).fetchall()]

    def event(self):
        return dict(self.conn.execute("SELECT * FROM package_pickup_scan_events WHERE id=194").fetchone())

    def test_delivered_refresh_reconciles_once_and_keeps_original_evidence(self):
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 1)
        event = self.event()
        self.assertEqual(event["result_status"], "matched_after_tracking_refresh")
        self.assertEqual(event["original_result_status"], "not_delivered")
        self.assertEqual(event["original_message"], "Old failure")
        self.assertEqual(event["scanned_at"], "2026-09-02T15:05:51+00:00")
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)
        self.assertEqual(self.conn.execute("SELECT amazon_picked_up FROM package_pickup_checks").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT scanned_at FROM package_pickup_delivery_records").fetchone()[0], event["scanned_at"])

    def test_previously_unknown_exact_barcode_links_when_tracking_arrives(self):
        self.conn.execute("UPDATE package_pickup_scan_events SET package_id=NULL,store_id=0,result_status='not_found'")
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 1)
        self.assertEqual(self.event()["odoo_order_name"], "NC24335")
        self.assertEqual(self.event()["store_id"], 1)

    def test_unknown_and_partial_and_wrong_store_are_not_guessed(self):
        for code,store in [('TBA334213621321',0),('06835',0),('TBA334200206835',2)]:
            with self.subTest(code=code,store=store):
                self.conn.execute("UPDATE package_pickup_scan_events SET scan_code=?,store_id=?", (code,store))
                self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_ambiguous_and_reassigned_packages_stay_unresolved(self):
        package = self.lookup(self.conn,'TBA334200206835',1)[0]
        self.matches.side_effect = None
        self.matches.return_value = [package, {**package,"id":999}]
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)
        self.matches.return_value = [{**package,"id":999}]
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_undone_scans_are_never_reactivated(self):
        self.conn.execute("UPDATE package_pickup_scan_events SET undone_at='2026-09-02T18:00:00Z'")
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_not_delivered_or_delivery_after_scan_stays_unresolved(self):
        for status in ['Arriving tomorrow','Delivered September 3']:
            with self.subTest(status=status):
                self.conn.execute("UPDATE amazon_dispatch_packages SET package_status=?", (status,))
                self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_payment_failure_stays_unresolved(self):
        self.payment.return_value = True
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_later_not_received_decision_is_not_overridden(self):
        self.conn.execute("UPDATE amazon_dispatch_packages SET not_received_at='2026-09-02T18:00:00+00:00'")
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)

    def test_existing_receipt_does_not_increment_counts_or_get_backdated(self):
        self.conn.execute("INSERT INTO package_pickup_delivery_records(package_id,scanned_at,scan_count) VALUES(50622,'2026-09-02T16:00:00Z',1)")
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 1)
        self.assertEqual(self.event()["duplicate"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM package_pickup_checks").fetchone()[0], 0)
        reset_package_pickup_scan_event(self.conn,self.event(),'2026-09-02T19:00:00Z','test','2026-09-02')
        self.assertEqual(self.conn.execute("SELECT scan_count FROM package_pickup_delivery_records").fetchone()[0], 1)
        self.assertEqual(reconcile_package_pickup_scans(self.conn), 0)


if __name__ == '__main__':
    unittest.main()
