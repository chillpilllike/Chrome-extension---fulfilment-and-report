"""Run against an isolated local PostgreSQL database, never the app database.

INVENTORY_TEST_DSN must name a local disposable database. Each test rolls back.
"""
import ast
import os
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Optional
import unittest

from app.services.inventory_history import install_inventory_history, inventory_filter


@unittest.skipUnless(os.getenv("INVENTORY_TEST_DSN"), "isolated PostgreSQL required")
class InventoryHistoryTests(unittest.TestCase):
    def setUp(self):
        import psycopg2
        from app.db.session import PostgresConnection
        self.raw = psycopg2.connect(os.environ["INVENTORY_TEST_DSN"])
        self.addCleanup(self.raw.close)
        self.addCleanup(self.raw.rollback)
        self.conn = PostgresConnection(self.raw)
        self.conn.execute("CREATE TEMP TABLE order_lines (id INTEGER, store_id INTEGER, amazon_cancelled_order_id TEXT, amazon_cancelled_at TEXT, odoo_order_name TEXT)")
        self.conn.execute("CREATE TEMP TABLE amazon_dispatch_packages (store_id INTEGER, amazon_order_id TEXT, received_at TEXT, package_status TEXT)")
        self.conn.execute("""CREATE TEMP TABLE inventory_items (
            id SERIAL PRIMARY KEY, store_id INTEGER DEFAULT 1, order_line_id INTEGER,
            status TEXT DEFAULT 'incoming', quantity REAL DEFAULT 0, asin TEXT DEFAULT 'B0FWDB18WK',
            product_name TEXT, source_odoo_order_name TEXT, amazon_account_name TEXT,
            source_received_at TEXT, source_delivered_at TEXT, source_shopify_cancelled_at TEXT,
            source_inventory_item_id INTEGER, reserved_order_line_id INTEGER, reserved_quantity REAL,
            reserved_at TEXT, used_at TEXT, source_type TEXT DEFAULT 'cancelled_order',
            amazon_order_id TEXT, notes TEXT, created_at TEXT DEFAULT '2026-09-01T00:00:00Z',
            updated_at TEXT DEFAULT '2026-09-05T00:00:00Z'
        )""")
        install_inventory_history(self.conn)

    def add(self, **values):
        columns = list(values)
        return self.conn.execute(f"INSERT INTO inventory_items ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) RETURNING *", list(values.values())).fetchone()

    def cancelled(self):
        self.conn.execute("INSERT INTO order_lines VALUES (10,1,'cancelled-order','2026-09-05T05:48:24Z','NC24369')")

    def test_cancelled_empty_incoming_is_archived_with_reason(self):
        self.cancelled()
        item = self.add(order_line_id=10, amazon_order_id="cancelled-order")
        self.assertEqual(item["status"], "archived")
        self.assertIn("cancelled", item["archive_reason"])
        self.assertTrue(item["archived_at"])

    def test_cancellation_never_hides_received_reserved_manual_or_replacement(self):
        self.cancelled()
        variants = [dict(source_received_at="received"), dict(source_delivered_at="delivered"),
                    dict(quantity=1), dict(status="reserved"), dict(status="available"),
                    dict(source_type="manual"), dict(amazon_order_id="replacement-order"), dict(store_id=2)]
        for extra in variants:
            values = dict(order_line_id=10, amazon_order_id="cancelled-order")
            values.update(extra)
            self.assertNotEqual(self.add(**values)["status"], "archived")

    def test_sent_is_archived_but_keeps_consumed_quantity(self):
        item = self.add(status="reserved", quantity=4, reserved_order_line_id=11)
        self.conn.execute("UPDATE inventory_items SET status='used', used_at='2026-09-05T12:00:00Z' WHERE id=?", (item["id"],))
        sent = self.conn.execute("SELECT * FROM inventory_items WHERE id=?", (item["id"],)).fetchone()
        self.assertEqual(sent["quantity"], 4)
        self.assertIn("consumed", sent["archive_reason"])
        event = self.conn.execute("SELECT * FROM inventory_movements ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(event["event_type"], "sent")
        self.assertEqual(event["previous_state"]["status"], "reserved")

    def test_stale_inventory_receipt_fields_do_not_hide_received_package(self):
        self.cancelled()
        self.conn.execute("INSERT INTO amazon_dispatch_packages VALUES (1,'cancelled-order','2026-09-03','Delivered')")
        item = self.add(order_line_id=10, amazon_order_id="cancelled-order")
        self.assertEqual(item["status"], "incoming")

    def test_repeated_sync_does_not_duplicate_history(self):
        item = self.add(status="available", quantity=4)
        self.conn.execute("UPDATE inventory_items SET updated_at='later' WHERE id=?", (item["id"],))
        count = self.conn.execute("SELECT COUNT(*) AS n FROM inventory_movements").fetchone()["n"]
        self.assertEqual(count, 1)
        install_inventory_history(self.conn)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM inventory_movements").fetchone()["n"], 1)

    def test_split_allocation_records_source_quantity_change(self):
        item = self.add(status="available", quantity=4)
        self.conn.execute("UPDATE inventory_items SET quantity=2 WHERE id=?", (item["id"],))
        allocation = self.add(status="reserved", quantity=2, source_inventory_item_id=item["id"], reserved_order_line_id=33)
        events = self.conn.execute("SELECT * FROM inventory_movements ORDER BY id").fetchall()
        self.assertEqual([e["event_type"] for e in events], ["created", "quantity_changed", "reserved"])
        self.assertEqual(events[-1]["current_state"]["source_inventory_item_id"], item["id"])
        self.assertNotEqual(allocation["id"], item["id"])

    def endpoint(self, name):
        @contextmanager
        def db():
            yield self.conn
        class HTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
        tree = ast.parse(Path("app/main.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
        fn.decorator_list = []
        ns = dict(Any=Any, Optional=Optional, db=db, HTTPException=HTTPException,
                  clean_text=lambda value: str(value or "").strip(), utc_now=lambda: "2026-09-05T12:00:00Z",
                  fast_page_cache_clear_matching=lambda _: None, inventory_filter=inventory_filter,
                  rows_to_dicts=lambda rows: [dict(row) for row in rows])
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "inventory-endpoint", "exec"), ns)
        return ns[name], HTTPException

    def test_archive_endpoint_requires_reason_and_blocks_reserved(self):
        call, error = self.endpoint("api_archive_inventory")
        item = self.add(status="reserved", quantity=4, reserved_order_line_id=33)
        with self.assertRaises(error): call(item["id"], {"reason": ""})
        with self.assertRaises(error): call(item["id"], {"reason": "old"})
        item = self.add(status="available", quantity=4)
        call(item["id"], {"reason": "Expired; do not use"})
        call(item["id"], {"reason": "Second reason ignored"})
        archived = self.conn.execute("SELECT * FROM inventory_items WHERE id=?", (item["id"],)).fetchone()
        self.assertEqual(archived["archive_reason"], "Expired; do not use")
        self.assertEqual(archived["quantity"], 4)

    def test_filters_search_and_pagination(self):
        self.add(status="available", quantity=2, product_name="Active")
        self.add(status="archived", quantity=0, product_name="Archived")
        call, _ = self.endpoint("list_inventory_items")
        rows, total = call(view="active")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["product_name"], "Active")
        self.assertEqual(call(view="archived", q="Archived")[1], 1)
        self.assertEqual(call(view="archived", q="no match")[1], 0)
        self.assertEqual(call(view="active", page=2, per_page=1)[0], [])

    def test_legacy_backfill_has_honest_baseline_and_is_repeatable(self):
        self.conn.execute("DROP TRIGGER inventory_archive_guard_trigger ON inventory_items")
        self.conn.execute("DROP TRIGGER inventory_movement_trigger ON inventory_items")
        self.cancelled()
        item = self.add(order_line_id=10, amazon_order_id="cancelled-order")
        install_inventory_history(self.conn)
        install_inventory_history(self.conn)
        events = self.conn.execute("SELECT * FROM inventory_movements WHERE inventory_id=? ORDER BY id", (item["id"],)).fetchall()
        self.assertEqual([e["event_type"] for e in events], ["history_started", "archived"])
        self.assertIn("earlier movements", events[0]["reason"])

    def test_timeline_follows_source_and_allocations_only(self):
        self.cancelled()
        source = self.add(status="available", quantity=4)
        child = self.add(status="reserved", quantity=2, source_inventory_item_id=source["id"], reserved_order_line_id=10)
        self.add(status="available", quantity=5)
        call, _ = self.endpoint("api_inventory_timeline")
        result = call(child["id"])
        self.assertEqual({e["inventory_id"] for e in result["items"]}, {source["id"], child["id"]})
        self.assertEqual(result["items"][0]["target_order_name"], "NC24369")


if __name__ == "__main__":
    unittest.main()
