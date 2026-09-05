"""Build public email image links without relying on a browser session."""
import re
from urllib.parse import urlsplit


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
