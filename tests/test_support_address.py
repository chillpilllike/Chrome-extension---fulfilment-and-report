import unittest,sqlite3,copy
from contextlib import contextmanager
from unittest.mock import patch
from fastapi import HTTPException
from app.support.address import update_delivery,snapshot,live_context
class Client:
 def __init__(self,current):self.current=current;self.writes=[];self.partner=7
 def execute(self,model,method,args,kwargs=None):
  self.writes.append((model,method,args,kwargs))
  if method=='create':self.current={**args[0],'id':9,'country_id':[1,'Australia'],'state_id':[2,'Victoria']};return 9
  if model=='sale.order' and method=='write':self.partner=args[1]['partner_shipping_id']
  return True
 def search_read(self,model,domain,fields,**kwargs):
  if model=='res.partner':return [self.current]
  if model=='sale.order':return [{'partner_shipping_id':[self.partner,'Delivery']}]
  return []
class Shop:
 def __init__(self,record,fail=False):self.record=copy.deepcopy(record);self.fail=fail
 def graphql(self,query,variables):
  if query.startswith('mutation'):
   if self.fail:return {'orderUpdate':{'userErrors':[{'message':'blocked'}]}}
   address=dict(variables['input']['shippingAddress']);address['countryCodeV2']=address.pop('countryCode');self.record['shippingAddress']=address;self.record['phone']=variables['input'].get('phone',self.record.get('phone'))
   return {'orderUpdate':{'order':{'id':self.record['id']},'userErrors':[]}}
  return {'order':self.record}
class AddressTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
  outer=self
  class Conn:
   def execute(self,sql,args=()):return outer.c.execute('SELECT 1') if 'pg_advisory' in sql else outer.c.execute(sql,args)
  @contextmanager
  def db():
   try:yield Conn();self.c.commit()
   except:self.c.rollback();raise
  self.db=db;self.raw={'id':4,'name':'#1','partner_id':[2,'Billing'],'write_date':'old'}
  self.current={'id':7,'name':'Test','street':'1 Example St','city':'Test','zip':'3000','country_id':[1,'Australia'],'state_id':[2,'Victoria'],'phone':'+61000000000'}
  self.client=Client(self.current);self.payload={'kind':'phone','phone':'+61400000000'}
  self.record={'id':'gid://shopify/Order/42','updatedAt':'old','displayFulfillmentStatus':'UNFULFILLED','shippingAddress':{'firstName':'Test','lastName':'Person','company':'Test company','address1':'1 Example St','address2':'Unit 2','city':'Test','zip':'3000','countryCodeV2':'AU','provinceCode':'VIC','phone':'+61000000000'}}
 def apply(self,shops,expected=None):
  ctx=(self.raw,self.current,[{'id':6,'state':'assigned'}],shops)
  with patch('app.support.address.live_context',return_value=ctx):return update_delivery(self.db,self.client,8,{'id':4},'uuid','test@example.com',self.payload,expected or snapshot(self.raw,self.current,shops))
 def test_updates_order_specific_contact_and_shopify_then_logs(self):
  shop=Shop(self.record);r=self.apply([(shop,self.record)])
  self.assertTrue(r['updated']);self.assertTrue(r['shopify_updated']);self.assertEqual('+61400000000',shop.record['shippingAddress']['phone']);self.assertEqual('Unit 2',shop.record['shippingAddress']['address2']);self.assertEqual('Test company',shop.record['shippingAddress']['company'])
  self.assertFalse(any(model=='res.partner' and method=='write' for model,method,_,_ in self.client.writes))
  self.assertTrue(any(method=='message_post' for _,method,_,_ in self.client.writes))
 def test_no_linked_shopify_never_claims_shopify_updated(self):
  r=self.apply([]);self.assertTrue(r['odoo_updated']);self.assertFalse(r['shopify_exists']);self.assertFalse(r['shopify_updated'])
 def test_shopify_rejection_never_changes_odoo_or_claims_success(self):
  r=self.apply([(Shop(self.record,True),self.record)]);self.assertFalse(r['updated']);self.assertTrue(r['needs_human']);self.assertFalse(r['odoo_updated']);self.assertFalse(any(method in {'write','create'} for _,method,_,_ in self.client.writes))
 def test_stale_snapshot_cannot_mutate(self):
  with self.assertRaises(HTTPException):self.apply([],expected='changed')
  self.assertFalse(self.client.writes)
 def test_completed_odoo_delivery_blocks_without_address_mutation(self):
  with patch.object(self.client,'search_read',side_effect=[[{'id':4,'state':'sale','partner_shipping_id':[7,'Delivery']}],[{'id':6,'state':'done'}]]):
   with self.assertRaises(HTTPException) as e:live_context(self.db,self.client,8,{'id':4})
  self.assertIn('dispatched',str(e.exception.detail));self.assertFalse(self.client.writes)
