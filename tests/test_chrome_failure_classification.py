import unittest

from app.main import chrome_fail_is_partial_quantity
from app.schemas.payloads import ChromeJobFailPayload


class ChromeFailureClassificationTests(unittest.TestCase):
    def test_empty_cart_verification_is_not_treated_as_low_stock(self):
        payload = ChromeJobFailPayload(
            message="Amazon cart stayed empty after Add to cart.",
            failure_code="cart_verification_failed",
            requested_quantity=2,
            fulfilled_quantity=0,
        )

        self.assertFalse(chrome_fail_is_partial_quantity(payload, payload.message))

    def test_cart_overage_is_not_treated_as_low_stock(self):
        payload = ChromeJobFailPayload(
            message="Amazon cart has too many units.",
            failure_code="cart_quantity_mismatch",
            requested_quantity=2,
            fulfilled_quantity=3,
            available_quantity=3,
        )

        self.assertFalse(chrome_fail_is_partial_quantity(payload, payload.message))

    def test_confirmed_partial_quantity_remains_missing(self):
        payload = ChromeJobFailPayload(
            message="Amazon only added one unit.",
            failure_code="partial_quantity",
            requested_quantity=2,
            fulfilled_quantity=1,
        )

        self.assertTrue(chrome_fail_is_partial_quantity(payload, payload.message))


if __name__ == "__main__":
    unittest.main()
