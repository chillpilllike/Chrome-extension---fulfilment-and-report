"""Presentation-only inventory labels; never change stock or release evidence."""


def annotate_inventory_labels(items, conn):
    ids = [int(item["id"]) for item in items]
    source_rows = {}
    if ids:
        placeholders = ",".join("?" for _ in ids)
        rows = conn.execute(f"""
            SELECT i.id, o.tracking_status
            FROM inventory_items i
            LEFT JOIN inventory_items source ON source.id=i.source_inventory_item_id
            LEFT JOIN order_lines o ON o.id=COALESCE(i.order_line_id, source.order_line_id)
              AND o.store_id=i.store_id
              AND o.amazon_order_id=i.amazon_order_id
              AND COALESCE(i.amazon_order_id, '')!=''
            WHERE i.id IN ({placeholders})
        """, ids).fetchall()
        source_rows = {int(row["id"]): dict(row) for row in rows}
    for item in items:
        item["stock_confidence"] = (
            "scanned" if item.get("source_received_at")
            else "legacy_delivery" if item.get("legacy_delivery_order_id") == item.get("amazon_order_id") and item.get("legacy_delivery_order_id")
            else "manual" if item.get("source_type") == "manual" else "pending"
        )
        item["source_label"] = {
            "manual": "Manual stock",
            "amazon_cancelled": "Cancelled customer order",
            "cancelled_order": "Cancelled customer order",
        }.get(item.get("source_type"), "Legacy / other stock")
        item["amazon_tracking_status"] = source_rows.get(int(item["id"]), {}).get("tracking_status") or "Not recorded"
