import inspect
import unittest
from pathlib import Path

from app import main


FRONTEND = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.tsx"
).read_text(encoding="utf-8")


class ManualInventoryFulfilmentTests(unittest.TestCase):
    def test_manual_inventory_requires_title_and_quantity_but_not_asin(self) -> None:
        source = inspect.getsource(main.api_create_inventory)

        self.assertIn('raise HTTPException(400, "Title is required.")', source)
        self.assertIn('raise HTTPException(400, "Quantity must be greater than zero.")', source)
        self.assertNotIn('raise HTTPException(400, "ASIN is required.")', source)
        self.assertIn('page_image_url = amazon_product_page_image_url(asin) if asin else ""', source)

    def test_inventory_allocation_requires_expiry_confirmation(self) -> None:
        source = inspect.getsource(main.api_attach_inventory_item)

        self.assertIn('if payload.get("expiry_confirmed") is not True:', source)
        self.assertIn('if item_status != "available":', source)
        self.assertIn("confirm the inventory item is not expired", source)
        self.assertIn("Fulfilled from inventory item #", source)

    def test_orders_page_has_inventory_picker_and_expiry_warning(self) -> None:
        self.assertIn("Fulfill from Inventory", FRONTEND)
        self.assertIn("setInventoryFulfilmentItems(available)", FRONTEND)
        self.assertIn("I checked the expiry date and confirm this item is not expired.", FRONTEND)
        self.assertIn("body: JSON.stringify({ line_id: line.id, expiry_confirmed: true })", FRONTEND)

    def test_manual_inventory_form_works_with_all_stores_filter(self) -> None:
        self.assertIn('label="Store" value={manualStoreId}', FRONTEND)
        self.assertIn("!form.product_name.trim()", FRONTEND)
        self.assertIn("Number(form.quantity || 0) <= 0", FRONTEND)


if __name__ == "__main__":
    unittest.main()
