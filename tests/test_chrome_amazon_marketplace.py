from contextlib import contextmanager
import unittest
from unittest.mock import Mock, patch

from app import main
from app.services.asin import amazon_india_product_asins

ASIN = "B000123456"
INDIA = f"https://www.amazon.in/gp/product/{ASIN}?th=1"


class IndiaMarketplaceGuard(unittest.TestCase):
    def sourced_row(self, **values):
        return {"id": 7, "store_id": 1, "asin": ASIN, "odoo_order_name": "NC 42652", **values}

    def test_matches_real_marketplace_and_html_link_without_matching_lookalikes(self):
        self.assertEqual(amazon_india_product_asins(f'<a href="{INDIA}">Source</a>'), {ASIN})
        for url in (
            f"https://amazon.in.example.org/dp/{ASIN}", f"https://amazon.com/dp/{ASIN}",
            f"https://example.org/amazon.in/dp/{ASIN}", f"https://amazon.in@example.org/dp/{ASIN}",
        ):
            self.assertFalse(amazon_india_product_asins(url), url)

    def test_imported_html_href_in_raw_template_is_retained_as_marketplace_evidence(self):
        row = self.sourced_row(raw_json={"combined_source_lines": [
            {"template": {"description": f'<a href="{INDIA}">Source</a>'}},
        ]})
        self.assertIn("Amazon India", main.chrome_amazon_india_source_message([row]))

    def test_ignores_unrelated_product_links_in_shared_order_notes(self):
        row = self.sourced_row(asin="B000999999", raw_json={"order": {"note": INDIA}})
        self.assertFalse(main.chrome_amazon_india_source_message([row]))

    def test_india_group_is_not_written_to_the_us_queue(self):
        connection = Mock()
        group = {"test": [self.sourced_row(internal_note=INDIA)]}
        count, messages = main.persist_chrome_order_groups(connection, group, {}, 1)
        self.assertEqual(count, 0)
        self.assertIn("Amazon India", messages[0])
        self.assertEqual(connection.mock_calls, [])

    def test_already_queued_india_group_is_blocked_before_place_order(self):
        connection = Mock()
        connection.execute.return_value.fetchall.return_value = [self.sourced_row(internal_note=INDIA)]
        @contextmanager
        def db():
            yield connection
        with patch.object(main, "db", db), patch.object(main, "ensure_chrome_job_owner"):
            with self.assertRaises(main.HTTPException) as caught:
                main.api_chrome_job_submitted("job", main.ChromeJobHeartbeatPayload(worker_id="worker"))
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("Amazon India", caught.exception.detail)
        self.assertEqual(connection.execute.call_count, 1)
