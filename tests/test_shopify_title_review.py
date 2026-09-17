import inspect
import json
import re
import sqlite3
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch, Mock
from app import main
from app.services import shopify_title_review as review

class Database:
    def __init__(self):
        self.raw=sqlite3.connect(':memory:')
        self.raw.row_factory=lambda c,r:dict(zip([v[0] for v in c.description],r))
        self.raw.create_function('GREATEST',2,max)
    def execute(self,sql,params=()):
        return self.raw.execute(re.sub(r'FOR UPDATE(?: SKIP LOCKED)?','',sql),params)

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn=Database()
        self.conn.execute('CREATE TABLE shopify_fulfilment_jobs(id TEXT PRIMARY KEY,status TEXT,attempts INTEGER,next_run_at TEXT,last_error TEXT,locked_at TEXT,updated_at TEXT)')
        self.conn.execute("INSERT INTO shopify_fulfilment_jobs(id,status,attempts) VALUES ('job','running',1)")
        ddl=re.search(r'CREATE TABLE IF NOT EXISTS shopify_title_reviews \(.*?\n            \);',inspect.getsource(main.init_db),re.S).group()
        self.conn.execute(ddl)
        @contextmanager
        def database():yield self.conn
        self.settings={'shopify_clean_titles_enabled':'true','shopify_title_approval_enabled':'true','shopify_title_remove_keywords':'secret\ndim'}
        self.job={'id':'job'}
        self.snapshot={'fingerprint':'abc','clean':True,'items':[{'key':'1:10','original_title':'Acme Red Box','prepared_title':'Red Box','sku':'REF-123-very-long-'+'9'*50,'brands':['Acme']}]}
        for name, obj in [('db',database),('get_service_settings',lambda:self.settings),('start_shopify_fulfilment_worker',Mock())]:
            p=patch.object(main,name,obj);p.start();self.addCleanup(p.stop)
    def hold(self):
        with self.assertRaises(review.ReviewRequired):main.resolve_shopify_title_review(self.job,self.snapshot,self.settings)
        self.conn.execute("UPDATE shopify_fulfilment_jobs SET status='pending_review'")
    def approve(self,title='Blue Box',revision=1):
        return main.api_approve_shopify_titles('job',{'revision':revision,'titles':{'1:10':title}})
    def test_manual_long_title_survives_approval_and_worker_validation(self):
        self.hold()
        title = 'one two three four five six seven eight'
        self.approve(title)
        result = main.resolve_shopify_title_review(self.job, self.snapshot, self.settings)
        self.assertEqual(result['items'][0]['prepared_title'], title)
        self.assertEqual(review.prepared_title(title), 'one two three four five six')

    def test_manual_punctuation_survives_approval_and_export_validation(self):
        self.hold()
        title = 'Dietary Supplement Black Seed Non-GMO, Gluten-Free'
        self.approve(title)
        result = main.resolve_shopify_title_review(self.job, self.snapshot, self.settings)
        self.assertEqual(result['items'][0]['prepared_title'], title)
        review.validate_items([dict(result['items'][0], brands=['PURELYNUTRIENT'])], True, 'dieta')

    def test_punctuation_does_not_bypass_exclusions(self):
        self.hold()
        for title in ['Red, secret box', '(Acme) Red Box', 'Red - dim box']:
            with self.assertRaises(main.HTTPException):
                self.approve(title)

    def test_manual_title_still_respects_character_limit(self):
        self.hold()
        with self.assertRaises(main.HTTPException):
            self.approve('x' * 256)

    def test_cleaning_boundaries_variants_and_six_words(self):
        self.assertEqual(review.prepared_title('ACME DIM dimensional red secret blue green yellow pink orange extra',['Acme'],'dim\nsecret'),'dimensional red blue green yellow pink')
        self.assertEqual(review.prepared_title('Acme ghost-legend red box',['Acme'],'ghost legend'),'red box')
        self.assertEqual(review.prepared_title('Acme secret',['Acme'],'secret'),'')
    def test_hold_edit_approve_and_exact_export_values(self):
        self.hold();self.approve()
        result=main.resolve_shopify_title_review(self.job,self.snapshot,self.settings)
        self.assertEqual(result['items'][0]['prepared_title'],'Blue Box')
        self.assertEqual(result['items'][0]['sku'],self.snapshot['items'][0]['sku'])
        self.assertEqual(self.conn.execute('SELECT status FROM shopify_fulfilment_jobs').fetchone()['status'],'queued')
    def test_double_approval_and_stale_revision_rejected(self):
        self.hold()
        with self.assertRaises(main.HTTPException) as error:self.approve(revision=2)
        self.assertEqual(error.exception.status_code,409)
        self.approve()
        with self.assertRaises(main.HTTPException):self.approve()
    def test_changed_source_invalidates_approval(self):
        self.hold();self.approve();self.snapshot['fingerprint']='changed'
        self.hold()
        row=self.conn.execute('SELECT * FROM shopify_title_reviews').fetchone()
        self.assertEqual((row['status'],row['revision']),('pending',2))
    def test_changed_source_keeps_operator_title_for_same_product(self):
        self.hold(); self.approve('Custom approved title')
        self.snapshot['fingerprint'] = 'changed-price'
        self.snapshot['items'][0]['price_unit'] = 200
        self.hold()
        saved = json.loads(self.conn.execute('SELECT snapshot_json FROM shopify_title_reviews').fetchone()['snapshot_json'])
        self.assertEqual(saved['items'][0]['prepared_title'], 'Custom approved title')
        self.assertEqual(saved['items'][0]['price_unit'], 200)

    def test_changed_product_does_not_inherit_operator_title(self):
        self.hold(); self.approve('Custom approved title')
        self.snapshot['fingerprint'] = 'changed-product'
        self.snapshot['items'][0]['original_title'] = 'Different product'
        self.hold()
        saved = json.loads(self.conn.execute('SELECT snapshot_json FROM shopify_title_reviews').fetchone()['snapshot_json'])
        self.assertEqual(saved['items'][0]['prepared_title'], 'Red Box')

    def test_legacy_fingerprint_keeps_existing_approval_valid(self):
        self.hold(); self.approve('Custom approved title')
        self.snapshot.update(fingerprint='new-format', legacy_fingerprint='abc')
        resolved = main.resolve_shopify_title_review(self.job, self.snapshot, self.settings)
        self.assertEqual(resolved['items'][0]['prepared_title'], 'Custom approved title')
        self.assertEqual(self.conn.execute('SELECT fingerprint FROM shopify_title_reviews').fetchone()['fingerprint'], 'new-format')

    def test_notes_and_tags_do_not_change_title_fingerprint(self):
        client = Mock()
        order = {'id': 1, 'order_line': [10], 'note': 'old note', 'tag_ids': [1]}
        client.get_order_by_number.side_effect = lambda name: dict(order)
        client.get_order_lines.return_value = [{'id': 10, 'product_id': [20, 'Box'], 'product_uom_qty': 1, 'price_unit': 10, 'price_total': 10}]
        client.get_product_product.return_value = {'id': 20, 'name': 'Red Box', 'default_code': 'REF', 'product_tmpl_id': False}
        module = SimpleNamespace(_should_ignore_odoo_line_item=lambda *args: False)
        def prepare():
            return review.prepare(module, review.FrozenOdoo(client), 'NC1', self.settings, Mock())
        before = prepare()
        order.update(note='Fulfilment updated', tag_ids=[2, 1])
        after = prepare()
        self.assertEqual(before['fingerprint'], after['fingerprint'])
        self.assertNotEqual(before['legacy_fingerprint'], after['legacy_fingerprint'])
        client.get_order_lines.return_value[0]['product_uom_qty'] = 2
        self.assertNotEqual(after['fingerprint'], prepare()['fingerprint'])
        client.get_order_lines.return_value[0]['product_uom_qty'] = 1
        order['partner_shipping_id'] = [22, 'New recipient']
        self.assertNotEqual(after['fingerprint'], prepare()['fingerprint'])

    def test_toggle_off_does_not_release_pending_reviews(self):
        self.hold();self.settings['shopify_title_approval_enabled']='false'
        self.hold()
    def test_automatic_mode_and_blank_title_hold(self):
        self.settings['shopify_title_approval_enabled']='false'
        self.assertEqual(main.resolve_shopify_title_review(self.job,self.snapshot,self.settings),self.snapshot)
        self.snapshot['items'][0]['prepared_title']=''
        self.hold()
    def test_invalid_edits_do_not_approve(self):
        self.hold()
        for title in ['', 'Acme box', 'secret box']:
            with self.assertRaises(main.HTTPException):self.approve(title)
        self.assertEqual(self.conn.execute('SELECT status FROM shopify_title_reviews').fetchone()['status'],'pending')
    def test_export_install_uses_reviewed_title_sku_for_existing_variants(self):
        observed=[]
        def ensure(odoo,*args,**kwargs):
            observed.append((kwargs['rename_manager'].resolve_title(),kwargs['rename_manager'].destination_sku()))
            return 123,None,None
        module=SimpleNamespace(ensure_product_variant_for_line=ensure,UPDATE_EXISTING_SKU_PRODUCTS=False,build_order_payload=lambda *a,**k:{'order':{'line_items':[{'variant_id':123,'quantity':1}]}})
        review.install_prepared_export(module,self.snapshot)
        line={'id':1,'product_id':[10,'name']}
        module.ensure_product_variant_for_line(None,line=line)
        payload=module.build_order_payload(order_lines=[line])
        self.assertEqual(payload['order']['line_items'][0]['title'],'Red Box')
        self.assertEqual(observed[0][1],self.snapshot['items'][0]['sku'])
        self.assertFalse(module.UPDATE_EXISTING_SKU_PRODUCTS)
    def test_export_mismatched_lines_stops(self):
        module=SimpleNamespace(ensure_product_variant_for_line=lambda *a,**k:None,UPDATE_EXISTING_SKU_PRODUCTS=False,build_order_payload=lambda *a,**k:{'order':{'line_items':[]}})
        review.install_prepared_export(module,self.snapshot)
        with self.assertRaises(ValueError):module.build_order_payload(order_lines=[{'id':1,'product_id':[10,'name']}])
    def test_brand_attributes_read(self):
        odoo=Mock()
        odoo.read.side_effect=[[{'attribute_line_ids':[3,4]}],[{'attribute_id':[1,'Brand'],'value_ids':[8]},{'attribute_id':[2,'Size'],'value_ids':[9]}],[{'name':'Acme'}]]
        self.assertEqual(review.product_brands(odoo,{'product_tmpl_id':[2,'Product']}),['Acme'])
    def test_frozen_source_is_read_once(self):
        odoo=Mock();odoo.get_order_lines.return_value=[{'id':1}]
        frozen=review.FrozenOdoo(odoo)
        self.assertEqual(frozen.get_order_lines([1]),frozen.get_order_lines([1]));odoo.get_order_lines.assert_called_once()

if __name__=='__main__':unittest.main()

class ExportRouteTests(unittest.TestCase):
    def test_both_routes_update_existing_product_and_preserve_long_internal_reference(self):
        for route in ['dtb','dtc']:
            module=main.load_external_script(str(main.BASE_DIR / 'app' / 'services' / 'shopify_scripts' / f'{route}_orders_export.py'),f'title_test_{route}')
            module.OVERRIDE_PRICE_MODE='odoo'
            module.STRIP_WORDS=[]
            sku='Internal-Reference-'+('9'*45)
            item={'key':'1:10','prepared_title':'Reviewed Storage Box','sku':sku}
            review.install_prepared_export(module,{'items':[item]})
            odoo=Mock();odoo.get_product_product.return_value={'name':'Original Product','default_code':sku,'product_tmpl_id':False,'barcode':''}
            state=Mock();state.get_variant_for_sku.return_value=(123,456)
            shop=Mock();shop.name='test'
            result=module.ensure_product_variant_for_line(odoo,shop,state,line={'id':1,'product_id':[10,'Product'],'price_unit':5,'name':'Original Product'},order_name='TEST',rename_manager=module.ProductRenameManager(False),override_prices_for_order=False,qty_raw=1,qty_int=1)
            self.assertEqual(result[0],123)
            self.assertEqual(shop.update_product.call_args.kwargs['title'],item['prepared_title'])
            self.assertEqual(state.set_variant_for_sku.call_args_list[0].args[1],sku)
    def test_worker_review_hold_does_not_send_failure_alert_or_refresh_shopify(self):
        database=Database()
        database.execute('CREATE TABLE shopify_fulfilment_jobs(id TEXT,status TEXT,attempts INTEGER,last_error TEXT,locked_at TEXT,updated_at TEXT)')
        database.execute("INSERT INTO shopify_fulfilment_jobs VALUES ('job','running',1,'','','')")
        @contextmanager
        def db():yield database
        job={'id':'job','route':'dtc','odoo_order_name':'TEST','store_id':1,'attempts':1}
        with patch.object(main,'db',db),patch.object(main,'claim_shopify_fulfilment_job',return_value=job),patch.object(main,'shopify_fulfilment_progress',return_value={'status':'running'}),patch.object(main,'set_shopify_fulfilment_progress'),patch.object(main,'increment_shopify_fulfilment_progress'),patch.object(main,'run_shopify_script_export',side_effect=review.ReviewRequired('Review titles')),patch.object(main,'sync_shopify_status_for_order_names') as sync,patch.object(main,'send_email_alert_async') as alert:
            self.assertTrue(main.process_one_shopify_fulfilment_job())
            sync.assert_not_called();alert.assert_not_called()
        row=database.execute('SELECT * FROM shopify_fulfilment_jobs').fetchone()
        self.assertEqual((row['status'],row['attempts']),('pending_review',0))

class SettingsTests(unittest.TestCase):
    setUp = ReviewTests.setUp
    def test_keyword_settings_use_json_storage_and_preserve_newlines(self):
        self.conn.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY,value TEXT,updated_at TEXT)')
        main.api_save_shopify_title_settings({'clean_titles':True,'require_approval':True,'remove_keywords':'dim\nghost legend'})
        rows={r['key']:json.loads(r['value']) for r in self.conn.execute('SELECT * FROM app_settings').fetchall()}
        self.assertEqual(rows['shopify_title_remove_keywords'],'dim\nghost legend')
        self.assertEqual(rows['shopify_title_approval_enabled'],'true')
