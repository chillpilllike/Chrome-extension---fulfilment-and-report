import inspect
import re
import sqlite3
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from pydantic import ValidationError
from app import main
from app.schemas.payloads import ReplacementPayload
from app.services.replacement_export import ReplacementExportOdoo
from app.services.shopify_scripts import dtc_orders_export, dtb_orders_export


class TestConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        return super().execute(sql.replace(" FOR UPDATE", ""), parameters)


class ReplacementQuantityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:', factory=TestConnection)
        self.conn.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        fields = {'id': 1, 'store_id': 2, 'odoo_order_id': 3, 'quantity': 1,
                  'asin': 'B000000001', 'product_name': '400 count', 'original_asin': None,
                  'original_product_name': None, 'original_quantity': None, 'replacement_quantity': None,
                  'replacement_asin': None, 'replacement_product_name': None, 'replacement_note': None,
                  'replacement_assigned_at': None, 'amazon_order_id': None, 'amazon_status': None,
                  'amazon_group_key': None, 'missing_asin': None, 'last_error': None,
                  'updated_at': None, 'state': 'pulled', 'chrome_claimed_by': None,
                  'inventory_allocated_quantity': 0, 'bundle_parent_line_id': None, 'bundle_component_count': 1, 'bundle_price_share': None}
        self.conn.execute('CREATE TABLE order_lines (' + ', '.join(fields) + ')')
        self.conn.execute('INSERT INTO order_lines VALUES (' + ','.join('?' for _ in fields) + ')', list(fields.values()))
        self.conn.commit()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.addCleanup(self.conn.close)
        for name, kwargs in {
            'db': {'side_effect': lambda: self.conn},
            'fetch_amazon_product_title': {'return_value': '200 count'},
            'get_store': {'return_value': MagicMock()}, 'OdooClient': {},
            'index_order_line': {}, 'auto_chrome_ordering_enabled': {'return_value': False},
            'fast_page_cache_clear_matching': {}, 'row_to_dict': {'side_effect': dict},
        }.items():
            self.stack.enter_context(patch.object(main, name, **kwargs))

    def assign(self, **kwargs):
        return main.api_assign_replacement(1, ReplacementPayload(store_id=2, asin='B000000002', **kwargs))['row']

    def test_change_repeat_and_reset_restore_original(self):
        row = self.assign(quantity=2)
        self.assertEqual((row['quantity'], row['original_quantity'], row['replacement_quantity']), (2, 1, 2))
        self.assertEqual(self.assign(quantity=3)['original_quantity'], 1)
        row = main.api_reset_replacement(1, {'store_id': 2})['row']
        self.assertEqual((row['quantity'], row['asin'], row['product_name']), (1, 'B000000001', '400 count'))
        self.assertIsNone(row['replacement_quantity'])

    def test_omission_keeps_current_quantity(self):
        self.assertEqual(self.assign()['quantity'], 1)
        self.assign(quantity=2)
        self.assertEqual(self.assign()['quantity'], 2)

    def test_refresh_upserts_preserve_replacement_quantity(self):
        self.assign(quantity=2)
        assignments = re.findall(r"quantity=COALESCE\(order_lines.replacement_quantity, excluded.quantity\)", inspect.getsource(main))
        self.assertEqual(len(assignments), 2)
        self.conn.execute('CREATE UNIQUE INDEX order_lines_id ON order_lines(id)')
        for assignment in assignments:
            self.conn.execute(f'INSERT INTO order_lines(id, quantity) VALUES (1, 1) ON CONFLICT(id) DO UPDATE SET {assignment}')
            self.assertEqual(self.conn.execute('SELECT quantity FROM order_lines').fetchone()['quantity'], 2)

    def test_reject_invalid_quantities(self):
        for quantity in (0, -1, 1.5, True, '2'):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                ReplacementPayload(store_id=2, asin='B000000002', quantity=quantity)

    def test_no_changes_while_queued_or_allocated(self):
        for field, value in [('amazon_status', 'chrome_queued'), ('inventory_allocated_quantity', 1), ('amazon_order_id', '123')]:
            with self.subTest(field=field):
                self.conn.execute(f'UPDATE order_lines SET {field}=?', (value,))
                self.conn.commit()
                with self.assertRaises(main.HTTPException):
                    self.assign(quantity=2)
                self.assertEqual(self.conn.execute('SELECT quantity FROM order_lines').fetchone()['quantity'], 1)
                self.conn.execute(f'UPDATE order_lines SET {field}=NULL')
                self.conn.commit()


class ReplacementExportTests(unittest.TestCase):
    def make_client(self, ids=(10,), quantity=2, image='NEW_IMAGE'):
        client = MagicMock()
        client.get_order_lines.return_value = [dict(id=i, name='400 count', product_id=[99, 'Original'], product_uom_qty=1,
            price_unit=40, price_total=40, price_subtotal=40, discount=0, display_type=False) for i in ids]
        loader = MagicMock(return_value=image)
        wrapper = ReplacementExportOdoo(client, [dict(id=7, source_ids=list(ids), quantity=quantity,
            replacement_asin='B000000002', replacement_product_name='200 count')], loader)
        return wrapper, client, loader

    def test_both_routes_build_new_quantity_product_and_image(self):
        for module in (dtc_orders_export, dtb_orders_export):
            with self.subTest(route=module.__name__):
                wrapper, client, loader = self.make_client()
                lines = wrapper.get_order_lines([10])
                shop = MagicMock(name='shop'); shop.name = 'test'
                shop.find_variant_by_sku.return_value = (None, None)
                shop.create_product_with_variant.return_value = (100, 101)
                shop.get_or_create_generic_customer.return_value = 50
                state = MagicMock()
                state.get_variant_for_sku.return_value = (None, None)
                with patch.object(module, 'ASSIGN_TO_GENERIC_CUSTOMER', True), patch.object(module, 'OVERRIDE_PRICES', False), patch.object(module, 'USE_SHOPIFY_VARIANT_PRICE_ON_ORDER_WHEN_OVERRIDES_OFF', False):
                    payload = module.build_order_payload(wrapper, shop, state, order={'name': 'TEST1', 'amount_total': 40},
                        order_lines=lines, billing_partner=None, shipping_partner=None, billing_country=None,
                        shipping_country=None, currency=None, order_tags=[], rename_manager=module.ProductRenameManager(enabled=False, mode='none'))
                self.assertEqual(payload['order']['line_items'][0]['quantity'], 2)
                self.assertEqual(payload['order']['line_items'][0]['variant_id'], 101)
                product = shop.create_product_with_variant.call_args.kwargs
                self.assertEqual(product['sku'], 'B000000002')
                self.assertEqual(product['image_b64'], 'NEW_IMAGE')
                self.assertEqual(product['title'], '200 count')
                self.assertEqual(lines[0]['price_unit'], 20)
                client.get_product_product.assert_not_called()
                client.get_product_template.assert_not_called()
                loader.assert_called_once_with('B000000002')

    def test_consolidated_sources_export_quantity_once_and_preserve_value(self):
        wrapper, _, _ = self.make_client(ids=(10, 11), quantity=3)
        lines = wrapper.get_order_lines([10, 11])
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['product_uom_qty'], 3)
        self.assertEqual(lines[0]['price_total'], 80)
        self.assertAlmostEqual(lines[0]['price_unit'] * 3, 80)

    def test_missing_image_blocks_instead_of_using_original(self):
        wrapper, client, _ = self.make_client(image='')
        with self.assertRaisesRegex(RuntimeError, 'Image sync failed'):
            wrapper.get_order_lines([10])
        client.get_product_product.assert_not_called()

    def test_missing_source_blocks_export(self):
        wrapper, client, _ = self.make_client()
        client.get_order_lines.return_value = []
        with self.assertRaisesRegex(RuntimeError, 'source lines changed'):
            wrapper.get_order_lines([10])

    def test_other_line_with_same_odoo_product_stays_unchanged(self):
        wrapper, client, _ = self.make_client()
        original = dict(client.get_order_lines.return_value[0], id=11)
        client.get_order_lines.return_value.append(original)
        lines = wrapper.get_order_lines([10, 11])
        self.assertEqual(lines[1], original)
        self.assertNotEqual(lines[0]['product_id'], lines[1]['product_id'])


if __name__ == '__main__':
    unittest.main()
