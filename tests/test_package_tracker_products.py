import json
import unittest

from app.main import (
    canonical_tracking_packages,
    compact_tracking_products,
    dispatch_codes_from_package,
    package_tracker_canonical_dispatch_rows,
    package_tracker_enforce_product_guard,
    package_tracker_fallback_asins_by_package,
    package_tracker_history_products_by_package,
    package_tracker_quantity_analysis,
    package_tracker_single_package_history_products,
    package_tracker_tracking_parts_from_lines,
)


class PackageTrackerProductTests(unittest.TestCase):
    def test_line_payloads_preserve_distinct_pre_tracking_shipments(self) -> None:
        first = {
            "tracking_payload": '[{"tracking_url":"https://www.amazon.com/progress-tracker/package?shipmentId=one","promise":"Arriving Tuesday","asins":["B000000001"]}]'
        }
        duplicate_first = dict(first)
        second = {
            "tracking_payload": '[{"tracking_url":"https://www.amazon.com/progress-tracker/package?itemId=two","promise":"Arriving tomorrow","asins":["B000000002"]}]'
        }

        parts = package_tracker_tracking_parts_from_lines([first, duplicate_first, second])

        self.assertEqual(len(parts), 2)
        self.assertEqual([part["promise"] for part in parts], ["Arriving Tuesday", "Arriving tomorrow"])

    def test_amazon_shipment_urls_get_distinct_stable_package_keys(self):
        first = dispatch_codes_from_package(
            {"tracking_url": "https://www.amazon.com/progress-tracker/package?orderId=113-1&shipmentId=FIRST"},
            "113-0000000-0000000",
        )
        second = dispatch_codes_from_package(
            {"tracking_url": "https://www.amazon.com/progress-tracker/package?orderId=113-1&shipmentId=SECOND"},
            "113-0000000-0000000",
        )

        self.assertTrue(first[0][0].startswith("AMZPKG-"))
        self.assertNotEqual(first[0][0], second[0][0])

    def test_order_number_is_not_a_physical_package_key(self):
        codes = dispatch_codes_from_package(
            {"tracking_url": "https://www.amazon.com/your-orders/order-details?orderID=113-0000000-0000000"},
            "113-0000000-0000000",
        )

        self.assertEqual(codes, [])

    def test_status_shadow_is_removed_when_real_shipment_exists(self):
        packages = canonical_tracking_packages([
            {"status_only": True, "tracking_url": "https://www.amazon.com/your-orders/order-details?orderID=113-1"},
            {"tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=REAL"},
        ])
        rows = package_tracker_canonical_dispatch_rows([
            {"amazon_order_id": "113-1111111-1111111", "scan_code": "11311111111111111", "tracking_url": "https://www.amazon.com/your-orders/order-details?orderID=113-1111111-1111111"},
            {"amazon_order_id": "113-1111111-1111111", "scan_code": "AMZPKG-ABC", "tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=REAL"},
        ])

        self.assertEqual(len(packages), 1)
        self.assertIn("shipmentId=REAL", packages[0]["tracking_url"])
        self.assertEqual([row["scan_code"] for row in rows], ["AMZPKG-ABC"])

    def test_verified_quantity_survives_storage_compaction(self):
        products = compact_tracking_products([
            {"asin": "B000000001", "quantity": 2, "quantity_verified": True},
            {"asin": "B000000002", "quantity": 1, "quantity_verified": False},
        ])

        self.assertEqual(products[0]["quantity"], 2)
        self.assertTrue(products[0]["quantity_verified"])
        self.assertNotIn("quantity", products[1])
        self.assertNotIn("quantity_verified", products[1])

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
        self.assertEqual(displayed.count("B000000003"), 0)
        self.assertEqual(packages[0]["unassigned_products"][0]["asin"], "B000000003")

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

    def test_amazon_history_items_map_one_to_one_to_empty_split_shipments(self):
        packages = [
            {"id": 30, "package_index": 1, "products_json": "[]"},
            {"id": 31, "package_index": 2, "products_json": "[]"},
        ]
        history = {
            "items_json": json.dumps([
                {"asin": "B000000003", "title": "Shipment one"},
                {"asin": "B000000004", "title": "Shipment two", "quantity": 2, "quantity_verified": True},
            ])
        }

        allocations = package_tracker_history_products_by_package(packages, history)

        self.assertEqual([product["asin"] for product in allocations[30]], ["B000000003"])
        self.assertEqual([product["asin"] for product in allocations[31]], ["B000000004"])
        self.assertEqual(allocations[31][0]["quantity"], 2)

    def test_history_products_are_counted_once_across_split_shipments(self):
        packages = [
            {"id": 40, "package_index": 1, "amazon_order_id": "111-5", "asins_json": '["B000000005"]', "products_json": "[]"},
            {"id": 41, "package_index": 2, "amazon_order_id": "111-5", "asins_json": '["B000000005"]', "products_json": "[]"},
        ]
        lines = [{"amazon_order_id": "111-5", "asin": "B000000005", "quantity": 1}]
        history = {"111-5": {"items_json": '[{"asin":"B000000005","quantity_verified":true}]'}}

        analysis = package_tracker_quantity_analysis(lines, packages, history)

        self.assertFalse(analysis["suspected_duplicate"])


if __name__ == "__main__":
    unittest.main()
