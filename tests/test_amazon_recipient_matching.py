import unittest

from fastapi import HTTPException

from app.main import (
    amazon_order_refs_from_text,
    amazon_history_order_refs_from_text,
    api_manual_amazon_match,
    chrome_completion_history_evidence_error,
    manual_order_refs_from_payload,
    parse_amazon_order_placed_date,
    package_tracker_missing_history_products,
    package_tracker_product_image_url,
    package_tracker_quantity_analysis,
)
from app.schemas.payloads import ChromeJobCompletePayload, ManualAmazonOrderMatchPayload


class AmazonRecipientMatchingTests(unittest.TestCase):
    def test_tracker_uses_captured_image_for_unambiguous_replacement_asin(self):
        product = {"asin": "B0D1QMKKQR", "image_url": ""}
        history_products = [{
            "asin": "B002NPCML0",
            "image_url": "https://m.media-amazon.com/images/I/captured.jpg",
        }]

        image_url = package_tracker_product_image_url(
            product,
            history_products,
            allow_unambiguous_replacement=True,
        )

        self.assertEqual(image_url, "https://m.media-amazon.com/images/I/captured.jpg")

    def test_tracker_does_not_borrow_ambiguous_history_image(self):
        product = {"asin": "B0D1QMKKQR", "image_url": ""}
        history_products = [
            {"asin": "B002NPCML0", "image_url": "https://m.media-amazon.com/images/I/one.jpg"},
            {"asin": "B099999999", "image_url": "https://m.media-amazon.com/images/I/two.jpg"},
        ]

        image_url = package_tracker_product_image_url(product, history_products)

        self.assertEqual(image_url, "/api/public/asin-image/B0D1QMKKQR")

    def test_duplicate_analysis_respects_odoo_quantity_across_amazon_orders(self):
        lines = [{"asin": "B012345678", "quantity": 2}]
        packages = [
            {"amazon_order_id": "111-1111111-1111111", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
            {"amazon_order_id": "111-2222222-2222222", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
        ]

        analysis = package_tracker_quantity_analysis(lines, packages, {})

        self.assertFalse(analysis["suspected_duplicate"])
        self.assertEqual(analysis["duplicate_items"], [])

    def test_duplicate_analysis_flags_only_quantity_above_odoo_total(self):
        lines = [{"asin": "B012345678", "quantity": 1}]
        packages = [
            {"amazon_order_id": "111-1111111-1111111", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
            {"amazon_order_id": "111-2222222-2222222", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
        ]

        analysis = package_tracker_quantity_analysis(lines, packages, {})

        self.assertTrue(analysis["suspected_duplicate"])
        self.assertEqual(analysis["duplicate_items"][0]["excess_quantity"], 1)

    def test_duplicate_analysis_combines_original_and_replacement_asins(self):
        lines = [{"asin": "B012345678", "replacement_asin": "B087654321", "quantity": 2}]
        packages = [
            {"amazon_order_id": "111-1111111-1111111", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
            {"amazon_order_id": "111-2222222-2222222", "products_json": '[{"asin":"B087654321"}]', "asins_json": "[]"},
        ]

        analysis = package_tracker_quantity_analysis(lines, packages, {})

        self.assertFalse(analysis["suspected_duplicate"])
        self.assertEqual(analysis["asin_mismatches"], [])

    def test_duplicate_analysis_reports_unmapped_amazon_asin(self):
        lines = [{"asin": "B012345678", "quantity": 1}]
        packages = [{"amazon_order_id": "111-1111111-1111111", "products_json": '[{"asin":"B099999999"}]', "asins_json": "[]"}]

        analysis = package_tracker_quantity_analysis(lines, packages, {})

        self.assertEqual(analysis["asin_mismatches"][0]["amazon_asin"], "B099999999")

    def test_duplicate_analysis_ignores_cancelled_amazon_packages(self):
        lines = [{"asin": "B012345678", "quantity": 1}]
        packages = [
            {"amazon_order_id": "111-1111111-1111111", "products_json": '[{"asin":"B012345678"}]', "asins_json": "[]"},
            {
                "amazon_order_id": "111-2222222-2222222",
                "package_status": "Order cancelled",
                "products_json": '[{"asin":"B012345678"},{"asin":"B099999999"}]',
                "asins_json": "[]",
            },
        ]

        analysis = package_tracker_quantity_analysis(lines, packages, {})

        self.assertFalse(analysis["suspected_duplicate"])
        self.assertEqual(analysis["asin_mismatches"], [])

    def test_tracker_adds_history_item_only_for_strict_product_superset(self):
        packages = [{"asins_json": '["B09NP8BRPB", "B00C2DHB5K"]', "products_json": "[]"}]
        history = {"items_json": '[{"asin":"B09NP8BRPB"},{"asin":"B00C2DHB5K"},{"asin":"B0F49XZ5PC","title":"Golden Thread"}]'}

        self.assertEqual(
            [product["asin"] for product in package_tracker_missing_history_products(packages, history)],
            ["B0F49XZ5PC"],
        )

    def test_tracker_does_not_turn_replacement_asin_into_extra_product(self):
        packages = [{"asins_json": '["B0BKQ3NR2H"]', "products_json": "[]"}]
        history = {"items_json": '[{"asin":"B0GJD97KNS"}]'}

        self.assertEqual(package_tracker_missing_history_products(packages, history), [])

    def test_amazon_history_order_date_is_normalized_for_tracker_cards(self):
        self.assertEqual(
            parse_amazon_order_placed_date("August 17, 2026"),
            "2026-08-17T00:00:00+00:00",
        )

    def test_recipient_pack_suffix_keeps_exact_order_reference(self):
        self.assertIn("NC20380", amazon_order_refs_from_text("Nutricity NC20380 1Pack"))

    def test_concatenated_pack_suffix_keeps_canonical_reference(self):
        self.assertEqual(
            amazon_order_refs_from_text("Nutricity NC204942 pack"),
            ["NC20494"],
        )

    def test_multiple_odoo_references_are_extracted_from_one_recipient(self):
        recipient = "Nutricity NC20380 ES00393 2Pack"
        self.assertEqual(
            amazon_history_order_refs_from_text(recipient),
            ["NC20380", "ES00393"],
        )

    def test_explicit_order_names_do_not_get_broadened_by_source_text(self):
        payload = ManualAmazonOrderMatchPayload(
            amazon_order_id="111-1111111-1111111",
            order_names=["NC20380"],
            source_text="Nutricity NC20380 1Pack and NC99999",
        )

        self.assertEqual(manual_order_refs_from_payload(payload), ["NC20380"])

    def test_manual_match_rejects_selected_order_missing_from_recipient(self):
        payload = ManualAmazonOrderMatchPayload(
            amazon_order_id="111-1111111-1111111",
            order_names=["NC19589"],
            source_text="Nutricity NC21230 1mg",
            line_ids=[123],
            replace_existing=True,
        )

        with self.assertRaises(HTTPException) as caught:
            api_manual_amazon_match(payload)

        self.assertEqual(caught.exception.status_code, 409)

    def test_manual_match_rejects_missing_recipient_evidence(self):
        payload = ManualAmazonOrderMatchPayload(
            amazon_order_id="111-1111111-1111111",
            order_names=["NC19589"],
            source_text="",
            line_ids=[123],
            replace_existing=True,
        )

        with self.assertRaises(HTTPException) as caught:
            api_manual_amazon_match(payload)

        self.assertEqual(caught.exception.status_code, 409)

    def test_chrome_completion_rejects_same_asin_for_different_recipient(self):
        payload = ChromeJobCompletePayload(
            amazon_order_id="111-1111111-1111111",
            amazon_recipient="Nutricity NC21230 1mg",
            amazon_asins=["B0BX7DV92P"],
        )
        rows = [{"odoo_order_name": "NC19589", "asin": "B0BX7DV92P"}]

        error = chrome_completion_history_evidence_error(
            "group", rows, payload, {}, payload.amazon_order_id
        )

        self.assertIn("does not match", error)

    def test_chrome_completion_accepts_exact_recipient_with_pack_suffix(self):
        payload = ChromeJobCompletePayload(
            amazon_order_id="111-1111111-1111111",
            amazon_recipient="Nutricity NC20380 1Pack",
            amazon_asins=["B0025YMU3E"],
        )
        rows = [{"odoo_order_name": "NC20380", "asin": "B0025YMU3E"}]

        error = chrome_completion_history_evidence_error(
            "group", rows, payload, {}, payload.amazon_order_id
        )

        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
