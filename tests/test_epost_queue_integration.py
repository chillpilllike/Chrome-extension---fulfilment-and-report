"""Exercise production filtering/pagination without starting background jobs or a DB."""
import ast
from pathlib import Path
from typing import Any, Optional
import unittest
from datetime import datetime, timezone
from app.services.epost_workflow import annotate_workflow, matches_queue


class QueueIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path("app/main.py").read_text())
        wanted = {"filter_epost_tracking_rows", "paged_epost_tracking_rows", "epost_tracking_row_matches_query"}
        cls.code = compile(ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted], type_ignores=[]), "epost_queue_functions", "exec")

    def setUp(self):
        self.snapshots = [{"id": i, "status": "There was an error locating tracking number", "last_update_at": "", "created_at": "2026-08-01"} for i in range(1, 126)]
        self.snapshots.append({"id": 126, "status": "Delivered", "last_update_at": "2026-08-02"})
        outer = self

        class Connection:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, query, params):
                outer.db_params = params
                return self
            def fetchall(self): return outer.snapshots

        def paginate(rows, page, per_page):
            return rows[(page-1)*per_page:page*per_page], len(rows), page, per_page

        def enrich(ids, stale_days):
            outer.enriched_ids = ids
            return [{"id": ident, "enriched": True} for ident in reversed(ids)]

        import re, json
        self.ns = {"Any": Any, "Optional": Optional, "re": re, "json": json,
                   "clean_text": lambda value: str(value or "").strip(), "matches_queue": matches_queue,
                   "annotate_epost_staleness": lambda row, days: annotate_workflow(row, days, datetime(2026, 9, 5, tzinfo=timezone.utc)),
                   "refresh_epost_lost_statuses": lambda store: None, "db": Connection,
                   "rows_to_dicts": lambda rows: [dict(row) for row in rows],
                   "pagination_bounds": lambda page, size: (page, size, (page-1)*size),
                   "paginate_values": paginate, "epost_tracking_rows_by_ids": enrich,
                   "epost_tracking_rows": lambda store: self.snapshots}
        exec(self.code, self.ns)

    def test_queue_filters_entire_store_before_page_enrichment(self):
        rows, total, page, size = self.ns["paged_epost_tracking_rows"](7, 2, 20, "lookup_error")
        self.assertEqual(125, total)
        self.assertEqual(list(range(21, 41)), [row["id"] for row in rows])
        self.assertEqual(20, len(self.enriched_ids))
        self.assertEqual((7, 7), self.db_params)
        self.assertEqual((2, 20), (page, size))

    def test_search_and_queue_are_intersected(self):
        self.snapshots[0]["odoo_order_name"] = "NH00642"
        rows = self.ns["filter_epost_tracking_rows"](self.snapshots, "lookup_error", 10, False, "NH00642")
        self.assertEqual([1], [row["id"] for row in rows])
        self.assertEqual([], self.ns["filter_epost_tracking_rows"](self.snapshots, "delivered", 10, False, "NH00642"))

    def test_legacy_lost_filter_requires_explicit_loss(self):
        self.snapshots[0]["epost_status"] = "lost"
        self.assertEqual([], self.ns["filter_epost_tracking_rows"](self.snapshots, "lost", 10, False))

    def test_archive_filter_before_pagination_and_search(self):
        self.snapshots[0]["archived_at"] = "2026-09-05"
        self.snapshots[0]["odoo_order_name"] = "OLD-ORDER"
        rows, total, _, _ = self.ns["paged_epost_tracking_rows"](7, 1, 20, "archived")
        self.assertEqual(1, total)
        self.assertEqual([1], [row["id"] for row in rows])
        normal = self.ns["filter_epost_tracking_rows"](self.snapshots, "all", 10, False)
        self.assertNotIn(1, [row["id"] for row in normal])
        self.assertEqual([], self.ns["filter_epost_tracking_rows"](self.snapshots, "all", 10, False, "OLD-ORDER"))
        archived = self.ns["filter_epost_tracking_rows"](self.snapshots, "archived", 10, False, "OLD-ORDER")
        self.assertEqual([1], [row["id"] for row in archived])


if __name__ == "__main__": unittest.main()
