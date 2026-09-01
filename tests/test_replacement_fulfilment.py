import inspect
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from app import main
from app.schemas import ProcessReplacementPayload


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = (ROOT / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8")
EXTENSION = (ROOT / "chrome-extension" / "content.js").read_text(encoding="utf-8")


class ReplacementFulfilmentTests(unittest.TestCase):
    def test_process_replacement_insert_is_well_formed_and_queues_clone(self) -> None:
        original = {
            "id": 3117885,
            "store_id": 3,
            "odoo_order_id": 1255,
            "odoo_order_name": "ES00378",
            "odoo_order_date": "2026-08-01T00:00:00+00:00",
            "odoo_line_id": 551,
            "product_name": "Collagen",
            "asin": "B098PTNRYX",
            "replacement_asin": "B098PTNRYX",
            "quantity": 2,
            "store_unit_price": 20,
            "store_total_price": 40,
            "state": "pulled",
            "odoo_status_label": "invoiced",
        }

        class Result:
            def __init__(self, rows=None, row=None):
                self.rows = rows or []
                self.row = row

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self):
                self.insert_params = None

            def execute(self, sql, params=()):
                normalized = " ".join(sql.split())
                if "ORDER BY id FOR UPDATE" in normalized:
                    return Result(rows=[original])
                if "SELECT replacement_sequence, state, amazon_order_id" in normalized:
                    return Result(row=None)
                if "MAX(replacement_sequence)" in normalized:
                    return Result(row={"sequence": 0})
                if "SELECT 1 FROM order_lines WHERE store_id" in normalized:
                    return Result(row=None)
                if normalized.startswith("INSERT INTO order_lines"):
                    self.insert_params = tuple(params)
                    self.assert_placeholder_count(sql, params)
                    return Result(row={"id": 9001})
                if normalized.startswith("UPDATE order_lines SET fulfilment_note"):
                    return Result()
                if normalized.startswith("SELECT * FROM order_lines WHERE id IN"):
                    return Result(rows=[original, {**original, "id": 9001, "replacement_run_id": "replacement-run"}])
                raise AssertionError(f"Unexpected SQL: {normalized[:180]}")

            @staticmethod
            def assert_placeholder_count(sql, params):
                if sql.count("?") != len(params):
                    raise AssertionError(f"SQL placeholders={sql.count('?')} params={len(params)}")

        connection = Connection()
        payload = ProcessReplacementPayload(
            store_id=3,
            line_ids=[3117885],
            quantities={3117885: 1},
            reason="lost",
            address_id=2,
            amazon_account_id=7,
        )
        with (
            patch.object(main, "db", return_value=nullcontext(connection)),
            patch.object(main, "index_order_line"),
            patch.object(main, "get_store", return_value={"id": 3}),
            patch.object(main, "post_order_note_once"),
            patch.object(main, "queue_chrome_order_groups_fast", return_value=(1, 0, 0, {"name": "Business"}, [])) as queue,
            patch.object(main, "fast_page_cache_clear_matching"),
            patch.object(main, "dashboard_data", return_value={"rows": []}),
        ):
            result = main.api_process_replacement(payload)

        self.assertTrue(result["ok"])
        self.assertEqual(result["replacement_sequence"], 1)
        self.assertIsNotNone(connection.insert_params)
        self.assertEqual(queue.call_args.args[3], [9001])

    def test_replacement_job_has_unique_recipient_and_metadata(self) -> None:
        row = {
            "id": 901,
            "store_id": 3,
            "odoo_order_id": 1255,
            "odoo_order_name": "ES00378",
            "asin": "B098PTNRYX",
            "replacement_asin": "B098PTNRYX",
            "original_asin": "B098PTNRYX",
            "quantity": 2,
            "inventory_allocated_quantity": 0,
            "store_total_price": 40,
            "store_unit_price": 20,
            "supplier_part_auxiliary_id": "",
            "product_name": "Collagen",
            "state": "submitted",
            "amazon_order_id": "",
            "amazon_group_key": "chrome-3-replacement-3-1255-r1-token-job",
            "amazon_account_id": 7,
            "amazon_status": "chrome_queued",
            "amazon_cancelled_order_id": "",
            "chrome_claimed_by": "",
            "chrome_claim_expires_at": "",
            "replacement_run_id": "replacement-3-1255-r1-token",
            "replacement_sequence": 1,
            "replacement_reason": "lost",
            "cost_approved_at": None,
        }
        account = {"id": 7, "name": "Business"}

        with patch.object(main, "chrome_account_type_routing_enabled", return_value=False):
            job = main.chrome_job_from_rows([row], {7: account})

        self.assertTrue(job["is_replacement"])
        self.assertEqual(job["replacement_label"], "R1")
        self.assertEqual(job["replacement_reason"], "lost")
        self.assertEqual(job["original_order_name"], "ES00378")
        self.assertEqual(job["recipient_name"], "Nutricity ES00378 R1")

    def test_replacement_bypasses_only_scoped_guards(self) -> None:
        date_guard = inspect.getsource(main.block_lines_before_min_odoo_order_date)
        shopify_guard = inspect.getsource(main.block_lines_with_fulfilled_shopify_orders)
        existing_order_guard = inspect.getsource(main.block_selected_orders_with_existing_amazon_orders)
        shopify_enqueue = inspect.getsource(main.enqueue_shopify_fulfilment_for_rows)

        self.assertIn("is_replacement_fulfilment_line(line)", date_guard)
        self.assertIn("replacement_lines", shopify_guard)
        self.assertIn("replacement_ids", existing_order_guard)
        self.assertIn("Never create a second Shopify fulfilment", shopify_enqueue)

    def test_process_replacement_creates_linked_lines_and_queues_chrome(self) -> None:
        source = inspect.getsource(main.api_process_replacement)

        self.assertIn("replacement_original_line_id", source)
        self.assertIn("replacement_run_id", source)
        self.assertIn("replacement_sequence", source)
        self.assertIn("queue_chrome_order_groups_fast", source)
        self.assertIn("Replacement R", source)

    def test_orders_ui_and_extension_expose_replacement_flow(self) -> None:
        self.assertIn("Process Replacement", FRONTEND)
        self.assertIn("Create and Queue Replacement", FRONTEND)
        self.assertIn("Replacement R{Number(row.replacement_sequence", FRONTEND)
        self.assertIn("It will not fulfil Shopify again", FRONTEND)
        self.assertIn("Nutricity replacement fulfilment", EXTENSION)
        self.assertIn("activeJob.job.replacement_label", EXTENSION)


if __name__ == "__main__":
    unittest.main()
