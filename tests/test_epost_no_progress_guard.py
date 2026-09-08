import unittest
from datetime import datetime, timedelta, timezone

from app.services.after_order import tracking_risk
from app.services.epost_workflow import annotate_workflow


class NoProgressGuardTests(unittest.TestCase):
    now = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def event(self, text, days=11):
        return {"Event": text, "EventDT": (self.now - timedelta(days=days)).isoformat()}

    def test_empty_and_electronic_records_never_allow_loss(self):
        for events in ([], [{}], [self.event("Data Received")],
                       [self.event("Shipment Announced.")], [self.event("Label Created")]):
            with self.subTest(events=events):
                self.assertFalse(tracking_risk(events, now=self.now).customer_lost_email_allowed)

    def test_one_dated_progress_event_is_enough_after_ten_days(self):
        for text in ("Shipment in Transit to the ePost Global Processing Center",
                     "Shipment heading to epost global sorting office", "Parcel Received and Processing",
                     "Parcel In-Transit", "Parcel Shipped", "Parcel processed"):
            with self.subTest(text=text):
                self.assertTrue(tracking_risk([self.event(text)], now=self.now).customer_lost_email_allowed)
                self.assertFalse(tracking_risk([self.event(text, 9)], now=self.now).customer_lost_email_allowed)

    def test_invalid_or_future_dates_cannot_start_clock(self):
        for stamp in ("", "invalid", "2027-01-01T00:00:00Z"):
            self.assertFalse(tracking_risk([{"Event": "Parcel Received", "EventDT": stamp}], now=self.now).customer_lost_email_allowed)

    def test_newer_update_and_lookup_error_block_loss(self):
        events = [self.event("Parcel Received"), self.event("Airline Delay", 1)]
        self.assertFalse(tracking_risk(events, now=self.now).customer_lost_email_allowed)
        self.assertFalse(tracking_risk([events[0]], status="There was an error locating tracking number", now=self.now).customer_lost_email_allowed)

    def test_provider_date_format_and_queue_consistency(self):
        text = "Shipment in Transit to the ePost Global Processing Center"
        stamp = "8/20/2026 4:26:00 PM"
        self.assertTrue(tracking_risk([{"status": text, "date": stamp}], now=self.now).customer_lost_email_allowed)
        self.assertEqual("stalled", annotate_workflow({"status": text, "last_update_at": stamp}, now=self.now)["workflow_queue"])


if __name__ == "__main__":
    unittest.main()
