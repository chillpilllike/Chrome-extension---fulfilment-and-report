import inspect
import unittest
from pathlib import Path

from app import main


FRONTEND = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.tsx"
).read_text(encoding="utf-8")
FRONTEND_CSS = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "index.css"
).read_text(encoding="utf-8")
EXTENSION = (
    Path(__file__).resolve().parents[1] / "chrome-extension" / "content.js"
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
        self.assertIn("body: JSON.stringify({ line_id: line.id, expiry_confirmed: true, replacement_confirmed: replacementConfirmed })", FRONTEND)

    def test_manual_inventory_form_does_not_require_a_store(self) -> None:
        self.assertNotIn('label="Store" value={manualStoreId}', FRONTEND)
        self.assertIn("!form.product_name.trim()", FRONTEND)
        self.assertIn("Number(form.quantity || 0) <= 0", FRONTEND)

    def test_inventory_location_is_required_for_manual_stock(self) -> None:
        source = inspect.getsource(main.api_create_inventory)

        self.assertEqual(main.normalize_inventory_location("brooklyn, usa", required=True), "Brooklyn, USA")
        self.assertEqual(main.normalize_inventory_location("WERRIBEE, AUSTRALIA", required=True), "Werribee, Australia")
        with self.assertRaisesRegex(Exception, "Select an inventory location"):
            main.normalize_inventory_location("", required=True)
        self.assertIn('normalize_inventory_location(payload.get("location"), required=True)', source)
        self.assertIn("Brooklyn, USA", FRONTEND)
        self.assertIn("Werribee, Australia", FRONTEND)
        self.assertIn("!form.location", FRONTEND)
        self.assertIn('{item.location || "Brooklyn, USA"}', FRONTEND)

    def test_existing_inventory_defaults_to_brooklyn_and_allocations_keep_location(self) -> None:
        init_source = inspect.getsource(main.init_db)
        reserve_source = inspect.getsource(main.reserve_inventory_for_line)
        attach_source = inspect.getsource(main.api_attach_inventory_item)

        self.assertIn("location TEXT NOT NULL DEFAULT 'Brooklyn, USA'", init_source)
        self.assertIn("SET location=?", init_source)
        self.assertIn('normalize_inventory_location(item.get("location"))', reserve_source)
        self.assertIn("image_url, image_source, location", attach_source)

    def test_orders_picker_uses_shared_inventory_across_stores(self) -> None:
        self.assertIn('api<{ items: InventoryItem[] }>("/api/inventory?page=1&per_page=100")', FRONTEND)
        source = inspect.getsource(main.api_attach_inventory_item)
        line_id_branch = source[source.index("if line_id:"):source.index("if inventory_asin:")]
        self.assertNotIn('candidate_clauses.append("store_id=?")', line_id_branch.split("else:", 1)[0])
        self.assertIn('get_store(int(line["store_id"]))', source)

    def test_inventory_search_by_stock_id_is_global(self) -> None:
        source = inspect.getsource(main.list_inventory_items)

        self.assertIn("CAST(id AS TEXT)=?", source)
        self.assertIn('stock_id_match = re.fullmatch', source)
        self.assertLess(source.index("if search:"), source.index("store_id, store_id"))
        self.assertIn("Stock #, ASIN", FRONTEND)

    def test_inventory_page_attach_is_cross_store_and_reports_exact_asin_mismatch(self) -> None:
        source = inspect.getsource(main.api_attach_inventory_item)

        self.assertNotIn('candidate_clauses.append("store_id=?")', source)
        self.assertIn("Replacement confirmation required:", source)
        self.assertIn("The inventory stock was not changed", source)
        self.assertIn("if len(candidates) > 1:", source)

    def test_customer_accepted_alternative_can_be_recorded_as_replacement_asin(self) -> None:
        source = inspect.getsource(main.api_attach_inventory_item)

        self.assertIn('replacement_confirmed = payload.get("replacement_confirmed") is True', source)
        self.assertIn("Replacement confirmation required:", source)
        self.assertIn("replacement_asin=?", source)
        self.assertIn("Customer accepted inventory alternative ASIN", source)
        self.assertIn("Alternative ASIN · customer acceptance required", FRONTEND)
        self.assertIn("The customer accepted this alternative product", FRONTEND)
        self.assertIn("replacement_confirmed: replacementConfirmed", FRONTEND)

    def test_inventory_warnings_use_app_dialogs_not_browser_popups(self) -> None:
        inventory_page = FRONTEND[FRONTEND.index("function InventoryPage("):FRONTEND.index("function CancelledOrdersPage(")]

        self.assertNotIn("window.prompt", inventory_page)
        self.assertNotIn("window.confirm", inventory_page)
        self.assertIn("Attach inventory stock", inventory_page)
        self.assertIn("Different ASIN detected", inventory_page)
        self.assertIn("Confirm inventory sent", inventory_page)
        self.assertIn("inventoryReplacementConfirmed", FRONTEND)

    def test_inventory_images_have_a_forced_cache_busting_reload(self) -> None:
        source = inspect.getsource(main.api_refresh_inventory_image)
        image_source = inspect.getsource(main.api_inventory_image)

        self.assertIn("amazon_product_page_image_url", source)
        self.assertIn("fetch_remote_image_response", source)
        self.assertIn("image_url=?, image_source=?, updated_at=?", source)
        self.assertIn('response.headers["Cache-Control"] = "no-store, max-age=0"', image_source)
        self.assertIn("Force reload image for stock", FRONTEND)
        self.assertIn("cache_buster", FRONTEND)

    def test_inventory_matching_is_global_and_uses_effective_replacement_asin(self) -> None:
        reserve_source = inspect.getsource(main.reserve_inventory_for_line)
        attach_source = inspect.getsource(main.api_attach_inventory_item)
        create_source = inspect.getsource(main.api_create_inventory)

        self.assertIn("asin = effective_inventory_asin(line)", reserve_source)
        self.assertIn("WHERE asin=? AND status='available'", reserve_source)
        self.assertNotIn("WHERE store_id=? AND asin=?", reserve_source)
        self.assertIn("effective_inventory_asin(row) == inventory_asin", attach_source)
        self.assertIn('{"inventory-v2", "orders", "dashboard"}', create_source)
        self.assertIn("line.replacement_asin || line.asin", FRONTEND)
        exact_search_source = inspect.getsource(main.fast_exact_order_reference_search)
        self.assertIn("NULLIF(filtered_order_lines.replacement_asin, '')", exact_search_source)
        self.assertNotIn("0 AS inventory_quantity", exact_search_source)

    def test_orders_and_chrome_show_inventory_allocation_status(self) -> None:
        self.assertIn("Inventory ready:", FRONTEND)
        self.assertIn("Partial inventory:", FRONTEND)
        self.assertIn("Fulfilled from inventory:", FRONTEND)
        self.assertIn(">Fulfilled {inventory.allocated}/{inventory.requested}</Badge>", FRONTEND)
        self.assertIn("border-l-emerald-500", FRONTEND)
        self.assertIn("inventory-availability-badge-ready", FRONTEND)
        self.assertIn("inventory-availability-badge-used", FRONTEND)
        self.assertIn("background: #047857 !important", FRONTEND_CSS)
        self.assertIn("color: #ffffff !important", FRONTEND_CSS)
        self.assertIn("were already allocated from inventory", EXTENSION)


if __name__ == "__main__":
    unittest.main()
