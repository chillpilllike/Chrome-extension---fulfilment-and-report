import unittest
import inspect
from unittest.mock import patch

from app import main


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


if __name__ == "__main__":
    unittest.main()
