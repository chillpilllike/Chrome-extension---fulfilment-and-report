"""Explicit storefront communication pauses; independent of email provider."""
from email.utils import parseaddr
from urllib.parse import urlsplit

# FinchKart is a separate website, not an alias of GofinchKart.
PAUSED_DOMAINS = frozenset({'finchkart.com'})
REASON = 'Email sending is disabled for finchkart.com by the store administrator.'


def paused_domain(value):
    value = str(value or '').strip().lower()
    if '@' in value:
        value = parseaddr(value)[1].rsplit('@', 1)[-1]
    host = urlsplit(value if '://' in value else '//'+value).hostname or ''
    return any(host == domain or host.endswith('.'+domain) for domain in PAUSED_DOMAINS)


def require_email_enabled(case=None, message=None):
    case, message = case or {}, message or {}
    if ((str(case.get('store_id')), str(case.get('website_id'))) == ('1', '74')
            or any(paused_domain(value) for value in (
                case.get('sender_domain'), message.get('from'), message.get('sender')))):
        raise ValueError(REASON)
