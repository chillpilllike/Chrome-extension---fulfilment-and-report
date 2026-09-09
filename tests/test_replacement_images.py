import base64
import inspect
import io
import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image
from app import main
from app.services.replacement_export import ReplacementExportOdoo
from app.services.replacement_images import ReplacementImageSyncError, image_failure_details, validate_image_base64
from tests import test_replacement_quantity as quantity_tests


def image_data(fmt='PNG', size=(1600, 1200)):
    output = io.BytesIO()
    Image.new('RGB', size, 'white').save(output, format=fmt)
    return base64.b64encode(output.getvalue()).decode('ascii')


class ImageValidationTests(unittest.TestCase):
    def test_preserves_high_resolution_original_bytes(self):
        for fmt in ('PNG', 'JPEG', 'WEBP'):
            with self.subTest(fmt=fmt):
                data = image_data(fmt)
                result = validate_image_base64(data)
                self.assertEqual(result['image_base64'], data)
                self.assertEqual((result['width'], result['height']), (1600, 1200))

    def test_invalid_oversized_and_non_image_files_rejected(self):
        for value in (None, '', 'bad!', base64.b64encode(b'<svg/>').decode(), 'A' * 16777220):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                validate_image_base64(value)
        with patch('app.services.replacement_images.MAX_IMAGE_PIXELS', 100):
            with self.assertRaisesRegex(ValueError, 'megapixels'):
                validate_image_base64(image_data(size=(20, 20)))

    def test_actionable_failure_and_unrelated_errors(self):
        error = ReplacementImageSyncError(7, 'B000000002')
        self.assertEqual(image_failure_details(str(error)), {'line_id': 7, 'asin': 'B000000002'})
        self.assertIsNone(image_failure_details('HTTP 401 Unauthorized'))

    def test_amazon_explicit_hires_precedes_small_dynamic_image(self):
        response = SimpleNamespace(status_code=200, text='''<img data-old-hires="https://m.media-amazon.com/images/I/full.jpg" data-a-dynamic-image='{"https://m.media-amazon.com/images/I/thumb.jpg":[300,300]}'/>''')
        with patch.object(main.requests, 'get', return_value=response):
            self.assertEqual(main.amazon_product_page_image_url('B000000002'), 'https://m.media-amazon.com/images/I/full.jpg')

    def test_amazon_dynamic_selects_largest_available_image(self):
        response = SimpleNamespace(status_code=200, text='''<img data-a-dynamic-image='{"https://m.media-amazon.com/images/I/small.jpg":[300,300],"https://m.media-amazon.com/images/I/large.jpg":[2000,2000]}'/>''')
        with patch.object(main.requests, 'get', return_value=response):
            self.assertEqual(main.amazon_product_page_image_url('B000000002'), 'https://m.media-amazon.com/images/I/large.jpg')


class ImageFlowTests(quantity_tests.ReplacementQuantityTests):
    def setUp(self):
        super().setUp()
        self.conn.execute('ALTER TABLE order_lines ADD COLUMN odoo_order_name TEXT')
        self.conn.execute("UPDATE order_lines SET odoo_order_name='TEST1'")
        self.conn.execute('ALTER TABLE order_lines ADD COLUMN replacement_run_id TEXT')
        self.conn.execute('ALTER TABLE order_lines ADD COLUMN odoo_line_id INTEGER')
        self.conn.execute('UPDATE order_lines SET odoo_line_id=10')
        schema = re.search(r'CREATE TABLE IF NOT EXISTS replacement_product_images \(.*?\);', inspect.getsource(main.init_db), re.S)[0]
        self.conn.execute(schema)
        self.conn.execute('''CREATE TABLE shopify_fulfilment_jobs (id TEXT PRIMARY KEY, store_id INTEGER,
            odoo_order_name TEXT, route TEXT, status TEXT, last_error TEXT, attempts INTEGER,
            max_attempts INTEGER, next_run_at TEXT, updated_at TEXT, completed_at TEXT)''')
        self.conn.execute('INSERT INTO shopify_fulfilment_jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            ('job1', 2, 'TEST1', 'dtc', 'dead', str(ReplacementImageSyncError(1, 'B000000002')), 1, 5, '', '', ''))
        self.conn.commit()
        self.worker = self.stack.enter_context(patch.object(main, 'start_shopify_fulfilment_worker'))

    def test_assignment_saves_manual_image_and_export_avoids_amazon(self):
        image = image_data()
        self.assign(quantity=2, image_base64=image)
        self.assertEqual(self.conn.execute('SELECT asin FROM replacement_product_images').fetchone()['asin'], 'B000000002')
        client = MagicMock()
        client.get_order_lines.return_value = [{'id': 10, 'product_uom_qty': 1, 'price_unit': 40}]
        with patch.object(main, 'amazon_product_page_image_url') as amazon:
            wrapper = main.shopify_replacement_export_client(client, 2, 'TEST1')
            line = wrapper.get_order_lines([10])[0]
            product = wrapper.get_product_product(line['product_id'][0])
            self.assertEqual(product['image_1920'], image)
            self.assertEqual(line['product_uom_qty'], 2)
            amazon.assert_not_called()

    def test_invalid_image_does_not_save_asin_or_quantity(self):
        with self.assertRaises(main.HTTPException):
            self.assign(quantity=2, image_base64='bad!')
        row = self.conn.execute('SELECT * FROM order_lines').fetchone()
        self.assertEqual((row['asin'], row['quantity']), ('B000000001', 1))

    def test_job_upload_saves_and_requeues_exact_failure(self):
        self.assign(quantity=2)
        result = main.api_shopify_replacement_image('job1', {'asin': 'B000000002', 'image_base64': image_data()})
        self.assertTrue(result['ok'])
        job = self.conn.execute('SELECT * FROM shopify_fulfilment_jobs').fetchone()
        self.assertEqual((job['status'], job['attempts'], job['last_error']), ('amazon_placed', 0, ''))
        self.worker.assert_called_once()

    def test_stale_asin_or_running_job_cannot_accept_upload(self):
        self.assign(quantity=2)
        for sql in ("UPDATE order_lines SET replacement_asin='B000000003'", "UPDATE shopify_fulfilment_jobs SET status='running'"):
            self.conn.execute(sql)
            self.conn.commit()
            with self.assertRaises(main.HTTPException) as error:
                main.api_shopify_replacement_image('job1', {'asin': 'B000000002', 'image_base64': image_data()})
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) AS n FROM replacement_product_images').fetchone()['n'], 0)
        self.worker.assert_not_called()

    def test_image_failure_worker_retries_three_times_then_pauses(self):
        job = self.conn.execute('SELECT * FROM shopify_fulfilment_jobs').fetchone()
        with patch.object(main, 'claim_shopify_fulfilment_job', return_value=job), \
             patch.object(main, 'shopify_fulfilment_progress', return_value={'status': 'running'}), \
             patch.object(main, 'set_shopify_fulfilment_progress'), \
             patch.object(main, 'increment_shopify_fulfilment_progress'), \
             patch.object(main, 'send_email_alert_async'), \
             patch.object(main.time, 'sleep'), \
             patch.object(main, 'run_shopify_script_export', side_effect=ReplacementImageSyncError(1, 'B000000002')) as export:
            self.assertTrue(main.process_one_shopify_fulfilment_job())
        job = self.conn.execute('SELECT * FROM shopify_fulfilment_jobs').fetchone()
        self.assertEqual(job['status'], 'dead')
        self.assertEqual(image_failure_details(job['last_error']), {'line_id': 1, 'asin': 'B000000002'})
        self.assertEqual(export.call_count, 3)

    def test_image_retry_refetches_and_succeeds(self):
        job = self.conn.execute('SELECT * FROM shopify_fulfilment_jobs').fetchone()
        with patch.object(main, 'claim_shopify_fulfilment_job', return_value=job), \
             patch.object(main, 'shopify_fulfilment_progress', return_value={'status': 'running'}), \
             patch.object(main, 'set_shopify_fulfilment_progress'), \
             patch.object(main, 'increment_shopify_fulfilment_progress'), \
             patch.object(main, 'send_email_alert_async'), \
             patch.object(main, 'sync_shopify_status_for_order_names'), \
             patch.object(main.time, 'sleep'), \
             patch.object(main, 'run_shopify_script_export', side_effect=[ReplacementImageSyncError(1, 'B000000002'), None]) as export:
            self.assertTrue(main.process_one_shopify_fulfilment_job())
        self.assertEqual(self.conn.execute('SELECT status FROM shopify_fulfilment_jobs').fetchone()['status'], 'completed')
        self.assertEqual(export.call_count, 2)


class ManualImageExportTests(unittest.TestCase):
    def test_manual_image_is_per_line_even_for_same_asin(self):
        client = MagicMock()
        client.get_order_lines.return_value = [{'id': 10}, {'id': 11}]
        rows = [dict(id=i, source_ids=[i+9], quantity=1, replacement_asin='B000000002', replacement_image_base64=f'IMAGE{i}') for i in (1, 2)]
        loader = MagicMock(side_effect=RuntimeError('Amazon unavailable'))
        wrapper = ReplacementExportOdoo(client, rows, loader)
        lines = wrapper.get_order_lines([10, 11])
        self.assertEqual([wrapper.get_product_product(line['product_id'][0])['image_1920'] for line in lines], ['IMAGE1', 'IMAGE2'])
        loader.assert_not_called()


class ImageRetryTests(unittest.TestCase):
    def test_missing_image_blocks_without_using_original(self):
        client = MagicMock()
        client.get_order_lines.return_value = [{'id': 10}]
        rows = [dict(id=1, source_ids=[10], quantity=1, replacement_asin='B000000002')]
        loader = MagicMock(side_effect=RuntimeError('Amazon unavailable'))
        with self.assertRaises(ReplacementImageSyncError):
            ReplacementExportOdoo(client, rows, loader).get_order_lines([10])
        loader.assert_called_once_with('B000000002')

    def test_linked_order_retry_refreshes_image_without_creating_order(self):
        client = MagicMock()
        client.get_order_by_number.return_value = {'order_line': [10]}
        client.get_order_lines.return_value = [{'id': 10}]
        rows = [dict(id=1, source_ids=[10], quantity=1, replacement_asin='B000000002')]
        loader = MagicMock(return_value='FRESH_IMAGE')
        wrapper = ReplacementExportOdoo(client, rows, loader)
        shop = MagicMock()
        shop.rest_base = 'https://test.myshopify.com/admin/api/test/'
        shop._request.return_value = {'product': {'images': [{'src': 'https://cdn.shopify.com/new.jpg'}]}}
        rename = MagicMock()
        rename.destination_sku.return_value = 'B000000002'
        with patch.object(main, 'shopify_order_line_variants', return_value=[{'old_sku': 'B000000002', 'product_id': 42}]):
            main.sync_existing_shopify_replacement_images(MagicMock(), wrapper, shop, 'TEST1', 99, rename)
        loader.assert_called_once_with('B000000002')
        request = shop._request.call_args.args
        self.assertEqual(request[0], 'PUT')
        self.assertTrue(request[1].endswith('products/42.json'))
        self.assertEqual(request[2]['product']['images'], [{'attachment': 'FRESH_IMAGE'}])
        shop.create_order.assert_not_called()


class ShopifyImageResponseTests(unittest.TestCase):
    def run_export(self, response=None, error=None):
        class Shop:
            def __init__(self, name, shop, *_args):
                self.name, self.shop = name, shop

            def _request(self, *_args, **_kwargs):
                if error:
                    raise RuntimeError(error)
                return response

        client = MagicMock()
        client.replacements = [{'id': 1, 'replacement_asin': 'B000000002'}]
        module = SimpleNamespace(
            ShopifyClient=Shop, UPDATE_EXISTING_SKU_PRODUCTS=False,
            DESTS=[{'name': 'test', 'shop': 'test.myshopify.com'}],
            OdooClient=lambda *_args: client,
            shopify_authorize_url=lambda *_args: '',
            get_shopify_access_token=lambda *_args: 'test-token',
        )
        observed_update_setting = []

        def ensure(odoo, shop, *_args, **_kwargs):
            observed_update_setting.append(module.UPDATE_EXISTING_SKU_PRODUCTS)
            return shop._request('POST', '/products', {'product': {'images': [{'attachment': 'image'}]}})

        module.ensure_product_variant_for_line = ensure
        module.sync_one_order_to_dest = lambda odoo, shop, *_args: module.ensure_product_variant_for_line(
            odoo, shop, line={'product_id': [-1, 'replacement']})
        store = SimpleNamespace(odoo_url='test', odoo_db='test', odoo_user='test', odoo_password='test')
        with patch.object(main, 'get_service_settings', return_value={'shopify_dtc_script_path': 'test'}), \
             patch.object(main, 'get_store', return_value=store), \
             patch.object(main, 'load_external_script', return_value=module), \
             patch.object(main, 'apply_shopify_runtime_settings'), \
             patch.object(main, 'shopify_api_rate_config', return_value=(1, 1)), \
             patch.object(main, 'shopify_wait_for_api_slot'), \
             patch.object(main, 'shopify_product_rename_manager'), \
             patch.object(main, 'shopify_replacement_export_client', return_value=client), \
             patch.object(main, 'shopify_should_check_existing_order', return_value=False), \
             patch.object(main, 'shopify_orders_by_name', return_value=[]):
            try:
                main.run_shopify_script_export({'store_id': 2, 'route': 'dtc', 'odoo_order_name': 'TEST1'})
            finally:
                self.assertEqual(observed_update_setting, [True])
                self.assertFalse(module.UPDATE_EXISTING_SKU_PRODUCTS)

    def test_shopify_image_rejection_becomes_upload_action(self):
        with self.assertRaises(ReplacementImageSyncError):
            self.run_export(error='HTTP 422: image attachment is invalid')

    def test_missing_image_in_success_response_becomes_upload_action(self):
        with self.assertRaises(ReplacementImageSyncError):
            self.run_export(response={'product': {'id': 10, 'images': []}})

    def test_image_success_and_non_image_errors_are_distinguished(self):
        self.run_export(response={'product': {'images': [{'id': 20, 'src': 'test'}]}})
        with self.assertRaisesRegex(RuntimeError, '401') as error:
            self.run_export(error='HTTP 401: Unauthorized')
        self.assertNotIsInstance(error.exception, ReplacementImageSyncError)


if __name__ == '__main__':
    unittest.main()
