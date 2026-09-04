import unittest
from unittest.mock import Mock

from app.main import OdooClient


class OdooSaleLineMonetaryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OdooClient.__new__(OdooClient)
        self.client.execute = Mock(return_value=True)

    def test_fulfilment_app_cannot_zero_sale_line_price(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "monetary updates are disabled"):
            self.client.write("sale.order.line", [166501], {"price_unit": 0.0})

        self.client.execute.assert_not_called()

    def test_fulfilment_app_cannot_change_sale_line_discount(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "monetary updates are disabled"):
            self.client.write("sale.order.line", [166501], {"discount": 100.0})

        self.client.execute.assert_not_called()

    def test_non_monetary_odoo_writes_are_unchanged(self) -> None:
        self.assertTrue(self.client.write("sale.order", [26133], {"note": "Audit note"}))

        self.client.execute.assert_called_once_with(
            "sale.order",
            "write",
            [[26133], {"note": "Audit note"}],
        )


if __name__ == "__main__":
    unittest.main()
