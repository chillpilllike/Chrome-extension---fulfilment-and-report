import json
import pathlib
import unittest

from app.main import (
    canonical_tracking_packages,
    compact_tracking_products,
    dispatch_codes_from_package,
    dispatch_shipment_alias_pairs,
    package_tracker_canonical_dispatch_rows,
    package_tracker_enforce_product_guard,
    package_tracker_fallback_asins_by_package,
    package_tracker_history_products_by_package,
    package_tracker_quantity_analysis,
    package_tracker_single_package_history_products,
    package_tracker_tracking_parts_from_lines,
    tracking_packages_by_line_with_one_to_one_fallback,
    tracking_package_shipment_key,
    tracking_unambiguous_replacement_product,
)


class PackageTrackerProductTests(unittest.TestCase):
    def test_tracking_updates_invalidate_related_parts_cache(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        update_start = source.index("def api_tracking_update_impl")
        update_end = source.index('@app.get("/api/tracking/payment-failures")', update_start)

        self.assertIn('"dispatch-related-parts"', source[update_start:update_end])

    def test_pickup_scan_invalidates_related_parts_cache(self):
        source = (pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        scan_start = source.index("def api_package_pickup_scan(")
        scan_end = source.index('@app.post("/api/package-pickups/settings")', scan_start)

        self.assertIn('"dispatch-related-parts"', source[scan_start:scan_end])

    def test_unambiguous_fallback_exposes_replacement_metadata(self):
        product = tracking_unambiguous_replacement_product(
            [{"asins": ["B000000099"], "products": [{"asin": "B000000099", "title": "Replacement"}]}],
            {"id": 1, "asin": "B000000001"},
        )

        self.assertEqual(product["asin"], "B000000099")
        self.assertEqual(product["title"], "Replacement")

    def test_exact_line_match_is_not_recorded_as_replacement(self):
        product = tracking_unambiguous_replacement_product(
            [{"asins": ["B000000001"], "products": [{"asin": "B000000001"}]}],
            {"id": 1, "asin": "B000000001"},
        )

        self.assertEqual(product, {})

    def test_one_unmatched_replacement_line_gets_one_remaining_shipment(self):
        exact = {"asins": ["B000000001"]}
        replacement = {"asins": ["B000000099"]}
        rows = [
            {"id": 1, "asin": "B000000001"},
            {"id": 2, "asin": "B000000002"},
        ]

        mapped, unmatched = tracking_packages_by_line_with_one_to_one_fallback(
            [exact, replacement], rows
        )

        self.assertEqual(mapped[1], [exact])
        self.assertEqual(mapped[2], [replacement])
        self.assertEqual(unmatched, [])

    def test_ambiguous_unmatched_replacements_are_not_guessed(self):
        rows = [{"id": 1, "asin": "B000000001"}, {"id": 2, "asin": "B000000002"}]
        mapped, unmatched = tracking_packages_by_line_with_one_to_one_fallback(
            [{"asins": ["B000000098"]}, {"asins": ["B000000099"]}], rows
        )

        self.assertEqual(mapped, {})
        self.assertEqual([row["id"] for row in unmatched], [1, 2])

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

    def test_physical_tracking_snapshot_replaces_temporary_alias_for_same_shipment(self):
        packages = canonical_tracking_packages([
            {
                "tracking_url": "https://www.amazon.com/progress-tracker/package?orderId=112-2304855-2001835&shipmentId=NkKj7J16V&packageIndex=0",
                "asins": ["B09PVPNXPL", "B0G2MGBQJP"],
                "products": [{"asin": "B09PVPNXPL", "title": "First"}],
            },
            {
                "tracking_id": "TBA334090270674",
                "tracking_url": "https://www.amazon.com/progress-tracker/package?orderId=112-2304855-2001835&shipmentId=NkKj7J16V&packageIndex=0",
                "status": "Delivered August 28",
            },
        ])

        self.assertEqual(len(packages), 1)
        self.assertEqual(packages[0]["tracking_id"], "TBA334090270674")
        self.assertEqual(packages[0]["asins"], ["B09PVPNXPL", "B0G2MGBQJP"])
        self.assertEqual(packages[0]["products"][0]["asin"], "B09PVPNXPL")

    def test_package_index_zero_does_not_collapse_distinct_split_shipments(self):
        first = {"tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=NkKj7J16V&packageIndex=0"}
        second = {"tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=Nx2G7M1NV&packageIndex=0"}

        self.assertNotEqual(tracking_package_shipment_key(first), tracking_package_shipment_key(second))
        self.assertEqual(len(canonical_tracking_packages([first, second])), 2)

    def test_physical_package_supersedes_temporary_row_for_same_shipment(self):
        rows = [
            {"id": 10, "amazon_order_id": "112-2304855-2001835", "scan_code": "AMZPKG-TEMP", "tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=NkKj7J16V&packageIndex=0"},
            {"id": 11, "amazon_order_id": "112-2304855-2001835", "scan_code": "TBA334090270674", "tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=NkKj7J16V&packageIndex=0"},
            {"id": 12, "amazon_order_id": "112-2304855-2001835", "scan_code": "AMZPKG-OTHER", "tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=Nx2G7M1NV&packageIndex=0"},
        ]

        self.assertEqual(dispatch_shipment_alias_pairs(rows), [(10, 11)])

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

    def test_guard_does_not_assign_missing_item_to_known_shipment(self):
        packages = [{
            "amazon_order_id": "111-6",
            "tracking_url": "https://www.amazon.com/progress-tracker/package?shipmentId=KNOWN",
            "products": [{"asin": "B000000006", "title": "Known shipment item"}],
        }]
        history = {"111-6": {"items_json": json.dumps([
            {"asin": "B000000006", "title": "Known shipment item"},
            {"asin": "B000000007", "title": "Unmapped shipment item"},
        ])}}

        guard = package_tracker_enforce_product_guard(packages, history)

        self.assertTrue(guard["complete"])
        self.assertEqual([product["asin"] for product in packages[0]["products"]], ["B000000006"])
        self.assertEqual([product["asin"] for product in packages[0]["unassigned_products"]], ["B000000007"])

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
