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
