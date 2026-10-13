import unittest
from unittest.mock import Mock
from types import SimpleNamespace
from app.services.shopify_scripts import dtc_orders_export, dtb_orders_export
from app.services import shopify_title_review
from app.main import is_delivery_line, should_skip_order_line, adjustment_line_kind


class ShippingExportTests(unittest.TestCase):
    modules = (dtc_orders_export, dtb_orders_export)

    def test_shipping_never_reaches_destination_writes(self):
        for module in self.modules:
            for flagged, sku, qty in [(True, 'CUSTOM-CARRIER', 1), (False, 'EPG_39_PACKET', 1),
                                      (False, 'epg_39_parcel', .5), (False, 'Delivery_007', .5)]:
                with self.subTest(module=module.__name__, flagged=flagged, sku=sku, qty=qty):
                    odoo, shop, state, rename = Mock(), Mock(), Mock(), Mock()
                    odoo.get_product_product.return_value = {'id': 1, 'default_code': sku}
                    result = module.ensure_product_variant_for_line(
                        odoo, shop, state, line={'id': 2, 'name': 'Renamed carrier',
                                                'is_delivery': flagged, 'product_id': [1, 'Renamed carrier']},
                        order_name='NC33546', rename_manager=rename,
                        override_prices_for_order=True, qty_raw=qty,
                        qty_int=1 if qty == 1 else None)
                    self.assertEqual(result, (None, None, None))
                    self.assertEqual(shop.mock_calls, [])
                    self.assertEqual(state.mock_calls, [])
                    self.assertEqual(rename.mock_calls, [])

    def test_legacy_shipping_and_real_merchandise(self):
        for module in self.modules:
            self.assertTrue(module._should_ignore_odoo_line_item('Delivery_007', 'renamed'))
            self.assertTrue(module._should_ignore_odoo_line_item('', 'Standard delivery'))
            self.assertTrue(module._should_ignore_odoo_line_item('EPG_39_PACKET', 'renamed'))
            for sku, title in [('REAL-SKU', 'Lotion Free Shipping'), ('EPG_VITAMINS', 'Vitamins'),
                               ('EPG_39_PACKET_EXTRA', 'Accessory')]:
                self.assertFalse(module._should_ignore_odoo_line_item(sku, title))

    def test_delivery_flag_schema_is_optional_and_cached(self):
        for module in self.modules:
            for available in (True, False):
                client = module.OdooClient('https://example.invalid', 'test', 'test', 'test')
                client._exec = Mock(return_value={'is_delivery': {'type': 'boolean'}} if available else {})
                client.read = Mock(return_value=[])
                client.get_order_lines([1]); client.get_order_lines([2])
                client._exec.assert_called_once()
                self.assertEqual('is_delivery' in client.read.call_args.args[2], available)

    def test_title_reviews_exclude_delivery_without_brand_or_title_work(self):
        for module in self.modules:
            client = Mock()
            client.get_order_by_number.return_value = {'order_line': [1, 2, 3]}
            client.get_order_lines.return_value = [
                {'id': 1, 'name': 'Renamed delivery', 'is_delivery': True, 'price_unit': 0},
                {'id': 2, 'name': 'EPG shipping', 'product_id': [2, 'EPG shipping'], 'price_unit': 5},
                {'id': 3, 'name': 'Lotion Free Shipping', 'product_id': [3, 'Lotion'], 'price_unit': 5}]
            client.get_product_product.side_effect = lambda pid: {
                'id': pid, 'default_code': 'EPG_39_PACKET' if pid == 2 else 'REAL-SKU',
                'name': 'EPG shipping' if pid == 2 else 'Lotion Free Shipping', 'product_tmpl_id': False}
            rename = Mock()
            rename.resolve_title.return_value = 'Lotion Free Shipping'
            rename.destination_sku.return_value = 'REAL-SKU'
            snapshot = shopify_title_review.prepare(module, client, 'NC33546', {}, rename)
            self.assertEqual([x['line_id'] for x in snapshot['items']], [3])
            self.assertEqual(rename.resolve_title.call_count, 1)

    def test_reporting_classifies_shipping_as_delivery(self):
        for line, sku in [({'name': 'Renamed', 'is_delivery': True}, 'CUSTOM'),
                          ({'name': 'Renamed'}, 'EPG_39_PACKET')]:
            product = {'default_code': sku, 'type': 'service'}
            self.assertTrue(is_delivery_line(line, sku))
            self.assertTrue(should_skip_order_line(line, product, {}))
            self.assertEqual(adjustment_line_kind(line, product, {}), 'delivery')
        self.assertFalse(is_delivery_line({'name': 'Lotion Free Shipping'}, 'REAL-SKU'))


if __name__ == '__main__':
    unittest.main()
