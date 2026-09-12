import json
import subprocess
import unittest
from pathlib import Path
from app import main

class LateDeliveryTests(unittest.TestCase):
    def test_explicit_late_headline_overrides_old_false_delivery(self):
        p = dict(status='delivered', order_status='Your delivery is taking longer than expected', promise='delivered', latest_event={'message':'Completed customs clearance.'})
        self.assertFalse(main.tracking_package_delivered(p))
        self.assertEqual(main.tracking_status_from_packages([p]), 'Delayed')
        self.assertFalse(main.tracking_package_delivered({'status':'Your package will be delivered on September 9'}))

    def test_real_delivery_and_later_delivery_snapshot_still_work(self):
        old = dict(status='delivered', order_status='Your delivery is taking longer than expected', shipment_id='Dv66mh70J')
        new = dict(status='Delivered September 12', tracking_id='ZS21716875199', shipment_id='Dv66mh70J')
        merged = main.merge_tracking_shipment_snapshots(old, new)
        self.assertTrue(main.tracking_package_delivered(merged))
        self.assertEqual(main.tracking_status_from_packages([merged]), 'Delivered')

    def test_china_post_tracking_identity(self):
        for code in ['ZS21716875199', 'TBA334235755020']:
            self.assertTrue(main.package_tracking_id_is_physical(code))
            self.assertTrue(main.dispatch_scan_code_is_physical(code))
        self.assertFalse(main.package_tracking_id_is_physical('Dv66mh70J'))

    def test_actual_extension_primary_headline_and_tracking_number(self):
        source = (Path(__file__).resolve().parents[1]/'tracking-extension/content.js').read_text()
        helpers = source[source.index('function physicalTrackingId('):source.index('function isoDate(')]
        status = source[source.index('function parseStatus()'):source.index('function parsePromise()')]
        script = '''const assert=require('assert/strict');
const clean=x=>String(x||'').trim();
const document={body:{innerText:'Your delivery is taking longer than expected. It will be delivered soon. Delivered'},querySelector:s=>s==='#primaryStatus'?{textContent:'Your delivery is taking longer than expected'}:null};
'''+helpers+status+'''
assert.equal(parseStatus(),'Your delivery is taking longer than expected');
assert.equal(trackingIdFromText('Tracking ID: ZS21716875199'),'ZS21716875199');
'''
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)

    def test_correction_requires_old_contradiction_and_same_shipment_asin(self):
        old = dict(status='delivered', order_status='Your delivery is taking longer than expected', shipment_id='Dv66mh70J', asins=['B0HF4PVTC7'])
        row = dict(asin='B0HF4PVTC7', replacement_asin='B0HF4PVTC7', tracking_payload=json.dumps([old]))
        current = dict(status='Running late', shipment_id='Dv66mh70J', asins=['B0HF4PVTC7'])
        self.assertTrue(main.tracking_can_correct_false_delivery(row,[current]))
        self.assertFalse(main.tracking_can_correct_false_delivery(row,[dict(current,shipment_id='other')]))
        self.assertFalse(main.tracking_can_correct_false_delivery(row,[dict(current,asins=['B000000099'])]))
        row['tracking_payload']=json.dumps([dict(old,order_status='Delivered September 2')])
        self.assertFalse(main.tracking_can_correct_false_delivery(row,[current]))

    def test_shipment_image_anchor_proves_asin_and_quantity(self):
        source = (Path(__file__).resolve().parents[1]/'tracking-extension/content.js').read_text()
        helper = source[source.index('function productItemsFrom('):source.index('function shipmentRootForTrackingLink(')]
        script = r"""
const assert=require('assert/strict');
const clean=x=>String(x||'').trim(), cleanProductTitle=clean, blockedProductCandidate=()=>false;
const absoluteUrl=x=>new URL(x,'https://www.amazon.com').href;
const image={getAttribute:k=>({alt:'CHILLFLEX Fari Hair Livana Liver Support, 2 Fl Oz',src:'https://example.com/product.jpg'})[k]||null};
const badge={textContent:'2',getAttribute:()=>null};
const link={textContent:'2',getAttribute:k=>k==='href'?'/gp/product/B0HF4PVTC7?ref=ppx_pt2_dt_b_prod_image':null,
 matches:s=>s.startsWith('a['), querySelectorAll:()=>[],
 querySelector:s=>s==='img'?image:s.includes('.itemImages-quantityLabel')?badge:null,
 closest:s=>s.includes('.itemImages-inline a')?link:null};
const root={querySelectorAll:s=>s.includes("a[href")?[link]:[],contains:e=>e===link};
""" + helper + r"""
const products=productItemsFrom(root);
assert.equal(products.length,1);
assert.equal(products[0].asin,'B0HF4PVTC7');
assert.equal(products[0].quantity,2);
assert.equal(products[0].quantity_verified,true);
"""
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
