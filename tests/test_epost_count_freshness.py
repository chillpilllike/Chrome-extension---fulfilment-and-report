"""Exercise the real API function against changing saved shipment snapshots."""
import ast
import copy
from pathlib import Path
from typing import Any, Optional
import unittest
from app.services.epost_workflow import QUEUES as EPOST_WORK_QUEUES, annotate_workflow, matches_queue


class EpostCountFreshnessTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(Path('app/main.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_epost_tracking')
        fn.decorator_list = []
        self.rows = [dict(id=i, status='There was an error locating tracking number') for i in range(13)]
        self.cache = {}
        outer = self

        class DB:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def execute(self, *args): return self
            def fetchall(self): return copy.deepcopy(outer.rows)

        def paged(store, page, size, status, days, stale, q):
            rows = [annotate_workflow(dict(r), days) for r in self.rows]
            rows = [r for r in rows if matches_queue(r, status) and (not q or q in str(r['id']))]
            return rows[(page-1)*size:page*size], len(rows), page, size

        def cache_set(key, value, ttl):
            self.cache[key] = copy.deepcopy(value)
            return value

        ns = dict(Any=Any, Optional=Optional, pagination_bounds=lambda p, s: (p, s, (p-1)*s),
                  clean_text=lambda s: str(s or '').strip(), paged_epost_tracking_rows=paged,
                  db=DB, EPOST_WORK_QUEUES=EPOST_WORK_QUEUES, annotate_workflow=annotate_workflow,
                  matches_queue=matches_queue, fast_page_cache_get=self.cache.get,
                  fast_page_cache_set=cache_set)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'epost_api', 'exec'), ns)
        self.api = ns['api_epost_tracking']

    def test_switching_every_queue_after_scan_uses_current_store_counts(self):
        queues = [*EPOST_WORK_QUEUES, 'all', 'attention', 'active', 'refund_claimed', 'refund_received', 'archived']
        for queue in queues:
            self.assertEqual(13, self.api(status=queue, include_summary=True)['summary']['lookup_error'])
        self.rows.extend(dict(id=i, status='There was an error locating tracking number') for i in range(13, 16))
        for queue in queues:
            with self.subTest(queue=queue):
                result = self.api(status=queue, include_summary=True)
                self.assertEqual(16, result['summary']['lookup_error'])
                self.assertEqual(result['summary'][queue], result['total'])

    def test_reload_search_and_pagination_do_not_replay_old_snapshots(self):
        for page, q in [(1, ''), (2, ''), (1, '1')]:
            self.api(status='lookup_error', page=page, per_page=5, q=q, include_summary=True)
        self.rows[1]['status'] = 'Delivered'
        self.rows[2]['archived_at'] = '2026-09-20'
        for page, q in [(1, ''), (2, ''), (1, '1')]:
            result = self.api(status='lookup_error', page=page, per_page=5, q=q, include_summary=True)
            self.assertEqual(11, result['summary']['lookup_error'])
            self.assertEqual(1, result['summary']['archived'])
            self.assertEqual(3 if q else 11, result['total'])
            self.assertNotIn(1, [r['id'] for r in result['rows']])


if __name__ == '__main__': unittest.main()
