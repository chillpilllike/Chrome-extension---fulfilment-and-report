import unittest

from app.main import preserved_order_line_refresh_state


class OrderRefreshStateTests(unittest.TestCase):
    def test_costly_review_survives_odoo_refresh(self) -> None:
        state, error = preserved_order_line_refresh_state({
            "state": "costly",
            "amazon_status": "cost_review",
            "amazon_order_id": "",
            "amazon_group_key": "chrome-1-24299-example",
            "last_error": "Approval required before fulfilment.",
        })

        self.assertEqual(state, "costly")
        self.assertEqual(error, "Approval required before fulfilment.")


if __name__ == "__main__":
    unittest.main()
