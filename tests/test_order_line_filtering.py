import unittest

from app.main import should_skip_order_line


class OrderLineFilteringTests(unittest.TestCase):
    def test_product_title_with_free_shipping_is_not_skipped(self):
        line = {
            "name": "Cramp 911 Muscle Relaxing Roll-On Lotion, 0.71 Oz, Free Shipping, New",
            "display_type": False,
            "price_unit": 38.52,
        }
        product = {"type": "consu", "default_code": ""}

        self.assertFalse(should_skip_order_line(line, product, {}))

    def test_standalone_shipping_charge_is_skipped(self):
        line = {"name": "Shipping", "display_type": False, "price_unit": 10}
        product = {"type": "consu", "default_code": ""}

        self.assertTrue(should_skip_order_line(line, product, {}))

    def test_standard_delivery_service_is_skipped(self):
        line = {"name": "Standard delivery (7-10 days)", "display_type": False, "price_unit": 10}
        product = {"type": "service", "default_code": "Delivery_007"}

        self.assertTrue(should_skip_order_line(line, product, {}))


if __name__ == "__main__":
    unittest.main()
