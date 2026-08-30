import unittest

from app.main import manual_pull_order_ineligible_reason, order_has_authorized_customer_transaction


class ManualOdooOrderPaymentEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.order = {
            "id": 24723,
            "name": "NC24703",
            "state": "sale",
            "invoice_status": "to invoice",
            "amount_total": 43.54,
            "currency_id": [143, "GBP"],
        }

    def test_full_authorized_transaction_is_eligible_before_invoicing(self) -> None:
        transactions = [{"state": "authorized", "amount": 43.54, "currency_id": [143, "GBP"]}]

        self.assertTrue(order_has_authorized_customer_transaction(self.order, transactions))
        self.assertEqual(manual_pull_order_ineligible_reason(self.order, [], transactions), "")

    def test_completed_transaction_is_eligible(self) -> None:
        transactions = [{"state": "done", "amount": 43.54, "currency_id": [143, "GBP"]}]

        self.assertEqual(manual_pull_order_ineligible_reason(self.order, [], transactions), "")

    def test_partial_authorization_remains_blocked(self) -> None:
        transactions = [{"state": "authorized", "amount": 20.0, "currency_id": [143, "GBP"]}]

        reason = manual_pull_order_ineligible_reason(self.order, [], transactions)

        self.assertIn("no paid invoice or full authorized payment", reason)

    def test_pending_or_wrong_currency_transaction_remains_blocked(self) -> None:
        transactions = [
            {"state": "pending", "amount": 43.54, "currency_id": [143, "GBP"]},
            {"state": "authorized", "amount": 43.54, "currency_id": [2, "USD"]},
        ]

        self.assertFalse(order_has_authorized_customer_transaction(self.order, transactions))


if __name__ == "__main__":
    unittest.main()
