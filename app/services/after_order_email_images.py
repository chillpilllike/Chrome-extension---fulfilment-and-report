"""Build public email image links without relying on a browser session."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode


def with_email_images(case, public_base_url):
    base = str(public_base_url or '').rstrip('/')
    parts = urlsplit(base)
    if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('A public HTTP(S) base URL is required for email images.')
    items = []
    for source in case.get('affected_items') or []:
        item = dict(source)
        image = str(item.get('thumbnail_url') or '').strip()
        asin = str(item.get('asin') or '').strip().upper()
        if not image and re.fullmatch(r'[A-Z0-9]{10}', asin):
            image = '/api/public/asin-image/' + asin
        if re.fullmatch(r'/api/public/asin-image/[A-Za-z0-9]{10}', image):
            image = base + image
        item['thumbnail_url'] = image
        items.append(item)
    return {**case, 'affected_items': items}


def with_email_recommendations(case, offers, action_url):
    """GET links only focus the order page; confirmation is a CSRF-protected POST."""
    parts = urlsplit(action_url)
    domain = str(case.get('sender_domain') or '')
    if not re.fullmatch(r'[a-zA-Z0-9.-]+', domain) or '.' not in domain:
        return case
    site = 'https://' + domain
    by_line = {int(o['line_id']): o for o in offers}
    items = []
    for source in case.get('affected_items') or []:
        item = dict(source)
        choices = []
        for p in by_line.get(int(item.get('line_id') or 0), {}).get('recommendations', []):
            template_id = int(p.get('product_tmpl_id') or 0)
            if not template_id:
                continue
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                     if k not in {'choice', 'line_id', 'selected_product_id'}]
            query += [('choice', 'offer_alternatives'), ('line_id', str(item['line_id'])), ('selected_product_id', str(template_id))]
            path = p.get('website_url') or '/shop/%s' % template_id
            if not path.startswith('/shop/') or path.startswith('//'):
                path = '/shop/%s' % template_id
            choices.append({'name': p['name'], 'thumbnail_url': site + '/web/image/product.template/%s/image_256' % template_id,
                'details_url': site + path, 'select_url': urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),'after-order-line-%s' % item['line_id']))})
        item['recommendations'] = choices
        items.append(item)
    return {**case, 'affected_items': items}
