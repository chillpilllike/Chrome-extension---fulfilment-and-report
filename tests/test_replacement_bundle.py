import ast
import inspect
import re
import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
from app import main
from app.schemas.payloads import ReplacementPayload
from app.services import replacement_bundle
from app.services.replacement_export import ReplacementExportOdoo
from tests.test_replacement_quantity import TestConnection
from tests.test_replacement_images import image_data


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:', factory=TestConnection)
        self.conn.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('CREATE TABLE stores (id INTEGER PRIMARY KEY)')
        self.conn.execute('INSERT INTO stores VALUES (2)')
        source = inspect.getsource(main.init_db)
        schema = re.search(r'CREATE TABLE IF NOT EXISTS order_lines \(.*?\);', source, re.S)[0]
        self.conn.execute(schema)
        columns = {r['name'] for r in self.conn.execute('PRAGMA table_info(order_lines)')}
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('ALTER TABLE order_lines ADD COLUMN '):
                column = node.value.split()[5]
                if column not in columns:
                    self.conn.execute(node.value)
                    columns.add(column)
        self.conn.execute(re.search(r'CREATE TABLE IF NOT EXISTS replacement_product_images \(.*?\);', source, re.S)[0])
        self.conn.execute('''INSERT INTO order_lines (id, store_id, odoo_order_id, odoo_order_name, odoo_line_id,
            asin, product_name, quantity, store_unit_price, store_total_price, store_total_native,
            store_currency, store_currency_rate_to_usd, created_at, updated_at, source_odoo_line_ids)
            VALUES (1, 2, 3, 'TEST1', 10, 'B000000001', 'Original bundle', 1, 100, 100, 100, 'USD', 1, 'now', 'now', '10')''')
        self.conn.commit()
        self.stack = ExitStack()
        self.addCleanup(self.conn.close)
        self.addCleanup(self.stack.close)
        for name, kwargs in {'db': {'side_effect': lambda: self.conn}, 'fetch_amazon_product_title': {'side_effect': lambda asin: asin},
            'index_order_line': {}, 'delete_order_line_index': {}, 'fast_page_cache_clear_matching': {},
            'auto_chrome_ordering_enabled': {'return_value': False}}.items():
            self.stack.enter_context(patch.object(main, name, **kwargs))

    def assign(self, line_id=1, components=None):
        return main.api_assign_replacement(line_id, ReplacementPayload(store_id=2, components=components or [
            {'asin': 'B000000002', 'quantity': 2}, {'asin': 'B000000003', 'quantity': 3}]))

    def rows(self):
        return self.conn.execute('SELECT * FROM order_lines ORDER BY id').fetchall()

    def test_creates_two_components_and_preserves_order_value(self):
        self.assign()
        rows = self.rows()
        self.assertEqual([(r['asin'], r['quantity']) for r in rows], [('B000000002', 2), ('B000000003', 3)])
        self.assertEqual(rows[1]['bundle_parent_line_id'], 1)
        self.assertLess(rows[1]['odoo_line_id'], 0)
        self.assertEqual(main.source_odoo_line_ids(rows[1]), [10])
        self.assertEqual(sum(main.order_line_store_total(row) for row in rows), 100)
        self.assertEqual([r['store_unit_price'] for r in rows], [25, 50 / 3])
        self.assertEqual(len({r['odoo_order_id'] for r in rows}), 1)

    def test_selecting_either_component_selects_both(self):
        self.assign()
        ids = [r['id'] for r in self.rows()]
        for selected in ids:
            self.assertEqual(replacement_bundle.expand_bundle_selection(self.conn, 2, [selected]), ids)
            self.assertEqual(main.normalize_selected_chrome_line_ids(self.conn, 2, [selected]), ids)
        with self.assertRaises(main.HTTPException):
            main.require_complete_bundle_queue(self.conn, self.rows()[:1])
        main.require_complete_bundle_queue(self.conn, self.rows())

    def test_chrome_job_contains_both_quantities_with_consumer_routing(self):
        self.assign()
        rows = self.rows()
        for row in rows:
            row.update(state='submitted', amazon_group_key='chrome-bundle', amazon_account_id=7)
        with patch.object(main, 'chrome_account_type_routing_enabled', return_value=True):
            job = main.chrome_job_from_rows(rows, {7: {'id': 7, 'name': 'Consumer'}})
            self.assertEqual(job['required_account_experience'], 'consumer')
            self.assertFalse(main.chrome_account_experience_matches(rows, 'business'))
        self.assertEqual([(item['asin'], item['quantity']) for item in job['items']], [('B000000002', 2), ('B000000003', 3)])
        self.assertNotEqual(job['items'][0]['line_ids'], job['items'][1]['line_ids'])
        self.assertEqual(sum(item['store_total_price'] for item in job['items']), 100)

    def test_edit_from_child_and_reset_restore_original_once(self):
        self.assign()
        child = self.rows()[1]['id']
        self.assign(child, [{'asin': 'B000000002', 'quantity': 4}, {'asin': 'B000000003', 'quantity': 1}])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.rows()[1]['id'], child)
        details = main.api_get_replacement_components(child)
        self.assertEqual(details['root_line_id'], 1)
        self.assertEqual(len(details['components']), 2)
        result = main.api_reset_replacement(child, {'store_id': 2})
        self.assertTrue(result['ok'])
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['asin'], rows[0]['quantity'], rows[0]['store_total_price'], rows[0]['store_unit_price']), ('B000000001', 1, 100, 100))

    def test_replacing_bundle_with_single_asin_removes_child(self):
        self.assign()
        self.assign(components=[{'asin': 'B000000004', 'quantity': 2}])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['store_total_native'], 100)
        self.assertEqual(self.rows()[0]['store_unit_price'], 50)
        self.assign(components=[{'asin': 'B000000004', 'quantity': 4}])
        self.assertEqual(self.rows()[0]['store_unit_price'], 25)

    def test_child_manual_image_survives_promotion_to_parent(self):
        image = image_data()
        self.assign(components=[{'asin': 'B000000002', 'quantity': 2},
                                {'asin': 'B000000003', 'quantity': 3, 'image_base64': image}])
        self.assign(components=[{'asin': 'B000000003', 'quantity': 4}])
        saved = self.conn.execute('SELECT * FROM replacement_product_images WHERE order_line_id=1 AND asin=?', ('B000000003',)).fetchone()
        self.assertEqual(saved['image_base64'], image)
        self.assertEqual(len(self.rows()), 1)

    def test_invalid_duplicate_quantities_and_locked_sibling_block_all_changes(self):
        with self.assertRaises(ValidationError):
            ReplacementPayload(store_id=2, components=[{'asin': 'B000000002', 'quantity': 0}])
        with self.assertRaises(main.HTTPException):
            self.assign(components=[{'asin': 'B000000002', 'quantity': 1}, {'asin': 'B000000002', 'quantity': 2}])
        self.assertEqual(len(self.rows()), 1)
        self.assign()
        child = self.rows()[1]['id']
        self.conn.execute('UPDATE order_lines SET amazon_order_id=? WHERE id=?', ('111-1111111-1111111', child))
        self.conn.commit()
        with self.assertRaises(main.HTTPException):
            self.assign(components=[{'asin': 'B000000004', 'quantity': 1}])
        with self.assertRaises(main.HTTPException):
            main.api_reset_replacement(1, {'store_id': 2})
        self.assertEqual(len(self.rows()), 2)

    def test_financial_refresh_keeps_component_quantities_and_combined_value(self):
        self.assign()
        self.conn.execute('UPDATE order_lines SET store_total_price=60, store_total_native=60, store_unit_price=30 WHERE id=1')
        replacement_bundle.sync_imported_bundle_finances(self.conn, 2)
        rows = self.rows()
        self.assertEqual(sum(row['store_total_native'] for row in rows), 120)
        self.assertEqual([r['quantity'] for r in rows], [2, 3])
        self.assertEqual(rows[1]['store_unit_price'], 20)

    def test_shopify_exports_two_components_with_distinct_images_not_original_bundle(self):
        self.assign()
        rows = self.rows()
        for row in rows:
            row['source_ids'] = [10]
        client = MagicMock()
        client.get_order_lines.return_value = [{'id': 10, 'product_id': [99, 'Original'], 'product_uom_qty': 1, 'price_unit': 100, 'price_total': 100, 'price_subtotal': 100}]
        wrapper = ReplacementExportOdoo(client, rows, lambda asin: 'IMAGE-' + asin)
        lines = wrapper.get_order_lines([10])
        self.assertEqual(len(lines), 2)
        self.assertEqual([line['product_uom_qty'] for line in lines], [2, 3])
        self.assertEqual(sum(line['price_total'] for line in lines), 100)
        products = [wrapper.get_product_product(line['product_id'][0]) for line in lines]
        self.assertEqual([p['default_code'] for p in products], ['B000000002', 'B000000003'])
        self.assertEqual([p['image_1920'] for p in products], ['IMAGE-B000000002', 'IMAGE-B000000003'])
        client.get_product_product.assert_not_called()


if __name__ == '__main__':
    unittest.main()
