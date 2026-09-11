"""Exact purchase and shipment identity checks for replacement components."""
import re
from app.services import amazon_bundles


def strict_line(row):
    return bool(amazon_bundles.line_components(row) or row.get('replacement_asin') or int(row.get('bundle_component_count') or 1) > 1)


def asin(value):
    value = str(value or '').strip().upper()
    return value if re.fullmatch(r'[A-Z0-9]{10}', value) else ''


def package_asins(package):
    if package.get('asin_evidence_source') == 'order_inferred' or package.get('item_proof_ignored'):
        return set()
    return {value for value in [asin(v) for v in package.get('asins') or []] if value}


def tracking_error(rows, packages):
    if not any(strict_line(row) for row in rows):
        return ''
    expected = {value for row in rows for value in (amazon_bundles.line_components(row) or {asin(row.get('replacement_asin') or row.get('asin')): 1})}
    observed_packages = [package_asins(package) for package in packages]
    # Amazon orders can contain additional, separately shipped products. Those
    # exact but unrelated packages must not veto a proven replacement shipment.
    # Unreadable/inferred evidence still fails closed, as does a wholly unrelated
    # update. Assignment remains per line, never per Amazon order.
    if any(not observed for observed in observed_packages) or not any(
        observed.intersection(expected) for observed in observed_packages
    ):
        return 'Replacement tracking needs an exact shipment ASIN match. Re-scan the Amazon order details and its package; existing order IDs and tracking were preserved.'
    return ''


def completion_error(rows, mappings):
    protected = [row for row in rows if int(row.get('bundle_component_count') or 1) > 1]
    if not protected:
        return ''
    by_id = {int(row['id']): row for row in rows}
    assignments = {}
    for mapping in mappings or []:
        order_id = str(mapping.get('amazon_order_id') or '').strip()
        product = asin(mapping.get('asin'))
        observed = {asin(v) for v in mapping.get('observed_asins') or []}
        if not re.fullmatch(r'\d{3}-\d{7}-\d{7}', order_id) or not product or product not in observed:
            return 'Replacement order mapping lacks exact ASIN evidence. Update/reload the fulfilment extension and retry reporting; do not place the order again.'
        for raw_id in mapping.get('line_ids') or []:
            try:
                line_id = int(raw_id)
            except (ValueError, TypeError):
                return 'Invalid replacement line ID in Amazon order mapping.'
            row = by_id.get(line_id)
            if not row or asin(row.get('asin')) != product:
                return 'Replacement ASIN does not match the mapped app line; Amazon order IDs were preserved.'
            if line_id in assignments and assignments[line_id] != order_id:
                return 'One replacement ASIN was found under multiple Amazon order IDs. Manual quantity reconciliation is required.'
            assignments[line_id] = order_id
    if any(int(row['id']) not in assignments for row in protected):
        return 'Every replacement component needs its own exact ASIN-to-Amazon-order mapping.'
    return ''


def delivered_quantity_complete(row, packages):
    if amazon_bundles.line_components(row):
        return amazon_bundles.delivered_complete(row, packages)
    expected = asin(row.get('replacement_asin') or row.get('asin'))
    quantity = float(row.get('quantity') or 1)
    total = 0.0
    for package in packages:
        products = [p for p in package.get('products') or [] if asin(p.get('asin')) == expected]
        for product in products:
            if not product.get('quantity_verified'):
                continue
            try:
                total += max(0, float(product.get('quantity') or 1))
            except (ValueError, TypeError):
                pass
    # A single exact unit needs no inferred quantity arithmetic.
    return total >= quantity or (quantity == 1 and bool(packages))
