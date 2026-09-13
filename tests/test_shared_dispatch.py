import unittest,json
from unittest.mock import Mock,patch
from contextlib import contextmanager
from datetime import datetime,timezone,timedelta
from app.support.post_order_chat import dispatch_update

class SharedDispatchTests(unittest.TestCase):
 def test_every_site_uses_same_complete_coverage_rule_without_source_fields(self):
  now=datetime.now(timezone.utc)
  for store,website in [(8,1),(2,1),(1,63),(1,74),(9,1)]:
   rows=[{'odoo_line_id':12,'quantity':1,'asin':'PRIVATE_SOURCE','tracking_checked_at':now.isoformat(),'tracking_payload':json.dumps([{'tracking_id':'INTERNAL_TRACKING','expected_delivery_date':(now+timedelta(days=1)).date().isoformat(),'products':[{'asin':'PRIVATE_SOURCE','quantity_verified':True,'quantity':1}]}])}]
   class Conn:
    def execute(self,sql,args):
     self.rows=[] if 'epost_global' in sql else rows
     if 'epost_global' in sql:self_test.assertEqual((store,42,website,website),args)
     return self
    def fetchall(self):return self.rows
   self_test=self
   @contextmanager
   def db():yield Conn()
   client=Mock();client.existing_fields.return_value=['id','product_id','product_uom_qty'];client.search_read.return_value=[{'id':12,'product_id':[5,'Item'],'product_uom_qty':1}]
   result=dispatch_update(db,client,store,website,{'id':42,'state':'sale'})
   self.assertEqual((now+timedelta(days=2)).date().isoformat(),result['estimated_dispatch'])
   for value in ['PRIVATE_SOURCE','INTERNAL_TRACKING','amazon','asin','supplier']:self.assertNotIn(value,json.dumps(result).lower() if value.islower() else json.dumps(result))
   rows[0]['tracking_checked_at']=(now-timedelta(hours=1)).isoformat()
   self.assertIsNone(dispatch_update(db,client,store,website,{'id':42,'state':'sale'})['estimated_dispatch'])
 def test_failed_source_and_unconfirmed_order_never_get_a_date(self):
  client=Mock();client.existing_fields.side_effect=RuntimeError('private source failure')
  for state in ['sale','draft','sent','cancel']:
   result=dispatch_update(None,client,1,63,{'id':42,'state':state})
   self.assertIsNone(result['estimated_dispatch']);self.assertNotIn('private source',result['reply'])
