import json
import unittest
from unittest.mock import patch, MagicMock
from app import main
from app.services import pickup_evidence_repair as repair
from test_pickup_auto_reconciliation import TestPickupAutoReconciliation as BaseScanFixture


class ShadowEvidenceTests(unittest.TestCase):
    def packages(self):
        order='113-5712612-6700215'
        product=dict(asin='B071FTZ8C9',quantity=1,quantity_verified=True)
        common=dict(amazon_order_id=order,asins=[product['asin']],products=[dict(product)],order_products=[dict(product)])
        return [
            dict(common,status='Arriving Monday',tracking_url=f'https://www.amazon.com/progress-tracker/package?orderId={order}&itemId=old'),
            dict(common,status='Delivered September 14',tracking_id='TBA334595546738',tracking_url=f'https://www.amazon.com/progress-tracker/package?orderId={order}&shipmentId=new'),
        ]

    def test_verified_single_unit_drops_item_shadow_without_shared_item_id(self):
        packages=main.canonical_tracking_packages(self.packages())
        self.assertEqual(len(packages),1)
        self.assertEqual(main.tracking_status_from_packages(packages),'Delivered')

    def test_quantity_ambiguity_and_other_order_never_merge(self):
        for change in ['unverified','quantity_two','other_order','two_physical']:
            packages=self.packages()
            if change=='unverified': packages[1]['order_products'][0]['quantity_verified']=False
            if change=='quantity_two': packages[1]['order_products'][0]['quantity']=2
            if change=='other_order':
                packages[1]['amazon_order_id']='113-0000000-0000000'
            if change=='two_physical':
                packages.append(dict(packages[1],tracking_id='TBA334595546739',tracking_url=packages[1]['tracking_url'].replace('new','second')))
            with self.subTest(change=change):
                self.assertEqual(len(main.canonical_tracking_packages(packages)),len(packages))

    def test_dispatch_shadow_after_tracking_payload_was_already_cleaned(self):
        old,target=self.packages()
        common=dict(amazon_order_id=target['amazon_order_id'],asins_json='["B071FTZ8C9"]',order_line_ids_json='[1]')
        rows=[dict(common,id=1,scan_code='AMZPKG-OLD',tracking_url=old['tracking_url']),
              dict(common,id=2,scan_code=target['tracking_id'],tracking_url=target['tracking_url'])]
        conn=MagicMock()
        conn.execute.return_value.fetchall.return_value=[dict(id=1,tracking_payload=json.dumps([target]))]
        self.assertEqual(repair.dispatch_shadow_pairs(main,conn,rows),[(1,2)])
        rows.append(dict(rows[1],id=3,scan_code='TBA334595546739'))
        self.assertEqual(repair.dispatch_shadow_pairs(main,conn,rows),[])


class OrphanScanRepairTests(BaseScanFixture):
    def orphan(self):
        self.conn.execute("""UPDATE package_pickup_scan_events SET matched=1,package_id=999,
            odoo_order_name='NC24335',amazon_order_id='111-4064278-0332216' WHERE id=194""")

    def test_deleted_package_relinks_original_scan_without_count_increment(self):
        self.orphan()
        with patch.object(main,'change_package_pickup_count') as count:
            self.assertEqual(repair.repair_orphan_scans(main,self.conn),1)
            self.assertEqual(repair.repair_orphan_scans(main,self.conn),0)
            count.assert_not_called()
        event=dict(self.conn.execute('SELECT * FROM package_pickup_scan_events WHERE id=194').fetchone())
        self.assertEqual(event['package_id'],50622)
        self.assertEqual(event['scanned_at'],'2026-09-02T15:05:51+00:00')
        row=dict(self.conn.execute('SELECT * FROM amazon_dispatch_packages WHERE id=50622').fetchone())
        self.assertEqual(row['received_at'],event['scanned_at'])

    def test_undo_wrong_order_and_later_reset_are_never_restored(self):
        self.orphan()
        for statement in [
            "UPDATE package_pickup_scan_events SET undone_at='2026-09-03' WHERE id=194",
            "UPDATE package_pickup_scan_events SET undone_at=NULL,odoo_order_name='OTHER' WHERE id=194",
            "UPDATE package_pickup_scan_events SET odoo_order_name='NC24335';",
        ]:
            self.conn.execute(statement)
            if statement.endswith(';'):
                self.conn.execute("UPDATE amazon_dispatch_packages SET not_received_at='2026-09-03T00:00:00+00:00'")
            self.assertEqual(repair.repair_orphan_scans(main,self.conn),0)


class FulfilledHistoryTests(unittest.TestCase):
    def test_full_fulfilment_is_separate_from_physical_scan_readiness(self):
        for status, cancelled, expected in [('FULFILLED','',True),('PARTIALLY_FULFILLED','',False),('FULFILLED','2026-09-16',False)]:
            event=dict(matched=1,package_id=10)
            conn=MagicMock()
            conn.execute.return_value.fetchone.return_value=dict(id=10,store_id=1,odoo_order_id=24586,odoo_order_name='NC24586')
            readiness=dict(ready_to_ship=False,total_packages=4,received_packages=2)
            with patch.object(main,'package_pickup_shopify_statuses',return_value={(1,'NC24586'):dict(fulfillment_status=status,cancelled_at=cancelled)}), patch.object(main,'package_pickup_order_readiness',return_value=readiness):
                main.pickup_history_current_readiness(conn,[event])
            self.assertEqual(event['shopify_fulfilled'],expected)
            self.assertFalse(event['current_order_readiness']['ready_to_ship'])
