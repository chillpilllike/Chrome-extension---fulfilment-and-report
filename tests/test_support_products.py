import unittest
from datetime import datetime,timezone
from app.support.products import product_context
NOW=datetime(2026,9,6,12,tzinfo=timezone.utc)
class Client:
 def __init__(self):self.calls=[];self.rows=[{'id':12,'name':'Example product','website_url':'/shop/example-12','website_description':'<p>Published features</p><script>bad()</script>'}]
 def fields_get(self,m):return {k:{} for k in ['id','name','website_id','website_url','is_published','active','website_description']}
 def search_read(self,m,d,f,**kw):self.calls.append(d);return self.rows
class ProductTests(unittest.TestCase):
 def setUp(self):self.c=Client();self.visits=[{'url':'https://secretgreen.com.au/shop/example-12','time':NOW.isoformat()}]
 def run_product(self):return product_context(self.c,self.visits,now=NOW)
 def test_published_product_requires_confirmation_and_uses_allowlist(self):
  d=self.run_product();self.assertTrue(d['confirmation_required']);self.assertEqual('Published features',d['published_description']);self.assertNotIn('list_price',d);self.assertIn(['website_id','in',[False,1]],self.c.calls[0]);self.assertIn(['is_published','=',True],self.c.calls[0])
 def test_home_page_never_falls_back_to_old_product(self):
  self.visits.insert(0,{'url':'https://secretgreen.com.au/','time':NOW.isoformat()});self.assertFalse(self.run_product()['found']);self.assertFalse(self.c.calls)
 def test_foreign_host_credentials_local_port_and_non_https_rejected(self):
  for url in ['https://evil.test/shop/example-12','https://secretgreen.com.au.evil.test/shop/example-12','https://user@secretgreen.com.au/shop/example-12','https://secretgreen.com.au:999/shop/example-12','http://secretgreen.com.au/shop/example-12','https://127.0.0.1/shop/example-12']:
   self.visits[0]['url']=url;self.assertFalse(self.run_product()['found'])
  self.assertFalse(self.c.calls)
 def test_old_page_cannot_be_claimed_current(self):
  self.visits[0]['time']='2026-09-05T12:00:00Z';self.assertFalse(self.run_product()['found'])
 def test_canonical_path_mismatch_rejected(self):
  self.visits[0]['url']='https://secretgreen.com.au/shop/other-12';self.assertFalse(self.run_product()['found'])
 def test_unpublished_or_foreign_product_returns_no_result(self):
  self.c.rows=[];self.assertFalse(self.run_product()['found'])
 def test_sourcing_description_not_returned(self):
  self.c.rows[0]['website_description']='Supplier Amazon sourcing';self.assertEqual('',self.run_product()['published_description'])
 def test_variant_not_inferred_from_query(self):
  self.visits[0]['url']+='?attribute_values=9';self.assertNotIn('variant',self.run_product())

 def test_catalog_identifier_and_empty_weight_are_not_product_facts(self):
  self.c.rows[0]['website_description']='Steel handle. SKU : ADIB0000VYXL6 Weight: 0.0 oz Package Dimensions: .'
  result=self.run_product()['published_description'];self.assertEqual('Steel handle.',result)
