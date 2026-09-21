"""SMS translations follow website-published languages, not installed catalogs."""
from app.services.notification_i18n import CATALOGS, normalize_language, catalog

TRANSLATED_SENDERS = frozenset({'nutricity','GofinchKart'})


def catalog_key(language):
    value=normalize_language(language)
    if not value:return ''
    for key in (value,value.split('_')[0]):
        if (CATALOGS/(key+'.json')).is_file():return key
    return ''


def allowed_catalogs(sender, languages):
    if sender not in TRANSLATED_SENDERS:return set()
    return {catalog_key(code) for code in languages if catalog_key(code) not in {'','en'}
            and catalog(code).get('complete') and not catalog(code).get('delivery_blocked')}


def website_language_allowed(sender, requested, languages):
    if (normalize_language(requested) or 'en_US').startswith('en'):return True
    return catalog_key(requested) in allowed_catalogs(sender,languages)
