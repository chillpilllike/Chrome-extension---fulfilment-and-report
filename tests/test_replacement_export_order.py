import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from app.services.replacement_export import ReplacementExportOdoo
from app.services import shopify_title_review as review


class ReplacementExportOrderTests(unittest.TestCase):
    def test_reordered_database_rows_keep_export_and_review_fingerprint_stable(self):
        order = {'id': 1, 'order_line': [10], 'partner_shipping_id': [20, 'Recipient']}
        line = {'id': 10, 'product_id': [30, 'Original'], 'product_uom_qty': 1,
                'price_unit': 40, 'price_total': 40, 'price_subtotal': 40}
        client = Mock()
        client.get_order_by_number.side_effect = lambda name: dict(order)
        client.get_order_lines.side_effect = lambda ids: [dict(line)]
        client.get_product_product.return_value = {'id': 30, 'default_code': 'ORIGINAL', 'product_tmpl_id': False}
        replacements = [dict(id=i, source_ids=[10], bundle_parent_line_id=1,
            bundle_component_count=2, quantity=2, replacement_asin=asin,
            replacement_product_name=title) for i, asin, title in
            [(1, 'B000000001', 'First drops'), (2, 'B000000002', 'Second drops')]]
        module = SimpleNamespace(_should_ignore_odoo_line_item=lambda *args: False)
        def prepare(rows):
            wrapper = ReplacementExportOdoo(client, rows, lambda asin: 'IMAGE-' + asin)
            snapshot = review.prepare(module, review.FrozenOdoo(wrapper), 'NC1',
                {'shopify_clean_titles_enabled': 'true'}, Mock())
            return wrapper, snapshot
        first, before = prepare(replacements)
        second, after = prepare(list(reversed(replacements)))
        self.assertEqual([x['product_id'][0] for x in first.get_order_lines([10])], [-1, -2])
        self.assertEqual(first.get_order_lines([10]), second.get_order_lines([10]))
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertEqual(before['items'], after['items'])
        changed = deepcopy(replacements)
        changed[0]['quantity'] = 3
        self.assertNotEqual(before['fingerprint'], prepare(changed)[1]['fingerprint'])
        order['partner_shipping_id'] = [21, 'New recipient']
        self.assertNotEqual(before['fingerprint'], prepare(replacements)[1]['fingerprint'])

if __name__ == '__main__':
    unittest.main()
