import unittest
from app.main import dispatch_shipment_alias_pairs, collapse_dispatch_related_parts, package_pickup_readiness_from_parts

class ItemAliasTests(unittest.TestCase):
    def rows(self):
        common=dict(store_id=1,odoo_order_id=24573,odoo_order_name='NC24567',amazon_order_id='111-0525120-0502608',asins_json='["B0HF7W3T66"]',order_line_ids_json='[3401964]')
        return [dict(common,id=57408,scan_code='AMZPKG-ABC',canonical_scan_code='AMZPKG-ABC',tracking_url='https://www.amazon.com/progress-tracker/package?itemId=jmrolpirnipqxsp&orderId=111-0525120-0502608',package_status='Ordered',received=False),dict(common,id=55209,scan_code='TBA334465777338',canonical_scan_code='TBA334465777338',tracking_url='https://www.amazon.com/gp/your-account/ship-track?itemId=jmrolpirnipqxsp&orderId=111-0525120-0502608&shipmentId=Nqcx8PYgc',package_status='Delivered September 11',received=True,received_at='2026-09-14T13:53:36+00:00')]
    def test_item_placeholder_collapses_to_received_physical_shipment(self):
        rows=self.rows();self.assertEqual(dispatch_shipment_alias_pairs(rows),[(57408,55209)])
        parts=collapse_dispatch_related_parts(rows);self.assertEqual(len(parts),1)
        self.assertEqual(parts[0]['id'],55209);self.assertTrue(parts[0]['received'])
        self.assertEqual(parts[0]['package_status'],'Delivered September 11')
        self.assertTrue(package_pickup_readiness_from_parts(parts)['ready_to_ship'])
    def test_ambiguous_multiple_physical_packages_not_collapsed(self):
        rows=self.rows();rows.append(dict(rows[1],id=123,scan_code='TBA334465777339',canonical_scan_code='TBA334465777339',tracking_url=rows[1]['tracking_url'].replace('Nqcx8PYgc','different')))
        self.assertEqual(dispatch_shipment_alias_pairs(rows),[])
        self.assertEqual(len(collapse_dispatch_related_parts(rows)),3)
    def test_different_order_asin_line_or_item_not_collapsed(self):
        for key,value in [('amazon_order_id','111-9999999-9999999'),('store_id',2),('asins_json','["B000OTHER1"]'),('order_line_ids_json','[99]'),('tracking_url','https://amazon.com/?itemId=other&shipmentId=Nqcx8PYgc')]:
            rows=self.rows();rows[1][key]=value
            with self.subTest(key=key):self.assertEqual(dispatch_shipment_alias_pairs(rows),[])
    def test_delivered_without_scan_stays_on_hold(self):
        row=self.rows()[1];row.update(received=False,received_at='')
        result=package_pickup_readiness_from_parts([row]);self.assertFalse(result['ready_to_ship'])
        self.assertIn('not yet scanned/received by the team',result['message'])
    def test_unresolved_line_is_not_an_extra_physical_package(self):
        rows=[self.rows()[1],dict(id=-12,unresolved_line_id=12,received=False)]
        result=package_pickup_readiness_from_parts(rows)
        self.assertEqual(result['total_packages'],1);self.assertEqual(result['received_packages'],1)
        self.assertFalse(result['ready_to_ship'])
