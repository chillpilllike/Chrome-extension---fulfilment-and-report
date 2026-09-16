"""Reconcile tracking shadows and genuine scans without inventing receipts."""
import json


def single_order_unit(m, package, asin):
    products = package.get('order_products') or []
    matches = [p for p in products if str(p.get('asin') or '').upper() == asin]
    return len(matches) == 1 and matches[0].get('quantity_verified') is True and matches[0].get('quantity') == 1


def single_unit_shadow_pairs(m, packages):
    pairs = []
    for index, old in enumerate(packages):
        if m.tracking_package_physical_id(old) or not m.tracking_package_shipment_key(old).startswith('item:'):
            continue
        asins = m.replacement_tracking.package_asins(old)
        orders = m.package_direct_amazon_order_ids(old)
        if len(asins) != 1 or len(orders) != 1:
            continue
        asin = next(iter(asins))
        if not single_order_unit(m, old, asin):
            continue
        candidates = [(i, p) for i, p in enumerate(packages)
                      if m.tracking_package_physical_id(p)
                      and m.package_direct_amazon_order_ids(p) == orders
                      and m.replacement_tracking.package_asins(p) == asins]
        if len(candidates) != 1:
            continue
        target_index, target = candidates[0]
        if not single_order_unit(m, target, asin):
            continue
        pairs.append((index, target_index))
    return pairs


def dispatch_shadow_pairs(m, conn, rows):
    if not any(m.tracking_package_shipment_key(r).startswith('item:') and not m.package_tracking_id_is_physical(r.get('canonical_scan_code') or r.get('scan_code')) for r in rows):
        return []
    line_ids = sorted({int(v) for r in rows for v in m.parse_json_list_value(r.get('order_line_ids_json'))})
    if not line_ids:
        return []
    sources = conn.execute(f"SELECT id,tracking_payload FROM order_lines WHERE id IN ({','.join('?' for _ in line_ids)})", line_ids).fetchall()
    result = []
    for line in sources:
        packages = m.parse_tracking_packages(line['tracking_payload'])
        for target in packages:
            asins = m.replacement_tracking.package_asins(target)
            orders = m.package_direct_amazon_order_ids(target)
            if len(asins) != 1 or len(orders) != 1 or not m.tracking_package_physical_id(target):
                continue
            if not single_order_unit(m, target, next(iter(asins))):
                continue
            order = next(iter(orders))
            scoped = [r for r in rows if r.get('amazon_order_id') == order
                      and line['id'] in m.parse_json_list_value(r.get('order_line_ids_json'))
                      and set(m.parse_json_list_value(r.get('asins_json'))) == asins]
            physical = [r for r in scoped if m.package_tracking_id_is_physical(r.get('canonical_scan_code') or r.get('scan_code'))]
            if len(physical) != 1 or m.normalize_dispatch_scan_code(physical[0].get('canonical_scan_code') or physical[0].get('scan_code')) != m.tracking_package_physical_id(target):
                continue
            aliases = [r for r in scoped if m.tracking_package_shipment_key(r).startswith('item:')
                       and not m.package_tracking_id_is_physical(r.get('canonical_scan_code') or r.get('scan_code'))
                       and set(m.parse_json_list_value(r.get('order_line_ids_json'))) == set(m.parse_json_list_value(physical[0].get('order_line_ids_json')))]
            result.extend((r['id'], physical[0]['id']) for r in aliases)
    return list(dict.fromkeys(result))


def repair_orphan_scans(m, conn):
    events = m.rows_to_dicts(conn.execute("""
        SELECT e.* FROM package_pickup_scan_events e
        WHERE e.matched=1 AND e.undone_at IS NULL AND e.package_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM amazon_dispatch_packages old WHERE old.id=e.package_id)
          AND EXISTS (SELECT 1 FROM amazon_dispatch_packages p
            WHERE (p.canonical_scan_code=e.scan_code OR p.scan_code=e.scan_code)
              AND p.store_id=e.store_id AND p.amazon_order_id=e.amazon_order_id
              AND UPPER(p.odoo_order_name)=UPPER(e.odoo_order_name))
        ORDER BY e.scanned_at,e.id LIMIT 1000 FOR UPDATE SKIP LOCKED
    """).fetchall())
    repaired = 0
    for event in events:
        code = m.normalize_dispatch_scan_code(event['scan_code'])
        if not m.dispatch_scan_code_is_physical(code):
            continue
        matches = m.rows_to_dicts(conn.execute("""
            SELECT * FROM amazon_dispatch_packages
            WHERE (canonical_scan_code=? OR scan_code=?) AND store_id=?
              AND amazon_order_id=? AND UPPER(odoo_order_name)=UPPER(?)
            FOR UPDATE
        """, (code,code,event['store_id'],event['amazon_order_id'],event['odoo_order_name'])).fetchall())
        if len(matches) != 1:
            continue
        package = matches[0]
        scan_time = m.parse_any_datetime(event['scanned_at'])
        reset_time = m.parse_any_datetime(package.get('not_received_at'))
        if not scan_time or (reset_time and reset_time >= scan_time):
            continue
        # Restore evidence from an actual accepted historical scan. This is not
        # a new receipt and must not increase daily pickup totals.
        conn.execute("""
            INSERT INTO package_pickup_delivery_records
              (package_id,delivered_at,scanned_at,last_scanned_at,scanned_code,scan_count,updated_at,created_at)
            VALUES (?,?,?,?,?,1,?,?)
            ON CONFLICT(package_id) DO UPDATE SET
              scanned_at=LEAST(COALESCE(package_pickup_delivery_records.scanned_at,excluded.scanned_at),excluded.scanned_at),
              last_scanned_at=GREATEST(COALESCE(package_pickup_delivery_records.last_scanned_at,excluded.last_scanned_at),excluded.last_scanned_at),
              scanned_code=excluded.scanned_code,scan_count=GREATEST(package_pickup_delivery_records.scan_count,1),
              updated_at=excluded.updated_at
        """, (package['id'],event['scanned_at'],event['scanned_at'],event['scanned_at'],code,m.utc_now(),event['scanned_at']))
        conn.execute("""
            UPDATE amazon_dispatch_packages SET received_at=LEAST(COALESCE(NULLIF(received_at,''),?),?),
              updated_at=? WHERE id=?
        """, (event['scanned_at'],event['scanned_at'],m.utc_now(),package['id']))
        conn.execute("""
            UPDATE package_pickup_scan_events SET package_id=?,reconciled_at=?,
              original_result_status=COALESCE(original_result_status,result_status),
              original_message=COALESCE(original_message,message)
            WHERE id=? AND undone_at IS NULL
        """, (package['id'],m.utc_now(),event['id']))
        repaired += 1
    if repaired:
        m.fast_page_cache_clear_matching({'package-pickups','dispatch-related-parts','dispatch-sorting-summary','dispatch-status'})
    return repaired
