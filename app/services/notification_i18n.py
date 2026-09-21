"""Offline notification catalogs shared by all Odoo websites.

Never translate customer data through an external service. Catalogs contain only
static copy; order identifiers, links, amounts and names remain interpolation data.
"""
import json
import re
import math
import string
from collections import Counter
from functools import lru_cache
from pathlib import Path
from string import Formatter

CATALOGS = Path(__file__).with_name('notification_locales')
RTL = {'ar', 'he', 'fa', 'ur', 'dv'}


def normalize_language(value):
    value = str(value or '').strip().replace('-', '_')
    if not re.fullmatch(r'[a-zA-Z]{2,3}(?:_[a-zA-Z0-9]{2,3})?(?:@(?:Cyrl|latin))?', value, re.I):
        return ''
    if '@' in value:
        return {'sr@cyrl':'sr@Cyrl', 'sr@latin':'sr@latin'}.get(value.lower(), '')
    parts = value.split('_', 1)
    return parts[0].lower() + ('_' + parts[1].upper() if len(parts) == 2 else '')


def resolve_language(order_language=None, customer_language=None, website_language=None):
    for source, value in [('order', order_language), ('customer', customer_language), ('website', website_language)]:
        language = normalize_language(value)
        if language:
            return {'requested_language': language, 'language_source': source}
    return {'requested_language': 'en_US', 'language_source': 'default'}


@lru_cache(maxsize=128)
def catalog(language):
    language = normalize_language(language)
    for candidate in (language, language.split('_')[0]):
        path = CATALOGS / (candidate + '.json')
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


def valid_translation(source, value):
    if not isinstance(value, str) or not value.strip() or '<' in value or '>' in value or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
        return False
    try:
        # Compare the full fields, not only their names: a conversion/format
        # specifier must never be introduced by a translation.
        fields = lambda text: Counter((name, spec, conv) for _,name,spec,conv in Formatter().parse(text) if name is not None)
        return fields(source) == fields(value)
    except ValueError:
        return False


class Translator:
    def __init__(self, language):
        self.requested = normalize_language(language) or 'en_US'
        data = catalog(self.requested)
        self.messages = data.get('messages', {}) if isinstance(data.get('messages', {}),dict) else {}
        source = catalog('en_US').get('messages', {})
        complete = bool(source) and data.get('complete') and not data.get('delivery_blocked') and all(
            valid_translation(key, self.messages.get(key))
            for key in source)
        self.language = self.requested if self.requested.startswith('en') or complete else 'en_US'
        self.version = data.get('version', 'english-v1') if self.language != 'en_US' else 'english-v1'
        self.fallback = '' if self.language == self.requested or self.requested.startswith('en') else 'Translation not yet complete; English fallback.'
        if self.fallback and data.get('delivery_blocked'):
            self.fallback = 'Translation requires native-language review; English fallback.'
        self.method = data.get('translation_method', 'maintained') if not self.fallback else 'english_fallback'
        self.human_reviewed = data.get('human_reviewed', False)

    def __call__(self, text, **values):
        translated = self.messages.get(text, text) if not self.language.startswith('en') else text
        return translated.format(**values) if values else translated

    @property
    def html_language(self):
        return {'sr@Cyrl':'sr-Cyrl','sr@latin':'sr-Latn'}.get(self.language,self.language.replace('_', '-'))

    @property
    def direction(self):
        return 'rtl' if self.language.split('_')[0] in RTL else 'ltr'

    def metadata(self):
        return {'requested_language': self.requested, 'sent_language': self.language,
                'fallback_reason': self.fallback, 'catalog_version': self.version,
                'translation_method': self.method, 'human_reviewed': self.human_reviewed}


def for_case(case):
    return Translator((case.get('context') or {}).get('requested_language'))


def language_headers(case):
    t = for_case(case)
    return {'X-Notification-Language':t.language, 'X-Requested-Language':t.requested}


def odoo_language(client, order_id):
    """Read current customer language for payment adapters without a care case."""
    def read(model, ids, fields):
        return client.execute(model, 'read', [ids], {'fields': fields})
    try:
        order = read('sale.order',[order_id],['partner_id','website_id'])[0]
        partner = read('res.partner',[order['partner_id'][0]],['lang'])[0]
        website_language = None
        if not partner.get('lang') and order.get('website_id'):
            website = read('website',[order['website_id'][0]],['default_lang_id'])[0]
            if website.get('default_lang_id'):
                website_language = read('res.lang',[website['default_lang_id'][0]],['code'])[0].get('code')
        return resolve_language(customer_language=partner.get('lang'),website_language=website_language)
    except Exception:
        return {'requested_language':'en_US','language_source':'lookup_unavailable'}


def language_inventory():
    manifest = json.loads((CATALOGS / 'manifest.json').read_text(encoding='utf-8'))
    return [{**value, 'code':code, **Translator(code).metadata()} for code,value in manifest.items()]


def sms_translation(language, kind, brand, order, link):
    language = normalize_language(language) or 'en_US'
    kind = {'tracking':'package_movement', 'price_difference':'alternative_payment'}.get(kind, kind)
    data = catalog(language)
    templates = data.get('sms', {}) if isinstance(data.get('sms', {}),dict) else {}
    template = templates.get(kind)
    try:
        fields = [(name,spec,conv) for _,name,spec,conv in Formatter().parse(template or '') if name is not None]
        valid_sms = bool(template) and set(fields) == {('brand','',None),('order','',None),('url','',None)}
        valid_sms = valid_sms and fields.count(('order','',None)) == 1 and fields.count(('url','',None)) == 1
    except ValueError:
        valid_sms = False
    if not data.get('complete') or data.get('delivery_blocked') or not valid_sms or not valid_translation(template,template):
        return None, {'requested_language':language,'sent_language':'en_US',
                      'fallback_reason':'' if language.startswith('en') else 'SMS translation requires review or is unavailable; English fallback.'}
    return template.format(brand=brand,order=order,url=link), {
        'requested_language':language,'sent_language':language,'fallback_reason':''}


def sms_segments(body, force_unicode=False):
    basic=set(string.ascii_letters+string.digits+'@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !"#¤%&\'()*+,-./:;<=>?¡ÄÖÑÜ§¿äöñüà')
    extended=set('^{}\\[~]|€\f')
    unicode=force_unicode or any(ch not in basic|extended for ch in body)
    units=len(body.encode('utf-16-le'))//2 if unicode else sum(2 if ch in extended else 1 for ch in body)
    single,multi=(70,67) if unicode else (160,153)
    return {'encoding':'Unicode' if unicode else 'GSM-7','parts':1 if units<=single else math.ceil(units/multi),
            'note':'Estimate only; provider billing and destination rates apply.'}
