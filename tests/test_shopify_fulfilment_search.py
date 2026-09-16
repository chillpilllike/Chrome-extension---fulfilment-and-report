import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import main


class ShopifyFulfilmentSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('''
            CREATE TABLE stores (id INTEGER, name TEXT, odoo_db TEXT, odoo_url TEXT);
            CREATE TABLE shopify_fulfilment_jobs (
                id TEXT, store_id INTEGER, odoo_order_name TEXT, route TEXT,
                status TEXT, created_at TEXT, last_error TEXT, odoo_order_id INTEGER);
            CREATE TABLE shopify_export_order_map (
                state_scope TEXT, src_order_key TEXT, dest_name TEXT, dest_order_id TEXT);
            CREATE TABLE shopify_export_oauth_tokens (state_scope TEXT, dest_name TEXT, shop TEXT);
            CREATE TABLE shopify_order_status_cache (
                store_id INTEGER, route TEXT, odoo_order_name TEXT, dest_name TEXT,
                shopify_order_name TEXT, fulfillment_status TEXT, financial_status TEXT,
                cancelled_at TEXT, synced_at TEXT);
            INSERT INTO stores VALUES (1, 'Store A', 'db-a', ''), (2, 'Store B', 'db-b', '');
            INSERT INTO shopify_fulfilment_jobs VALUES
                ('a', 1, 'NC24059', 'dtc', 'completed', '2026-09-01', '', 1),
                ('b', 1, 'NC24060', 'dtc', 'queued', '2026-09-02', '', 2),
                ('c', 2, 'NC24059', 'dtb', 'completed', '2026-09-03', '', 3),
                ('d', 1, 'SPECIAL_%', 'dtc', 'queued', '2026-09-04', '', 4);
            INSERT INTO shopify_export_order_map VALUES ('dtc', 'db-a:NC24059', 'DTC', '987654321');
            INSERT INTO shopify_order_status_cache VALUES
                (1, 'dtc', 'NC24059', 'DTC', '#DTC1050', 'fulfilled', 'paid', '', '2026-09-01');
        ''')

        @contextmanager
        def database():
            yield self.conn

        patcher = patch.object(main, 'db', database)
        patcher.start()
        self.addCleanup(patcher.stop)

    def search(self, **kwargs):
        rows, total, _, _ = main.list_shopify_fulfilment_jobs(**kwargs)
        return [row['id'] for row in rows], total

    def test_partial_case_insensitive_order_search_before_pagination(self):
        self.assertEqual(self.search(search=' nc24059 ', per_page=1), (['c'], 2))
        self.assertEqual(self.search(search='nc24059', per_page=1, page=2), (['a'], 2))

    def test_shopify_reference_and_id_with_optional_hash(self):
        for term in ['#DTC1050', 'dtc105', '#987654321', '76543']:
            with self.subTest(term=term):
                self.assertEqual(self.search(search=term), (['a'], 1))

    def test_store_and_status_filters_are_preserved(self):
        self.assertEqual(self.search(search='24059', store_id=1), (['a'], 1))
        self.assertEqual(self.search(search='24059', status='queued'), ([], 0))

    def test_literal_wildcards_and_no_matches(self):
        self.assertEqual(self.search(search='_%'), (['d'], 1))
        self.assertEqual(self.search(search="' OR 1=1 --"), ([], 0))
        self.assertEqual(self.search(search='missing'), ([], 0))

    def test_api_passes_search_to_paginated_query(self):
        with patch.object(main, 'shopify_oauth_route_status', return_value=[]), \
             patch.object(main, 'shopify_fulfilment_job_status_counts', return_value={}), \
             patch.object(main, 'shopify_fulfilment_progress', return_value={}):
            result = main.api_shopify_fulfilment_jobs(search='#987654321', store_id=1)
        self.assertEqual(result['total'], 1)
        self.assertEqual([row['id'] for row in result['jobs']], ['a'])

    def test_blank_search_keeps_existing_results(self):
        self.assertEqual(self.search(search='  ')[1], 4)


if __name__ == '__main__':
    unittest.main()
