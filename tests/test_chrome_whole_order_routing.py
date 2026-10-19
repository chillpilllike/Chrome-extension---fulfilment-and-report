import json
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from app import main


def line(line_id, asin, **changes):
    return {"id": line_id, "store_id": 1, "odoo_order_id": 32411,
            "asin": asin, "quantity": 1, "state": "submitted", **changes}


class ChromeWholeOrderRoutingTests(unittest.TestCase):
    def test_nc32436_remaining_asin_keeps_consumer_routing(self):
        remaining = line(2, "B0DVHW5GPK", quantity=2)
        conn = Mock()
        conn.execute.return_value.fetchall.return_value = [
            line(1, "B084DDY56X", state="delivered", amazon_order_id="114-0073650-8405851"), remaining,
        ]
        with patch.object(main, "chrome_account_type_routing_enabled", return_value=True):
            rows = main.chrome_order_routing_context(conn, [remaining])
            self.assertEqual(main.required_chrome_account_experience(rows), "consumer")
            self.assertFalse(main.chrome_account_experience_matches(rows, "business"))
            self.assertTrue(main.chrome_account_experience_matches(rows, "consumer"))

    def test_inventory_and_ignored_lines_do_not_create_another_purchasable_asin(self):
        remaining = line(2, "B0DVHW5GPK")
        for sibling in [line(1, "B084DDY56X", state="inventory"),
                        line(1, "B084DDY56X", state="delivered", inventory_allocated_quantity=1),
                        line(1, "B084DDY56X", state="ignored"),
                        line(1, "B0DVHW5GPK")]:
            with self.subTest(sibling=sibling), patch.object(main, "chrome_account_type_routing_enabled", return_value=True):
                conn = Mock()
                conn.execute.return_value.fetchall.return_value = [sibling, remaining]
                rows = main.chrome_order_routing_context(conn, [remaining])
                self.assertTrue(main.chrome_account_experience_matches(rows, "business"))
                self.assertFalse(main.chrome_account_experience_matches(rows, "consumer"))

    def test_missing_and_costly_siblings_keep_original_consumer_route(self):
        for state in ["missing", "costly", "error"]:
            conn = Mock()
            conn.execute.return_value.fetchall.return_value = [line(1, "B084DDY56X", state=state), line(2, "B0DVHW5GPK")]
            with self.subTest(state=state), patch.object(main, "chrome_account_type_routing_enabled", return_value=True):
                rows = main.chrome_order_routing_context(conn, [line(2, "B0DVHW5GPK")])
                self.assertEqual(main.required_chrome_account_experience(rows), "consumer")

    def test_submission_rejects_wrong_or_unverified_claim_account_for_both_shapes(self):
        for required, claimed in [("consumer", "business"), ("business", "consumer"), ("consumer", "")]:
            with self.subTest(required=required, claimed=claimed):
                conn = Mock()
                conn.execute.return_value.fetchall.return_value = [line(2, "B0DVHW5GPK")]
                conn.execute.return_value.fetchone.return_value = {
                    "request_json": json.dumps({"automatic_claim": False, "claim_account_experience": claimed}),
                }
                @contextmanager
                def db():
                    yield conn
                routing_rows = [line(2, "B0DVHW5GPK", _chrome_order_asin_count=2 if required == "consumer" else 1)]
                with patch.object(main, "db", db), patch.object(main, "ensure_chrome_job_owner"), patch.object(main, "chrome_account_type_routing_enabled", return_value=True), patch.object(main, "chrome_order_routing_context", return_value=routing_rows), patch.object(main, "filter_lines_by_live_odoo_status") as live:
                    with self.assertRaises(main.HTTPException) as caught:
                        main.api_chrome_job_submitted("group", main.ChromeJobHeartbeatPayload(worker_id="worker"))
                    self.assertEqual(caught.exception.status_code, 409)
                    self.assertIn(f"requires a {required}", caught.exception.detail)
                    live.assert_not_called()
                    self.assertFalse(any("UPDATE order_lines" in call.args[0] for call in conn.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
