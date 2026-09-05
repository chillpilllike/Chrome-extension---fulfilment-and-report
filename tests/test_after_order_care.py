import unittest
from datetime import datetime, timedelta, timezone

from app.services.after_order import (
    can_remove_affected_items,
    unavailable_notice_block_reason,
    normalize_customer_decision,
    resolve_delivery_recipient,
    tracking_risk,
    trustpilot_review_url,
)


class PartialRemovalTests(unittest.TestCase):
    def setUp(self):
        self.item = {"id": 1, "asin": "B000000001", "quantity": 3, "state": "missing"}
        self.affected = [{"line_id": 1, "asin": "B000000001"}]

    def test_single_asin_with_multiple_units_cannot_be_removed(self):
        self.assertFalse(can_remove_affected_items([self.item], self.affected))

    def test_shipping_or_duplicate_asin_does_not_count_as_remaining_product(self):
        for other in ({"id": 2, "asin": "", "quantity": 1}, {"id": 2, "asin": "B000000001", "quantity": 1}):
            self.assertFalse(can_remove_affected_items([self.item, other], self.affected))

    def test_other_available_asin_allows_partial_removal(self):
        self.assertTrue(can_remove_affected_items([self.item, {"id": 2, "asin": "B000000002", "quantity": 1}], self.affected))

    def test_all_affected_items_cannot_be_removed(self):
        other = {"id": 2, "asin": "B000000002", "quantity": 1}
        self.assertFalse(can_remove_affected_items([self.item, other], self.affected + [{"line_id": 2, "asin": "B000000002"}]))

    def test_missing_cancelled_refunded_or_zero_quantity_do_not_count(self):
        for fields in ({"state": "missing"}, {"odoo_status_label": "cancelled"}, {"odoo_status_label": "refunded"}, {"quantity": 0}):
            other = {"id": 2, "asin": "B000000002", "quantity": 1, **fields}
            self.assertFalse(can_remove_affected_items([self.item, other], self.affected))

    def test_unknown_affected_product_fails_closed(self):
        self.assertFalse(can_remove_affected_items([self.item], []))


class UnavailableNoticeSuppressionTests(unittest.TestCase):
    def setUp(self):
        self.line = {"id": 1, "state": "missing", "order_engine": "chrome"}
        self.affected = [{"line_id": 1}]

    def test_missing_item_is_eligible_for_review_not_automatic_send(self):
        self.assertEqual("", unavailable_notice_block_reason([self.line], self.affected))

    def test_manual_third_party_and_inventory_override_missing_status(self):
        for engine in ("third_party", "manual_amazon", "inventory"):
            self.assertTrue(unavailable_notice_block_reason([{**self.line, "order_engine": engine}], self.affected))

    def test_placed_order_reference_overrides_stale_missing_status(self):
        self.assertTrue(unavailable_notice_block_reason([{**self.line, "amazon_order_id": "supplier-reference"}], self.affected))

    def test_placement_on_another_line_does_not_hide_missing_item(self):
        self.assertEqual("", unavailable_notice_block_reason([self.line, {"id": 2, "state": "ordered"}], self.affected))

    def test_resolved_missing_flag_or_unknown_line_blocks_notice(self):
        for lines in ([], [{**self.line, "state": "pulled"}], [{**self.line, "id": 2}]):
            self.assertTrue(unavailable_notice_block_reason(lines, self.affected))

    def test_cancelled_or_refunded_order_blocks_notice(self):
        for label in ("cancelled", "refunded"):
            self.assertTrue(unavailable_notice_block_reason([{**self.line, "odoo_status_label": label}], self.affected))


class AfterOrderTrackingRiskTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)

    def test_blank_provider_page_is_never_lost(self):
        risk = tracking_risk([], now=self.now, stale_days=10)
        self.assertEqual("awaiting_first_scan", risk.state)
        self.assertFalse(risk.customer_lost_email_allowed)

    def test_manifest_only_is_not_physical_possession(self):
        risk = tracking_risk(
            [{"Event": "Data Received", "EventDT": "2026-08-01T12:00:00+00:00"}],
            now=self.now,
            stale_days=10,
        )
        self.assertEqual("awaiting_first_scan", risk.state)
        self.assertFalse(risk.customer_lost_email_allowed)

    def test_stale_clock_starts_after_physical_event(self):
        risk = tracking_risk(
            [{"Event": "Parcel Received and Processing", "EventDT": (self.now - timedelta(days=11)).isoformat()}],
            now=self.now,
            stale_days=10,
        )
        self.assertEqual("suspected_lost", risk.state)
        self.assertTrue(risk.customer_lost_email_allowed)

    def test_recent_physical_event_is_in_transit(self):
        risk = tracking_risk(
            [{"Event": "Shipment in Transit to the processing center", "EventDT": (self.now - timedelta(days=1)).isoformat()}],
            now=self.now,
            stale_days=10,
        )
        self.assertEqual("in_transit", risk.state)
        self.assertFalse(risk.customer_lost_email_allowed)

    def test_explicit_not_delivered_status_is_an_exception(self):
        risk = tracking_risk(
            [{"Event": "Parcel received", "EventDT": self.now.isoformat()}],
            status="Not delivered",
            now=self.now,
        )
        self.assertEqual("carrier_exception", risk.state)
        self.assertFalse(risk.customer_lost_email_allowed)


class AfterOrderDecisionTests(unittest.TestCase):
    def test_decisions_are_provider_neutral(self):
        self.assertEqual("exclude_item_and_proceed", normalize_customer_decision("exclude-item-and-proceed"))
        self.assertEqual("replacement", normalize_customer_decision("Replacement"))

    def test_unknown_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_customer_decision("approve without review")

    def test_delivery_confirmation_decisions_are_supported(self):
        self.assertEqual("received", normalize_customer_decision("received"))
        self.assertEqual("not_received", normalize_customer_decision("not received"))

    def test_test_mode_always_overrides_customer_recipient(self):
        self.assertEqual(
            "sonianuj1284@gmail.com",
            resolve_delivery_recipient(
                "real-customer@example.com",
                test_mode=True,
                test_recipient="sonianuj1284@gmail.com",
            ),
        )

    def test_live_mode_uses_customer_recipient(self):
        self.assertEqual(
            "real-customer@example.com",
            resolve_delivery_recipient(
                "real-customer@example.com",
                test_mode=False,
                test_recipient="sonianuj1284@gmail.com",
            ),
        )

    def test_trustpilot_url_is_derived_from_website_domain(self):
        self.assertEqual(
            "https://www.trustpilot.com/review/nutricity.ca",
            trustpilot_review_url("https://nutricity.ca/shop"),
        )


if __name__ == "__main__":
    unittest.main()
