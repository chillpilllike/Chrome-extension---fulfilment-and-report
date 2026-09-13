import inspect
import json
import re
import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from app import main
from app.schemas.payloads import ThirdPartyTrackingPayload, PackagePickupScanPayload

CODE = '1ZR1B5860338051528'
STAMP = '2026-09-11T15:49:13+00:00'

class Connection:
    def __init__(self):
        self.raw = sqlite3.connect(':memory:')
        self.raw.row_factory = lambda c, r: dict(zip([x[0] for x in c.description],r))
        self.raw.create_function('GREATEST',2,max)
    def execute(self,sql,params=()):
        sql=re.sub(r'FOR UPDATE(?: SKIP LOCKED)?','',sql).replace('ADD COLUMN IF NOT EXISTS','ADD COLUMN')
        if 'pg_advisory_xact_lock' in sql:return self.raw.execute('SELECT 1')
        return self.raw.execute(sql,params)

class ThirdPartyTrackingTests(unittest.TestCase):
    def setUp(self):
        self.conn=Connection()
        source=inspect.getsource(main.init_db)
        for name in ['amazon_dispatch_packages','package_pickup_checks','package_pickup_non_amazon']:
            ddl=re.search(r'CREATE TABLE IF NOT EXISTS '+name+r' \(.*?\n            \);',source,re.S).group()
            self.conn.execute(ddl)
        self.conn.execute("ALTER TABLE amazon_dispatch_packages ADD COLUMN source_type TEXT DEFAULT 'amazon'")
        self.conn.execute('''CREATE TABLE package_pickup_delivery_records (package_id INTEGER PRIMARY KEY,delivered_at TEXT,scanned_at TEXT,last_scanned_at TEXT,scanned_code TEXT,scan_count INTEGER DEFAULT 0,updated_at TEXT,created_at TEXT)''')
        self.conn.execute('''CREATE TABLE order_lines(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,order_engine TEXT,amazon_order_id TEXT,state TEXT,asin TEXT,replacement_asin TEXT,product_name TEXT,quantity REAL,manual_estimated_delivery_at TEXT)''')
        for line,order,store in [(1,23144,1),(2,23144,1),(3,888,1),(4,999,2)]:
            self.conn.execute("INSERT INTO order_lines VALUES (?,?,?,?,'third_party','Supplier 001579033','ordered','B0CZ5CV6PF','','Product',1,'')",(line,store,order,'NC23124' if order==23144 else 'Other'))
        main.ensure_package_pickup_scan_history_table(self.conn)
        @contextmanager
        def database():
            yield self.conn
        def exact(conn,code,store_id=None):
            return conn.execute("SELECT * FROM amazon_dispatch_packages WHERE scan_code=? AND (? IS NULL OR store_id=?)",(code,store_id,store_id)).fetchall()
        def readiness(conn,package):
            packages=conn.execute('SELECT * FROM amazon_dispatch_packages WHERE store_id=? AND odoo_order_id=?',(package['store_id'],package['odoo_order_id'])).fetchall()
            return main.package_pickup_readiness_from_parts([{**p,'received':bool(p['received_at'])} for p in packages])
        for name,value in [('db',database),('package_pickup_strict_scan_matches',exact),('amazon_order_has_open_payment_failure',lambda *a:False),('package_pickup_order_readiness',readiness),('fast_page_cache_clear_matching',lambda *a:None)]:
            mock=patch.object(main,name,value);mock.start();self.addCleanup(mock.stop)
        self.addCleanup(self.conn.raw.close)
    def assign(self,ids=None,**kwargs):
        return main.api_assign_third_party_tracking(ThirdPartyTrackingPayload(store_id=1,line_ids=ids or [1],tracking_id=CODE,**kwargs))
    def test_assign_reconciles_original_scan_and_does_not_double_count(self):
        event=main.record_package_pickup_scan_event(self.conn,scan_code=CODE,result_status='not_found',matched=False,message='not found',scanned_at=STAMP,store_id=0)
        result=self.assign()
        self.assertEqual(result['reconciled_scans'],1)
        saved=self.conn.execute('SELECT * FROM package_pickup_scan_events WHERE id=?',(event,)).fetchone()
        self.assertEqual(saved['scanned_at'],STAMP)
        self.assertEqual(saved['original_result_status'],'not_found')
        self.assertEqual(saved['odoo_order_name'],'NC23124')
        self.assertEqual(saved['matched'],1)
        self.assertEqual(self.assign()['reconciled_scans'],0)
        duplicate=main.api_package_pickup_scan(PackagePickupScanPayload(scan_code=CODE))
        self.assertTrue(duplicate['duplicate'])
        count=self.conn.execute('SELECT * FROM package_pickup_checks').fetchone()
        self.assertEqual((count['amazon_picked_up'],count['non_amazon_picked_up'],count['pickup_date']),(0,1,'2026-09-11'))
        line=self.conn.execute('SELECT * FROM order_lines WHERE id=1').fetchone()
        self.assertEqual(line['amazon_order_id'],'Supplier 001579033')
        self.assertEqual(line['state'],'ordered')
    def test_assignment_waits_for_receipt_and_supports_multiple_packages(self):
        first=self.assign()
        self.assertIsNone(self.conn.execute('SELECT received_at FROM amazon_dispatch_packages WHERE id=?',(first['package_id'],)).fetchone()['received_at'])
        main.api_assign_third_party_tracking(ThirdPartyTrackingPayload(store_id=1,line_ids=[2],tracking_id='OTHER123456789'))
        result=main.api_package_pickup_scan(PackagePickupScanPayload(scan_code=CODE))
        self.assertTrue(result['matched'])
        self.assertFalse(result['package']['order_readiness']['ready_to_ship'])
        result=main.api_package_pickup_scan(PackagePickupScanPayload(scan_code='OTHER123456789'))
        self.assertTrue(result['matched'])
        self.assertTrue(result['package']['order_readiness']['ready_to_ship'])
    def test_conflicts_store_order_and_received_identity_are_guarded(self):
        first=self.assign()
        for ids in [[3],[4],[1,3],[2]]:
            with self.assertRaises(main.HTTPException):self.assign(ids)
        main.api_package_pickup_scan(PackagePickupScanPayload(scan_code=CODE))
        with self.assertRaises(main.HTTPException):
            main.api_assign_third_party_tracking(ThirdPartyTrackingPayload(store_id=1,line_ids=[1],tracking_id='NEW123456789',package_id=first['package_id']))
        self.assign(package_id=first['package_id'],tracking_url='https://www.ups.com/track?tracknum='+CODE)
        self.assertEqual(self.conn.execute('SELECT tracking_url FROM amazon_dispatch_packages').fetchone()['tracking_url'],'https://www.ups.com/track?tracknum='+CODE)
    def test_undo_reconciled_scan_restores_non_amazon_count(self):
        event=main.record_package_pickup_scan_event(self.conn,scan_code=CODE,result_status='not_found',matched=False,message='missing',scanned_at=STAMP)
        self.assign()
        row=self.conn.execute('SELECT * FROM package_pickup_scan_events WHERE id=?',(event,)).fetchone()
        row.update(day_start_utc='2026-09-11T04:00:00+00:00',day_end_utc='2026-09-12T04:00:00+00:00')
        result=main.reset_package_pickup_scan_event(self.conn,row,'2026-09-13T12:00:00+00:00','Test undo','2026-09-11')
        self.assertEqual(result['counts_reduced'],1)
        count=self.conn.execute('SELECT * FROM package_pickup_checks').fetchone()
        self.assertEqual(count['non_amazon_picked_up'],0)
        self.assertEqual(count['amazon_picked_up'],0)
        self.assertEqual(main.reconcile_package_pickup_scans(self.conn,only_scan_code=CODE),0)
    def test_validation_rejects_unsafe_urls_and_wrong_line_type(self):
        for code,url in [(CODE,'javascript:alert(1)'),(CODE,'https://user:pass@example.com'),('113-6618710-8061050',''),('https://example.com','')]:
            with self.assertRaises(main.HTTPException):main.validate_third_party_tracking_input(code,url)
        self.conn.execute("UPDATE order_lines SET order_engine='chrome' WHERE id=1")
        with self.assertRaises(main.HTTPException):self.assign()
    def test_amazon_not_delivered_guard_remains(self):
        self.assign()
        self.conn.execute("UPDATE amazon_dispatch_packages SET source_type='amazon'")
        result=main.api_package_pickup_scan(PackagePickupScanPayload(scan_code=CODE))
        self.assertTrue(result['not_delivered'])
        self.assertFalse(result['matched'])
    def test_saving_counts_does_not_duplicate_tracked_parcels_as_placeholders(self):
        from app.schemas.payloads import PackagePickupCountPayload
        main.record_package_pickup_scan_event(self.conn,scan_code=CODE,result_status='not_found',matched=False,message='missing',scanned_at=STAMP)
        self.assign()
        with patch.object(main,'package_pickup_data',return_value={}):
            main.api_package_pickup_counts(PackagePickupCountPayload(store_id=1,pickup_date='2026-09-11',non_amazon_picked_up=1))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) AS n FROM package_pickup_non_amazon').fetchone()['n'],0)
        self.assertEqual(self.conn.execute('SELECT non_amazon_picked_up FROM package_pickup_checks').fetchone()['non_amazon_picked_up'],1)
