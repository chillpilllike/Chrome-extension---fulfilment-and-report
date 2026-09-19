import json
import os
import unittest
from unittest.mock import MagicMock, patch
from app import main as m
from app.services import dispatch_purchase_identity as identity


class BarcodeOwnershipTests(unittest.TestCase):
    def test_same_owner_keeps_barcode_other_purchase_gets_stable_placeholder(self):
        order=dict(store_id=1,odoo_order_id=26931,amazon_order_id='113-0000000-0000001')
        conn=MagicMock();conn.execute.return_value.fetchone.return_value=order
        code='TBA999999999999'
        self.assertEqual(identity.protected_code(m,conn,code,code,order),(code,code))
        other=dict(order,amazon_order_id='113-0000000-0000002')
        result=identity.protected_code(m,conn,code,code,other)
        self.assertTrue(result[0].startswith('AMZPKG-'))
        self.assertEqual(result,identity.protected_code(m,conn,code,code,other))
        self.assertFalse(m.package_tracking_id_is_physical(result[0]))

    def test_bulk_batch_keeps_two_purchases_even_before_either_exists(self):
        conn=MagicMock();conn.execute.return_value.fetchone.return_value=None
        first=['TBA999999999999','TBA999999999999','TBA999999999999','113-0000000-0000001','',1,26931,'NC26931']+['']*19
        second=list(first);second[3]='113-0000000-0000002'
        values=identity.protect_values(m,conn,[tuple(first),tuple(second)])
        self.assertEqual(len({r[0] for r in values}),2)


@unittest.skipUnless(os.getenv('PICKUP_TEST_POSTGRES')=='1','requires isolated PostgreSQL')
class ShipmentVisibilityIntegration(unittest.TestCase):
    def test_missing_carrier_id_survives_repeated_tracking_refresh(self):
        from app.db.session import POSTGRES_URL
        self.assertIn('127.0.0.1:55439',POSTGRES_URL)
        oid='113-0000000-0000099'
        package=dict(amazon_order_id=oid,shipment_id='VISIBILITYTEST',tracking_url=f'https://www.amazon.com/progress-tracker/package?orderId={oid}&shipmentId=VISIBILITYTEST',status='Delivered September 14',asins=['B000TEST01'])
        with m.db() as conn:
            conn.execute('DELETE FROM amazon_dispatch_packages WHERE amazon_order_id=?',(oid,))
            conn.execute("DELETE FROM order_lines WHERE odoo_order_name='TESTVISIBILITY'")
            conn.execute("""INSERT INTO order_lines(store_id,odoo_order_id,odoo_order_name,odoo_line_id,asin,quantity,product_name,state,order_engine,amazon_order_id,tracking_status,tracking_payload,created_at,updated_at)
                VALUES(1,999998,'TESTVISIBILITY',999998,'B000TEST01',1,'Test','delivered','chrome',?,'Delivered',?,?,?)""",(oid,json.dumps([package]),m.utc_now(),m.utc_now()))
            m.sync_dispatch_packages_for_order(conn,oid)
            for _ in range(3):m.refresh_dispatch_packages_from_tracking(conn,oid,'',[package])
            rows=conn.execute('SELECT * FROM amazon_dispatch_packages WHERE amazon_order_id=?',(oid,)).fetchall()
            self.assertEqual(len(rows),1)
            self.assertTrue(rows[0]['scan_code'].startswith('AMZPKG-'))
            self.assertFalse(rows[0]['received_at'])
            self.assertEqual(rows[0]['package_status'],'Delivered September 14')
