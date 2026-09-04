import unittest
from datetime import datetime, timedelta, timezone

from app.services.after_order import (
    normalize_customer_decision,
    resolve_delivery_recipient,
    tracking_risk,
    trustpilot_review_url,
)


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
        self.assertTrue(risk.customer_lost_email_allowed)


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
