import json
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import main
from app.schemas.payloads import ManualAmazonOrderMatchPayload


class HistoryBindingGuardTests(unittest.TestCase):
    def row(self, id=1, asin="B00G3A1UGO", order="", **kw):
        return dict(id=id, asin=asin, amazon_order_id=order, state="ordered", **kw)

    def test_track_all_does_not_bind_sibling_asins(self):
        rows = [self.row(), self.row(2, "B0876DXBXN"), self.row(3, "B0FSG4V4H8")]
        allowed, skipped = main.safe_history_match_rows(rows, "114-1687848-6942605", {"B00G3A1UGO"})
        self.assertEqual([r["id"] for r in allowed], [1])
        self.assertEqual(len(skipped), 2)

    def test_exact_asin_cannot_replace_an_existing_order(self):
        allowed, skipped = main.safe_history_match_rows(
            [self.row(order="113-5692721-3297812")], "114-1687848-6942605", {"B00G3A1UGO"})
        self.assertEqual(allowed, [])
        self.assertIn("preserved", skipped[0]["reason"])

    def test_no_asin_evidence_never_assigns(self):
        self.assertEqual(main.safe_history_match_rows([self.row()], "111-1111111-1111111", set())[0], [])

    def test_replacement_requires_the_effective_asin(self):
        row = self.row(replacement_asin="B0876DXBXN")
        self.assertEqual(main.safe_history_match_rows([row], "111-1111111-1111111", {"B00G3A1UGO"})[0], [])
        self.assertEqual(len(main.safe_history_match_rows([row], "111-1111111-1111111", {"B0876DXBXN"})[0]), 1)

    def test_old_history_cannot_bind_a_replacement_run(self):
        row = self.row(replacement_run_id=3)
        self.assertEqual(main.safe_history_match_rows([row], "111-1111111-1111111", {row["asin"]})[0], [])

    def test_duplicate_asin_is_ambiguous(self):
        rows = [self.row(), self.row(2)]
        self.assertEqual(main.safe_history_match_rows(rows, "111-1111111-1111111", {"B00G3A1UGO"})[0], [])

    def test_configured_default_is_not_observed_account_identity(self):
        self.assertEqual(main.preserve_real_amazon_account_name("Default Amazon Business", "Amit"), "Amit")
        self.assertEqual(main.preserve_real_amazon_account_name("Amit", "Default Amazon Business"), "Amit")

    def test_cancelled_lines_stay_cancelled(self):
        row = self.row()
        row["state"] = "cancelled"
        self.assertEqual(main.safe_history_match_rows([row], "111-1111111-1111111", {row["asin"]})[0], [])

    def test_legacy_endpoint_preserves_delivery_and_other_purchases(self):
        raw = sqlite3.connect(":memory:")
        raw.row_factory = sqlite3.Row
        self.addCleanup(raw.close)
        raw.executescript("""
            CREATE TABLE amazon_order_history_unmatched (amazon_order_id TEXT,asins_json TEXT,status TEXT,amazon_account_name TEXT,amazon_account_type TEXT);
            CREATE TABLE order_lines (id INTEGER,store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,
                asin TEXT,amazon_order_id TEXT,amazon_order_url TEXT,amazon_account_name TEXT,amazon_account_type TEXT,order_engine TEXT,
                amazon_status TEXT,state TEXT,tracking_status TEXT,tracking_payload TEXT,missing_asin TEXT,
                last_error TEXT,ordered_at TEXT,updated_at TEXT,odoo_status_label TEXT);
        """)
        order = "114-1687848-6942605"
        raw.execute("INSERT INTO amazon_order_history_unmatched (amazon_order_id,asins_json,status) VALUES (?,?,?)", (order, '["B00G3A1UGO"]', "Delivered"))
        for id, asin, amazon_id in [(1,"B00G3A1UGO",order),(2,"B0876DXBXN",order),(3,"B0FSG4V4H8","113-5692721-3297812")]:
            raw.execute("""INSERT INTO order_lines
                (id,store_id,odoo_order_id,odoo_order_name,asin,amazon_order_id,amazon_account_name,
                 state,amazon_status,tracking_status,tracking_payload,last_error,order_engine)
                VALUES (?,1,24099,'NC24079',?,?, 'Amit','delivered','ordered','Delivered','parcel evidence','retained','chrome')""",
                (id,asin,amazon_id))

        class Conn:
            def execute(self, sql, params=()):
                return raw.execute(sql.replace("FOR UPDATE", ""), params)

        @contextmanager
        def database():
            yield Conn()

        payload = ManualAmazonOrderMatchPayload(amazon_order_id=order, order_names=["NC24079"],
            source_text="Nutricity NC24079 Multi", line_ids=[1,2,3], replace_existing=True, order_date="August 30, 2026")
        before = [dict(r) for r in raw.execute("SELECT * FROM order_lines ORDER BY id")]
        with patch.object(main, "db", database), patch.object(main, "clear_order_progress_caches"), patch.object(main.threading, "Timer"):
            result = main.api_manual_amazon_match(payload)
        after = [dict(r) for r in raw.execute("SELECT * FROM order_lines ORDER BY id")]
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(before[1:], after[1:])
        for field in ["state","amazon_status","tracking_status","tracking_payload","last_error"]:
            self.assertEqual(after[0][field], before[0][field])


if __name__ == "__main__":
    unittest.main()
