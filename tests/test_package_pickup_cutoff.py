import unittest

from app.main import (
    normalize_package_pickup_confirmation_status,
    normalize_package_pickup_time,
    package_pickup_business_date,
    package_pickup_delivery_timestamp,
    package_tracker_delivery_date,
    package_tracker_delivery_kind,
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

    def test_explicit_local_delivery_display_controls_the_date(self) -> None:
        self.assertEqual(package_pickup_business_date("2026-08-20T00:00:00Z", "Aug 19, 2026 04:47 PM"), "2026-08-19")

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
