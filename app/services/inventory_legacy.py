"""Retire the unsafe legacy-delivery inventory exception."""


def legacy_delivery_available(existing, line, evidence):
    # A delivered customer order is not proof that its product reached the
    # warehouse. Keep the call site during the schema transition, but never let
    # a legacy marker bypass the physical-receipt and Shopify-cancellation gates.
    return False


def install_inventory_legacy(conn):
    conn.execute("ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS legacy_delivery_order_id TEXT")
    # Earlier releases promoted delivered/cancelled orders without a warehouse
    # receipt. Archive only those still-active, unallocated source records. The
    # quantity and legacy marker remain on the record and in movement history.
    conn.execute("""
        UPDATE inventory_items i SET
            status='archived',
            archived_at=COALESCE(NULLIF(i.archived_at, ''), CURRENT_TIMESTAMP::text),
            archive_reason='Archived after legacy-inventory audit: no physical warehouse receipt scan; stock was not proven physically present. Recorded quantity and source evidence retained for audit.',
            updated_at=CURRENT_TIMESTAMP::text
        WHERE i.status='available'
          AND COALESCE(i.legacy_delivery_order_id, '')!=''
          AND i.legacy_delivery_order_id=i.amazon_order_id
          AND COALESCE(i.source_received_at, '')=''
          AND i.reserved_order_line_id IS NULL
          AND i.source_inventory_item_id IS NULL
    """)
