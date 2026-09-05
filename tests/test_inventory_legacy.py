import ast
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Union
import unittest

from app.services.inventory_legacy import install_inventory_legacy, legacy_delivery_available
from tests.test_inventory_history import InventoryHistoryTests


class LegacyPolicyTests(unittest.TestCase):
    def test_requires_enrollment_matching_purchase_and_actual_delivered_status(self):
        line = dict(amazon_order_id="original", odoo_status_label="cancelled", tracking_status="Delivered")
        existing = dict(legacy_delivery_order_id="original")
        self.assertTrue(legacy_delivery_available(existing, line, {}))
        self.assertFalse(legacy_delivery_available(None, line, {}))
        self.assertFalse(legacy_delivery_available({}, line, {}))
        for changes in [dict(amazon_order_id="replacement"), dict(odoo_status_label="sale"), dict(tracking_status="Not delivered"), dict(amazon_cancelled_at="today", amazon_cancelled_order_id="original")]:
            self.assertFalse(legacy_delivery_available(existing, {**line, **changes}, {}))


@unittest.skipUnless(os.getenv("INVENTORY_TEST_DSN"), "isolated PostgreSQL required")
class LegacyMigrationTests(unittest.TestCase):
    add = InventoryHistoryTests.add

    def setUp(self):
        InventoryHistoryTests.setUp(self)
        for definition in ["amazon_order_id TEXT", "tracking_status TEXT", "tracking_checked_at TEXT", "odoo_status_label TEXT", "quantity REAL"]:
            self.conn.execute("ALTER TABLE order_lines ADD COLUMN " + definition)
        for definition in ["source_odoo_order_id INTEGER", "amazon_order_url TEXT", "source_tracking_id TEXT", "source_odoo_status TEXT"]:
            self.conn.execute("ALTER TABLE inventory_items ADD COLUMN " + definition)
        self.conn.execute("CREATE UNIQUE INDEX ON inventory_items(store_id,order_line_id)")
        self.conn.execute("INSERT INTO order_lines(id,store_id,odoo_order_name,amazon_order_id,tracking_status,tracking_checked_at,odoo_status_label,quantity) VALUES (10,1,'NC21500','original','Delivered','2026-09-01','cancelled',4)")

    def test_backfill_deducts_allocations_and_never_enrolls_future_records(self):
        source = self.add(order_line_id=10, amazon_order_id="original")
        self.add(status="archived", quantity=1, source_inventory_item_id=source["id"])
        install_inventory_legacy(self.conn)
        item = self.conn.execute("SELECT * FROM inventory_items WHERE id=?", (source["id"],)).fetchone()
        self.assertEqual((item["status"], item["quantity"], item["legacy_delivery_order_id"]), ("available", 3, "original"))
        self.assertIsNone(item["source_received_at"])
        self.conn.execute("INSERT INTO order_lines(id,store_id,amazon_order_id,tracking_status,odoo_status_label,quantity) VALUES (11,1,'later','Delivered','cancelled',2)")
        later = self.add(order_line_id=11, amazon_order_id="later")
        install_inventory_legacy(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM inventory_items WHERE id=?", (later["id"],)).fetchone()["status"], "incoming")

    def test_does_not_enroll_undelivered(self):
        self.conn.execute("UPDATE order_lines SET tracking_status='Not delivered'")
        item = self.add(order_line_id=10, amazon_order_id="original")
        install_inventory_legacy(self.conn)
        self.assertIsNone(self.conn.execute("SELECT legacy_delivery_order_id FROM inventory_items WHERE id=?", (item["id"],)).fetchone()["legacy_delivery_order_id"])

    def test_received_reserved_archived_and_fully_allocated_are_untouched(self):
        ids = []
        for order_id, overrides in [(11, dict(source_received_at='2026-09-01')), (12, dict(status='reserved')), (13, dict(status='archived')), (14, dict(source_type='manual'))]:
            self.conn.execute("INSERT INTO order_lines(id,store_id,amazon_order_id,tracking_status,odoo_status_label,quantity) VALUES (?,1,'original','Delivered','cancelled',4)", (order_id,))
            ids.append(self.add(order_line_id=order_id,amazon_order_id='original',**overrides)['id'])
        source = self.add(order_line_id=10,amazon_order_id='original')
        self.add(status='archived',quantity=4,source_inventory_item_id=source['id'])
        ids.append(source['id'])
        install_inventory_legacy(self.conn)
        for inventory_id in ids:
            self.assertIsNone(self.conn.execute("SELECT legacy_delivery_order_id FROM inventory_items WHERE id=?", (inventory_id,)).fetchone()['legacy_delivery_order_id'])

    def test_package_receipt_excludes_legacy_exception(self):
        source = self.add(order_line_id=10, amazon_order_id="original")
        self.conn.execute("INSERT INTO amazon_dispatch_packages VALUES (1,'original','received','Delivered')")
        install_inventory_legacy(self.conn)
        self.assertEqual(self.conn.execute("SELECT status FROM inventory_items WHERE id=?", (source["id"],)).fetchone()["status"], "incoming")

    def test_background_sync_keeps_remaining_quantity_and_scan_upgrade(self):
        source = self.add(order_line_id=10, amazon_order_id="original")
        self.add(status="reserved", quantity=1, source_inventory_item_id=source["id"])
        install_inventory_legacy(self.conn)
        evidence = dict(delivered=True, received_at="", received_quantity=0, shopify_cancelled_at="", tracking_id="TRACK", delivered_at="2026-09-01")
        @contextmanager
        def db():
            yield self.conn
        fn = next(n for n in ast.parse(Path('app/main.py').read_text()).body if isinstance(n, ast.FunctionDef) and n.name=='ensure_inventory_for_line')
        ns = dict(Any=Any, Union=Union, db=db, inventory_source_evidence=lambda conn,row: evidence, legacy_delivery_available=legacy_delivery_available, utc_now=lambda: '2026-09-05')
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'legacy-sync','exec'),ns)
        line = dict(self.conn.execute("SELECT * FROM order_lines WHERE id=10").fetchone())
        line.update(asin='B0F3RXG74P',product_name='Example',odoo_order_id=10,amazon_order_url='',amazon_account_name='')
        ns['ensure_inventory_for_line'](line)
        ns['ensure_inventory_for_line'](line)
        item = self.conn.execute("SELECT * FROM inventory_items WHERE id=?", (source['id'],)).fetchone()
        self.assertEqual((item['status'],item['quantity']),('available',3))
        evidence.update(received_at='2026-09-06',received_quantity=4)
        ns['ensure_inventory_for_line'](line)
        item = self.conn.execute("SELECT * FROM inventory_items WHERE id=?", (source['id'],)).fetchone()
        self.assertEqual((item['status'],item['quantity'],item['source_received_at']),('available',3,'2026-09-06'))
