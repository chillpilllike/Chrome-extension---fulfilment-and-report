"""Bounded, one-time exception for existing delivered customer-cancelled stock."""


def legacy_delivery_available(existing, line, evidence):
    return bool(
        existing and existing.get("legacy_delivery_order_id")
        and existing.get("legacy_delivery_order_id") == line.get("amazon_order_id")
        and str(line.get("odoo_status_label") or "").lower() in {"cancelled", "refunded"}
        and str(line.get("tracking_status") or "").strip().lower() == "delivered"
        and not (line.get("amazon_cancelled_at") and line.get("amazon_cancelled_order_id") == line.get("amazon_order_id"))
    )


def install_inventory_legacy(conn):
    exists = conn.execute("SELECT 1 FROM pg_attribute WHERE attrelid='inventory_items'::regclass AND attname='legacy_delivery_order_id' AND NOT attisdropped").fetchone()
    if exists:
        return
    conn.execute("ALTER TABLE inventory_items ADD COLUMN legacy_delivery_order_id TEXT")
    # Only presently qualifying records enter the exception. The migration never
    # enrolls future inventory, pending deliveries, reserved or archived stock.
    conn.execute("""
        UPDATE inventory_items i SET
            legacy_delivery_order_id=o.amazon_order_id,
            quantity=o.quantity-COALESCE((
                SELECT SUM(a.quantity) FROM inventory_items a WHERE a.source_inventory_item_id=i.id
                  AND a.status IN ('reserved', 'used', 'archived')
            ), 0),
            status='available',
            source_delivered_at=COALESCE(NULLIF(i.source_delivered_at, ''), o.tracking_checked_at),
            source_odoo_status=o.odoo_status_label,
            notes=CONCAT_WS(' | ', NULLIF(i.notes, ''), 'Legacy stock exception: Amazon delivered and customer order cancelled; no physical receipt scan. Remaining quantity excludes prior allocations.'),
            updated_at=CURRENT_TIMESTAMP::text
        FROM order_lines o
        WHERE i.order_line_id=o.id AND i.store_id=o.store_id
          AND i.amazon_order_id=o.amazon_order_id AND COALESCE(i.amazon_order_id, '')!=''
          AND i.status IN ('incoming', 'available') AND i.source_inventory_item_id IS NULL AND i.source_type!='manual'
          AND COALESCE(i.source_received_at, '')=''
          AND LOWER(TRIM(COALESCE(o.tracking_status, '')))='delivered'
          AND LOWER(COALESCE(o.odoo_status_label, '')) IN ('cancelled', 'refunded')
          AND NOT (COALESCE(o.amazon_cancelled_at, '')!='' AND COALESCE(o.amazon_cancelled_order_id, '')=o.amazon_order_id)
          AND NOT EXISTS (SELECT 1 FROM amazon_dispatch_packages p WHERE p.store_id=i.store_id
              AND p.amazon_order_id=i.amazon_order_id AND COALESCE(p.received_at, '')!='')
          AND o.quantity>COALESCE((SELECT SUM(a.quantity) FROM inventory_items a
              WHERE a.source_inventory_item_id=i.id AND a.status IN ('reserved', 'used', 'archived')), 0)
    """)
