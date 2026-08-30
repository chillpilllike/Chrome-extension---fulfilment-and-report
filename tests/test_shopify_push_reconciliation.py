import inspect
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import app.main as main


class _DbContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, traceback):
        return False


class ShopifyPushReconciliationTests(unittest.TestCase):
    def test_missing_push_scan_requires_eligible_amazon_order_without_map(self) -> None:
        source = inspect.getsource(main.missing_shopify_push_candidates)

        self.assertIn("COALESCE(order_lines.amazon_order_id, '') != ''", source)
        self.assertIn("order_lines.state IN ('ordered', 'dispatched', 'delivered')", source)
        self.assertIn("NOT EXISTS", source)
        self.assertIn("shopify_export_order_map", source)

    def test_reconciler_recovers_terminal_job_and_starts_worker(self) -> None:
        candidate = {
            "store_id": 1,
            "odoo_order_id": 23914,
            "odoo_order_name": "NC23894",
            "job_id": "job-1",
            "job_status": "completed",
        }
        line = {
            "id": 3335545,
            "store_id": 1,
            "odoo_order_id": 23914,
            "odoo_order_name": "NC23894",
            "order_engine": "chrome",
            "state": "ordered",
            "amazon_order_id": "111-0947886-1933067",
        }
        connection = MagicMock()
        update_cursor = MagicMock(rowcount=1)
        select_cursor = MagicMock()
        select_cursor.fetchall.return_value = [line]
        connection.execute.side_effect = [update_cursor, select_cursor]

        with (
            patch.object(main, "missing_shopify_push_candidates", return_value=[candidate]),
            patch.object(main, "db", return_value=_DbContext(connection)),
            patch.object(main, "enqueue_shopify_fulfilment_for_rows", return_value=1) as enqueue,
            patch.object(main, "start_shopify_fulfilment_worker") as start_worker,
            patch.object(main, "fast_page_cache_clear_matching"),
        ):
            result = main.reconcile_missing_shopify_pushes(start_worker=True)

        self.assertEqual(result["recovered_jobs"], 1)
        self.assertEqual(result["queued"], 1)
        enqueue.assert_called_once_with([line])
        start_worker.assert_called_once_with()
        update_sql = connection.execute.call_args_list[0].args[0]
        self.assertIn("status='amazon_placed'", update_sql)
        self.assertIn("completed_at=NULL", update_sql)

    def test_reconciler_does_not_clobber_running_job(self) -> None:
        candidate = {
            "store_id": 1,
            "odoo_order_id": 23914,
            "odoo_order_name": "NC23894",
            "job_id": "job-1",
            "job_status": "running",
            "job_locked_at": datetime.now(timezone.utc).isoformat(),
        }
        with (
            patch.object(main, "missing_shopify_push_candidates", return_value=[candidate]),
            patch.object(main, "db") as database,
            patch.object(main, "enqueue_shopify_fulfilment_for_rows") as enqueue,
            patch.object(main, "start_shopify_fulfilment_worker") as start_worker,
        ):
            result = main.reconcile_missing_shopify_pushes(start_worker=True)

        self.assertEqual(result["candidates"], 1)
        self.assertEqual(result["queued"], 0)
        database.assert_not_called()
        enqueue.assert_not_called()
        start_worker.assert_not_called()

    def test_reconciler_recovers_stale_running_job(self) -> None:
        candidate = {
            "store_id": 1,
            "odoo_order_id": 23914,
            "odoo_order_name": "NC23894",
            "job_id": "job-1",
            "job_status": "running",
            "job_locked_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        line = {
            "id": 3335545,
            "store_id": 1,
            "odoo_order_id": 23914,
            "odoo_order_name": "NC23894",
            "order_engine": "chrome",
            "state": "ordered",
            "amazon_order_id": "111-0947886-1933067",
        }
        connection = MagicMock()
        connection.execute.side_effect = [MagicMock(rowcount=1), MagicMock(**{"fetchall.return_value": [line]})]

        with (
            patch.object(main, "missing_shopify_push_candidates", return_value=[candidate]),
            patch.object(main, "db", return_value=_DbContext(connection)),
            patch.object(main, "enqueue_shopify_fulfilment_for_rows", return_value=1),
            patch.object(main, "start_shopify_fulfilment_worker") as start_worker,
            patch.object(main, "fast_page_cache_clear_matching"),
        ):
            result = main.reconcile_missing_shopify_pushes(start_worker=True)

        self.assertEqual(result["recovered_jobs"], 1)
        start_worker.assert_called_once_with()

    def test_enqueue_note_is_idempotent(self) -> None:
        source = inspect.getsource(main.enqueue_shopify_fulfilment_for_rows)

        self.assertIn("WHEN fulfilment_note LIKE ? THEN fulfilment_note", source)


if __name__ == "__main__":
    unittest.main()
