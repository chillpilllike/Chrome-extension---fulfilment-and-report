"""Preserve purchase identity when a carrier barcode is reported for another order."""
import hashlib


def owner(row):
    return (int(row.get('store_id') or 0), str(row.get('odoo_order_id') or row.get('odoo_order_name') or ''), str(row.get('amazon_order_id') or ''))


def protected_code(m, conn, code, display, order):
    if not m.package_tracking_id_is_physical(code):
        return code, display
    existing = conn.execute('SELECT * FROM amazon_dispatch_packages WHERE scan_code=?', (code,)).fetchone()
    if not existing or owner(existing) == owner(order):
        return code, display
    # Conflicting evidence must not steal a physical parcel or its receipt.
    # Keep the second purchase visible as an unconfirmed shipment instead.
    key = '|'.join(map(str, (*owner(order), code)))
    fallback = 'AMZPKG-' + hashlib.sha256(key.encode()).hexdigest()[:24].upper()
    return fallback, 'Tracking association needs review'


def protect_values(m, conn, values):
    result = []
    pending = {}
    for value in values:
        row = list(value)
        order = dict(amazon_order_id=row[3], store_id=row[5], odoo_order_id=row[6], odoo_order_name=row[7])
        code, display = protected_code(m, conn, row[0], row[2], order)
        if code in pending and pending[code] != owner(order):
            key = '|'.join(map(str, (*owner(order), row[0])))
            code, display = 'AMZPKG-' + hashlib.sha256(key.encode()).hexdigest()[:24].upper(), 'Tracking association needs review'
        pending[code] = owner(order)
        row[0] = row[1] = code
        row[2] = display
        result.append(tuple(row))
    return result


def reconcile_shared_packages(m, conn, amazon_order_id):
    """Link independently captured purchases in the same Odoo order to one parcel.

    A related-order link alone is insufficient: each purchase must have its own
    order-scoped capture with the identical physical barcode and shipment ASIN.
    Keep purchase rows/URLs separate; only their physical identity is shared.
    """
    scopes = conn.execute('''SELECT DISTINCT store_id, odoo_order_id, odoo_order_name
        FROM amazon_purchase_tracking_lines WHERE amazon_order_id=?''', (amazon_order_id,)).fetchall()
    linked = 0
    for scope in scopes:
        if not scope['store_id'] or not scope['odoo_order_id']:
            continue
        lines = conn.execute('''SELECT * FROM amazon_purchase_tracking_lines
            WHERE store_id=? AND odoo_order_id=? AND state!='cancelled'
              AND COALESCE(order_engine,'')!='third_party' ''',
            (scope['store_id'], scope['odoo_order_id'])).fetchall()
        captures = {}
        for line in lines:
            for package in m.parse_tracking_packages(line['tracking_payload']):
                oid = line['amazon_order_id']
                code = m.tracking_package_physical_id(package)
                if (not code or m.package_direct_amazon_order_ids(package) != {oid}
                        or package.get('asin_evidence_source') == 'order_inferred'
                        or not (package.get('asins') or package.get('products'))
                        or not m.package_matches_line(package, line)):
                    continue
                captures.setdefault(code, {}).setdefault(oid, []).append((line, package))
        for code, purchases in captures.items():
            if len(purchases) < 2:
                continue
            physical = conn.execute('SELECT * FROM amazon_dispatch_packages WHERE scan_code=?', (code,)).fetchone()
            if (not physical or owner(physical)[:2] != owner(dict(scope))[:2]
                    or physical['amazon_order_id'] not in purchases):
                continue
            for oid, evidence in purchases.items():
                rows = conn.execute('SELECT * FROM amazon_dispatch_packages WHERE amazon_order_id=? AND store_id=? AND odoo_order_id=?',
                                    (oid, scope['store_id'], scope['odoo_order_id'])).fetchall()
                linked_keys = set()
                for row in sorted(rows, key=lambda r: int(r['id']), reverse=True):
                    if row['scan_code'] == code:
                        continue
                    if m.package_tracking_id_is_physical(row['scan_code']):
                        continue
                    # A placeholder must name the exact captured shipment and line.
                    key = m.tracking_package_shipment_key(row)
                    line_ids = set(m.parse_json_list_value(row['order_line_ids_json']))
                    if key in linked_keys or not key or not any(key == m.tracking_package_shipment_key(p) and line['id'] in line_ids for line, p in evidence):
                        continue
                    conn.execute('UPDATE amazon_dispatch_packages SET canonical_scan_code=?, display_code=? WHERE id=?',
                                 (code, code, row['id']))
                    linked_keys.add(key)
                    linked += 1
            # Remove superseded placeholders only after physical identity is proven.
            for oid in purchases:
                m.collapse_dispatch_shipment_alias_rows(conn, oid)
    return linked
