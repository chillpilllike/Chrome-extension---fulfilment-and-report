import unittest

from app import main


class TrackingAccountIdentityTests(unittest.TestCase):
    def test_name_and_type_pair_matches(self):
        rows = [{"amazon_account_name": "Amit", "amazon_account_type": "consumer"}]
        self.assertEqual(main.tracking_account_identity_error(rows, " amit ", "CONSUMER"), "")

    def test_same_name_on_other_account_type_is_rejected(self):
        rows = [{"amazon_account_name": "Amit", "amazon_account_type": "business"}]
        self.assertIn("account-type mismatch", main.tracking_account_identity_error(rows, "Amit", "consumer"))

    def test_other_name_on_same_account_type_is_rejected(self):
        rows = [{"amazon_account_name": "Sergey", "amazon_account_type": "business"}]
        self.assertIn("account-name mismatch", main.tracking_account_identity_error(rows, "Amit", "business"))

    def test_known_identity_requires_both_values(self):
        rows = [{"amazon_account_name": "Sergey", "amazon_account_type": "business"}]
        self.assertIn("account-name mismatch", main.tracking_account_identity_error(rows, "", "business"))
        self.assertIn("account-type mismatch", main.tracking_account_identity_error(rows, "Sergey", ""))

    def test_placeholder_name_does_not_override_real_type_guard(self):
        rows = [{"amazon_account_name": "Amazon Tracking Track All", "amazon_account_type": "consumer"}]
        self.assertEqual(main.tracking_account_identity_error(rows, "Amit", "consumer"), "")


if __name__ == "__main__":
    unittest.main()
