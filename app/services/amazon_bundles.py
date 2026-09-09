"""Reviewed Amazon virtual bundles; purchase identity stays the parent ASIN.

Only Amazon's explicit bundle-component section is acceptable catalog evidence.
Never learn this relationship from recipient names, titles, or a mismatched cart.
Quantities are per purchased bundle. Unknown bundles remain blocked for review.
"""
import math
import json
import re
from urllib.parse import urlsplit

CATALOG = {
    "B0D51GVTKS": {
        "source_url": "https://www.amazon.com/dp/B0D51GVTKS",
        "verified_at": "2026-09-09",
        "source": "bundleComponentDetails_feature_div",
        "components": {"B00ENS39XK": 1, "B00F7OZJQE": 1},
    },
}


def validate_evidence(evidence):
    parent = str(evidence.get('parent_asin') or '').upper()
    page = urlsplit(str(evidence.get('source_url') or ''))
    children = evidence.get('components') or {}
    if (not re.fullmatch(r'[A-Z0-9]{10}', parent)
            or evidence.get('observed_asin') != parent
            or page.scheme != 'https' or page.hostname != 'www.amazon.com'
            or not re.search(r'/(?:dp|gp/product)/' + parent + r'(?:/|$)', page.path)
            or evidence.get('source') != 'bundleComponentDetails_feature_div'
            or not isinstance(children, dict) or not 2 <= len(children) <= 20
            or parent in children
            or any(not re.fullmatch(r'[A-Z0-9]{10}', child) or not 0 < quantity(count) <= 100 or quantity(count) != int(quantity(count)) for child, count in children.items())):
        raise ValueError('Bundle components need exact evidence from the authorized Amazon product bundle section.')
    if components(parent) and components(parent) != children:
        raise ValueError('Amazon bundle composition changed; review it before changing existing purchase and tracking evidence.')
    return parent, {child: int(count) for child, count in children.items()}


def load_catalog(conn):
    for row in conn.execute("SELECT value FROM app_settings WHERE key LIKE 'amazon_bundle:%'").fetchall():
        evidence = json.loads(row['value'])
        parent, children = validate_evidence(evidence)
        CATALOG[parent] = {**evidence, 'components': children}


def components(asin):
    return dict(CATALOG.get(str(asin or '').strip().upper(), {}).get('components', {}))


def line_components(row):
    return components(row.get('replacement_asin') or row.get('asin'))


def quantity(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else 0
    except (TypeError, ValueError):
        return 0


def expand_quantities(values):
    result = {}
    for parent, units in values.items():
        for child, per_bundle in (components(parent) or {parent: 1}).items():
            result[child] = result.get(child, 0) + quantity(units) * per_bundle
    return result


def history_matches(row, observed, quantities=None):
    expected = line_components(row)
    if not expected or set(expected) != set(observed):
        return False
    units = quantity(row.get('quantity'))
    return bool(units and quantities and all(
        abs(quantity(quantities.get(child)) - count * units) < 0.0001
        for child, count in expected.items()
    ))


def delivered_complete(row, packages):
    expected = {child: count * quantity(row.get('quantity')) for child, count in line_components(row).items()}
    totals = dict.fromkeys(expected, 0)
    seen = set()
    for package in packages:
        if package.get('status_only') or package.get('asin_evidence_source') == 'order_inferred' or package.get('item_proof_ignored'):
            continue
        key = package.get('tracking_id') or package.get('tracking_number') or package.get('trackingId') or package.get('shipment_id') or package.get('shipmentId') or package.get('tracking_url')
        if not key or key in seen:
            continue
        seen.add(key)
        observed = set(package.get('asins') or [])
        for child in expected.keys() & observed:
            products = [p for p in package.get('products') or [] if p.get('asin') == child and p.get('quantity_verified')]
            # One explicitly listed unit needs no inferred multi-unit arithmetic.
            totals[child] += sum(quantity(p.get('quantity')) for p in products) if products else (1 if expected[child] == 1 else 0)
    return bool(expected) and all(totals[child] >= count > 0 for child, count in expected.items())
