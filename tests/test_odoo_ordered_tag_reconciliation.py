import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.main import reconcile_missing_odoo_ordered_tags


class OdooOrderedTagReconciliationTests(unittest.TestCase):
    def test_only_missing_ordered_tags_are_written(self) -> None:
        candidates = [
            {"store_id": 1, "odoo_order_id": 24067, "odoo_order_name": "NC24047"},
            {"store_id": 1, "odoo_order_id": 24068, "odoo_order_name": "NC24048"},
        ]
        odoo = Mock()
        odoo.search_read.return_value = [
            {"id": 24067, "name": "NC24047", "tag_ids": []},
            {"id": 24068, "name": "NC24048", "tag_ids": [77]},
        ]

        with (
            patch("app.main.recent_app_ordered_odoo_orders", return_value=candidates),
            patch("app.main.get_store", return_value=SimpleNamespace(id=1)),
            patch("app.main.OdooClient", return_value=odoo),
            patch("app.main.ensure_odoo_order_tag", return_value=77),
        ):
            result = reconcile_missing_odoo_ordered_tags(days=30)

        odoo.write.assert_called_once_with("sale.order", [24067], {"tag_ids": [(4, 77)]})
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["repaired"], 1)
        self.assertEqual(result["missing_orders"], ["NC24047"])
        self.assertEqual(result["errors"], [])

    def test_transient_store_failure_is_returned_for_next_scheduled_retry(self) -> None:
        candidates = [{"store_id": 1, "odoo_order_id": 24067, "odoo_order_name": "NC24047"}]
        with (
            patch("app.main.recent_app_ordered_odoo_orders", return_value=candidates),
            patch("app.main.get_store", side_effect=RuntimeError("temporary Odoo outage")),
        ):
            result = reconcile_missing_odoo_ordered_tags()

        self.assertEqual(result["repaired"], 0)
        self.assertEqual(result["errors"][0]["store_id"], 1)
        self.assertIn("temporary Odoo outage", result["errors"][0]["error"])


if __name__ == "__main__":
    unittest.main()
