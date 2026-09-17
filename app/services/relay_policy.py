"""Fail-closed payment identity and receipt rules. No network or database access."""
import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr
from html import unescape
from urllib.parse import urlsplit


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).split()).casefold()


def cents(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Invalid payment amount')
        return int(amount * 100)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid payment amount') from None


def payment_key(value):
    try:
        u = urlsplit(str(value or ''))
        if u.scheme != 'https' or u.netloc != 'relay.cash' or u.query or u.fragment:
            return ''
        m = re.fullmatch(r'/pay/([A-Za-z0-9_-]+)(?:/receipt)?/?', u.path)
        return m.group(1) if m else ''
    except ValueError:
        return ''


def match_capture(snapshot, capture):
    """Exact email identifies a customer; names/phones may differ across orders."""
    for key in ('invoice_number', 'order_number', 'customer_email'):
        expected = normalized(snapshot.get(key))
        actual = normalized(capture.get(key))
        if not expected or not actual or expected != actual:
            raise ValueError('Relay/Odoo mismatch: ' + key)
    if not normalized(capture.get('customer_name')):
        raise ValueError('Missing Relay customer name')
    if capture.get('currency') != 'USD' or type(capture.get('amount_cents')) is not int or snapshot['amount_cents'] != capture['amount_cents']:
        raise ValueError('Relay/Odoo currency or amount mismatch')
    if not re.fullmatch(r'[A-Za-z0-9_-]{5,100}', str(capture.get('relay_invoice_id', ''))):
        raise ValueError('Missing stable Relay invoice ID')
    if snapshot.get('state') not in ('pending',):
        raise ValueError('Order is not awaiting payment')
    if snapshot.get('initiated_at'):
        raise ValueError('Payment already initiated')
    return True


def authenticate_forwarder(message, settings):
    expected = settings['receiving_address'].lower()
    targets = message.get('received_for') or message.get('to') or []
    if expected not in {parseaddr(v)[1].lower() for v in targets}:
        raise ValueError('Different receiving address')
    sender = parseaddr(message.get('from', ''))[1].lower()
    allowed = {v.strip().lower() for v in settings['forwarders'].splitlines() if v.strip()}
    if sender not in allowed:
        raise ValueError('Forwarder is not allowed')
    headers = {k.lower(): v for k, v in (message.get('headers') or {}).items()}
    auth = headers.get('authentication-results', '')
    if isinstance(auth, list):
        auth = auth[0] if auth else ''
    auth = str(auth).lower()
    trusted = {v.strip().lower() for v in settings.get('authserv_ids', '').splitlines() if v.strip()}
    # Never trust an arbitrary sender-provided authserv-id or authentication quote.
    if not trusted or auth.split(';', 1)[0].strip() not in trusted:
        raise ValueError('Receiving gateway authentication is not configured or verified')
    domain = sender.rsplit('@', 1)[-1]
    if not re.search(r'\bdmarc=pass\b[^;]*\bheader\.from=' + re.escape(domain) + r'(?:\s|;|$)', auth):
        raise ValueError('Forwarder DMARC alignment did not pass')


def parse_receipt(message, settings, resolve_tracking=None):
    """Authenticate the actual forwarder, never a From: line quoted in the body.

    Receiving API access authenticates transport from Resend, not email authorship.
    Require the receiver's first Authentication-Results field with aligned DMARC.
    Unknown templates/authentication fail to manual review.
    """
    authenticate_forwarder(message, settings)
    markup = message.get('html') or ''
    if markup.startswith('data:text/html'):
        from urllib.parse import unquote
        import base64
        header, data = markup.split(',', 1)
        markup = base64.b64decode(data).decode('utf-8', 'replace') if ';base64' in header else unquote(data)
    raw = unescape((message.get('text') or '') + '\n' + markup)
    if len(raw) > 2000000:
        raise ValueError('Receipt is too large')
    plain = ' '.join(re.sub('<[^>]+>', ' ', raw).split())
    subject = str(message.get('subject', '')).casefold()
    if not ('payment is on the way for your invoice' in subject and 'initiated a payment for your invoice' in plain.casefold() and 'being processed' in plain.casefold()):
        raise ValueError('Unrecognized Relay receipt template; review required')
    # The legal footer contains $3,000,000 and $250,000 insurance limits.
    # Parse each MIME alternative independently and only its transaction section.
    sections=[]
    for alternative in (message.get('text') or '', markup):
        clean=unescape(re.sub('<[^>]+>', ' ', alternative))
        clean=' '.join(clean.split())
        start=re.search(r'initiated a payment for your invoice',clean,re.I)
        if not start:
            continue
        section=clean[start.start():]
        footer=re.search(r'This payment request was created|Have a question or need help|Relay Financial Technologies',section,re.I)
        if not footer:
            raise ValueError('Receipt transaction boundary is missing')
        sections.append(section[:footer.start()])
    transaction=' '.join(sections)
    amounts = {cents(v.replace(',', '')) for v in re.findall(r'(?:USD\s*|\$)\s*([0-9]+(?:,[0-9]{3})*\.[0-9]{2})(?!\d)', transaction)}
    # Links may be attributes in HTML, so extract URLs before removing markup.
    keys = {payment_key(v.rstrip('.,)>')) for v in re.findall(r'https://relay\.cash/[^\s<>"\']+', raw)} - {''}
    if not keys and resolve_tracking:
        # Outlook forwarding can replace the direct text URL with Relay's click
        # tracker. Only resolve the receipt CTA, after receiver authentication.
        from html.parser import HTMLParser
        class ReceiptLinks(HTMLParser):
            def __init__(self):
                super().__init__(); self.href = ''; self.label = []; self.links = set()
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    self.href = dict(attrs).get('href', ''); self.label = []
            def handle_data(self, data):
                if self.href: self.label.append(data)
            def handle_endtag(self, tag):
                if tag == 'a':
                    if normalized(' '.join(self.label)) == 'view details in relay':
                        self.links.add(self.href)
                    self.href = ''; self.label = []
        parser = ReceiptLinks(); parser.feed(markup)
        if len(parser.links) == 1:
            key = payment_key(resolve_tracking(parser.links.pop()))
            if key: keys.add(key)
    if len(amounts) != 1 or len(keys) != 1:
        raise ValueError('Receipt must contain exactly one payment link and amount')
    return {'amount_cents': amounts.pop(), 'payment_key': keys.pop(), 'kind': 'initiated',
            'digest': hashlib.sha256((subject + '\n' + plain).encode()).hexdigest()}
