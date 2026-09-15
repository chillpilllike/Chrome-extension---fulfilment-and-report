"""Provider-neutral SMS outbox. No fallback or retry after an uncertain send."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

import requests
from fastapi import APIRouter, HTTPException
from app.services.alternative_workflow import Runtime

TEST_NUMBER = '+19296526393'
PROVIDERS = {'odoo', 'msg91', 'twilio'}
KINDS = {'new_order_welcome', 'expected_dispatch', 'item_unavailable', 'delivery_confirmation',
         'package_lost', 'package_movement', 'tracking', 'warehouse_dispatch_delay', 'alternative_payment'}
SCHEMA = '''CREATE TABLE IF NOT EXISTS after_order_sms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER NOT NULL UNIQUE REFERENCES after_order_messages(id),
 case_id INTEGER NOT NULL REFERENCES after_order_cases(id), provider TEXT NOT NULL,
 recipient TEXT NOT NULL, test_mode INTEGER NOT NULL, body TEXT NOT NULL,
 snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'awaiting_approval',
 attempts INTEGER NOT NULL DEFAULT 0, provider_id TEXT, last_error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);'''


def number(value):
    value = re.sub(r'[\s().-]', '', str(value or ''))
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise ValueError('SMS requires a phone number with an explicit +country code.')
    return value


def recipient(value, test):
    return TEST_NUMBER if test else number(value)


def validate_target(row, current_test):
    if row['test_mode']:
        if row['recipient'] != TEST_NUMBER:
            raise ValueError('Test SMS can only be sent to ' + TEST_NUMBER)
    elif current_test:
        raise ValueError('Customer SMS is blocked while test mode is enabled.')
    number(row['recipient'])


class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))


def order_link(markup, domain):
    parser = Links(); parser.feed(markup or '')
    for link in parser.links:
        p = urlsplit(link)
        if p.scheme == 'https' and p.hostname == domain and not p.username and not p.password and p.path.startswith('/my/orders/'):
            return link
    return 'https://' + domain + '/my/orders'


def render(kind, order, brand, link):
    summaries = {
        'new_order_welcome':'Thank you for your order. We will begin processing it soon.',
        'expected_dispatch':'There is an update to your expected dispatch date.',
        'item_unavailable':'An item in your order needs your choice.',
        'delivery_confirmation':'The carrier marked your parcel delivered. Please confirm receipt.',
        'package_lost':'Your parcel needs attention. Please review your options.',
        'warehouse_dispatch_delay':'Dispatch is delayed. Our team is working on it. No action needed.',
        'alternative_payment':'Please review the payment details for your selected alternative.',
    }
    return f'{brand}: Order {order}. {summaries.get(kind, "Your parcel has a tracking update.")} {link}'


def digest(row):
    return hashlib.sha256(json.dumps({k:row[k] for k in ('provider','recipient','test_mode','body','snapshot_json','attempts')},sort_keys=True).encode()).hexdigest()


def validate_config(payload):
    if type(payload.get('enabled')) is not bool or payload.get('provider') not in PROVIDERS or not isinstance(payload.get('mappings'), dict):
        raise ValueError('Provide enabled, provider and website mappings.')
    raw = json.dumps(payload['mappings'])
    if len(raw) > 50000 or re.search(r'auth.?key|token|password|secret', raw, re.I):
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
        uid = hashlib.sha256(('care-sms:' + str(row['id'])).encode()).hexdigest()[:32]
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

    def phone(self, case):
        r = self.r; client = r.OdooClient(r.get_store(case['store_id']))
        order = client.read('sale.order',[case['odoo_order_id']],['partner_id','state','website_id'])[0]
        if order['state'] not in {'sale','done'} or not order.get('website_id') or order['website_id'][0] != case.get('website_id'):
            raise ValueError('A current confirmed order on the matching website is required.')
        fields = client.existing_fields('res.partner',['mobile','phone','phone_blacklisted'])
        if 'phone_blacklisted' not in fields:
            raise ValueError('Odoo phone suppression support must be verified before customer SMS.')
        partner = client.read('res.partner',[order['partner_id'][0]],fields)[0]
        if partner.get('phone_blacklisted'):
            raise ValueError('Customer phone is blocked from SMS in Odoo.')
        return number(partner.get('mobile') or partner.get('phone'))

    def prepare(self, email_id):
        r = self.r; config = self.config()
        if not config['enabled']:
            return None
        with r.db() as conn:
            email = conn.execute('SELECT * FROM after_order_messages WHERE id=?',(email_id,)).fetchone()
            existing = conn.execute('SELECT * FROM after_order_sms WHERE email_id=?',(email_id,)).fetchone()
        if existing:
            return dict(existing)
        if not email or email['template_kind'] not in KINDS:
            return None  # Marketing/review invitations and dispatch confirmations are excluded.
        email = dict(email); case = r.after_order_case_by_id(email['case_id'])
        r.require_after_order_case_in_scope(case)
        case = r.hydrate_after_order_recipient_and_domain(case,strict=not email['test_mode'])
        if not email['test_mode'] and email.get('request_fingerprint') and email['request_fingerprint'] != r.request_fingerprint(case):
            raise ValueError('Source email is stale. Prepare a current email before SMS.')
        domain = case.get('sender_domain') or ''
        if not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}',domain):
            raise ValueError('Verified website domain is required for SMS.')
        kind = email['template_kind']; provider = config['provider']
        site = config['mappings'].get(f"{case['store_id']}:{case['website_id']}",{})
        mapping = dict(site.get(provider) or {})
        if not email['test_mode'] and not site.get('transactional_sms_enabled'):
            raise ValueError('Enable transactional SMS for this website after reviewing customer consent and destination requirements.')
        to = recipient(None if email['test_mode'] else self.phone(case),bool(email['test_mode']))
        link = order_link(email['html_preview'],domain)
        brand = (case.get('context') or {}).get('website_name') or case.get('store_name') or domain
        values = {'order':case['odoo_order_name'],'brand':brand,'url':link}
        body = render(kind,values['order'],brand,link)
        if provider == 'msg91':
            mapping = {**mapping,**(mapping.get('templates',{}).get(kind) or {})}
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
                    'email_created_at':email.get('created_at')}
        with r.db() as conn:
            conn.execute('''INSERT INTO after_order_sms(email_id,case_id,provider,recipient,test_mode,body,snapshot_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(email_id) DO NOTHING''',
                (email_id,case['id'],provider,to,email['test_mode'],body,json.dumps(snapshot),r.utc_now(),r.utc_now()))
            row = dict(conn.execute('SELECT * FROM after_order_sms WHERE email_id=?',(email_id,)).fetchone())
            r.record_after_order_event(conn,case['id'],'sms_prepared',details={'sms_id':row['id'],'provider':provider,'test_mode':bool(email['test_mode'])})
        return row

    def companion(self, email_id):
        try:
            row = self.prepare(email_id)
            if row and row['status'] == 'awaiting_approval':
                with self.r.db() as conn:
                    email = dict(conn.execute('SELECT * FROM after_order_messages WHERE id=?',(email_id,)).fetchone())
                if row['test_mode'] or (email['template_kind']=='new_order_welcome' and email['status'] in {'sent','delivered'}):
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

    def send(self, sms_id, approval='', automatic=False):
        r = self.r
        with r.db() as conn:
            raw = conn.execute('SELECT * FROM after_order_sms WHERE id=? FOR UPDATE',(sms_id,)).fetchone()
            if not raw:
                raise ValueError('SMS not found.')
            row = dict(raw); snapshot = json.loads(row['snapshot_json'])
            if not self.config()['enabled']:
                raise ValueError('SMS sending is disabled.')
            validate_target(row,r.after_order_email_test_mode())
            if row['status'] not in {'awaiting_approval','failed'} or row['attempts'] >= 3:
                raise ValueError('SMS already attempted or uncertain. Check provider logs; do not resend.')
            if automatic:
                email = conn.execute('SELECT template_kind,status FROM after_order_messages WHERE id=?',(row['email_id'],)).fetchone()
                if row['attempts'] or row['status'] != 'awaiting_approval' or (not row['test_mode'] and not (email['template_kind']=='new_order_welcome' and email['status'] in {'sent','delivered'})):
                    raise ValueError('This SMS needs individual approval.')
            elif digest(row) != approval:
                raise ValueError('Review the current SMS preview before approving.')
            case = r.after_order_case_by_id(row['case_id']); r.require_after_order_case_in_scope(case)
            if not row['test_mode']:
                case = r.hydrate_after_order_recipient_and_domain(case, strict=True)
                created = datetime.fromisoformat((snapshot.get('email_created_at') or row['created_at']).replace('Z', '+00:00'))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < datetime.now(timezone.utc) - timedelta(days=3):
                    raise ValueError('SMS preview is stale. Prepare a current notification instead.')
                site = self.config()['mappings'].get(f"{case['store_id']}:{case['website_id']}", {})
                if not site.get('transactional_sms_enabled'):
                    raise ValueError('Customer SMS has been disabled for this website.')
                welcome = snapshot['kind'] == 'new_order_welcome'
                if (self.phone(case) != row['recipient'] or r.request_fingerprint(case) != snapshot['request_fingerprint']
                        or case.get('sender_domain') != snapshot['domain'] or case.get('confirmed_at') or case.get('current_decision')
                        or (case.get('status') == 'resolved' and not welcome) or not r.after_order_tracking_is_current(case)):
                    raise ValueError('Order/recipient changed; SMS approval is blocked.')
                if welcome:
                    if case.get('case_type') != 'new_order_welcome':
                        raise ValueError('Welcome SMS requires a newly confirmed order.')
                    r.welcome_emails.validate(case)
                if snapshot['kind'] == 'warehouse_dispatch_delay':
                    r.warehouse_dispatch_delay.validate(case)
                if snapshot['kind'] in {'tracking', 'package_movement'} and r.after_order_tracking_updates_opted_out(case, case.get('customer_email') or ''):
                    raise ValueError('Customer opted out of tracking updates.')
                email = conn.execute('SELECT * FROM after_order_messages WHERE id=?', (row['email_id'],)).fetchone()
                payload = json.loads(dict(email).get('payload_json') or '{}')
                if payload.get('_care_reminder_parent'):
                    r.care_reminders.validate(int(payload['_care_reminder_parent']), int(payload['_care_reminder_number']), case)
                if snapshot['kind'] == 'item_unavailable':
                    review = r.after_order_unavailable_review(case, for_send=True)
                    if review['blocked'] or not review['approved'] or not r.alternative_workflow.ready(case):
                        raise ValueError('Sourcing review is incomplete or expired.')
                if snapshot['kind'] == 'delivery_confirmation':
                    r.delivery_checkin_case(case, enforce_delay=True)
            conn.execute("UPDATE after_order_sms SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?",(r.utc_now(),sms_id))
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
            raise ValueError('MSG91 delivery receipts are not connected yet. Check the request ID in MSG91 logs; acceptance does not confirm delivery.')
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
            conn.execute('UPDATE after_order_sms SET status=?,updated_at=? WHERE id=?', (status,r.utc_now(),sms_id))
            if row['status'] != status:
                r.record_after_order_event(conn,row['case_id'],'sms_delivery_status',details={'sms_id':sms_id,'status':status})
        return {'ok':True,'status':status}

    def router(self):
        router = APIRouter(prefix='/api/after-order/sms')

        @router.get('/settings')
        def settings():
            return {**self.config(),'test_number':TEST_NUMBER,'test_mode':self.r.after_order_email_test_mode(),
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
            if not raw:
                return {'row':None}
            row = dict(raw); row['approval_digest']=digest(row); row.pop('snapshot_json')
            return {'row':row}

        @router.post('/email/{email_id}/prepare')
        def prepare(email_id:int):
            try:
                self.prepare(email_id)
                self.companion(email_id)
                return preview(email_id)
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
        return router
