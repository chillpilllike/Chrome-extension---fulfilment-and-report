import sqlite3
import unittest
from app.services.inventory_labels import annotate_inventory_labels


class InventoryLabelsTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE inventory_items (id INTEGER, source_inventory_item_id INTEGER, order_line_id INTEGER, store_id INTEGER, amazon_order_id TEXT)")
        self.conn.execute("CREATE TABLE order_lines (id INTEGER, store_id INTEGER, amazon_order_id TEXT, tracking_status TEXT)")
        self.conn.execute("INSERT INTO order_lines VALUES (10,1,'amazon-1','Delivered')")
        self.conn.execute("INSERT INTO inventory_items VALUES (1,NULL,10,1,'amazon-1')")
        self.conn.execute("INSERT INTO inventory_items VALUES (28,1,NULL,1,'amazon-1')")

    def test_legacy_allocations_show_customer_cancellation_and_amazon_delivery(self):
        rows = [{"id": 28, "source_type": "amazon_cancelled", "status": "reserved", "quantity": 1}]
        annotate_inventory_labels(rows, self.conn)
        self.assertEqual(rows[0]["source_label"], "Cancelled customer order")
        self.assertEqual(rows[0]["amazon_tracking_status"], "Delivered")
        self.assertEqual(rows[0]["source_type"], "amazon_cancelled")
        self.assertEqual((rows[0]["status"], rows[0]["quantity"]), ("reserved", 1))

    def test_replacement_amazon_order_does_not_supply_wrong_delivery_status(self):
        self.conn.execute("UPDATE order_lines SET amazon_order_id='replacement'")
        rows = [{"id": 28, "source_type": "amazon_cancelled"}]
        annotate_inventory_labels(rows, self.conn)
        self.assertEqual(rows[0]["amazon_tracking_status"], "Not recorded")

    def test_other_store_does_not_supply_delivery_status(self):
        self.conn.execute("UPDATE order_lines SET store_id=2")
        rows = [{"id": 28, "source_type": "cancelled_order"}]
        annotate_inventory_labels(rows, self.conn)
        self.assertEqual(rows[0]["amazon_tracking_status"], "Not recorded")

    def test_manual_unknown_and_empty(self):
        rows = [{"id": 1, "source_type": "manual"}, {"id": 28, "source_type": ""}]
        annotate_inventory_labels(rows, self.conn)
        self.assertEqual([row["source_label"] for row in rows], ["Manual stock", "Legacy / other stock"])
        annotate_inventory_labels([], self.conn)

    def test_legacy_and_physical_scan_confidence_are_distinct(self):
        rows = [{"id": 1, "legacy_delivery_order_id": "amazon-1", "amazon_order_id": "amazon-1"},
                {"id": 28, "legacy_delivery_order_id": "amazon-1", "amazon_order_id": "amazon-1", "source_received_at": "2026-09-05"}]
        annotate_inventory_labels(rows, self.conn)
        self.assertEqual([row['stock_confidence'] for row in rows], ['legacy_delivery', 'scanned'])
