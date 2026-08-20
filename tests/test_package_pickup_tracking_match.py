import unittest

from app.main import package_pickup_placeholder_rows, package_pickup_tracking_matches


class PackagePickupTrackingMatchTests(unittest.TestCase):
    def test_count_placeholders_preserve_type_and_exact_row_count(self) -> None:
        self.assertEqual(
            package_pickup_placeholder_rows("amazon", 2),
            [(1, -1, "Amazon Package 1"), (2, -2, "Amazon Package 2")],
        )
        self.assertEqual(
            package_pickup_placeholder_rows("non_amazon", 2),
            [(1, 1, "Non-Amazon Package 1"), (2, 2, "Non-Amazon Package 2")],
        )

    def test_last_five_characters_match(self) -> None:
        self.assertTrue(package_pickup_tracking_matches("3X9Q7", "TBA1234567893X9Q7"))

    def test_last_four_characters_are_rejected(self) -> None:
        self.assertFalse(package_pickup_tracking_matches("X9Q7", "TBA1234567893X9Q7"))

    def test_full_tracking_must_match_exactly(self) -> None:
        self.assertTrue(package_pickup_tracking_matches("TBA1234567893X9Q7", "TBA1234567893X9Q7"))
        self.assertFalse(package_pickup_tracking_matches("TBA1234567893X9Q8", "TBA1234567893X9Q7"))


if __name__ == "__main__":
    unittest.main()
