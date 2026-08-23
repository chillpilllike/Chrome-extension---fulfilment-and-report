import inspect
import unittest

import app.main as main


class TrackingEmptyPayloadGuardTests(unittest.TestCase):
    def test_empty_tracking_updates_are_rejected_before_database_writes(self) -> None:
        source = inspect.getsource(main.api_tracking_update_impl)
        guard = source.index("if (\n        not packages")
        first_database_write_scope = source.index("with db() as conn:")

        self.assertLess(guard, first_database_write_scope)
        self.assertIn("Empty Amazon tracking update rejected", source)

    def test_browserless_repair_can_be_limited_to_affected_amazon_orders(self) -> None:
        source = inspect.getsource(main.browserless_tracking_orders)

        self.assertIn("amazon_order_ids", source)
        self.assertIn("order_id not in requested_order_ids", source)


if __name__ == "__main__":
    unittest.main()
