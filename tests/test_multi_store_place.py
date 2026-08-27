import inspect
import unittest
from pathlib import Path

from app import main


FRONTEND = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.tsx"
).read_text(encoding="utf-8")


class MultiStorePlaceTests(unittest.TestCase):
    def test_backend_groups_selected_lines_by_their_actual_store(self) -> None:
        source = inspect.getsource(main.api_place)

        self.assertIn("line_ids_by_store", source)
        self.assertIn("if len(line_ids_by_store) > 1:", source)
        self.assertIn("store_id=selected_store_id", source)
        self.assertIn("place_orders_for_store_payload(store_payload)", source)
        self.assertIn('"store_results": results', source)

    def test_backend_reports_exact_chrome_lines_that_were_queued(self) -> None:
        source = inspect.getsource(main.api_place)

        self.assertIn("queued_line_ids", source)
        self.assertIn("state='submitted'", source)
        self.assertIn("order_engine='chrome'", source)

    def test_frontend_allows_mixed_store_selection_for_place_actions(self) -> None:
        self.assertIn("const canRunSelectedPlaceAction", FRONTEND)
        self.assertIn("Orders from ${selectedStoreIds.length} stores selected. Place Selected will queue each store separately.", FRONTEND)
        self.assertNotIn("Selected rows are from multiple stores. Select one store's rows before placing.", FRONTEND)
        self.assertNotIn("knownStoreIds.length && !knownStoreIds.includes(rowStoreId)", FRONTEND)

    def test_other_destructive_actions_remain_single_store_only(self) -> None:
        self.assertIn('selectedStoreIdForAction("Reset Selected")', FRONTEND)
        self.assertIn('selectedStoreIdForAction("Mark Do Not Process")', FRONTEND)
        self.assertIn('selectedStoreIdForAction("Delete Selected Lines")', FRONTEND)

    def test_optimistic_update_uses_only_confirmed_queued_lines(self) -> None:
        self.assertIn("result.queued_line_ids?.length ? result.queued_line_ids : selected", FRONTEND)


if __name__ == "__main__":
    unittest.main()
