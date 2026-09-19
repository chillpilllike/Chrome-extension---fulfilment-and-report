import json
import os
import unittest
from app import main as m


class SharedParcelDisplayTests(unittest.TestCase):
    def test_same_barcode_counts_once_and_retains_both_purchases_and_scan(self):
        first = dict(id=1, store_id=1, odoo_order_id=50, amazon_order_id='113-0000000-0000001',
                     canonical_scan_code='TBA999999999999', scan_code='TBA999999999999',
                     order_line_ids_json='[10]', asins_json='["B000000001"]',
                     tracking_url='https://www.amazon.com/progress-tracker/package?orderId=113-0000000-0000001&shipmentId=ONE',
                     received_at='2026-09-15T15:00:32+00:00', pickup_scanned_at='2026-09-15T15:00:32+00:00')
        second = dict(first, id=2, amazon_order_id='113-0000000-0000002', scan_code='AMZPKG-SHARED',
                      order_line_ids_json='[11]', asins_json='["B000000002"]', received_at=None, pickup_scanned_at=None,
                      tracking_url='https://www.amazon.com/progress-tracker/package?orderId=113-0000000-0000002&shipmentId=TWO')
        rows = m.collapse_dispatch_related_parts([first, second])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 1)
        self.assertEqual(set(json.loads(rows[0]['order_line_ids_json'])), {10, 11})
        self.assertEqual(len(rows[0]['amazon_order_ids']), 2)
        scanned = m.dedupe_dispatch_package_rows([second, first])
        self.assertEqual(len(scanned), 1)
        self.assertEqual(scanned[0]['id'], 1)
        self.assertEqual(scanned[0]['received_at'], first['received_at'])
        self.assertEqual(rows[0]['pickup_scanned_at'], first['pickup_scanned_at'])
        self.assertEqual(len(m.collapse_dispatch_related_parts([first, dict(second, odoo_order_id=51)])), 2)


@unittest.skipUnless(os.getenv('PICKUP_TEST_POSTGRES') == '1', 'requires isolated PostgreSQL')
class SharedParcelCaptureTests(unittest.TestCase):
    def test_independent_captures_required_and_repeated_sync_keeps_identity(self):
        from app.db.session import POSTGRES_URL
        self.assertIn('127.0.0.1:55439', POSTGRES_URL)
        oids = ['113-0000000-0000081', '113-0000000-0000082']
        code = 'TBA999999999981'
        with m.db() as c:
            c.execute("DELETE FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSHARED'")
            c.execute("DELETE FROM order_lines WHERE odoo_order_name='TESTSHARED'")
            packages=[]
            for i, oid in enumerate(oids):
                p=dict(amazon_order_id=oid, tracking_id=code, asins=[f'B00000008{i}'], status='Delivered September 14',
                       tracking_url=f'https://www.amazon.com/progress-tracker/package?orderId={oid}&shipmentId=SHARED{i}')
                packages.append(p)
                c.execute('''INSERT INTO order_lines(store_id,odoo_order_id,odoo_order_name,odoo_line_id,asin,quantity,product_name,state,order_engine,amazon_order_id,tracking_status,tracking_payload,created_at,updated_at)
                   VALUES(1,999981,'TESTSHARED',?,?,1,'Test','delivered','chrome',?,'Delivered',?,?,?)''',
                   (999981+i,f'B00000008{i}',oid,json.dumps([p]) if i==0 else '[]',m.utc_now(),m.utc_now()))
            m.sync_dispatch_packages_for_order(c,oids[0])
            c.execute('UPDATE amazon_dispatch_packages SET received_at=? WHERE scan_code=?', ('2026-09-15T15:00:32+00:00',code))
            second=dict(c.execute('SELECT * FROM order_lines WHERE amazon_order_id=?',(oids[1],)).fetchone())
            values=m.dispatch_bulk_package_rows_for_order(oids[1],[dict(second,tracking_payload=json.dumps([packages[1]]))],m.utc_now())[1]
            m.bulk_upsert_dispatch_package_rows(c,values)
            row=c.execute('SELECT * FROM amazon_dispatch_packages WHERE amazon_order_id=?',(oids[1],)).fetchone()
            self.assertNotEqual(row['canonical_scan_code'],code, 'Uncorroborated bulk input must remain a conflict')
            for bad in [dict(packages[1],asins=['B000BAD000']),dict(packages[1],amazon_order_id=oids[0])]:
                c.execute('UPDATE order_lines SET tracking_payload=? WHERE amazon_order_id=?',(json.dumps([bad]),oids[1]))
                m.dispatch_purchase_identity.reconcile_shared_packages(m,c,oids[1])
                row=c.execute('SELECT * FROM amazon_dispatch_packages WHERE amazon_order_id=?',(oids[1],)).fetchone()
                self.assertNotEqual(row['canonical_scan_code'],code)
            c.execute('UPDATE order_lines SET tracking_payload=? WHERE amazon_order_id=?',(json.dumps([packages[1]]),oids[1]))
            for _ in range(3):
                for oid in oids:m.sync_dispatch_packages_for_order(c,oid)
            rows=m.rows_to_dicts(c.execute("SELECT * FROM amazon_dispatch_packages WHERE odoo_order_name='TESTSHARED'").fetchall())
            self.assertEqual(len(rows),2)
            self.assertEqual({r['amazon_order_id'] for r in rows},set(oids))
            parts=m.collapse_dispatch_related_parts(rows)
            self.assertEqual(len(parts),1)
            self.assertEqual(parts[0]['received_at'],'2026-09-15T15:00:32+00:00')
