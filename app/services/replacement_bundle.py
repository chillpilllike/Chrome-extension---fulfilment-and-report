"""Keep replacement components as real fulfilment rows in one customer order."""
from __future__ import annotations

import uuid

TOTAL_FIELDS = ('store_total_price', 'store_subtotal_native', 'store_delivery_native',
                'store_discount_native', 'store_adjustment_native', 'store_total_native')


def read_bundle(conn, line_id, store_id=None, lock=False):
    row = conn.execute('SELECT * FROM order_lines WHERE id=?', (line_id,)).fetchone()
    if not row or (store_id is not None and int(row['store_id']) != int(store_id)):
        raise ValueError('Order line not found in this store.')
    root_id = int(row.get('bundle_parent_line_id') or row['id'])
    # Lock parent first for every entry point, including edits opened on a child.
    suffix = ' FOR UPDATE' if lock else ''
    root = conn.execute('SELECT * FROM order_lines WHERE id=?' + suffix, (root_id,)).fetchone()
    if not root:
        raise ValueError('Original bundle line no longer exists.')
    children = conn.execute('SELECT * FROM order_lines WHERE bundle_parent_line_id=? ORDER BY id' + suffix, (root_id,)).fetchall()
    return dict(root), [dict(root), *(dict(child) for child in children)]


def ensure_editable(rows):
    for row in rows:
        if row.get('amazon_order_id') or row.get('chrome_claimed_by') or row.get('amazon_status') in {
            'chrome_queued', 'chrome_ordering', 'chrome_submitting', 'order_submitted', 'submitted', 'reporting_complete'
        } or row.get('state') in {'ordered', 'dispatched', 'delivered', 'inventory'}:
            raise ValueError('Reset fulfilment and remove all bundle components from the Chrome queue before editing the bundle.')
        if float(row.get('inventory_allocated_quantity') or 0) > 0 or float(row.get('inventory_sent_quantity') or 0) > 0:
            raise ValueError('Release allocated inventory for all bundle components before editing the bundle.')
        if row.get('replacement_run_id'):
            raise ValueError('Edit the original order line before creating a resend replacement.')


def original_values(root):
    share = float(root.get('bundle_price_share') or 1)
    quantity = float(root.get('original_quantity') or root.get('quantity') or 1)
    values = {field: float(root[field]) / share if root.get(field) is not None else None for field in TOTAL_FIELDS}
    values['store_unit_price'] = float(root.get('store_unit_price') or 0)
    if root.get('bundle_price_share') is not None:
        values['store_unit_price'] *= float(root.get('quantity') or 1) / share / quantity
    values['quantity'] = quantity
    return values


def assign_components(conn, root, existing, components, note, now):
    ensure_editable(existing)
    original = original_values(root)
    share = 1 / len(components)
    kept = {root['id']}
    updated_ids = []
    for index, component in enumerate(components):
        quantity = component['quantity']
        values = {field: original[field] * share if original[field] is not None else None for field in TOTAL_FIELDS}
        values.update(
            quantity=quantity, replacement_quantity=quantity, original_quantity=original['quantity'],
            store_unit_price=original['store_unit_price'] * original['quantity'] * share / quantity,
            asin=component['asin'], replacement_asin=component['asin'],
            original_asin=root.get('original_asin') or root['asin'],
            original_product_name=root.get('original_product_name') or root.get('product_name'),
            product_name=component['title'], replacement_product_name=component['title'],
            replacement_note=note, replacement_assigned_at=now,
            bundle_parent_line_id=root['id'] if index else None,
            bundle_component_count=len(components), bundle_price_share=share,
            state='pulled', amazon_status=None, amazon_group_key=None, missing_asin=None, last_error=None,
            amazon_unit_price=None, amazon_total_price=None, chrome_profit_total=None,
            cost_approved_at=None, cost_review_loss=None, updated_at=now,
        )
        prior = root if index == 0 else next((row for row in existing[1:] if row['id'] not in kept and row.get('asin') == component['asin']), None)
        if prior:
            conn.execute('UPDATE order_lines SET ' + ', '.join(f'{key}=?' for key in values) + ' WHERE id=?', [*values.values(), prior['id']])
            row_id = prior['id']
        else:
            clone = dict(root)
            clone.pop('id')
            clone.update(values)
            clone.update(odoo_line_id=-int(uuid.uuid4().int % 1_900_000_000 + 1), created_at=now, order_engine='chrome')
            # The clone keeps the real Odoo source ids, never its synthetic import key.
            clone['source_odoo_line_ids'] = root.get('source_odoo_line_ids') or str(root['odoo_line_id'])
            row_id = conn.execute('INSERT INTO order_lines (' + ', '.join(clone) + ') VALUES (' + ','.join('?' for _ in clone) + ') RETURNING id', list(clone.values())).fetchone()['id']
        kept.add(row_id)
        updated_ids.append(row_id)
    removed = [row['id'] for row in existing[1:] if row['id'] not in kept]
    for row_id in removed:
        conn.execute('DELETE FROM order_lines WHERE id=?', (row_id,))
    return updated_ids, removed


def reset_components(conn, root, existing, now):
    ensure_editable(existing)
    original = original_values(root)
    original.update(asin=root.get('original_asin') or root['asin'], product_name=root.get('original_product_name') or root['product_name'],
                    replacement_asin=None, replacement_product_name=None, replacement_quantity=None,
                    original_asin=None, original_product_name=None, original_quantity=None,
                    bundle_parent_line_id=None, bundle_component_count=1, bundle_price_share=None,
                    replacement_note=None, replacement_assigned_at=None, state='pulled', amazon_status=None,
                    amazon_group_key=None, missing_asin=None, last_error=None, updated_at=now)
    conn.execute('UPDATE order_lines SET ' + ', '.join(f'{key}=?' for key in original) + ' WHERE id=?', [*original.values(), root['id']])
    removed = [row['id'] for row in existing[1:]]
    for row_id in removed:
        conn.execute('DELETE FROM order_lines WHERE id=?', (row_id,))
    return removed


def expand_bundle_selection(conn, store_id, ids):
    if not ids:
        return ids
    rows = conn.execute('SELECT id, bundle_parent_line_id FROM order_lines WHERE store_id=? AND id IN (' + ','.join('?' for _ in ids) + ')', [store_id, *ids]).fetchall()
    roots = sorted({int(row.get('bundle_parent_line_id') or row['id']) for row in rows})
    if not roots:
        return ids
    placeholders = ','.join('?' for _ in roots)
    siblings = conn.execute(f'SELECT id FROM order_lines WHERE store_id=? AND (id IN ({placeholders}) OR bundle_parent_line_id IN ({placeholders}))', [store_id, *roots, *roots]).fetchall()
    return sorted(set(ids) | {int(row['id']) for row in siblings})


def sync_imported_bundle_finances(conn, store_id):
    roots = conn.execute('SELECT * FROM order_lines WHERE store_id=? AND bundle_parent_line_id IS NULL AND bundle_component_count>1', (store_id,)).fetchall()
    for root in roots:
        children = conn.execute('SELECT * FROM order_lines WHERE bundle_parent_line_id=?', (root['id'],)).fetchall()
        for child in children:
            ratio = float(child['bundle_price_share']) / float(root['bundle_price_share'])
            values = {field: float(root[field]) * ratio if root.get(field) is not None else None for field in TOTAL_FIELDS}
            values['store_unit_price'] = float(root.get('store_unit_price') or 0) * float(root['quantity']) * ratio / float(child['quantity'])
            for field in ('store_currency', 'store_currency_rate_to_usd', 'odoo_order_state', 'odoo_invoice_status', 'odoo_status_label'):
                values[field] = root.get(field)
            conn.execute('UPDATE order_lines SET ' + ', '.join(f'{key}=?' for key in values) + ' WHERE id=?', [*values.values(), child['id']])
