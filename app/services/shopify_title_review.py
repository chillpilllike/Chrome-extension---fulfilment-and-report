"""Prepare immutable, order-scoped Shopify titles before any destination writes."""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from pathlib import Path

DEFAULT_KEYWORDS = Path(__file__).with_name('shopify_title_keywords.txt').read_text().strip()


def enabled(settings, key):
    return str(settings.get(key, 'false')).lower() in {'1', 'true', 'yes', 'on'}


def normalize(value):
    return unicodedata.normalize('NFKC', str(value or '')).strip()


def clean_title(title, brands=(), keywords=''):
    title = normalize(title)
    phrases = sorted({normalize(x) for x in [*brands, *keywords.splitlines()] if normalize(x)}, key=len, reverse=True)
    for phrase in phrases:
        # Token boundaries prevent short exclusions from deleting part of another word.
        parts = re.split(r'[\s-]+', phrase)
        pattern = r'(?<!\w)' + r'[\s-]+'.join(re.escape(p) for p in parts) + r'(?!\w)'
        title = re.sub(pattern, ' ', title, flags=re.I)
    title = re.sub(r'[^\w\s%+./-]', ' ', title)
    words = [word.strip('-/.+') for word in title.split()]
    return ' '.join(word for word in words if word) .split()[:6]


def prepared_title(title, brands=(), keywords=''):
    return ' '.join(clean_title(title, brands, keywords))


class ReviewRequired(Exception):
    pass


class FrozenOdoo:
    """Use the exact source read for preparation throughout this export attempt."""
    def __init__(self, client):
        self.client = client
        self.cache = {}
    def __getattr__(self, name):
        target = getattr(self.client, name)
        if name not in {'get_order_by_number', 'get_order_lines', 'get_product_product', 'get_product_template'}:
            return target
        def cached(*args):
            key = (name, json.dumps(args))
            if key not in self.cache:
                self.cache[key] = target(*args)
            return self.cache[key]
        return cached


def product_brands(odoo, product):
    ref = product.get('product_tmpl_id') or []
    if not ref or int(ref[0]) <= 0:
        return []
    templates = odoo.read('product.template', [int(ref[0])], ['attribute_line_ids'])
    ids = (templates[0].get('attribute_line_ids') or []) if templates else []
    if not ids:
        return []
    attrs = odoo.read('product.template.attribute.line', ids, ['attribute_id', 'value_ids'])
    value_ids = []
    for attr in attrs:
        attribute = attr.get('attribute_id') or []
        if len(attribute) > 1 and str(attribute[1]).strip().casefold() == 'brand':
            value_ids.extend(attr.get('value_ids') or [])
    return [normalize(v['name']) for v in odoo.read('product.attribute.value', value_ids, ['name'])] if value_ids else []


def line_key(line):
    ref = line.get('product_id') or [0]
    return f"{line['id']}:{ref[0]}"


def prepare(module, odoo, order_name, settings, rename_manager):
    order = odoo.get_order_by_number(order_name)
    if not order:
        raise ValueError(f'Odoo order not found: {order_name}')
    lines = odoo.get_order_lines([int(x) for x in order.get('order_line') or []])
    clean = enabled(settings, 'shopify_clean_titles_enabled')
    keywords = settings.get('shopify_title_remove_keywords', DEFAULT_KEYWORDS)
    items = []
    brands_cache = {}
    for line in lines:
        if line.get('display_type') or float(line.get('price_unit') or 0) < 0 or float(line.get('price_total') or 0) < 0:
            continue
        ref = line.get('product_id') or [0]
        pp = odoo.get_product_product(int(ref[0])) if ref[0] else {}
        pp = pp or {}
        sku = normalize(pp.get('default_code'))
        if module._should_ignore_odoo_line_item(sku, line.get('name')):
            continue
        title = normalize(pp.get('name') or line.get('name'))
        brand_product = pp
        if int(ref[0]) < 0:
            # Replacement virtual products retain the original Odoo internal reference.
            raw_client = getattr(odoo.client, 'client', odoo.client)
            source = raw_client.get_order_lines([int(line['id'])])
            if source and source[0].get('product_id'):
                brand_product = raw_client.get_product_product(int(source[0]['product_id'][0])) or {}
                sku = normalize(brand_product.get('default_code'))
        brand_key = str(brand_product.get('product_tmpl_id'))
        if brand_key not in brands_cache:
            brands_cache[brand_key] = product_brands(odoo, brand_product) if clean else []
        brands = brands_cache[brand_key]
        dest_title = prepared_title(title, brands, keywords) if clean else rename_manager.resolve_title(order_name=order_name, sku=sku, original_title=title)
        dest_sku = sku if clean else rename_manager.destination_sku(source_sku=module.maybe_decode_asin_sku(sku), source_title=title)
        items.append(dict(key=line_key(line), line_id=line['id'], product_id=ref[0], original_title=title,
                          prepared_title=dest_title, sku=dest_sku or '', brands=brands,
                          quantity=line.get('product_uom_qty'), price_unit=line.get('price_unit'),
                          price_total=line.get('price_total'), discount=line.get('discount')))
    source = dict(items=items, clean=clean, keywords=keywords if clean else '', order=order)
    fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True, default=str).encode()).hexdigest()
    return dict(items=items, fingerprint=fingerprint, clean=clean)


def validate_items(items, clean, keywords):
    if not items:
        raise ValueError('No exportable product lines were found.')
    titles_by_sku = {}
    for item in items:
        title = normalize(item.get('prepared_title'))
        if not title:
            raise ValueError('Every product needs a prepared title. Edit the empty titles before approval.')
        if clean:
            if not item.get('sku'):
                raise ValueError('A product is missing its Odoo Internal Reference. Add it in Odoo and refresh the review.')
            if title != prepared_title(title, item.get('brands') or [], keywords):
                raise ValueError('Prepared titles must have at most six words and exclude the brand and removal keywords.')
        sku = item.get('sku')
        if sku and sku in titles_by_sku and titles_by_sku[sku] != title:
            raise ValueError('Lines sharing an Internal Reference must use the same prepared title.')
        if sku:
            titles_by_sku[sku] = title
        if len(title) > 255:
            raise ValueError('Prepared titles must be at most 255 characters.')


class LineRenameManager:
    enabled = True
    require_reviewed_title = True
    def __init__(self, item):
        self.item = item
    def resolve_title(self, **kwargs):
        return self.item['prepared_title']
    def destination_sku(self, **kwargs):
        return self.item['sku'] or None


def install_prepared_export(module, snapshot):
    items = {item['key']: item for item in snapshot['items']}
    original = module.ensure_product_variant_for_line
    def ensure(odoo, *args, **kwargs):
        item = items.get(line_key(kwargs['line']))
        if item:
            kwargs['rename_manager'] = LineRenameManager(item)
        update = module.UPDATE_EXISTING_SKU_PRODUCTS
        try:
            if item:
                module.UPDATE_EXISTING_SKU_PRODUCTS = True
            result = original(odoo, *args, **kwargs)
            if item and result[1] is not None:
                result[1].update(title=item['prepared_title'], sku=item['sku'])
            return result
        finally:
            module.UPDATE_EXISTING_SKU_PRODUCTS = update
    module.ensure_product_variant_for_line = ensure
    original_build = module.build_order_payload
    def build(*args, **kwargs):
        payload = original_build(*args, **kwargs)
        exported = payload['order']['line_items']
        reviewed = [items[line_key(line)] for line in kwargs['order_lines'] if line_key(line) in items]
        if len(exported) != len(reviewed):
            raise ValueError('Exported lines changed after title review; refresh the review.')
        for line, item in zip(exported, reviewed):
            line.update(title=item['prepared_title'], sku=item['sku'])
        return payload
    module.build_order_payload = build
