"""Validated contact fields for Relay's separate country and national-phone controls."""
import re
import phonenumbers as pn


def contact_details(partner, country, email):
    if str(partner.get('email') or '').strip().lower() != email.strip().lower():
        raise ValueError('Odoo billing email changed; review the invoice')
    code = str(country.get('code') or '').upper()
    if code not in pn.SUPPORTED_REGIONS:
        raise ValueError('Billing country is missing or unsupported')
    raw = str(partner.get('phone') or partner.get('mobile') or '').strip()
    if not raw or not re.fullmatch(r'[+\d\s().-]+', raw):
        raise ValueError('Billing phone is missing or ambiguous; correct it in Odoo')
    if raw.startswith('00') and not raw.startswith('0011'):
        raw = '+' + raw[2:]
    try:
        number = pn.parse(raw, code)
    except pn.NumberParseException:
        raise ValueError('Billing phone cannot be parsed') from None
    region = pn.region_code_for_number(number)
    if not pn.is_valid_number(number) or number.extension or not region:
        raise ValueError('Billing phone is invalid; correct it in Odoo')
    return {'email': email, 'country_code': code,
            'country_name': country['name'], 'phone_country_code': region,
            'dial_code': str(number.country_code),
            'national_number': pn.national_significant_number(number),
            'phone_e164': pn.format_number(number, pn.PhoneNumberFormat.E164)}
