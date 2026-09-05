import unittest
from app.services.after_order_email_images import with_email_images
from app.services.after_order_email import render_after_order_email


class EmailImageTests(unittest.TestCase):
    def test_legacy_dispatch_item_gets_an_absolute_thumbnail(self):
        case = {'case_type': 'expected_dispatch', 'affected_items': [{'asin': 'B00IP1E3O0', 'product_name': 'Product'}]}
        result = with_email_images(case, 'https://app.example/')
        self.assertEqual(result['affected_items'][0]['thumbnail_url'], 'https://app.example/api/public/asin-image/B00IP1E3O0')
        self.assertNotIn('thumbnail_url', case['affected_items'][0])
        _, markup, _ = render_after_order_email(result, 'https://shop.example/order', actions=[], labels={})
        self.assertIn('src="https://app.example/api/public/asin-image/B00IP1E3O0"', markup)

    def test_preview_relative_and_existing_store_images(self):
        case = {'affected_items': [{'thumbnail_url': '/api/public/asin-image/B00IP1E3O0'}, {'thumbnail_url': 'https://shop.example/image.jpg'}]}
        result = with_email_images(case, 'https://app.example')
        self.assertTrue(result['affected_items'][0]['thumbnail_url'].startswith('https://app.example/'))
        self.assertEqual(result['affected_items'][1]['thumbnail_url'], 'https://shop.example/image.jpg')

    def test_missing_invalid_asins_do_not_invent_product_images(self):
        result = with_email_images({'affected_items': [{'asin': ''}, {'asin': '../private'}]}, 'https://app.example')
        self.assertEqual([i['thumbnail_url'] for i in result['affected_items']], ['', ''])

    def test_invalid_public_base_is_rejected(self):
        for base in ('//app.example', 'javascript:alert(1)', 'https://secret@app.example'):
            with self.assertRaises(ValueError):
                with_email_images({}, base)
