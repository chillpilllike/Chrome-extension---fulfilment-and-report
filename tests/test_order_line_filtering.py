import unittest
from unittest.mock import patch

from app import redis_support
from app import main as app_main

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

    def test_public_package_tracker_never_uses_hour_long_stale_cache(self):
        self.assertFalse(redis_support.stale_cache_enabled(("public-package-tracker", 1)))

    def test_tracking_cache_clear_also_invalidates_public_tracker(self):
        app_main._FAST_PAGE_CACHE[("tracking-orders", 1)] = (float("inf"), {"old": True})
        app_main._FAST_PAGE_CACHE[("public-package-tracker", 1)] = (float("inf"), {"old": True})
        with patch.object(redis_support, "page_cache_clear_matching") as clear_redis:
            app_main.fast_page_cache_clear_matching({"tracking-orders"})

        cleared_prefixes = clear_redis.call_args.args[0]
        self.assertIn("public-package-tracker", cleared_prefixes)
        self.assertNotIn(("public-package-tracker", 1), app_main._FAST_PAGE_CACHE)


if __name__ == "__main__":
    unittest.main()
