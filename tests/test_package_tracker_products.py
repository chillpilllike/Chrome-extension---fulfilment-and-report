import json
import unittest

from app.main import (
    package_tracker_enforce_product_guard,
    package_tracker_fallback_asins_by_package,
    package_tracker_quantity_analysis,
    package_tracker_single_package_history_products,
)


class PackageTrackerProductTests(unittest.TestCase):
    def test_single_package_uses_every_amazon_history_item(self):
        history = {
            "items_json": json.dumps([
                {"asin": "B000000001", "title": "First item"},
                {"asin": "B000000002", "title": "Second item"},
            ])
        }

        products = package_tracker_single_package_history_products([{"id": 1}], history)

        self.assertEqual([product["asin"] for product in products], ["B000000001", "B000000002"])

    def test_split_shipments_do_not_repeat_full_order_history(self):
        history = {"items_json": json.dumps([{"asin": "B000000001", "title": "First item"}])}

        products = package_tracker_single_package_history_products([{"id": 1}, {"id": 2}], history)

        self.assertEqual(products, [])

    def test_guard_restores_missing_associated_product_once(self):
        packages = [
            {"amazon_order_id": "111-1", "products": [{"asin": "B000000001", "title": "First"}]},
            {"amazon_order_id": "111-1", "products": [{"asin": "B000000002", "title": "Second"}]},
        ]
        history = {
            "111-1": {
                "items_json": json.dumps([
                    {"asin": "B000000001", "title": "First"},
                    {"asin": "B000000002", "title": "Second"},
                    {"asin": "B000000003", "title": "Third"},
                ])
            }
        }

        guard = package_tracker_enforce_product_guard(packages, history)

        self.assertTrue(guard["complete"])
        self.assertEqual(guard["expected_products"], 3)
        self.assertEqual(guard["displayed_products"], 3)
        self.assertEqual(guard["recovered_products"], 1)
        displayed = [product["asin"] for package in packages for product in package["products"]]
        self.assertEqual(displayed.count("B000000003"), 1)

    def test_guard_blocks_package_without_any_product_association(self):
        packages = [{"amazon_order_id": "111-2", "products": []}]

        guard = package_tracker_enforce_product_guard(packages, {})

        self.assertFalse(guard["complete"])
        self.assertEqual(guard["unverified_amazon_orders"], ["111-2"])

    def test_split_shipments_do_not_repeat_order_level_fallback_asins(self):
        packages = [
            {"id": 10, "package_index": 1, "amazon_order_id": "111-3", "asins_json": '["B000000001"]', "products_json": "[]"},
            {"id": 11, "package_index": 2, "amazon_order_id": "111-3", "asins_json": '["B000000001"]', "products_json": "[]"},
        ]
        lines = [{"amazon_order_id": "111-3", "asin": "B000000001", "quantity": 1}]

        allocations = package_tracker_fallback_asins_by_package(packages, lines, "111-3")

        self.assertEqual(allocations, {10: ["B000000001"], 11: []})
        analysis = package_tracker_quantity_analysis(lines, packages, {})
        self.assertFalse(analysis["suspected_duplicate"])

    def test_split_shipments_keep_repeated_asin_when_odoo_quantity_proves_two_units(self):
        packages = [
            {"id": 20, "package_index": 1, "amazon_order_id": "111-4", "asins_json": '["B000000002"]', "products_json": "[]"},
            {"id": 21, "package_index": 2, "amazon_order_id": "111-4", "asins_json": '["B000000002"]', "products_json": "[]"},
        ]
        lines = [{"amazon_order_id": "111-4", "asin": "B000000002", "quantity": 2}]

        allocations = package_tracker_fallback_asins_by_package(packages, lines, "111-4")

        self.assertEqual(allocations, {20: ["B000000002"], 21: ["B000000002"]})


if __name__ == "__main__":
    unittest.main()
