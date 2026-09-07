import unittest
from datetime import datetime, timezone
from app.support.live import order_evidence
from app.support.policy import public_order
import json

NOW=datetime(2026,9,6,12,tzinfo=timezone.utc)
class Conn:
    def __init__(self,rows,outbound=()):self.rows=rows;self.outbound=outbound
    def execute(self,sql,args):self.result=self.outbound if 'epost_global' in sql else self.rows;return self
    def fetchall(self):return self.result

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.order={'id':1,'name':'SG12','state':'sale','amount_total':15,'currency_id':[1,'AUD'],'items':[{'id':2,'product_id':[3,'Item'],'product_uom_qty':2}]}
        self.row={'odoo_line_id':2,'quantity':2,'asin':'PRIVATE_ASIN','tracking_checked_at':NOW.isoformat(),'tracking_payload':json.dumps([{'tracking_id':'private','expected_delivery_date':'2026-09-08','products':[{'asin':'PRIVATE_ASIN','quantity_verified':True,'quantity':2}]}])}
    def test_complete_fresh_coverage_can_estimate(self):
        e=order_evidence(Conn([self.row]),8,self.order,NOW)
        self.assertEqual('2026-09-08',e.expected_dates[0].isoformat())
    def test_unverified_quantity_cannot_estimate(self):
        self.row['tracking_payload']=self.row['tracking_payload'].replace('true','false')
        self.assertIn(None,order_evidence(Conn([self.row]),8,self.order,NOW).expected_dates)
    def test_missing_order_line_withdraws_estimate(self):
        self.order['items'].append({'id':4,'product_id':[5,'Missing'],'product_uom_qty':1})
        self.assertEqual(1,order_evidence(Conn([self.row]),8,self.order,NOW).observed_at.year)
    def test_stale_source_is_not_refreshed_by_read(self):
        self.row['tracking_checked_at']='2026-09-05T00:00:00+00:00'
        self.assertEqual(5,order_evidence(Conn([self.row]),8,self.order,NOW).observed_at.day)
    def test_one_delivered_shipment_does_not_claim_full_delivery(self):
        e=order_evidence(Conn([self.row],[{'status':'Delivered','last_checked_at':NOW.isoformat()}]),8,self.order,NOW)
        self.assertEqual('partial',e.outbound_state)
    def test_customer_payload_excludes_internal_data(self):
        e=order_evidence(Conn([self.row]),8,self.order,NOW)
        result=json.dumps(public_order({**self.order,'margin':99,'customer':{'name':'Private'},'odoo_url':'admin'},e))
        for forbidden in ['PRIVATE_ASIN','private','margin','customer','odoo_url']:
            self.assertNotIn(forbidden,result)
