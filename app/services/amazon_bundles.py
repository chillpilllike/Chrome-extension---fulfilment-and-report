"""Reviewed Amazon virtual bundles; purchase identity stays the parent ASIN.

Evidence comes from explicit bundle components or the verified multi-pack size selector.
Never learn this relationship from recipient names, titles, or a mismatched cart.
Quantities are per purchased bundle. Unknown bundles remain blocked for review.
"""
import math
import json
import re
from urllib.parse import urlsplit

CATALOG = {
    "B0BV67RXQH": {
        "parent_asin": "B0BV67RXQH", "observed_asin": "B0BV67RXQH",
        "source_url": "https://www.amazon.com/dp/B0BV67RXQH",
        "verified_at": "2026-09-09", "source": "amazon_multipack_size_selector",
        "selected_size": "24 Count (Pack of 2)", "single_size": "24 Count (Pack of 1)",
        "pack_count": 2, "single_asin": "B076F324JN",
        "components": {"B076F324JN": 2},
    },
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
    multipack = evidence.get('source') == 'amazon_multipack_size_selector'
    if multipack:
        selected = re.fullmatch(r'(.+?) \(Pack of ([0-9]+)\)', str(evidence.get('selected_size') or ''))
        single = re.fullmatch(r'(.+?) \(Pack of 1\)', str(evidence.get('single_size') or ''))
        if (not selected or not single or selected[1] != single[1]
                or not 2 <= int(selected[2]) <= 100
                or evidence.get('pack_count') != int(selected[2])
                or children != {evidence.get('single_asin'): int(selected[2])}):
            raise ValueError('Multi-pack evidence must identify the same size, its single unit, and exact pack count.')
    if (not re.fullmatch(r'[A-Z0-9]{10}', parent)
            or evidence.get('observed_asin') != parent
            or page.scheme != 'https' or page.hostname != 'www.amazon.com'
            or not re.search(r'/(?:dp|gp/product)/' + parent + r'(?:/|$)', page.path)
            or evidence.get('source') not in {'bundleComponentDetails_feature_div', 'amazon_multipack_size_selector'}
            or not isinstance(children, dict) or not (1 if multipack else 2) <= len(children) <= 20
            or parent in children
            or any(not re.fullmatch(r'[A-Z0-9]{10}', child) or not 0 < quantity(count) <= 100 or quantity(count) != int(quantity(count)) for child, count in children.items())):
        raise ValueError('Bundle components need exact evidence from the authorized Amazon product bundle section.')
    if components(parent) and components(parent) != children:
        raise ValueError('Amazon bundle composition changed; review it before changing existing purchase and tracking evidence.')
    return parent, {child: int(count) for child, count in children.items()}


def decode_evidence(value):
    """PostgreSQL JSON/JSONB is already decoded; legacy text still needs parsing."""
    evidence = value if isinstance(value, dict) else json.loads(value)
    if not isinstance(evidence, dict):
        raise ValueError("Bundle evidence must be a JSON object.")
    return dict(evidence)


def load_catalog(conn):
    for row in conn.execute("SELECT value FROM app_settings WHERE key LIKE 'amazon_bundle:%'").fetchall():
        evidence = decode_evidence(row['value'])
        parent, children = validate_evidence(evidence)
        CATALOG[parent] = {**evidence, 'components': children}


def components(asin):
    return dict(CATALOG.get(str(asin or '').strip().upper(), {}).get('components', {}))


def multipack_evidence(asin):
    entry = CATALOG.get(str(asin or "").upper(), {})
    return dict(entry) if entry.get("source") == "amazon_multipack_size_selector" else None


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
