"""Archive changes visibility only: real endpoint SQL exercised against an isolated DB."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import sqlite3
import unittest

from app.services.epost_workflow import annotate_workflow, matches_queue


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE epost_global_tracking (id INTEGER PRIMARY KEY, archived_at TEXT, status TEXT, events_json TEXT, refund_status TEXT)")
        self.conn.execute("INSERT INTO epost_global_tracking VALUES (1, NULL, 'Delivered', '[1,2]', 'claimed')")
        self.invalidations = []

        @contextmanager
        def db():
            with self.conn:
                yield self.conn

        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code

        tree = ast.parse(Path("app/main.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "api_epost_archive")
        fn.decorator_list = []
        self.ns = dict(Any=Any, EpostArchivePayload=SimpleNamespace, db=db, HTTPException=HTTPException,
                       utc_now=lambda: "2026-09-05T12:00:00Z", fast_page_cache_clear_matching=self.invalidations.append)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "archive", "exec"), self.ns)

    def test_archive_restore_preserves_carrier_and_refund_history(self):
        call = self.ns["api_epost_archive"]
        call(1, SimpleNamespace(archived=True))
        row = dict(self.conn.execute("SELECT * FROM epost_global_tracking").fetchone())
        self.assertTrue(row["archived_at"])
        self.assertEqual(("Delivered", "[1,2]", "claimed"), (row["status"], row["events_json"], row["refund_status"]))
        call(1, SimpleNamespace(archived=False))
        self.assertIsNone(self.conn.execute("SELECT archived_at FROM epost_global_tracking").fetchone()[0])
        self.assertEqual([{"epost-tracking"}, {"epost-tracking"}], self.invalidations)

    def test_archive_is_idempotent(self):
        self.ns["api_epost_archive"](1, SimpleNamespace(archived=True))
        self.ns["utc_now"] = lambda: "later"
        self.ns["api_epost_archive"](1, SimpleNamespace(archived=True))
        self.assertEqual("2026-09-05T12:00:00Z", self.conn.execute("SELECT archived_at FROM epost_global_tracking").fetchone()[0])

    def test_unknown_record_is_404(self):
        with self.assertRaises(self.ns["HTTPException"]) as result:
            self.ns["api_epost_archive"](999, SimpleNamespace(archived=True))
        self.assertEqual(404, result.exception.status_code)
        self.assertEqual([], self.invalidations)

    def test_archived_excluded_from_all_normal_queues(self):
        row = annotate_workflow({"status": "Delivered", "archived_at": "2026-09-05", "refund_status": "claimed"})
        for queue in ["all", "", "attention", "active", "delivered", "refund_claimed"]:
            self.assertFalse(matches_queue(row, queue), queue)
        self.assertTrue(matches_queue(row, "archived"))
        row["archived_at"] = None
        self.assertFalse(matches_queue(row, "archived"))
        self.assertTrue(matches_queue(row, "delivered"))
        self.assertTrue(matches_queue(row, "refund_claimed"))


if __name__ == "__main__":
    unittest.main()
