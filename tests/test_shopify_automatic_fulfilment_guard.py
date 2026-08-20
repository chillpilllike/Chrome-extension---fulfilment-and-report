import inspect
import unittest

import app.main as main


class ShopifyAutomaticFulfilmentGuardTests(unittest.TestCase):
    def test_tracking_updates_cannot_create_shopify_fulfilments(self) -> None:
        source = inspect.getsource(main.api_tracking_update)

        self.assertNotIn("fulfillmentCreate", source)
        self.assertNotIn("fulfill_shopify", source)
        self.assertFalse(hasattr(main, "fulfill_mapped_shopify_order"))
        self.assertFalse(hasattr(main, "fulfill_shopify_orders_for_delivered_amazon_order"))


if __name__ == "__main__":
    unittest.main()
