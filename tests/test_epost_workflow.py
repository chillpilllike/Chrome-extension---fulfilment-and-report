import unittest
from datetime import datetime, timezone
from app.services.epost_workflow import QUEUES, annotate_workflow, matches_queue, persisted_status


class EpostWorkflowTests(unittest.TestCase):
    now = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)

    def classify(self, status="", date="", **extra):
        return annotate_workflow({"status": status, "last_update_at": date, "created_at": "2026-08-01T00:00:00Z", **extra}, now=self.now)

    def test_unchecked_blank_is_not_lost(self):
        row = self.classify()
        self.assertEqual("unscanned", row["workflow_queue"])
        self.assertIsNone(row["days_since_update"])
        self.assertEqual(35, row["days_since_import"])
        self.assertFalse(row["suspected_lost"])

    def test_checked_blank_and_electronic_events_are_awaiting_scan(self):
        for status in ("", "Data Received", "Electronic information submitted by shipper", "The item is pre-advised", "Shipment Announced", "Shipment in Transit to the ePost Global Processing Center"):
            with self.subTest(status=status):
                row = self.classify(status, "2026-08-01", last_checked_at="2026-09-05")
                self.assertEqual("awaiting_first_scan", row["workflow_queue"])
                self.assertFalse(row["suspected_lost"])

    def test_lookup_error_stays_lookup_error_despite_legacy_lost(self):
        row = self.classify("There was an error locating tracking number", epost_status="lost")
        self.assertEqual("lookup_error", row["workflow_queue"])
        self.assertFalse(matches_queue(row, "lost"))

    def test_delivery_negations_and_exceptions(self):
        for status in ("Not delivered", "Delivery failed", "ATTEMPTED DELIVERY ABROAD", "Undeliverable", "Return to Sender - Maximum Attempts Reached", "Confiscated by Customs Authorities", "Invalid KYC/ID, Customer is required to provide correct KYC documents", "Held at Delivery Depot/Delivery Office", "Parcel unsafe to leave at address"):
            with self.subTest(status=status):
                row = self.classify(status, "2026-08-01")
                self.assertEqual("carrier_exception", row["workflow_queue"])
                self.assertTrue(matches_queue(row, "active"))
                self.assertFalse(matches_queue(row, "delivered"))

    def test_positive_delivery_and_explicit_loss(self):
        for status in ("DELIVERED", "Item successfully delivered to community mailbox or parcel locker.", "The parcel has been delivered / dropped off."):
            self.assertEqual("delivered", self.classify(status, "2026-08-01")["workflow_queue"])
        for status in ("Lost", "Shipment lost", "Parcel is lost", "Lost in transit"):
            self.assertEqual("confirmed_lost", self.classify(status)["workflow_queue"])
        self.assertNotEqual("confirmed_lost", self.classify("Parcel is not lost")["workflow_queue"])

    def test_boundary_and_future_dates(self):
        self.assertEqual("stalled", self.classify("Parcel processed", "2026-08-26T12:00:00Z")["workflow_queue"])
        self.assertEqual("in_transit", self.classify("Parcel processed", "2026-08-26T12:00:01Z")["workflow_queue"])
        for stamp in ("", "not a date", "2026-09-20T12:00:00Z"):
            row = self.classify("Parcel processed", stamp)
            self.assertFalse(row["suspected_lost"])
            self.assertIsNone(row["days_since_update"])

    def test_portal_dates_and_threshold(self):
        row = self.classify("Parcel processed", "8/23/2026 11:00:00 AM")
        self.assertEqual(13, row["days_since_update"])
        self.assertEqual("stalled", row["workflow_queue"])
        annotate_workflow(row, 14, self.now)
        self.assertEqual("in_transit", row["workflow_queue"])

    def test_expected_events_and_unknown_messages_need_review(self):
        for status in ("Expected delivery date updated", "Expected arrival", "Parcel not yet received", "Unrecognized carrier message", "Will be delivered tomorrow"):
            self.assertEqual("needs_review", self.classify(status, "2026-08-01")["workflow_queue"])

    def test_refunds_are_separate_from_delivery(self):
        row = self.classify("Delivered", "2026-08-01", refund_status="claimed")
        self.assertTrue(matches_queue(row, "refund_claimed"))
        self.assertFalse(matches_queue(row, "attention"))
        self.assertFalse(matches_queue(row, "refund_received"))

    def test_persisted_status_does_not_call_blank_or_exception_lost(self):
        self.assertEqual("lookup_error", persisted_status("There was an error locating tracking number", ""))
        self.assertEqual("carrier_exception", persisted_status("Not delivered", "2026-01-01"))
        self.assertEqual("awaiting_first_scan", persisted_status("", ""))
        self.assertEqual("lost", persisted_status("Shipment lost", ""))

    def test_each_record_has_one_queue_and_an_action(self):
        rows = [self.classify(s) for s in ("", "Delivered", "Parcel lost", "Unknown", "Data received", "Parcel processed")]
        for row in rows:
            self.assertEqual(1, sum(matches_queue(row, key) for key in QUEUES))
            self.assertTrue(row["next_action"])
            self.assertTrue(row["suggested_owner"])


if __name__ == "__main__":
    unittest.main()
