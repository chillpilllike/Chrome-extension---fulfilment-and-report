import os
import unittest
from unittest.mock import patch
from app.services import amazon_purchase_allocations as purchases

class QuantityRules(unittest.TestCase):
    line={'quantity':4,'asin':'B0FX4ZFT2Z'}
    def rows(self):return [dict(amazon_order_id=f'111-0000000-000000{i}',quantity=1) for i in range(1,5)]
    def test_four_orders_are_four_units(self):self.assertEqual(sum(r['quantity'] for r in purchases.validate_allocations(self.line,self.rows())),4)
    def test_overallocation_duplicates_and_invalid_quantity(self):
        for rows in [self.rows()+[dict(amazon_order_id='111-0000000-0000005',quantity=1)],self.rows()+[self.rows()[0]],[dict(amazon_order_id='111-0000000-0000001',quantity=float('nan'))],[dict(amazon_order_id='111-0000000-0000001',quantity=1.5)]]:
            with self.assertRaises(ValueError):purchases.validate_allocations(self.line,rows)
    def test_partial_cancelled_and_delivered_rollup(self):
        rows=[dict(quantity=1,state='delivered'),dict(quantity=1,state='ordered')]
        self.assertFalse(purchases.summary(self.line,rows)['complete'])
        self.assertEqual(purchases.summary(self.line,rows)['unallocated_quantity'],2)
        rows=[dict(quantity=1,state='delivered') for _ in range(4)]
        self.assertTrue(purchases.summary(self.line,rows)['complete'])
        rows[0]['state']='cancelled';self.assertFalse(purchases.summary(self.line,rows)['complete'])

@unittest.skipUnless(os.getenv('PURCHASE_TEST_POSTGRES')=='1','requires isolated local PostgreSQL')
class PurchaseIntegration(unittest.TestCase):
    def setUp(self):
        from app import main
        self.m=main
        from app.db import session
        self.assertIn('127.0.0.1:55439',session.POSTGRES_URL)
        for name in ['index_order_line','enqueue_shopify_fulfilment_for_rows','mark_history_tracking_order_status','upsert_amazon_otp_from_tracking_payload']:
            p=patch.object(main,name,return_value=0);p.start();self.addCleanup(p.stop)
        with main.db() as c:
            c.execute("DELETE FROM package_pickup_scan_events WHERE odoo_order_name='TESTSPLIT'")
            c.execute("DELETE FROM package_pickup_delivery_records WHERE package_id IN (SELECT id FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSPLIT')")
            c.execute("DELETE FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSPLIT'")
            c.execute("DELETE FROM order_lines WHERE odoo_order_name='TESTSPLIT'")
            self.store=c.execute('SELECT id FROM stores ORDER BY id LIMIT 1').fetchone()['id']
            self.line=c.execute('''INSERT INTO order_lines(store_id,odoo_order_id,odoo_order_name,odoo_line_id,asin,quantity,product_name,state,order_engine,amazon_order_id,created_at,updated_at)
               VALUES(?,999999,'TESTSPLIT',999999,'B0FX4ZFT2Z',4,'Test product','ordered','chrome','111-0000000-0000001',?,?) RETURNING id''',(self.store,main.utc_now(),main.utc_now())).fetchone()['id']
        self.rows=QuantityRules().rows()
    def update(self,i,status='Delivered September 16',quantity=1,asin='B0FX4ZFT2Z',cancel=False):
        from app.schemas.payloads import ChromeTrackingUpdatePayload
        oid=self.rows[i-1]['amazon_order_id']
        p=dict(amazon_order_id=oid,amazon_account_name='',order_cancelled=cancel,packages=[] if cancel else [dict(tracking_id=f'TBA99999999999{i}',tracking_url=f'https://www.amazon.com/progress-tracker/package?orderId={oid}&shipmentId=TEST{i}',status=status,asins=[asin],products=[dict(asin=asin,quantity=quantity,quantity_verified=True)])])
        return self.m.api_tracking_update(ChromeTrackingUpdatePayload(**p))
    def test_four_purchases_independent_tracking_and_pickup(self):
        saved=purchases.save(self.m,self.store,self.line,self.rows);self.assertEqual(saved['allocated_quantity'],4)
        with self.m.db() as c:
            sources=c.execute('SELECT amazon_order_id,quantity FROM amazon_purchase_tracking_lines WHERE id=?',(self.line,)).fetchall()
        self.assertEqual(len(sources),4)
        for i in range(1,5):
            self.update(i)
            from app.schemas.payloads import PackagePickupScanPayload
            scan=self.m.api_package_pickup_scan(PackagePickupScanPayload(scan_code=f'TBA99999999999{i}',store_id=self.store))
            self.assertTrue(scan['matched'])
            with self.m.db() as c:
                packages=c.execute("SELECT * FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSPLIT'").fetchall()
                self.assertEqual(len(packages),i)
                ready=self.m.package_pickup_order_readiness(c,dict(packages[0]))
                self.assertEqual(ready['ready_to_ship'],i==4)
                parent=c.execute('SELECT * FROM order_lines WHERE id=?',(self.line,)).fetchone()
                self.assertEqual(parent['quantity'],4)
                self.assertEqual(parent['state']=='delivered',i==4)
        self.update(1);self.assertEqual(purchases.get(self.m,self.store,self.line)['delivered_quantity'],4)
        self.update(2,cancel=True)
        detail=purchases.get(self.m,self.store,self.line);self.assertEqual(detail['unallocated_quantity'],1);self.assertFalse(detail['complete'])
    def test_two_units_one_purchase_needs_two_units_of_shipment_evidence(self):
        rows=[dict(self.rows[0],quantity=2),dict(self.rows[1],quantity=2)]
        purchases.save(self.m,self.store,self.line,rows)
        self.update(1,quantity=1)
        self.assertEqual(purchases.get(self.m,self.store,self.line)['delivered_quantity'],0)
        self.update(1,quantity=2)
        self.assertEqual(purchases.get(self.m,self.store,self.line)['delivered_quantity'],2)
        self.assertFalse(purchases.get(self.m,self.store,self.line)['complete'])
        self.update(2,quantity=2)
        self.assertTrue(purchases.get(self.m,self.store,self.line)['complete'])

    def test_queue_search_and_cancelled_replacement_allocation(self):
        purchases.save(self.m,self.store,self.line,self.rows)
        orders,rows,total,_,_=self.m.paged_tracking_orders(self.store,'all','TESTSPLIT')
        self.assertEqual(len(orders),4)
        found=self.m.fast_exact_order_reference_search(self.rows[3]['amazon_order_id'],self.store)
        self.assertTrue(any(r['id']==self.line and len(r['amazon_purchases'])==4 for r in found['rows']))
        self.update(1,cancel=True)
        new=self.rows+[dict(amazon_order_id='111-0000000-0000005',quantity=1)]
        saved=purchases.save(self.m,self.store,self.line,new)
        self.assertEqual(saved['allocated_quantity'],4)
        with self.m.db() as c:
            with self.assertRaises(self.m.HTTPException):purchases.guard_changes(self.m,c,[self.line])

    def test_wrong_asin_rolls_back_and_partial_allocation_holds(self):
        purchases.save(self.m,self.store,self.line,self.rows[:1])
        with self.assertRaises(self.m.HTTPException):self.update(1,asin='B000WRONG1')
        self.update(1)
        with self.m.db() as c:
            p=dict(c.execute("SELECT * FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSPLIT'").fetchone())
            c.execute("UPDATE amazon_dispatch_packages SET received_at=?,scan_status='received_unopened' WHERE id=?",(self.m.utc_now(),p['id']))
            r=self.m.package_pickup_order_readiness(c,p)
            self.assertFalse(r['ready_to_ship']);self.assertIn('3 unit(s)',r['message'])

    def test_incomplete_purchase_blocks_shopify_and_item_alias_promotes(self):
        purchases.save(self.m,self.store,self.line,self.rows[:1])
        with self.assertRaises(ValueError):
            purchases.assert_export_coverage(self.m,dict(store_id=self.store,odoo_order_name='TESTSPLIT'))
        from app.schemas.payloads import ChromeTrackingUpdatePayload
        oid=self.rows[0]['amazon_order_id']
        url=f'https://www.amazon.com/progress-tracker/package?orderId={oid}&itemId=SAMEITEM'
        package=dict(tracking_url=url,status='Ordered',asins=['B0FX4ZFT2Z'])
        self.m.api_tracking_update(ChromeTrackingUpdatePayload(amazon_order_id=oid,packages=[package]))
        package.update(tracking_id='TBA999999999991',tracking_url=url+'&shipmentId=PHYSICAL',status='Delivered September 16')
        self.m.api_tracking_update(ChromeTrackingUpdatePayload(amazon_order_id=oid,packages=[package]))
        detail=purchases.get(self.m,self.store,self.line)
        self.assertEqual(detail['delivered_quantity'],1)
        self.assertEqual(len(self.m.parse_tracking_packages(detail['allocations'][0]['tracking_payload'])),1)
