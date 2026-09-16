import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services import shopify_title_review as review
from app.services.replacement_export import ReplacementExportOdoo


class ShopifyFallbackSkuTests(unittest.TestCase):
    def prepare(self, reference='', replacement=False):
        client = Mock()
        client.url = 'https://source.example'
        client.db = 'store'
        client.get_order_by_number.return_value = {'order_line': [11221]}
        client.get_order_lines.return_value = [{
            'id': 11221, 'product_id': [15, 'Product'], 'name': 'Product',
            'price_unit': 20, 'price_total': 20, 'price_subtotal': 20, 'product_uom_qty': 1,
        }]
        client.get_product_product.return_value = {
            'id': 15, 'name': 'Product', 'default_code': reference, 'product_tmpl_id': False,
        }
        wrapped = client
        if replacement:
            wrapped = ReplacementExportOdoo(client, [{
                'id': 3528708, 'source_ids': [11221], 'quantity': 1,
                'replacement_asin': 'B08FF4J72S', 'replacement_product_name': 'Replacement Product',
                'replacement_image_base64': 'saved-image',
            }], Mock(side_effect=AssertionError('Should use saved image')))
        module = SimpleNamespace(_should_ignore_odoo_line_item=lambda *args: False)
        snapshot = review.prepare(
            module, review.FrozenOdoo(wrapped), 'SG00130',
            {'shopify_clean_titles_enabled': 'true', 'shopify_title_remove_keywords': ''}, Mock(),
        )
        review.validate_items(snapshot['items'], True, '')
        client._exec.assert_not_called()
        client.write.assert_not_called()
        client.read.assert_not_called()
        return snapshot['items'][0]['sku']

    def test_saved_replacement_asin_used_without_original_reference(self):
        self.assertEqual(self.prepare(replacement=True), 'B08FF4J72S')

    def test_existing_reference_preserved_with_or_without_replacement(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                self.assertEqual(self.prepare('EXISTING-REF', replacement), 'EXISTING-REF')

    def test_missing_reference_gets_stable_shopify_only_sku(self):
        first = self.prepare()
        self.assertRegex(first, r'^SHOP-[A-F0-9]{20}$')
        self.assertEqual(first, self.prepare())

    def test_generated_sku_is_scoped_to_store_and_product(self):
        client = SimpleNamespace(url='https://source.example', db='one')
        first = review.shopify_only_sku(client, {'id': 15}, {'id': 11221})
        self.assertEqual(first, review.shopify_only_sku(client, {'id': 15}, {'id': 99999}))
        self.assertNotEqual(first, review.shopify_only_sku(client, {'id': 16}, {'id': 11221}))
        client.db = 'two'
        self.assertNotEqual(first, review.shopify_only_sku(client, {'id': 15}, {'id': 11221}))
