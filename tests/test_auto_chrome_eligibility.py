import json
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from app import main


def line(line_id=1, **changes):
    return {
        "id": line_id, "store_id": 1, "odoo_order_id": 32456,
        "odoo_order_name": "NC32436", "odoo_order_date": "2026-10-18 12:00:00",
        "asin": f"B{line_id:09d}", "quantity": 2, "state": "submitted",
        "amazon_order_id": "", "amazon_status": "chrome_queued",
        "inventory_allocated_quantity": 0, **changes,
    }


class AutoChromeEligibilityTests(unittest.TestCase):
    def reason(self, selected, siblings=None, start_date="2026-10-18"):
        return main.automatic_chrome_order_block_reason(selected, siblings or selected, start_date)

    def test_cutoff_inclusive_and_not_import_date(self):
        self.assertEqual(self.reason([line()]), "")
        old = line(odoo_order_date="2026-10-06 12:00:00", pulled_at="2026-10-19T12:00:00Z")
        self.assertIn("predates", self.reason([old]))
        self.assertIn("cannot be verified", self.reason([line(odoo_order_date="")]))
        self.assertIn("valid auto-ordering start date", self.reason([line()], start_date=""))

    def test_duplicate_sibling_blocks_whole_order(self):
        for state, amazon_id in [("ordered", "111-1234567-1234567"), ("pulled", "111-1234567-1234567"), ("delivered", "")]:
            with self.subTest(state=state):
                self.assertIn("already has purchased", self.reason([line()], [line(), line(2, state=state, amazon_order_id=amazon_id)]))

    def test_inventory_fulfilled_order_cannot_be_reordered_after_bad_refresh(self):
        for state in ["inventory", "delivered", "pulled", "submitted"]:
            with self.subTest(state=state):
                self.assertTrue(self.reason([line(state=state, inventory_allocated_quantity=2)]))

    def test_all_remaining_items_are_required_for_both_account_shapes(self):
        first, second = line(), line(2)
        self.assertEqual(self.reason([first]), "")
        self.assertEqual(self.reason([first, second]), "")
        for state in ["missing", "costly", "error", "pulled", "submitted"]:
            with self.subTest(state=state):
                self.assertIn("only part", self.reason([first], [first, line(2, state=state)]))
        self.assertIn("no valid ASIN", self.reason([first], [first, line(2, asin="", state="pulled")]))

    def test_inventory_and_ignored_siblings_need_no_amazon_purchase(self):
        first = line(inventory_allocated_quantity=1)
        siblings = [first, line(2, state="inventory", inventory_allocated_quantity=2), line(3, state="ignored")]
        self.assertEqual(self.reason([first], siblings), "")
        self.assertEqual(main.inventory_purchase_quantity(first), 1)

    def test_refresh_preserves_fulfilled_and_ignored_states_without_amazon_id(self):
        for state in ["inventory", "ignored", "ordered", "dispatched", "delivered"]:
            with self.subTest(state=state):
                self.assertEqual(main.preserved_order_line_refresh_state({"state": state, "last_error": "keep"}), (state, "keep"))

    def test_live_ordered_tag_blocks_automatic_but_manual_still_works(self):
        client = Mock()
        client.existing_fields.return_value = ["id", "tag_ids", "date_order"]
        client.read.return_value = [{"id": 32456, "name": "NC32436", "state": "sale", "date_order": "2026-10-18 12:00:00"}]
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = []
        def tags(_client, _orders, name):
            return {32456} if name == main.ODOO_ORDERED_TAG_NAME else set()
        with patch.object(main, "get_store"), patch.object(main, "OdooClient", return_value=client), patch.object(main, "odoo_order_ids_with_tag", side_effect=tags), patch.object(main, "fast_page_cache_clear_matching"), patch.object(main, "get_service_settings", return_value={"auto_chrome_fulfil_start_date": "2026-10-18"}):
            allowed, blocked, _ = main.filter_lines_by_live_odoo_status(conn, 1, [line()], automatic=True)
            self.assertEqual((allowed, blocked), ([], 1))
            allowed, blocked, _ = main.filter_lines_by_live_odoo_status(conn, 1, [line()])
            self.assertEqual((len(allowed), blocked), (1, 0))

    def test_cutoff_rechecked_against_live_odoo_date(self):
        client = Mock()
        client.existing_fields.return_value = ["id", "tag_ids", "date_order"]
        client.read.return_value = [{"id": 32456, "name": "NC32436", "state": "sale", "date_order": "2026-10-06 12:00:00"}]
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = []
        with patch.object(main, "get_store"), patch.object(main, "OdooClient", return_value=client), patch.object(main, "odoo_order_ids_with_tag", return_value=set()), patch.object(main, "fast_page_cache_clear_matching"), patch.object(main, "get_service_settings", return_value={"auto_chrome_fulfil_start_date": "2026-10-18"}):
            self.assertEqual(main.filter_lines_by_live_odoo_status(conn, 1, [line()], automatic=True)[1], 1)

    def test_api_distinguishes_manual_claims_and_saves_provenance(self):
        conn = Mock()
        @contextmanager
        def db():
            yield conn
        for manual in [True, False]:
            with self.subTest(manual=manual), patch.object(main, "db", db), patch.object(main, "auto_chrome_ordering_enabled", return_value=True), patch.object(main, "reconcile_exact_submitted_chrome_jobs_from_history", return_value=[]), patch.object(main, "repair_missing_chrome_order_chatter", return_value=[]), patch.object(main, "claim_next_chrome_job", return_value={"group_key": "test"}) as claim, patch.object(main, "exact_amazon_history_match_for_chrome_job", return_value=None):
                main.api_chrome_jobs(manual_claim=manual)
                self.assertEqual(claim.call_args.kwargs["automatic"], not manual)
                self.assertEqual(conn.execute.call_args.args[1], (not manual, "test"))

    def test_final_submission_rechecks_automatic_policy_and_never_marks_blocked_job_submitted(self):
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [line()]
        conn.execute.return_value.fetchone.return_value = {"request_json": json.dumps({"automatic_claim": True})}
        @contextmanager
        def db():
            yield conn
        with patch.object(main, "db", db), patch.object(main, "ensure_chrome_job_owner"), patch.object(main, "auto_chrome_ordering_enabled", return_value=True), patch.object(main, "filter_automatic_chrome_orders", return_value=([], 1, ["Order already fulfilled"])) as check, patch.object(main, "filter_lines_by_live_odoo_status") as live:
            with self.assertRaises(main.HTTPException) as caught:
                main.api_chrome_job_submitted("test", main.ChromeJobHeartbeatPayload(worker_id="worker"))
            self.assertEqual(caught.exception.status_code, 409)
            check.assert_called_once()
            live.assert_not_called()
            self.assertFalse(any("UPDATE order_lines" in call.args[0] for call in conn.execute.call_args_list))

    def test_failed_existing_purchase_report_never_returns_order_for_checkout(self):
        with patch.object(main, "auto_chrome_ordering_enabled", return_value=True), patch.object(main, "reconcile_exact_submitted_chrome_jobs_from_history", return_value=[]), patch.object(main, "repair_missing_chrome_order_chatter", return_value=[]), patch.object(main, "claim_next_chrome_job", return_value={"group_key": "test"}), patch.object(main, "exact_amazon_history_match_for_chrome_job", return_value={"amazon_order_id": "111-1234567-1234567"}), patch.object(main, "complete_chrome_job_from_exact_history_match", side_effect=RuntimeError("report interrupted")):
            self.assertEqual(main.api_chrome_jobs()["jobs"], [])


if __name__ == "__main__":
    unittest.main()
