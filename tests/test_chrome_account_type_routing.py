import unittest
import inspect
import pathlib
from unittest.mock import patch

from app import main


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "frontend" / "src" / "App.tsx").read_text()


class ChromeAccountTypeRoutingTests(unittest.TestCase):
    def test_auto_queue_does_not_requeue_already_submitted_jobs(self) -> None:
        source = inspect.getsource(main.place_orders)
        self.assertIn('if ordering_engine == "chrome":', source)
        self.assertIn('clean_text(line["state"]) != "submitted"', source)

    def test_disabled_routing_accepts_either_detected_type(self) -> None:
        rows = [{"id": 1}, {"id": 2}]
        with patch.object(main, "get_service_settings", return_value={"chrome_route_orders_by_account_type": "false"}):
            self.assertEqual(main.required_chrome_account_experience(rows), "any")
            self.assertTrue(main.chrome_account_experience_matches(rows, "consumer"))
            self.assertTrue(main.chrome_account_experience_matches(rows, "business"))

    def test_enabled_routing_maps_multiple_lines_to_consumer(self) -> None:
        rows = [{"id": 1}, {"id": 2}]
        with patch.object(main, "get_service_settings", return_value={"chrome_route_orders_by_account_type": "true"}):
            self.assertEqual(main.required_chrome_account_experience(rows), "consumer")
            self.assertTrue(main.chrome_account_experience_matches(rows, "consumer"))
            self.assertFalse(main.chrome_account_experience_matches(rows, "business"))

    def test_enabled_routing_maps_single_line_to_business_even_for_quantity(self) -> None:
        rows = [{"id": 1, "quantity": 8}]
        with patch.object(main, "get_service_settings", return_value={"chrome_route_orders_by_account_type": "true"}):
            self.assertEqual(main.required_chrome_account_experience(rows), "business")
            self.assertTrue(main.chrome_account_experience_matches(rows, "business"))
            self.assertFalse(main.chrome_account_experience_matches(rows, "consumer"))

    def test_original_multi_line_order_stays_consumer_after_inventory_reduces_amazon_rows(self) -> None:
        rows = [{"id": 1, "quantity": 2, "source_line_count": 3}]
        with patch.object(main, "get_service_settings", return_value={"chrome_route_orders_by_account_type": "true"}):
            self.assertEqual(main.chrome_source_line_item_count(rows), 3)
            self.assertEqual(main.required_chrome_account_experience(rows), "consumer")

    def test_claim_filters_account_type_before_candidate_limit(self) -> None:
        source = inspect.getsource(main.claim_next_chrome_job)
        account_filter = source.index("? = 'consumer'")
        candidate_limit = source.index("LIMIT 250", account_filter)
        self.assertLess(account_filter, candidate_limit)
        self.assertIn("COUNT(*) = 1", source[account_filter:candidate_limit])
        self.assertIn("source_line_count", source[account_filter:candidate_limit])

    def test_queue_snapshot_filters_jobs_and_count_by_account_type(self) -> None:
        source = inspect.getsource(main.chrome_queue_snapshot)
        self.assertIn("account_experience", source)
        self.assertGreaterEqual(source.count("? = 'consumer'"), 2)
        self.assertGreaterEqual(source.count("? = 'business'"), 2)
        self.assertGreaterEqual(source.count("source_line_count"), 2)

        endpoint_source = inspect.getsource(main.api_chrome_jobs)
        self.assertIn("account_experience=account_experience", endpoint_source)

    def test_auto_order_start_date_is_inclusive_and_blocks_older_orders(self) -> None:
        self.assertTrue(main.chrome_order_is_on_or_after_start_date(
            [{"odoo_order_date": "2026-08-25T00:00:00+00:00"}],
            "2026-08-25",
        ))
        self.assertTrue(main.chrome_order_is_on_or_after_start_date(
            [{"odoo_order_date": "2026-08-24T20:00:00+00:00"}],
            "2026-08-25",
        ))
        self.assertFalse(main.chrome_order_is_on_or_after_start_date(
            [{"odoo_order_date": "2026-08-24T18:00:00+00:00"}],
            "2026-08-25",
        ))
        self.assertFalse(main.chrome_order_is_on_or_after_start_date(
            [{"odoo_order_date": ""}],
            "2026-08-25",
        ))

    def test_pause_resets_only_unsubmitted_queue_and_blocks_claims(self) -> None:
        pause_source = inspect.getsource(main.api_pause_chrome_auto_ordering)
        reset_source = inspect.getsource(main.reset_unsubmitted_chrome_queue)
        claim_source = inspect.getsource(main.api_chrome_jobs)
        self.assertIn('set_setting("auto_chrome_fulfil_enabled", "false")', pause_source)
        self.assertLess(
            pause_source.index('set_setting("auto_chrome_fulfil_enabled", "false")'),
            pause_source.index("with _AUTO_CHROME_QUEUE_CONTROL_LOCK"),
        )
        self.assertIn("reset_unsubmitted_chrome_queue", pause_source)
        self.assertIn("NOT IN ('order_submitted', 'reporting_complete')", reset_source)
        self.assertIn("if not auto_chrome_ordering_enabled()", claim_source)

    def test_resume_requires_date_and_ui_prompts_for_it(self) -> None:
        resume_source = inspect.getsource(main.api_resume_chrome_auto_ordering)
        scheduler_source = inspect.getsource(main.autosync_loop)
        place_source = inspect.getsource(main.place_orders)
        self.assertIn("Choose a valid auto-ordering start date", resume_source)
        self.assertIn('set_setting("auto_chrome_fulfil_last_run_at", "")', resume_source)
        self.assertIn("minimum_odoo_order_date=start_date", scheduler_source)
        self.assertLess(
            place_source.index("eligible_date_order_ids"),
            place_source.index("block_selected_orders_with_existing_amazon_orders", place_source.index("eligible_date_order_ids")),
        )
        self.assertIn("Pause Auto Ordering & Reset Queue", FRONTEND)
        self.assertIn("Queue eligible Odoo orders since", FRONTEND)


if __name__ == "__main__":
    unittest.main()
