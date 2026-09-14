import json
import sqlite3
import unittest
from app import main


def package(i=1, status='Delivered', scanned='2026-09-10', lines=None, **extra):
    return {'id':i,'store_id':1,'odoo_order_id':10,'odoo_order_name':'NC1',
            'scan_code':f'TBA{i:012d}','canonical_scan_code':f'TBA{i:012d}',
            'amazon_order_id':'113-1111111-1111111','package_index':i,
            'package_status':status,'promise':'','pickup_scanned_at':scanned,
            'order_line_ids_json':json.dumps(lines if lines is not None else [1]),**extra}

class ReadyViewTests(unittest.TestCase):
    def complete(self,parts,lines=None):return main.package_pickup_order_complete(parts,lines or [{'id':1,'state':'ordered'}])
    def test_all_packages_delivered_and_scanned(self):
        self.assertTrue(self.complete([package(1),package(2)]))
    def test_one_or_two_undelivered_exclude_whole_order(self):
        for count in [1,2]:
            self.assertFalse(self.complete([package(1)]+[package(i+2,status='Running late') for i in range(count)]))
    def test_delivery_alone_is_not_a_scan(self):
        self.assertFalse(self.complete([package(1),package(2,scanned='')]))
    def test_untracked_order_line_blocks(self):
        self.assertFalse(self.complete([package()], [{'id':1,'state':'ordered'},{'id':2,'state':'pending'}]))
    def test_cancelled_line_does_not_block(self):
        self.assertTrue(self.complete([package()], [{'id':1,'state':'ordered'},{'id':2,'state':'cancelled'}]))
    def test_no_mapping_no_confirmation(self):
        self.assertFalse(self.complete([package(lines=[])]))
    def test_not_received_blocks_even_with_old_scan(self):
        self.assertFalse(self.complete([package(not_received_at='2026-09-12')]))
    def test_no_twenty_package_truncation(self):
        self.assertFalse(self.complete([package(i) for i in range(1,25)]+[package(25,status='In transit')]))
    def test_missing_order_lines_do_not_prove_ready(self):
        self.assertFalse(main.package_pickup_order_complete([package()],[]))
    def test_batch_query_includes_other_dates_and_keeps_stores_separate(self):
        c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
        c.executescript('''CREATE TABLE amazon_dispatch_packages(id,store_id,odoo_order_id,odoo_order_name,scan_code,canonical_scan_code,amazon_order_id,package_index,package_status,promise,order_line_ids_json);
        CREATE TABLE package_pickup_delivery_records(package_id,scanned_at);
        CREATE TABLE order_lines(id,store_id,odoo_order_name,state);''')
        for i,store,status,scanned in [(1,1,'Delivered','2026-09-10'),(2,1,'In transit',''),(3,2,'Delivered','2026-01-01')]:
            p=package(i,status=status)
            c.execute('INSERT INTO amazon_dispatch_packages VALUES(?,?,?,?,?,?,?,?,?,?,?)',(i,store,10,'NC1',p['scan_code'],p['scan_code'],p['amazon_order_id'],i,status,'','[1]'))
            c.execute('INSERT INTO package_pickup_delivery_records VALUES (?,?)',(i,scanned))
        c.executemany('INSERT INTO order_lines VALUES (?,?,?,?)',[(1,1,'NC1','ordered'),(1,2,'NC1','ordered')])
        rows=[{'store_id':store,'odoo_order_name':'NC1','pickup_scanned_at':'2026-09-10','shopify_fulfilled':fulfilled} for store,fulfilled in [(1,False),(2,False),(2,True)]]
        main.annotate_pickup_complete_orders(c,[{'amazon_packages':rows}])
        self.assertEqual([r['order_ready_for_shopify'] for r in rows],[False,True,False])

if __name__=='__main__':unittest.main()
