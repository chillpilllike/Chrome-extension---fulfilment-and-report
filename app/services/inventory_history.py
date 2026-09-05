"""Durable inventory audit, installed alongside the application's PostgreSQL schema."""


def install_inventory_history(conn):
    for name in ("archived_at", "archive_reason"):
        conn.execute(f"ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS {name} TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory_movements (
            id BIGSERIAL PRIMARY KEY,
            inventory_id INTEGER NOT NULL,
            occurred_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            previous_state JSONB,
            current_state JSONB NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inventory_movements_item ON inventory_movements(inventory_id, id)")
    # A baseline is explicitly an observation, not an invented historical movement.
    conn.execute("""
        INSERT INTO inventory_movements(inventory_id, occurred_at, event_type, current_state, reason)
        SELECT i.id, CURRENT_TIMESTAMP::text, 'history_started', to_jsonb(i),
               'Existing record when movement history was enabled; earlier movements may not be available.'
        FROM inventory_items i
        WHERE NOT EXISTS (SELECT 1 FROM inventory_movements m WHERE m.inventory_id=i.id)
    """)
    conn.execute("""
        CREATE OR REPLACE FUNCTION inventory_archive_guard() RETURNS trigger AS $$
        BEGIN
            IF NEW.status = 'used' AND COALESCE(NEW.archived_at, '') = '' THEN
                NEW.archived_at := COALESCE(NULLIF(NEW.used_at, ''), NEW.updated_at);
                NEW.archive_reason := 'Sent to the assigned order; stock consumed.';
            ELSIF NEW.status = 'incoming' AND NEW.quantity <= 0
                AND COALESCE(NEW.source_received_at, '') = ''
                AND COALESCE(NEW.source_delivered_at, '') = ''
                AND NEW.reserved_order_line_id IS NULL
                AND NEW.source_type != 'manual'
                AND NOT EXISTS (
                    SELECT 1 FROM amazon_dispatch_packages p
                    WHERE p.store_id=NEW.store_id AND p.amazon_order_id=NEW.amazon_order_id
                    AND (COALESCE(p.received_at, '')!='' OR LOWER(COALESCE(p.package_status, '')) LIKE 'delivered%')
                )
                AND (TG_OP = 'INSERT' OR OLD.status = 'incoming')
                AND NOT EXISTS (
                    SELECT 1 FROM inventory_items prior WHERE prior.order_line_id=NEW.order_line_id
                    AND prior.store_id=NEW.store_id AND prior.id IS DISTINCT FROM NEW.id
                    AND (prior.status!='incoming' OR prior.quantity>0
                        OR COALESCE(prior.source_received_at, '')!=''
                        OR COALESCE(prior.source_delivered_at, '')!='')
                )
                AND EXISTS (
                    SELECT 1 FROM order_lines o WHERE o.id=NEW.order_line_id
                    AND o.store_id=NEW.store_id
                    AND COALESCE(NEW.amazon_order_id, '') != ''
                    AND o.amazon_cancelled_order_id=NEW.amazon_order_id
                    AND COALESCE(o.amazon_cancelled_at, '') != ''
                ) THEN
                NEW.status := 'archived';
                NEW.archived_at := CURRENT_TIMESTAMP::text;
                NEW.archive_reason := 'Amazon purchase cancelled before receipt; no stock or delivery evidence.';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    conn.execute("""
        CREATE OR REPLACE FUNCTION inventory_record_movement() RETURNS trigger AS $$
        DECLARE event_name TEXT; old_snapshot JSONB;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                event_name := 'created';
                IF NEW.status = 'reserved' THEN event_name := 'reserved'; END IF;
                IF NEW.status IN ('archived', 'used') THEN event_name := 'archived'; END IF;
            ELSE
                old_snapshot := to_jsonb(OLD);
                IF (to_jsonb(NEW) - 'updated_at') = (old_snapshot - 'updated_at') THEN RETURN NEW; END IF;
                IF NEW.status = 'used' AND OLD.status != 'used' THEN event_name := 'sent';
                ELSIF NEW.archived_at IS DISTINCT FROM OLD.archived_at THEN event_name := 'archived';
                ELSIF NEW.used_at IS DISTINCT FROM OLD.used_at THEN event_name := 'sent';
                ELSIF NEW.status IS DISTINCT FROM OLD.status THEN event_name := 'status_changed';
                ELSIF NEW.quantity IS DISTINCT FROM OLD.quantity THEN event_name := 'quantity_changed';
                ELSE event_name := 'evidence_updated'; END IF;
            END IF;
            INSERT INTO inventory_movements(inventory_id, occurred_at, event_type, previous_state, current_state, reason)
            VALUES (NEW.id, CURRENT_TIMESTAMP::text, event_name, old_snapshot, to_jsonb(NEW),
                    CASE WHEN event_name IN ('archived', 'sent') THEN COALESCE(NEW.archive_reason, NEW.notes, '') ELSE COALESCE(NEW.notes, '') END);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    conn.execute("DROP TRIGGER IF EXISTS inventory_archive_guard_trigger ON inventory_items")
    conn.execute("CREATE TRIGGER inventory_archive_guard_trigger BEFORE INSERT OR UPDATE ON inventory_items FOR EACH ROW EXECUTE FUNCTION inventory_archive_guard()")
    conn.execute("DROP TRIGGER IF EXISTS inventory_movement_trigger ON inventory_items")
    conn.execute("CREATE TRIGGER inventory_movement_trigger AFTER INSERT OR UPDATE ON inventory_items FOR EACH ROW EXECUTE FUNCTION inventory_record_movement()")
    # Reconcile legacy records once; the guard applies the same rules to future writes.
    conn.execute("""
        UPDATE inventory_items i SET updated_at=i.updated_at
        WHERE COALESCE(i.archived_at, '') = '' AND (
            i.status='used' OR (
                i.status='incoming' AND i.quantity<=0
                AND COALESCE(i.source_received_at, '')='' AND COALESCE(i.source_delivered_at, '')=''
                AND i.reserved_order_line_id IS NULL AND i.source_type!='manual'
                AND EXISTS (SELECT 1 FROM order_lines o WHERE o.id=i.order_line_id AND o.store_id=i.store_id
                    AND COALESCE(i.amazon_order_id, '')!='' AND o.amazon_cancelled_order_id=i.amazon_order_id
                    AND COALESCE(o.amazon_cancelled_at, '')!='')
            )
        )
    """)


def inventory_filter(view):
    if view == "archived":
        return "status IN ('archived', 'used')"
    if view in {"available", "incoming", "reserved"}:
        return "status='" + view + "'"
    return "status NOT IN ('archived', 'used')"
