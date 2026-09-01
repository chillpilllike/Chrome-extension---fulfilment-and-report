import unittest

from app.main import aggregate_items_by_asin, inventory_purchase_quantity


class InventoryAllocationLogicTests(unittest.TestCase):
    def test_partial_inventory_leaves_only_remainder_for_amazon(self) -> None:
        line = {
            "id": 91,
            "asin": "B012345678",
            "quantity": 3,
            "inventory_allocated_quantity": 2,
            "store_total_price": 30,
            "store_unit_price": 10,
            "supplier_part_auxiliary_id": "",
            "replacement_asin": "",
            "original_asin": "",
            "product_name": "Example",
            "odoo_order_name": "NC12345",
        }

        self.assertEqual(inventory_purchase_quantity(line), 1)
        items = aggregate_items_by_asin([line])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"], 1)
        self.assertEqual(items[0]["requested_quantity"], 3)
        self.assertEqual(items[0]["inventory_quantity"], 2)
        self.assertEqual(items[0]["store_total_price"], 10)

    def test_replacement_asin_is_the_inventory_and_amazon_purchase_key(self) -> None:
        line = {
            "id": 93,
            "asin": "B000000001",
            "replacement_asin": "B000000002",
            "original_asin": "B000000001",
            "quantity": 2,
            "inventory_allocated_quantity": 1,
            "store_total_price": 20,
            "store_unit_price": 10,
            "supplier_part_auxiliary_id": "",
            "product_name": "Replacement example",
            "odoo_order_name": "NC25165",
        }

        items = aggregate_items_by_asin([line])
        self.assertEqual(items[0]["asin"], "B000000002")
        self.assertTrue(items[0]["uses_replacement_asin"])

    def test_full_inventory_removes_item_from_amazon_payload(self) -> None:
        line = {
            "id": 92,
            "asin": "B012345678",
            "quantity": 2,
            "inventory_allocated_quantity": 2,
            "store_total_price": 20,
            "store_unit_price": 10,
            "supplier_part_auxiliary_id": "",
            "replacement_asin": "",
            "original_asin": "",
            "product_name": "Example",
            "odoo_order_name": "NC12346",
        }

        self.assertEqual(inventory_purchase_quantity(line), 0)
        self.assertEqual(aggregate_items_by_asin([line]), [])

    def test_inventory_allocation_never_exceeds_requested_quantity(self) -> None:
        self.assertEqual(inventory_purchase_quantity({"quantity": 2, "inventory_allocated_quantity": 3}), 0)


if __name__ == "__main__":
    unittest.main()
