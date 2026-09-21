"""Provider-neutral SMS outbox. No fallback or retry after an uncertain send."""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests
import phonenumbers as pn
from fastapi import APIRouter, HTTPException, Request
from app.services.alternative_workflow import Runtime
from app.services.notification_i18n import normalize_language, sms_translation, sms_segments, catalog

TEST_NUMBER = '+19296526393'
PROVIDERS = {'odoo', 'msg91', 'twilio'}
KINDS = {'expected_dispatch', 'item_unavailable', 'no_alternatives', 'delivery_confirmation',
         'package_movement', 'tracking', 'warehouse_dispatch_delay', 'alternative_payment',
         'price_difference', 'refund_request_received', 'refund_completed',
         'trustpilot_review', 'delivery_issue_received', 'new_order_welcome'}
AUTOMATIC_KINDS = {'trustpilot_review', 'delivery_issue_received', 'new_order_welcome'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS after_order_sms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER NOT NULL UNIQUE REFERENCES after_order_messages(id),
 case_id INTEGER NOT NULL REFERENCES after_order_cases(id), provider TEXT NOT NULL,
 recipient TEXT NOT NULL, test_mode INTEGER NOT NULL, body TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'awaiting_approval',
 attempts INTEGER NOT NULL DEFAULT 0, provider_id TEXT, last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_order_sms_first_movement (
 parcel_key TEXT PRIMARY KEY, email_id INTEGER NOT NULL REFERENCES after_order_messages(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_order_sms_welcome (
 order_key TEXT PRIMARY KEY, sms_id INTEGER NOT NULL UNIQUE REFERENCES after_order_sms(id),
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_order_sms_localizations (
 provider TEXT NOT NULL, sender TEXT NOT NULL, language TEXT NOT NULL,
 template_kind TEXT NOT NULL, mapping_json TEXT NOT NULL,
 PRIMARY KEY(provider,sender,language,template_kind)
);
CREATE TABLE IF NOT EXISTS after_order_sms_attempts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, sms_id INTEGER NOT NULL REFERENCES after_order_sms(id),
 attempt_number INTEGER NOT NULL, status TEXT NOT NULL, provider_id TEXT, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(sms_id,attempt_number)
);
CREATE TABLE IF NOT EXISTS after_order_sms_receipts (
 digest TEXT PRIMARY KEY, provider_id TEXT NOT NULL, recipient TEXT NOT NULL,
 status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
);'''


def preparation_reason(email, enabled=True):
    if not email:
        return 'Source email not found.'
    if not eligible(email):
        return 'This email type is excluded from the selected SMS notification policy (including reminders).'
    if not enabled:
        return 'SMS sending is disabled in Settings.'
    return ''


def eligible(email):
    payload = json.loads(email.get('payload_json') or '{}')
    return email.get('template_kind') in KINDS and not payload.get('_care_reminder_parent')


def movement_key(case, test_mode):
    code = str(case.get('tracking_code') or '').strip().upper()
    if not code:
        raise ValueError('A verified parcel tracking code is required for the one-time movement SMS.')
    return json.dumps([case['store_id'], case['website_id'], code, bool(test_mode)])


def number(value):
    value = re.sub(r'[\s().-]', '', str(value or ''))
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('SMS requires a phone number with an explicit +country code.')
    return value


def recipient(value, test):
    return TEST_NUMBER if test else number(value)


def customer_number(value, country_code=None):
    """Normalize only with the phone owner's verified Odoo country, never the store domain."""
    raw = str(value or '').strip()
    region = str(country_code or '').upper()
    if not raw or not re.fullmatch(r'[+\d\s().-]+', raw):
        raise ValueError('Customer phone is missing or ambiguous. Correct it in Odoo.')
    if not raw.startswith('+') and region not in pn.SUPPORTED_REGIONS:
        raise ValueError('National customer phone requires a verified Odoo contact country.')
    try:
        parsed = pn.parse(raw, region if region in pn.SUPPORTED_REGIONS else None)
    except pn.NumberParseException:
        raise ValueError('Customer phone cannot be parsed. Correct it in Odoo.') from None
    if parsed.extension or not pn.is_valid_number(parsed):
        raise ValueError('Customer phone is invalid. Correct it in Odoo.')
    return number(pn.format_number(parsed, pn.PhoneNumberFormat.E164))


def validate_target(row, current_test):
    if row['test_mode']:
        if row['recipient'] != TEST_NUMBER:
            raise ValueError('Test SMS can only be sent to ' + TEST_NUMBER)
    elif current_test:
        raise ValueError('Customer SMS is blocked while test mode is enabled.')
    number(row['recipient'])


def order_link(domain, order_id):
    # Use verified case identity, never the first link in an email (which may
    # be an action URL, another order, or an admin/fulfilment-app URL).
    if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', str(domain or '')) or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) for label in domain.split('.')):
        raise ValueError('A verified website domain is required for the SMS order link.')
    if isinstance(order_id, bool) or not re.fullmatch(r'[1-9][0-9]*', str(order_id or '')):
        raise ValueError('A valid Odoo order ID is required for the SMS order link.')
    # Standard portal authentication is retained. Never bypass ownership checks
    # or put expiring action credentials into a generic SMS link.
    return f'https://{domain}/my/orders/{order_id}'


def render(kind, order, brand, link):
    summaries = {
        'new_order_welcome':"Thank you for your order! We will begin processing it soon. View your order:",
        'trustpilot_review':'How was your experience? Share an honest review:',
        'delivery_issue_received':"Thank you for letting us know your order hasn't arrived. Our team will investigate the delivery and contact you shortly.",
        'expected_dispatch':'There is an update to your expected dispatch date.',
        'item_unavailable':'An item in your order needs your choice.',
        'no_alternatives':'An item is unavailable and no alternatives were found. Please choose how to proceed.',
        'delivery_confirmation':'The carrier marked your parcel delivered. Please confirm receipt.',
        'warehouse_dispatch_delay':'Dispatch is delayed. Our team is working on it. No action needed.',
        'alternative_payment':'Please review the payment details for your selected alternative.',
        'price_difference':'Please review the payment details for your selected alternative.',
        'refund_request_received':'Your refund request is under review. Please allow 24-48 hours for a response.',
        'refund_completed':'Your refund has been processed. Your payment provider determines when the credit appears.',
    }
    return f'{brand}: Order {order}. {summaries.get(kind, "Your parcel has a tracking update.")} {link}'


def digest(row):
    return hashlib.sha256(json.dumps({k:row[k] for k in ('provider','recipient','test_mode','body','snapshot_json','attempts','status')},sort_keys=True).encode()).hexdigest()


def verify_followup_template(mapping):
    """Never send a new follow-up through an unapproved or changed MSG91 template."""
    response = requests.post('https://control.msg91.com/api/v5/sms/getTemplateVersions',
        headers={'authkey':os.getenv('MSG91_AUTH_KEY', '')},
        json={'template_id':mapping.get('template_id')}, timeout=20)
    response.raise_for_status()
    active = [x for x in response.json().get('data', []) if str(x.get('active_status')) == '1']
    if len(active) != 1 or str(active[0].get('status')) != '1':
        raise ValueError('This SMS template is awaiting MSG91 approval; no SMS was sent.')
    if active[0].get('sender_id') != mapping.get('sender') or active[0].get('template_data') != mapping.get('text'):
        raise ValueError('MSG91 template content or sender changed. Review the website mapping before sending.')


def validate_config(payload):
    if type(payload.get('enabled')) is not bool or payload.get('provider') not in PROVIDERS or not isinstance(payload.get('mappings'), dict):
        raise ValueError('Provide enabled, provider and website mappings.')
    raw = json.dumps(payload['mappings'])
    if len(raw.encode('utf-8')) > 500000:
        raise ValueError('Website SMS mappings exceed the 500 KB configuration limit.')
    def contains_credential_field(value):
        # Brand names (e.g. SecretGreen) are values, not credential fields.
        if isinstance(value, dict):
            return any(re.search(r'auth.?key|token|password|secret', str(key), re.I)
                       or contains_credential_field(child) for key, child in value.items())
        if isinstance(value, list):
            return any(contains_credential_field(child) for child in value)
        return False
    if contains_credential_field(payload['mappings']):
        raise ValueError('Keep credentials in runtime secrets, not website mappings.')
    for key, site in payload['mappings'].items():
        if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', key) or not isinstance(site, dict):
            raise ValueError('Each website mapping must be an object keyed by store_id:website_id.')
        if type(site.get('transactional_sms_enabled', False)) is not bool:
            raise ValueError('transactional_sms_enabled must be true or false.')
        for provider in PROVIDERS:
            mapping = site.get(provider, {})
            if not isinstance(mapping, dict):
                raise ValueError('Provider mappings must be objects.')
            if provider == 'msg91':
                templates = mapping.get('templates', {})
                if not isinstance(templates, dict) or any(k not in KINDS or not isinstance(v, dict) for k, v in templates.items()):
                    raise ValueError('MSG91 templates must map supported notification kinds to objects.')
                for template in templates.values():
                    if any(not isinstance(v, str) for v in template.values()):
                        raise ValueError('Template IDs and template text must be strings.')
            for field in ('sender', 'messaging_service_sid'):
                if field in mapping and not isinstance(mapping[field], str):
                    raise ValueError('Sender and messaging service must be strings.')
    return raw


class Rejected(Exception):
    pass


def deliver(row, mapping, client=None):
    """Called only after durable send reservation. Never retries network requests."""
    target = number(row['recipient']); provider = row['provider']
    if provider == 'odoo':
        # Creating the outgoing record hands it to Odoo's official SMS queue.
        # Do not call send() too: the Odoo cron could already be processing it.
        uid = hashlib.sha256(('care-sms:' + str(row['id']) + ':' + str(row.get('attempts',0) + 1)).encode()).hexdigest()[:32]
        ids = client.execute('sms.sms','search',[[('uuid','=',uid)]])
        ident = ids[0] if ids else client.execute('sms.sms','create',[{'number':target,'body':row['body'],'uuid':uid}])
        return str(ident), 'queued'
    if provider == 'twilio':
        account = os.getenv('TWILIO_ACCOUNT_SID',''); token = os.getenv('TWILIO_AUTH_TOKEN','')
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}',account) or not token:
            raise Rejected('Configure TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN runtime secrets.')
        data = {'To':target,'Body':row['body']}
        sender = mapping.get('messaging_service_sid') or mapping.get('sender')
        if not sender:
            raise Rejected('Configure this website Twilio sender or messaging service.')
        data['MessagingServiceSid' if mapping.get('messaging_service_sid') else 'From'] = sender
        response = requests.post(f'https://api.twilio.com/2010-04-01/Accounts/{account}/Messages.json',auth=(account,token),data=data,timeout=25)
    elif provider == 'msg91':
        key = os.getenv('MSG91_AUTH_KEY','')
        if not key or not mapping.get('template_id') or not mapping.get('sender'):
            raise Rejected('Configure MSG91_AUTH_KEY and this website approved template/sender mapping.')
        if target.startswith('+91') and not mapping.get('dlt_template_id'):
            raise Rejected('An approved India DLT template mapping is required for this destination.')
        response = requests.post('https://control.msg91.com/api/v5/flow/',headers={'authkey':key},
            json={'template_id':mapping['template_id'],'sender':mapping['sender'],
                  'recipients':[{'mobiles':target[1:],**mapping.get('variables',{})}]},timeout=25)
    else:
        raise Rejected('Unsupported SMS provider.')
    if 400 <= response.status_code < 500:
        raise Rejected(f'{provider} rejected SMS (HTTP {response.status_code}). Check provider logs/configuration.')
    response.raise_for_status()
    data = response.json()
    if provider == 'msg91' and data.get('type') != 'success':
        raise Rejected('MSG91 rejected SMS. Check template, DLT, balance and provider logs.')
    ident = data.get('sid') if provider == 'twilio' else data.get('message')
    if not isinstance(ident,str) or not ident:
        raise RuntimeError('Provider acceptance uncertain; reconcile before any retry.')
    return ident, 'accepted'


class SMS:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def config(self):
        settings = self.r.get_service_settings()
        return {'enabled':settings.get('after_order_sms_enabled') == 'true',
                'provider':settings.get('after_order_sms_provider') or 'odoo',
                'mappings':json.loads(settings.get('after_order_sms_mappings') or '{}')}

    def pending_welcomes(self):
        """Recover unattempted welcomes after template/configuration setup, never old orders."""
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_sms_started_at')
        if not started or not self.config()['enabled'] or r.after_order_email_test_mode():
            return
        since = max(str(started), (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())
        with r.db() as conn:
            rows = conn.execute('''SELECT m.id FROM after_order_messages m
                LEFT JOIN after_order_sms s ON s.email_id=m.id
                WHERE m.template_kind='new_order_welcome' AND m.test_mode=0 AND m.created_at>=?
                AND m.status NOT IN ('cancelled','superseded')
                AND (s.id IS NULL OR (s.status='awaiting_approval' AND s.attempts=0))
                ORDER BY m.id''', (since,)).fetchall()
        for row in rows:
            self.companion(row['id'])

    def receipt(self, payload):
        """Called only after webhook authentication. Reports never initiate sends."""
        ident=str(payload.get('requestId') or '')
        target=number('+'+str(payload.get('telNum') or '').lstrip('+'))
        code=str(payload.get('status'))
        status={'0':'sent','1':'delivered','2':'provider_failed','9':'blocked',
                '16':'rejected','25':'rejected','17':'blocked','20':'blocked'}.get(code)
        if not re.fullmatch(r'[A-Za-z0-9_-]{12,100}',ident) or not status:
            raise ValueError('Unsupported SMS delivery report.')
        details={k:str(payload.get(k) or '')[:500] for k in ('requestId','telNum','status','deliveryTime','failureReason','credit','smsLength')}
        key=hashlib.sha256(json.dumps(details,sort_keys=True).encode()).hexdigest()
        with self.r.db() as conn:
            conn.execute('''INSERT INTO after_order_sms_receipts(digest,provider_id,recipient,status,details_json,created_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(digest) DO NOTHING''',(key,ident,target,status,json.dumps(details),self.r.utc_now()))
            rows=conn.execute("SELECT * FROM after_order_sms WHERE provider='msg91' AND provider_id=? AND recipient=? FOR UPDATE",(ident,target)).fetchall()
            for raw in rows:
                row=dict(raw)
                # Late queued/sent receipts cannot regress a final outcome or a newer attempt.
                if row['status'] in {'sending','delivered','provider_failed','rejected','blocked'}:
                    continue
                conn.execute('UPDATE after_order_sms SET status=?,last_error=?,updated_at=? WHERE id=?',
                    (status,details['failureReason'] or None,self.r.utc_now(),row['id']))
                conn.execute('UPDATE after_order_sms_attempts SET status=?,error=?,updated_at=? WHERE sms_id=? AND provider_id=?',
                    (status,details['failureReason'] or None,self.r.utc_now(),row['id'],ident))
                if row['status']!=status:
                    self.r.record_after_order_event(conn,row['case_id'],'sms_delivery_status',details={'sms_id':row['id'],'status':status,'provider_id':ident})
        return {'ok':True}

    def webhook_router(self):
        router=APIRouter()
        @router.post('/api/public/after-order-webhooks/msg91')
        async def receive(request:Request):
            secret=os.getenv('MSG91_WEBHOOK_SECRET','')
            if len(secret)<32 or not hmac.compare_digest(request.headers.get('X-MSG91-Webhook-Secret',''),secret):
                raise HTTPException(401,'Invalid webhook authentication')
            body=await request.body()
            if len(body)>32768:
                raise HTTPException(413,'Delivery report too large')
            try:
                payload=json.loads(body)
                if not isinstance(payload,dict):
                    raise ValueError('Expected one delivery report')
                return self.receipt(payload)
            except (ValueError,TypeError) as exc:
                raise HTTPException(400,'Invalid delivery report') from exc
        return router

    def phone(self, case):
        r = self.r; client = r.OdooClient(r.get_store(case['store_id']))
        order = client.read('sale.order',[case['odoo_order_id']],['partner_id','state','website_id'])[0]
        if order['state'] not in {'sale','done'} or not order.get('website_id') or order['website_id'][0] != case.get('website_id'):
            raise ValueError('A current confirmed order on the matching website is required.')
        fields = client.existing_fields('res.partner',['mobile','phone','phone_blacklisted','country_id'])
        if 'phone_blacklisted' not in fields:
            raise ValueError('Odoo phone suppression support must be verified before customer SMS.')
        partner = client.read('res.partner',[order['partner_id'][0]],fields)[0]
        if partner.get('phone_blacklisted'):
            raise ValueError('Customer phone is blocked from SMS in Odoo.')
        country = partner.get('country_id')
        code = None
        if country:
            countries = client.read('res.country',[country[0]],['code'])
            code = countries[0].get('code') if countries else None
        return customer_number(partner.get('mobile') or partner.get('phone'), code)

    def prepare(self, email_id):
        r = self.r; config = self.config()
        if not config['enabled']:
            return None
        with r.db() as conn:
            email = conn.execute('SELECT * FROM after_order_messages WHERE id=?',(email_id,)).fetchone()
            existing = conn.execute('SELECT * FROM after_order_sms WHERE email_id=?',(email_id,)).fetchone()
        if not email or not eligible(dict(email)):
            return None  # Reminders, lost-package and marketing SMS are excluded.
        if existing:
            return dict(existing)
        email = dict(email); case = r.after_order_case_by_id(email['case_id'])
        r.require_after_order_case_in_scope(case)
        case = r.hydrate_after_order_recipient_and_domain(case,strict=not email['test_mode'])
        if not email['test_mode'] and email.get('request_fingerprint') and email['request_fingerprint'] != r.request_fingerprint(case):
            raise ValueError('Source email is stale. Prepare a current email before SMS.')
        domain = case.get('sender_domain') or ''
        if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}',domain):
            raise ValueError('Verified website domain is required for SMS.')
        kind = email['template_kind']; provider = config['provider']
        if kind in {'tracking','package_movement'} and (case.get('context') or {}).get('risk_state') != 'in_transit':
            return None
        if kind == 'item_unavailable':
            affected = {int(item['line_id']) for item in case.get('affected_items', [])}
            if affected and affected.issubset(r.alternative_workflow.unavailable_without_alternatives(case)):
                kind = 'no_alternatives'
        site = config['mappings'].get(f"{case['store_id']}:{case['website_id']}",{})
        mapping = dict(site.get(provider) or {})
        if not email['test_mode'] and not site.get('transactional_sms_enabled'):
            raise ValueError('Enable transactional SMS for this website after reviewing customer consent and destination requirements.')
        to = recipient(None if email['test_mode'] else self.phone(case),bool(email['test_mode']))
        link = order_link(domain,case.get('odoo_order_id'))
        if kind == 'trustpilot_review':
            link = r.trustpilot_review_url(domain)
        brand = (case.get('context') or {}).get('website_name') or case.get('store_name') or domain
        values = {'order':case['odoo_order_name'],'brand':brand,'url':link}
        body = render(kind,values['order'],brand,link)
        requested_language = normalize_language((case.get('context') or {}).get('requested_language')) or 'en_US'
        translated_body, language = sms_translation(requested_language,kind,brand,values['order'],link)
        if translated_body and provider != 'msg91':
            body = translated_body
        if provider == 'msg91':
            mapping = {**mapping,**(mapping.get('templates',{}).get(kind) or {})}
            if not requested_language.startswith('en'):
                language = {'requested_language':requested_language,'sent_language':'en_US',
                            'fallback_reason':'No approved localized MSG91 template; English fallback.'}
                with r.db() as conn:
                    localized = conn.execute('''SELECT mapping_json FROM after_order_sms_localizations
                        WHERE provider='msg91' AND sender=? AND language IN (?,?) AND template_kind=?
                        ORDER BY CASE WHEN language=? THEN 0 ELSE 1 END''',
                        (mapping.get('sender',''),requested_language,requested_language.split('_')[0],kind,requested_language)).fetchone()
                if localized and not catalog(requested_language).get('delivery_blocked'):
                    candidate = json.loads(localized['mapping_json'])
                    try:
                        if candidate.get('sender') != mapping.get('sender'):
                            raise ValueError('Localized template sender does not match the website.')
                        verify_followup_template(candidate)
                    except (ValueError,requests.RequestException):
                        pass  # Explicit user policy: use approved English while translation awaits approval.
                    else:
                        mapping = candidate
                        language = {'requested_language':requested_language,'sent_language':requested_language,'fallback_reason':''}
            if kind in AUTOMATIC_KINDS or not requested_language.startswith('en'):
                if language['sent_language'].startswith('en') and not mapping.get('templates', {}).get(kind):
                    raise ValueError('Configure a dedicated approved MSG91 template for this notification.')
                verify_followup_template(mapping)
            template = mapping.get('text') or ''
            names = set(re.findall(r'##(\w+)##',template))
            if not template or not names.issubset(values):
                raise ValueError('Configure exact MSG91 template text; supported variables: ##order##, ##brand##, ##url##.')
            mapping['variables'] = {name:values[name] for name in names}
            body = template
            for name in names:
                body = body.replace('##'+name+'##',str(values[name]))
        if len(body) > 1000:
            raise ValueError('SMS exceeds the 1,000-character safety limit. Review the template/link.')
        snapshot = {'mapping':mapping,'request_fingerprint':r.request_fingerprint(case),'domain':domain,'kind':kind,
                    'email_created_at':email.get('created_at'),'language':language}
        with r.db() as conn:
            if kind in {'tracking', 'package_movement'}:
                if not self.reserve_movement(conn, case, email):
                    return None
            conn.execute('''INSERT INTO after_order_sms(email_id,case_id,provider,recipient,test_mode,body,snapshot_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(email_id) DO NOTHING''',
                (email_id,case['id'],provider,to,email['test_mode'],body,json.dumps(snapshot),r.utc_now(),r.utc_now()))
            row = dict(conn.execute('SELECT * FROM after_order_sms WHERE email_id=?',(email_id,)).fetchone())
            r.record_after_order_event(conn,case['id'],'sms_prepared',details={'sms_id':row['id'],'provider':provider,'test_mode':bool(email['test_mode'])})
        return row

    def refresh_language(self, sms_id):
        """Reconcile pending/confirmed-failed MSG91 snapshots, never resend.

        A changed manual preview receives a new digest and needs fresh approval.
        Automatic notifications can use the approved fallback immediately.
        """
        r = self.r
        with r.db() as conn:
            raw = conn.execute('SELECT * FROM after_order_sms WHERE id=? FOR UPDATE', (sms_id,)).fetchone()
            if not raw:
                return
            row = dict(raw)
            if row['provider'] != 'msg91' or row['status'] not in {'awaiting_approval','failed','provider_failed','undelivered'} or row['attempts'] >= 3:
                return
            snapshot = json.loads(row['snapshot_json'])
            requested = snapshot.get('language', {}).get('requested_language', 'en_US')
            if requested.startswith('en'):
                return
            case = r.after_order_case_by_id(row['case_id'])
            config = self.config()
            base = dict(config['mappings'].get(f"{case['store_id']}:{case['website_id']}", {}).get('msg91') or {})
            kind = snapshot['kind']
            english = base.get('templates', {}).get(kind)
            language = {'requested_language':requested, 'sent_language':'en_US',
                        'fallback_reason':'No approved localized MSG91 template; English fallback.'}
            mapping = None
            data = catalog(requested)
            localized = conn.execute('''SELECT mapping_json FROM after_order_sms_localizations
                WHERE provider='msg91' AND sender=? AND language IN (?,?) AND template_kind=?
                ORDER BY CASE WHEN language=? THEN 0 ELSE 1 END''',
                (base.get('sender',''), requested, requested.split('_')[0], kind, requested)).fetchone()
            if localized and data.get('complete') and not data.get('delivery_blocked'):
                candidate = json.loads(localized['mapping_json'])
                try:
                    if candidate.get('sender') != base.get('sender'):
                        raise ValueError('Localized template sender does not match the website.')
                    verify_followup_template(candidate)
                except (ValueError, requests.RequestException):
                    pass
                else:
                    mapping = candidate
                    language = {'requested_language':requested, 'sent_language':requested, 'fallback_reason':''}
            if mapping is None:
                if not english:
                    raise ValueError('Configure a dedicated approved English fallback for this notification.')
                mapping = {**base, **english}
                verify_followup_template(mapping)  # Fail closed if English is not approved either.
            link = order_link(snapshot['domain'], case.get('odoo_order_id'))
            if kind == 'trustpilot_review':
                link = r.trustpilot_review_url(snapshot['domain'])
            values = {'order':case['odoo_order_name'], 'url':link,
                      'brand':(case.get('context') or {}).get('website_name') or case.get('store_name') or snapshot['domain']}
            template = mapping.get('text') or ''
            names = set(re.findall(r'##(\w+)##', template))
            if not template or not names.issubset(values):
                raise ValueError('Invalid MSG91 template variables; no SMS was sent.')
            mapping['variables'] = {name:values[name] for name in names}
            body = template
            for name in names:
                body = body.replace('##'+name+'##', str(values[name]))
            if len(body) > 1000:
                raise ValueError('SMS exceeds the 1,000-character safety limit.')
            if snapshot['mapping'] == mapping and snapshot.get('language') == language and row['body'] == body:
                return
            previous = snapshot.get('language', {}).get('sent_language')
            snapshot.update(mapping=mapping, language=language)
            conn.execute('UPDATE after_order_sms SET body=?,snapshot_json=?,updated_at=? WHERE id=?',
                         (body, json.dumps(snapshot), r.utc_now(), sms_id))
            r.record_after_order_event(conn,row['case_id'],'sms_language_reselected',details={
                'sms_id':sms_id,'previous_language':previous,**language})

    def companion(self, email_id):
        try:
            row = self.prepare(email_id)
            if row and row['status'] == 'awaiting_approval':
                if row['test_mode'] or json.loads(row['snapshot_json']).get('kind') in AUTOMATIC_KINDS:
                    self.send(row['id'],automatic=True)
        except Exception:
            # A companion must never change the outcome of the independent email.
            try:
                with self.r.db() as conn:
                    email = conn.execute('SELECT case_id FROM after_order_messages WHERE id=?',(email_id,)).fetchone()
                    if email:
                        self.r.record_after_order_event(conn,email['case_id'],'sms_needs_attention',details={'email_id':email_id,'reason':'SMS preparation blocked. Open the SMS preview to check configuration.'})
            except Exception:
                pass

    def reserve_movement(self, conn, case, email):
        # Unique reservation survives retries, provider changes and concurrent workers.
        key = movement_key(case, email['test_mode'])
        prior = conn.execute('''SELECT s.email_id,s.snapshot_json FROM after_order_sms s
            JOIN after_order_cases c ON c.id=s.case_id
            WHERE c.store_id=? AND c.website_id=? AND UPPER(TRIM(c.tracking_code))=?
              AND s.test_mode=? AND s.attempts>0 AND s.email_id<>?''',
            (case['store_id'],case['website_id'],str(case['tracking_code']).strip().upper(),email['test_mode'],email['id'])).fetchall()
        if any(json.loads(row['snapshot_json']).get('kind') in {'tracking','package_movement'} for row in prior):
            return False  # Includes pre-policy sends and uncertain attempts.
        conn.execute('''INSERT INTO after_order_sms_first_movement(parcel_key,email_id,created_at)
            VALUES(?,?,?) ON CONFLICT(parcel_key) DO NOTHING''', (key,email['id'],self.r.utc_now()))
        owner = conn.execute('SELECT email_id FROM after_order_sms_first_movement WHERE parcel_key=?', (key,)).fetchone()
        return owner['email_id'] == email['id']

    def send(self, sms_id, approval='', automatic=False, resend=False):
        r = self.r
        self.refresh_language(sms_id)
        with r.db() as conn:
            raw = conn.execute('SELECT * FROM after_order_sms WHERE id=? FOR UPDATE',(sms_id,)).fetchone()
            if not raw:
                raise ValueError('SMS not found.')
            row = dict(raw); snapshot = json.loads(row['snapshot_json'])
            email = conn.execute('SELECT * FROM after_order_messages WHERE id=?',(row['email_id'],)).fetchone()
            if not email or not eligible(dict(email)) or snapshot['kind'] not in KINDS:
                raise ValueError('This notification is excluded from SMS by the current cost-control policy.')
            if snapshot['kind'] != email['template_kind'] and not (email['template_kind'] == 'item_unavailable' and snapshot['kind'] == 'no_alternatives'):
                raise ValueError('Source notification type changed. Prepare a current SMS.')
            if not self.config()['enabled']:
                raise ValueError('SMS sending is disabled.')
            if catalog(snapshot.get('language',{}).get('sent_language')).get('delivery_blocked'):
                raise ValueError('This translation requires native-language review. Prepare an English fallback preview.')
            if row['provider'] == 'msg91' and (snapshot['kind'] in AUTOMATIC_KINDS or not snapshot.get('language',{}).get('requested_language','en_US').startswith('en')):
                verify_followup_template(snapshot['mapping'])
            validate_target(row,r.after_order_email_test_mode())
            allowed = {'delivered'} if resend else {'awaiting_approval','failed','provider_failed','undelivered'}
            if row['status'] not in allowed or row['attempts'] >= 3 or (resend and automatic):
                raise ValueError('SMS already attempted or uncertain. Check provider logs; do not resend.')
            if resend and snapshot['kind'] in {'tracking','package_movement','trustpilot_review','delivery_issue_received','new_order_welcome'}:
                raise ValueError('This is a once-only notification. Duplicate sends are blocked.')
            if automatic:
                if row['attempts'] or row['status'] != 'awaiting_approval' or (not row['test_mode'] and snapshot['kind'] not in AUTOMATIC_KINDS):
                    raise ValueError('This SMS needs individual approval.')
            elif digest(row) != approval:
                raise ValueError('Review the current SMS preview before approving.')
            case = r.after_order_case_by_id(row['case_id']); r.require_after_order_case_in_scope(case)
            if snapshot['kind'] == 'new_order_welcome':
                if email['status'] in {'cancelled','superseded'}:
                    raise ValueError('This welcome notification was cancelled or superseded.')
                if not row['test_mode']:
                    r.welcome_emails.validate(case)
                key = json.dumps([case['store_id'], case['odoo_order_id'], bool(row['test_mode'])])
                conn.execute('''INSERT INTO after_order_sms_welcome(order_key,sms_id,created_at)
                    VALUES(?,?,?) ON CONFLICT(order_key) DO NOTHING''', (key,sms_id,r.utc_now()))
                if conn.execute('SELECT sms_id FROM after_order_sms_welcome WHERE order_key=?',(key,)).fetchone()['sms_id'] != sms_id:
                    raise ValueError('A welcome SMS is already reserved for this order. Duplicate blocked.')
            expected_link = order_link(snapshot['domain'], case.get('odoo_order_id'))
            if snapshot['kind'] == 'trustpilot_review':
                expected_link = r.trustpilot_review_url(snapshot['domain'])
            urls = re.findall(r'https?://[^\s<>"\']+', row['body'])
            if expected_link not in urls or any(url != expected_link for url in urls):
                raise ValueError('SMS contains an outdated or incorrect order link. Prepare a new notification; do not resend this preview.')
            if snapshot['kind'] in {'tracking', 'package_movement'}:
                if (case.get('context') or {}).get('risk_state') != 'in_transit':
                    raise ValueError('A current in-transit event is required for movement SMS.')
                if not self.reserve_movement(conn, case, dict(email)):
                    raise ValueError('The first movement SMS is already reserved for this parcel. Later movement texts are blocked.')
            if not row['test_mode']:
                case = r.hydrate_after_order_recipient_and_domain(case, strict=True)
                if snapshot.get('language',{}).get('requested_language','en_US') != ((case.get('context') or {}).get('requested_language') or 'en_US'):
                    raise ValueError('Customer language changed. Prepare and review a new SMS.')
                created = datetime.fromisoformat((snapshot.get('email_created_at') or row['created_at']).replace('Z', '+00:00'))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < datetime.now(timezone.utc) - timedelta(days=3):
                    raise ValueError('SMS preview is stale. Prepare a current notification instead.')
                site = self.config()['mappings'].get(f"{case['store_id']}:{case['website_id']}", {})
                if not site.get('transactional_sms_enabled'):
                    raise ValueError('Customer SMS has been disabled for this website.')
                financial = snapshot['kind'] in {'price_difference','alternative_payment','refund_request_received','refund_completed'}
                followup = snapshot['kind'] in {'trustpilot_review', 'delivery_issue_received'}
                welcome = snapshot['kind'] == 'new_order_welcome'
                if (self.phone(case) != row['recipient'] or r.request_fingerprint(case) != snapshot['request_fingerprint']
                        or case.get('sender_domain') != snapshot['domain']
                        or (not financial and not followup and not welcome and (case.get('confirmed_at') or case.get('current_decision') or case.get('status') == 'resolved'))
                        or not r.after_order_tracking_is_current(case)):
                    raise ValueError('Order/recipient changed; SMS approval is blocked.')
                if financial:
                    # Financial source messages need their own verified event/amount adapter.
                    # Do not infer a payment or completed refund from a button click.
                    raise ValueError('Financial SMS is held until its verified payment/refund event adapter is connected.')
                if followup:
                    r.delivery_followups.reserve(conn, case, snapshot['kind'], 'sms', sms_id)
                if snapshot['kind'] == 'warehouse_dispatch_delay':
                    r.warehouse_dispatch_delay.validate(case)
                if snapshot['kind'] in {'tracking', 'package_movement'} and r.after_order_tracking_updates_opted_out(case, case.get('customer_email') or ''):
                    raise ValueError('Customer opted out of tracking updates.')
                email = conn.execute('SELECT * FROM after_order_messages WHERE id=?', (row['email_id'],)).fetchone()
                payload = json.loads(dict(email).get('payload_json') or '{}')
                if payload.get('_care_reminder_parent'):
                    r.care_reminders.validate(int(payload['_care_reminder_parent']), int(payload['_care_reminder_number']), case)
                if snapshot['kind'] in {'item_unavailable', 'no_alternatives'}:
                    review = r.after_order_unavailable_review(case, for_send=True)
                    if review['blocked'] or not review['approved'] or not r.alternative_workflow.ready(case):
                        raise ValueError('Sourcing review is incomplete or expired.')
                if snapshot['kind'] == 'delivery_confirmation':
                    r.delivery_checkin_case(case, enforce_delay=True)
            conn.execute("UPDATE after_order_sms SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?",(r.utc_now(),sms_id))
            # Legacy aggregate history remains in its case events; do not invent
            # precise attempt timestamps for sends that predate this table.
            conn.execute('''INSERT INTO after_order_sms_attempts(sms_id,attempt_number,status,created_at,updated_at)
                VALUES(?,?,'sending',?,?)''',(sms_id,row['attempts']+1,r.utc_now(),r.utc_now()))
            r.record_after_order_event(conn,row['case_id'],'sms_send_authorized',actor_type='system' if automatic else 'team',details={'sms_id':sms_id,'automatic':automatic})
        try:
            validate_target(row,r.after_order_email_test_mode())
            client = r.OdooClient(r.get_store(case['store_id'])) if row['provider']=='odoo' else None
            ident,status = deliver(row,snapshot['mapping'],client)
            error = None
        except Rejected as exc:
            ident,status,error = None,'failed',str(exc)
        except Exception:
            ident,status,error = None,'delivery_unknown','Acceptance uncertain. Inspect provider logs before any further send.'
        with r.db() as conn:
            conn.execute('UPDATE after_order_sms SET status=?,provider_id=?,last_error=?,updated_at=? WHERE id=?',(status,ident,error,r.utc_now(),sms_id))
            conn.execute('UPDATE after_order_sms_attempts SET status=?,provider_id=?,error=?,updated_at=? WHERE sms_id=? AND attempt_number=?',
                         (status,ident,error,r.utc_now(),sms_id,row['attempts']+1))
            r.record_after_order_event(conn,row['case_id'],'sms_send_result',details={'sms_id':sms_id,'status':status,'provider_id':ident,'error':error})
        return {'ok':status in {'accepted','queued'},'status':status,'error':error}

    def refresh(self, sms_id):
        r = self.r
        with r.db() as conn:
            raw = conn.execute('SELECT * FROM after_order_sms WHERE id=?', (sms_id,)).fetchone()
        if not raw:
            raise ValueError('SMS not found.')
        row = dict(raw)
        if not row['provider_id']:
            raise ValueError('No provider reference exists. Check uncertain attempts in the provider dashboard.')
        if row['provider'] == 'msg91':
            with r.db() as conn:
                receipt=conn.execute('SELECT details_json FROM after_order_sms_receipts WHERE provider_id=? AND recipient=? ORDER BY created_at DESC LIMIT 1',(row['provider_id'],row['recipient'])).fetchone()
            if receipt:
                self.receipt(json.loads(receipt['details_json']))
                with r.db() as conn:
                    current=conn.execute('SELECT status FROM after_order_sms WHERE id=?',(sms_id,)).fetchone()
                return {'ok':True,'status':current['status']}
            raise ValueError('No MSG91 delivery report received yet. Acceptance is not confirmed delivery. Check MSG91 logs; do not resend an uncertain message.')
        if row['provider'] == 'odoo':
            case = r.after_order_case_by_id(row['case_id'])
            client = r.OdooClient(r.get_store(case['store_id']))
            records = client.read('sms.sms', [int(row['provider_id'])], ['state'])
            if not records:
                raise ValueError('Odoo has removed its queue record. Delivery cannot be inferred; check Odoo SMS tracking.')
            status = {'outgoing':'queued','process':'processing','pending':'sent','sent':'delivered',
                      'error':'provider_failed','canceled':'cancelled'}.get(records[0]['state'])
        else:
            account = os.getenv('TWILIO_ACCOUNT_SID', '')
            token = os.getenv('TWILIO_AUTH_TOKEN', '')
            if not re.fullmatch(r'AC[0-9a-fA-F]{32}', account) or not token or not re.fullmatch(r'S[MM][0-9a-fA-F]{32}', row['provider_id']):
                raise ValueError('Twilio credentials or message reference are missing.')
            response = requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{account}/Messages/{row["provider_id"]}.json', auth=(account,token),timeout=25)
            response.raise_for_status()
            status = response.json().get('status')
            if status == 'failed':
                status = 'provider_failed'  # Never retry a dispatched message through this UI.
        if status not in {'queued','processing','sent','delivered','provider_failed','cancelled','canceled','undelivered','accepted','sending'}:
            raise ValueError('Provider returned an unrecognized delivery status.')
        with r.db() as conn:
            # A slow status query must never overwrite a newer retry attempt.
            conn.execute('UPDATE after_order_sms SET status=?,updated_at=? WHERE id=? AND attempts=? AND provider_id=? AND status<>?', (status,r.utc_now(),sms_id,row['attempts'],row['provider_id'],'sending'))
            conn.execute('UPDATE after_order_sms_attempts SET status=?,updated_at=? WHERE sms_id=? AND attempt_number=? AND provider_id=?',
                         (status,r.utc_now(),sms_id,row['attempts'],row['provider_id']))
            if row['status'] != status:
                r.record_after_order_event(conn,row['case_id'],'sms_delivery_status',details={'sms_id':sms_id,'status':status})
        return {'ok':True,'status':status}

    def router(self):
        router = APIRouter(prefix='/api/after-order/sms')

        @router.get('/log')
        def log(store_id:int=0, status:str='', q:str='', page:int=1):
            cutoff = (datetime.now(timezone.utc)-timedelta(days=30)).isoformat()
            where=['s.updated_at>=?']; args=[cutoff]
            if store_id:
                where.append('c.store_id=?'); args.append(store_id)
            if status:
                where.append('s.status=?'); args.append(status)
            if q:
                where.append('(LOWER(c.odoo_order_name) LIKE ? OR s.recipient LIKE ? OR LOWER(c.sender_domain) LIKE ?)')
                args.extend(['%'+q.strip().lower()[:100]+'%']*3)
            clause=' AND '.join(where)
            with self.r.db() as conn:
                total=conn.execute('SELECT COUNT(*) AS n FROM after_order_sms s JOIN after_order_cases c ON c.id=s.case_id WHERE '+clause,args).fetchone()['n']
                rows=conn.execute('''SELECT s.id,s.email_id,s.provider,s.recipient,s.test_mode,s.status,s.body,s.attempts,s.provider_id,s.snapshot_json,
                    s.last_error,s.created_at,s.updated_at,c.odoo_order_name,c.sender_domain
                    FROM after_order_sms s JOIN after_order_cases c ON c.id=s.case_id WHERE '''+clause+
                    ' ORDER BY s.updated_at DESC,s.id DESC LIMIT 30 OFFSET ?',[*args,(max(1,page)-1)*30]).fetchall()
            results = []
            for raw in rows:
                item = dict(raw)
                item['language'] = json.loads(item.pop('snapshot_json')).get('language', {})
                results.append(item)
            return {'rows':results,'total':total,'days':30}

        @router.get('/settings')
        def settings():
            return {**self.config(),'test_number':TEST_NUMBER,'test_mode':self.r.after_order_email_test_mode(),
                    'supported_kinds':sorted(KINDS),
                    'policy':'selected-events-first-movement-v1',
                    'language_policy':'live-approved-localized-else-approved-english-v2',
                    'credentials':{'odoo':True,'twilio':bool(os.getenv('TWILIO_ACCOUNT_SID') and os.getenv('TWILIO_AUTH_TOKEN')),'msg91':bool(os.getenv('MSG91_AUTH_KEY'))}}

        @router.post('/settings')
        def save(payload:dict):
            try:
                raw = validate_config(payload)
            except ValueError as exc:
                raise HTTPException(400,str(exc)) from exc
            self.r.set_service_settings({'after_order_sms_enabled':str(payload['enabled']).lower(),'after_order_sms_provider':payload['provider'],'after_order_sms_mappings':raw})
            return settings()

        @router.get('/email/{email_id}')
        def preview(email_id:int):
            with self.r.db() as conn:
                raw = conn.execute('SELECT * FROM after_order_sms WHERE email_id=?',(email_id,)).fetchone()
                email = conn.execute('SELECT * FROM after_order_messages WHERE id=?',(email_id,)).fetchone()
            if not raw:
                reason=preparation_reason(dict(email) if email else None,self.config()['enabled'])
                return {'row':None,'can_prepare':not bool(reason),'reason':reason or 'No SMS is saved yet. Prepare it to validate the phone, website, template and current order.'}
            try:
                self.refresh_language(raw['id'])
            except (ValueError, requests.RequestException):
                pass  # Keep the last preview visible; send still enforces live approval.
            with self.r.db() as conn:
                raw = conn.execute('SELECT * FROM after_order_sms WHERE id=?',(raw['id'],)).fetchone()
            row = dict(raw); row['approval_digest']=digest(row)
            kind=json.loads(row['snapshot_json']).get('kind')
            row['language']=json.loads(row['snapshot_json']).get('language',{})
            row['segment_estimate']=sms_segments(row['body'],json.loads(row['snapshot_json']).get('mapping',{}).get('sms_type')=='UNICODE')
            row['can_resend']=row['status']=='delivered' and row['attempts']<3 and kind not in {'tracking','package_movement','trustpilot_review','delivery_issue_received','new_order_welcome'}
            row.pop('snapshot_json')
            with self.r.db() as conn:
                attempts=conn.execute('SELECT * FROM after_order_sms_attempts WHERE sms_id=? ORDER BY attempt_number',(row['id'],)).fetchall()
            return {'row':row,'attempts':[dict(x) for x in attempts]}

        @router.post('/email/{email_id}/prepare')
        def prepare(email_id:int):
            try:
                self.prepare(email_id)
                self.companion(email_id)
                result=preview(email_id)
                if not result['row'] and result.get('can_prepare'):
                    result['reason']='No eligible in-transit event or approved SMS template was found. No SMS was sent.'
                return result
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc

        @router.post('/{sms_id}/approve-send')
        def send(sms_id:int,payload:dict):
            try:
                return self.send(sms_id,approval=str(payload.get('approval_digest') or ''))
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc

        @router.post('/{sms_id}/refresh')
        def refresh(sms_id:int):
            try:
                return self.refresh(sms_id)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc
            except Exception as exc:
                raise HTTPException(502,'Provider status check failed. No SMS was sent.') from exc

        @router.post('/{sms_id}/resend')
        def resend(sms_id:int,payload:dict):
            if payload.get('confirm_duplicate_charge') is not True:
                raise HTTPException(409,'Confirm that a second SMS may incur another charge.')
            try:
                return self.send(sms_id,approval=str(payload.get('approval_digest') or ''),resend=True)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from exc
        return router
