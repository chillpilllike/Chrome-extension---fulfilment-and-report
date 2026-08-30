import inspect
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import main
from app.core.config import DEFAULT_SERVICE_SETTINGS


class _Cursor:
    def fetchall(self):
        return []


class _Connection:
    def execute(self, _sql, _params=()):
        return _Cursor()


class OdooShopifyCancellationTests(unittest.TestCase):
    def test_exact_active_shopify_match_is_cancelled_and_verified(self) -> None:
        client = SimpleNamespace(name="DTC Store", shop="dtc.myshopify.com", cancel_order=Mock())
        active = {
            "id": "9001",
            "name": "#NC24703",
            "cancelled_at": "",
            "fulfillment_status": "UNFULFILLED",
        }
        verified = {**active, "cancelled_at": "2026-08-31T00:00:00Z"}
        with (
            patch.object(main, "db", return_value=nullcontext(_Connection())),
            patch.object(main, "get_store", return_value=SimpleNamespace(odoo_db="nutricity")),
            patch.object(main, "shopify_clients_for_route", side_effect=lambda route: [client] if route == "dtc" else []),
            patch.object(main, "shopify_orders_by_name", return_value=[active]),
            patch.object(main, "shopify_order_by_id", return_value=verified),
            patch.object(main, "upsert_shopify_order_status") as upsert,
            patch.object(main, "record_odoo_shopify_cancellation_result") as record,
            patch.object(main, "fast_page_cache_clear_matching"),
        ):
            result = main.cancel_shopify_orders_for_odoo_order(1, 24703, "NC24703")

        client.cancel_order.assert_called_once_with(9001, restock=False, refund=False, reason="other")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["cancelled"], 1)
        upsert.assert_called_once()
        record.assert_called_once()

    def test_fulfilled_shopify_match_is_left_for_manual_review(self) -> None:
        client = SimpleNamespace(name="DTB Store", shop="dtb.myshopify.com", cancel_order=Mock())
        fulfilled = {
            "id": "9002",
            "name": "NC24703",
            "cancelled_at": "",
            "fulfillment_status": "FULFILLED",
        }
        with (
            patch.object(main, "db", return_value=nullcontext(_Connection())),
            patch.object(main, "get_store", return_value=SimpleNamespace(odoo_db="nutricity")),
            patch.object(main, "shopify_clients_for_route", side_effect=lambda route: [client] if route == "dtb" else []),
            patch.object(main, "shopify_orders_by_name", return_value=[fulfilled]),
            patch.object(main, "upsert_shopify_order_status"),
            patch.object(main, "record_odoo_shopify_cancellation_result"),
            patch.object(main, "fast_page_cache_clear_matching"),
        ):
            result = main.cancel_shopify_orders_for_odoo_order(1, 24703, "NC24703")

        client.cancel_order.assert_not_called()
        self.assertEqual(result["status"], "manual_review")
        self.assertEqual(result["protected"], 1)
        self.assertFalse(result["ok"])

    def test_manual_action_cancels_odoo_before_shopify(self) -> None:
        source = inspect.getsource(main.api_cancel_odoo_and_shopify_orders)
        self.assertLess(source.index('"action_cancel"'), source.index("cancel_shopify_orders_for_odoo_order("))
        self.assertIn('live_order.get("state")', source)
        self.assertIn("mark_cancelled_odoo_order_locally", source)

    def test_automatic_sync_retries_only_incomplete_shopify_cancellations(self) -> None:
        source = inspect.getsource(main.sync_cancelled_orders_for_store)
        scheduler = inspect.getsource(main.autosync_loop)
        self.assertIn("odoo_shopify_cancellation_needs_sync", source)
        self.assertIn("cancel_shopify_orders_for_odoo_order", source)
        self.assertIn("max(15", scheduler)
        self.assertEqual(DEFAULT_SERVICE_SETTINGS["cancelled_orders_sync_interval_minutes"], "15")

    def test_orders_ui_has_confirmed_bulk_cancellation_action(self) -> None:
        frontend = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
        self.assertIn("Cancel Odoo + Shopify", frontend)
        self.assertIn('/api/lines/cancel-odoo-shopify', frontend)
        self.assertIn("Already-fulfilled Shopify orders will be left unchanged", frontend)
        self.assertIn("Odoo → Shopify Cancellation Check", frontend)


if __name__ == "__main__":
    unittest.main()
