import unittest
from unittest.mock import patch

from app.main import (
    normalize_package_pickup_confirmation_status,
    normalize_package_pickup_time,
    package_pickup_business_date,
    package_pickup_card_date,
    package_pickup_delivery_timestamp,
    package_pickup_scan_history,
    package_pickup_order_readiness,
    package_tracker_delivery_date,
    package_tracker_delivery_kind,
    record_package_pickup_scan_event,
)


class PackagePickupDeliveryDateTests(unittest.TestCase):
    def test_before_pickup_time_stays_on_delivery_date(self) -> None:
        self.assertEqual(
            package_pickup_business_date("2026-08-17T08:42:00-04:00"),
            "2026-08-17",
        )

    def test_after_pickup_time_stays_on_delivery_date(self) -> None:
        self.assertEqual(
            package_pickup_business_date("2026-08-17T11:00:00-04:00"),
            "2026-08-17",
        )

    def test_friday_delivery_stays_on_friday(self) -> None:
        self.assertEqual(
            package_pickup_business_date("2026-08-14T11:00:00-04:00"),
            "2026-08-14",
        )

    def test_weekend_deliveries_stay_on_their_delivery_dates(self) -> None:
        self.assertEqual(package_pickup_business_date("2026-08-15T11:00:00-04:00"), "2026-08-15")
        self.assertEqual(package_pickup_business_date("2026-08-16T02:14:00-04:00"), "2026-08-16")

    def test_brooklyn_date_is_used_across_utc_midnight(self) -> None:
        self.assertEqual(package_pickup_business_date("2026-08-20T01:15:00Z"), "2026-08-19")

    def test_date_only_delivery_marker_does_not_shift_to_previous_day(self) -> None:
        self.assertEqual(package_pickup_business_date("2026-08-20T00:00:00+00:00"), "2026-08-20")
        self.assertEqual(package_pickup_business_date("2026-08-20T00:00:00Z"), "2026-08-20")

    def test_explicit_local_delivery_display_controls_the_date(self) -> None:
        self.assertEqual(package_pickup_business_date("2026-08-20T00:00:00Z", "Aug 19, 2026 04:47 PM"), "2026-08-19")

    def test_first_pickup_scan_moves_package_to_scanned_brooklyn_date(self) -> None:
        self.assertEqual(
            package_pickup_card_date(
                "2026-08-20T15:00:00Z",
                "2026-08-22T01:15:00Z",
            ),
            "2026-08-21",
        )

    def test_unscanned_package_remains_on_amazon_delivery_date(self) -> None:
        self.assertEqual(
            package_pickup_card_date("2026-08-20T15:00:00Z"),
            "2026-08-20",
        )

    def test_scan_history_rejects_invalid_date_before_querying_database(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            package_pickup_scan_history(scan_date="08/27/2026")

    def test_scan_event_snapshots_exact_linked_order_and_tracking(self) -> None:
        class FakeConnection:
            def __init__(self) -> None:
                self.calls = []

            def execute(self, sql, params=None):
                self.calls.append((sql, tuple(params or ())))
                return self

            def fetchall(self):
                return []

        connection = FakeConnection()
        record_package_pickup_scan_event(
            connection,
            scan_code="TBA333598452175",
            result_status="matched",
            matched=True,
            message="NC22982 scanned successfully.",
            scanned_at="2026-08-27T01:02:03+00:00",
            package={
                "id": 41,
                "store_id": 1,
                "canonical_scan_code": "TBA333598452175",
                "amazon_order_id": "111-2222222-3333333",
                "odoo_order_name": "NC22982",
                "recipient_ref": "Nutricity NC22982",
            },
        )
        insert_params = connection.calls[-1][1]
        self.assertEqual(insert_params[6], "NC22982")
        self.assertEqual(insert_params[7], "111-2222222-3333333")
        self.assertEqual(insert_params[8], "TBA333598452175")
        self.assertEqual(insert_params[9], "Nutricity NC22982")

    @patch("app.main.dispatch_related_parts")
    def test_order_readiness_requires_every_related_package(self, related_parts) -> None:
        related_parts.return_value = [
            {"received": True, "scan_code": "TBA-FIRST"},
            {"received": False, "scan_code": "TBA-LATER", "delivery_label": "Arriving tomorrow"},
        ]
        readiness = package_pickup_order_readiness(object(), {"odoo_order_name": "NC23000"})
        self.assertFalse(readiness["ready_to_ship"])
        self.assertEqual(readiness["received_packages"], 1)
        self.assertEqual(readiness["remaining_packages"], 1)
        self.assertEqual(readiness["pending_packages"][0]["shipment_id"], "TBA-LATER")
        self.assertIn("Arriving tomorrow", readiness["message"])

    @patch("app.main.dispatch_related_parts")
    def test_complete_order_is_ready_to_ship(self, related_parts) -> None:
        related_parts.return_value = [{"received": True}, {"received": True}]
        readiness = package_pickup_order_readiness(object(), {"odoo_order_name": "NC23000"})
        self.assertTrue(readiness["ready_to_ship"])
        self.assertEqual(readiness["received_packages"], 2)
        self.assertIn("can be shipped", readiness["message"])

    def test_delivery_timestamp_uses_tracking_record_instead_of_confirmation_update(self) -> None:
        self.assertEqual(
            package_pickup_delivery_timestamp(
                {
                    "package_status": "delivered",
                    "promise": "delivered",
                    "updated_at": "2026-08-20T10:30:43+00:00",
                },
                {},
                {"updated_at": "2026-08-19T20:46:51+00:00"},
            ),
            "2026-08-19T20:46:51+00:00",
        )

    def test_saved_delivery_timestamp_cannot_be_replaced(self) -> None:
        self.assertEqual(
            package_pickup_delivery_timestamp(
                {
                    "pickup_delivered_at": "2026-08-19T20:46:51+00:00",
                    "updated_at": "2026-08-21T10:00:00+00:00",
                },
                {"amazon_delivered_at": "2026-08-21T10:00:00+00:00"},
                {"updated_at": "2026-08-21T10:00:00+00:00"},
            ),
            "2026-08-19T20:46:51+00:00",
        )

    def test_pickup_time_accepts_valid_time_and_rejects_invalid_time(self) -> None:
        self.assertEqual(normalize_package_pickup_time("14:05"), "14:05")
        self.assertEqual(normalize_package_pickup_time("25:90"), "09:30")

    def test_confirmation_status_is_explicit_and_resettable(self) -> None:
        self.assertEqual(normalize_package_pickup_confirmation_status("received"), "received")
        self.assertEqual(normalize_package_pickup_confirmation_status("not-received"), "not_received")
        self.assertEqual(normalize_package_pickup_confirmation_status("unconfirmed"), "unconfirmed")
        self.assertEqual(normalize_package_pickup_confirmation_status("unknown"), "")

    def test_only_actual_delivered_status_is_counted(self) -> None:
        self.assertEqual(package_tracker_delivery_kind("Delivered August 18", ""), "delivered")
        self.assertEqual(package_tracker_delivery_kind("Delivery date currently unavailable", ""), "arriving")
        self.assertEqual(package_tracker_delivery_kind("We're sorry your delivery is late", ""), "exception")

    def test_delivered_today_uses_the_brooklyn_calendar_date(self) -> None:
        self.assertEqual(
            package_tracker_delivery_date("Delivered today", "", "2026-08-21T01:15:00+00:00"),
            "2026-08-20T00:00:00-04:00",
        )


if __name__ == "__main__":
    unittest.main()
