"""Relay payment bridge: durable captures, matching, receipt polling and email outbox.

Injected host functions keep this independent from the application's large main module.
Only the server owns Odoo and Resend credentials. The extension credential can only
resolve and upload matching links; it cannot confirm orders or change settings.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlsplit, urljoin

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .relay_policy import match_capture, payment_key, parse_receipt

DEFAULTS = {'enabled': False, 'store_ids': [], 'receiving_address': 'relay-payments@taloofalut.resend.app',
            'forwarders': 'am-it@outlook.com', 'authserv_ids': '', 'public_base_url': '', 'test_mode': True}


def now():
    return datetime.now(timezone.utc).isoformat()


class RelayPayments:
    def __init__(self, *, db, get_store, client_factory, get_settings, set_settings, staff_check, email_test_mode):
        self.db, self.get_store, self.client_factory = db, get_store, client_factory
        self.get_settings, self.set_settings, self.staff_check = get_settings, set_settings, staff_check
        self.email_test_mode = email_test_mode
        self.lock = threading.Lock()

    def settings(self):
        raw = self.get_settings().get('relay_payment_settings') or '{}'
        return {**DEFAULTS, **json.loads(raw)}

    def ensure(self):
        with self.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS relay_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, store_id INTEGER NOT NULL,
                request_id TEXT NOT NULL UNIQUE, case_id INTEGER,
                snapshot_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'waiting_link',
                relay_invoice_id TEXT UNIQUE, payment_key TEXT UNIQUE, payment_link TEXT,
                capture_json TEXT, pay_token TEXT NOT NULL UNIQUE, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS relay_receipts (
                email_id TEXT PRIMARY KEY, digest TEXT UNIQUE, payment_id INTEGER,
                status TEXT NOT NULL, reason TEXT, evidence_json TEXT, created_at TEXT NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS relay_email_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id INTEGER NOT NULL,
                kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', message_id INTEGER,
                provider_id TEXT, payload_json TEXT, error TEXT, attempted_at TEXT,
                created_at TEXT NOT NULL, UNIQUE(payment_id,kind))''')

            c.execute('''CREATE TABLE IF NOT EXISTS relay_customer_aliases (
                payment_id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, source_hash TEXT NOT NULL,
                relay_invoice_id TEXT NOT NULL UNIQUE, customer_name TEXT NOT NULL, approved_at TEXT NOT NULL)''')

    def approve_customer_alias(self, payment_id, payload):
        name = str(payload.get('customer_name', '')).strip()
        invoice_id = str(payload.get('relay_invoice_id', '')).strip()
        import re
        if not name or len(name) > 200 or not re.fullmatch(r'[A-Za-z0-9_-]{5,100}', invoice_id):
            raise ValueError('Enter the exact Relay customer name and invoice ID')
        with self.db() as c:
            row = c.execute('SELECT * FROM relay_payments WHERE id=?', (payment_id,)).fetchone()
        if not row or row['status'] != 'waiting_link' or row['payment_link']:
            raise ValueError('Only an unlinked payment request can receive a customer-name mapping')
        snap = self.current(row)
        for key in ('request_id', 'order_number', 'customer_email', 'amount_cents'):
            if payload.get(key) != snap.get(key):
                raise ValueError('Order identity changed; reload before approving')
        if snap.get('state') != 'pending' or snap.get('initiated_at'):
            raise ValueError('Payment is no longer awaiting a link')
        with self.db() as c:
            c.execute('''INSERT INTO relay_customer_aliases
                (payment_id,request_id,source_hash,relay_invoice_id,customer_name,approved_at)
                VALUES (?,?,?,?,?,?)''',
                (payment_id,snap['request_id'],snap['source_hash'],invoice_id,name,now()))
        return {'ok': True}

    def match_customer_capture(self, row, snapshot, capture):
        with self.db() as c:
            alias = c.execute('SELECT * FROM relay_customer_aliases WHERE payment_id=?', (row['id'],)).fetchone()
        if alias:
            if (alias['request_id'] != snapshot['request_id'] or alias['source_hash'] != snapshot['source_hash']
                    or alias['relay_invoice_id'] != capture.get('relay_invoice_id')):
                raise ValueError('Approved contact mapping does not match this invoice')
            snapshot = {**snapshot, 'customer_name': alias['customer_name']}
        return match_capture(snapshot, capture)

    def client(self, store_id):
        return self.client_factory(self.get_store(int(store_id)))

    def rpc(self, row, method, *args):
        snap = json.loads(row['snapshot_json'])
        return self.client(row['store_id']).execute('payment.transaction', method, [[snap['transaction_id']], *args])

    def current(self, row):
        if row['store_id'] not in self.settings()['store_ids']:
            raise ValueError('Store is not enabled')
        fresh = self.rpc(row, 'relay_bridge_snapshot')
        old = json.loads(row['snapshot_json'])
        for k in ('request_id', 'source_hash', 'customer_email', 'customer_name', 'amount_cents', 'order_number', 'website_id', 'database_uuid', 'qbo_invoice_id', 'invoice_number', 'qbo_realm', 'order_id', 'company_id', 'currency'):
            if fresh.get(k) != old.get(k):
                raise ValueError('Current Odoo payment differs: ' + k)
        if fresh['state'] not in ('pending', 'done'):
            raise ValueError('Payment is no longer active')
        return fresh

    def sync(self):
        failed=[]
        for sid in self.settings()['store_ids']:
            try:
                self.sync_store(sid)
            except Exception:
                failed.append(str(sid))
        if failed:
            raise ValueError('Relay sync needs attention for stores: '+','.join(failed))

    def sync_store(self, sid):
        store = self.get_store(int(sid))
        client = self.client(sid)
        website_id = getattr(store, 'website_id', None)
        if website_id:
            website_ids = [int(website_id)]
        else:
            sites = client.execute('website', 'search_read', [[]], {'fields': ['id']})
            website_ids = sorted({int(site['id']) for site in sites})
        for scoped_id in website_ids:
            self.sync_website(sid, client, scoped_id)

    def sync_website(self, sid, client, website_id):
        offset = 0
        while True:
            batch = client.execute('payment.transaction', 'relay_bridge_pending', [website_id, offset])
            for snap in batch['records']:
                if snap['website_id'] != website_id:
                    raise ValueError('Odoo website scope mismatch')
                with self.db() as c:
                    found = c.execute('SELECT * FROM relay_payments WHERE request_id=?', (snap['request_id'],)).fetchone()
                    if found:
                        continue  # Never overwrite the original matching evidence.
                    stamp = now()
                    case = c.execute('''INSERT INTO after_order_cases
                        (case_key,store_id,website_id,odoo_order_id,odoo_order_name,case_type,status,severity,title,customer_email,affected_items_json,context_json,created_at,updated_at)
                        VALUES (?,?,?,?,?,'relay_payment','needs_attention','medium','Relay payment request',?,?,?, ?,?)
                        ON CONFLICT(case_key) DO UPDATE SET updated_at=excluded.updated_at RETURNING id''',
                        ('relay:'+snap['request_id'], sid, snap['website_id'], snap['order_id'], snap['order_number'], snap['customer_email'],
                         json.dumps(snap['items']), json.dumps({'website_name':snap['website_name'], 'payment_request':snap['request_id']}),stamp,stamp)).fetchone()
                    c.execute('''INSERT INTO relay_payments (store_id,request_id,case_id,snapshot_json,pay_token,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING''',
                        (sid,snap['request_id'],case['id'],json.dumps(snap),secrets.token_urlsafe(32),stamp,stamp))
            if not batch['more']:
                break
            offset += 100


    def refresh_bound(self):
        with self.db() as c:
            rows=c.execute("SELECT * FROM relay_payments WHERE payment_link IS NOT NULL AND status!='settled' ORDER BY updated_at LIMIT 100").fetchall()
        for row in rows:
            try:
                snap=self.current(row)
                state='settled' if snap['state']=='done' else 'processing' if snap.get('initiated_at') else row['status']
                with self.db() as c:
                    c.execute('UPDATE relay_payments SET status=?,last_error=NULL,updated_at=? WHERE id=?',(state,now(),row['id']))
            except Exception:
                with self.db() as c:
                    c.execute('UPDATE relay_payments SET last_error=?,updated_at=? WHERE id=?',('Current Odoo payment requires review or reconnection',now(),row['id']))

    def resolve(self, capture):
        if len(json.dumps(capture)) > 20000:
            raise ValueError('Capture is too large')
        matches = []
        with self.db() as c:
            rows = c.execute("SELECT * FROM relay_payments WHERE status IN ('waiting_link','ready','email_sent')").fetchall()
        for row in rows:
            try:
                self.match_customer_capture(row, json.loads(row['snapshot_json']), capture)
                matches.append(row)
            except ValueError:
                pass
        if len(matches) != 1:
            raise ValueError('No unique exact Odoo order/customer/USD amount match; no email sent')
        row = matches[0]
        if row['store_id'] not in self.settings()['store_ids']:
            raise ValueError('Store is not enabled')
        self.match_customer_capture(row, self.current(row), capture)
        return row

    def contact(self, capture):
        # Resolve before disclosing contact data; never match on amount/name alone.
        row = self.resolve(capture)
        snap = self.current(row)
        client = self.client(row['store_id'])
        orders = client.execute('sale.order', 'read', [[snap['order_id']]], {'fields': ['partner_invoice_id']})
        if len(orders) != 1 or not orders[0].get('partner_invoice_id'):
            raise ValueError('Billing contact is unavailable')
        partners = client.execute('res.partner', 'read', [[orders[0]['partner_invoice_id'][0]]],
                                  {'fields': ['email', 'phone', 'mobile', 'country_id']})
        if len(partners) != 1 or not partners[0].get('country_id'):
            raise ValueError('Billing country is unavailable')
        countries = client.execute('res.country', 'read', [[partners[0]['country_id'][0]]],
                                   {'fields': ['code', 'name']})
        if len(countries) != 1:
            raise ValueError('Billing country is unavailable')
        from .relay_contact import contact_details
        result = contact_details(partners[0], countries[0], snap['customer_email'])
        self.match_customer_capture(row, self.current(row), capture)
        return {'ok': True, 'invoice_number': snap['invoice_number'],
                'relay_invoice_id': capture['relay_invoice_id'], 'contact': result}

    def capture(self, capture):
        row = self.resolve(capture)
        link = capture.get('payment_link', '')
        key = payment_key(link)
        if not key or link != 'https://relay.cash/pay/' + key:
            raise ValueError('Invalid payment link')
        snap = json.loads(row['snapshot_json'])
        # Odoo binds globally before we publish any link/email. RPC is idempotent.
        self.rpc(row, 'relay_bridge_bind', snap['request_id'], snap['source_hash'], link,
                 capture['relay_invoice_id'], capture['amount_cents'])
        with self.db() as c:
            locked = c.execute('SELECT * FROM relay_payments WHERE id=? FOR UPDATE', (row['id'],)).fetchone()
            if locked['payment_link'] and (locked['payment_link'] != link or locked['relay_invoice_id'] != capture['relay_invoice_id']):
                raise ValueError('Existing link differs; replacement requires review')
            c.execute("UPDATE relay_payments SET payment_link=?,payment_key=?,relay_invoice_id=?,capture_json=?,status=CASE WHEN status='waiting_link' THEN 'ready' ELSE status END,last_error=NULL,updated_at=? WHERE id=?",
                      (link,key,capture['relay_invoice_id'],json.dumps(capture),now(),row['id']))
            c.execute("INSERT INTO relay_email_outbox (payment_id,kind,created_at) VALUES (?,'request',?) ON CONFLICT(payment_id,kind) DO NOTHING", (row['id'],now()))
        return {'ok': True, 'payment_id': row['id'], 'order_number': snap['order_number'], 'email_status': 'queued'}

    def resend_api(self, path):
        key = os.getenv('RESEND_RECEIVING_API_KEY') or os.getenv('RESEND_API_KEY')
        if not key:
            raise ValueError('Resend receiving API key is not configured')
        response = requests.get('https://api.resend.com'+path, headers={'Authorization':'Bearer '+key}, timeout=20)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def resolve_receipt_tracking(url):
        # Never attach Resend/Odoo credentials, follow arbitrary redirects, or
        # fetch a payment page. Only Relay's known click endpoint is requested.
        for _ in range(4):
            if payment_key(url):
                return url
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.netloc != 'links.relayfi.com' or not parsed.path.startswith('/s/c/'):
                raise ValueError('Receipt tracking destination is not allowed')
            response = requests.get(url, allow_redirects=False, timeout=15, stream=True)
            try:
                if response.status_code not in (301, 302, 303, 307, 308):
                    raise ValueError('Receipt tracking link did not redirect to a payment receipt')
                url = urljoin(url, response.headers.get('Location', ''))
            finally:
                response.close()
        if payment_key(url):
            return url
        raise ValueError('Receipt tracking redirect limit reached')

    def receive(self):
        # Persistent pagination prevents bursts/backlogs exceeding 100 messages from starving.
        cursor = self.get_settings().get('relay_receiving_cursor') or ''
        from urllib.parse import quote
        listing = self.resend_api('/emails/receiving?limit=100'+('&after='+quote(cursor,safe='') if cursor else ''))
        for summary in listing.get('data', []):
            email_id = summary['id']
            with self.db() as c:
                old = c.execute('SELECT status FROM relay_receipts WHERE email_id=?', (email_id,)).fetchone()
            if old and old['status'] != 'retry':
                continue
            targets = summary.get('received_for') or summary.get('to') or []
            if self.settings()['receiving_address'].lower() not in {v.lower() for v in targets}:
                continue
            full = self.resend_api('/emails/receiving/'+quote(email_id,safe=''))
            try:
                evidence = parse_receipt(full,self.settings(),self.resolve_receipt_tracking)
                with self.db() as c:
                    duplicate=c.execute('SELECT email_id FROM relay_receipts WHERE digest=? AND email_id!=?',(evidence['digest'],email_id)).fetchone()
                    if duplicate:
                        c.execute("INSERT INTO relay_receipts (email_id,status,reason,created_at) VALUES (?,'duplicate','Receipt already recorded',?) ON CONFLICT(email_id) DO UPDATE SET status='duplicate'",(email_id,now()))
                    else:
                        c.execute("INSERT INTO relay_receipts (email_id,digest,status,evidence_json,created_at) VALUES (?,?,'pending',?,?) ON CONFLICT(email_id) DO UPDATE SET digest=excluded.digest,status='pending',reason=NULL,evidence_json=excluded.evidence_json",
                                  (email_id,evidence['digest'],json.dumps(evidence),now()))
            except ValueError as exc:
                with self.db() as c:
                    c.execute("INSERT INTO relay_receipts (email_id,status,reason,created_at) VALUES (?,'review',?,?) ON CONFLICT(email_id) DO UPDATE SET status='review',reason=excluded.reason", (email_id,str(exc),now()))
        data = listing.get('data') or []
        self.set_settings({'relay_receiving_cursor':data[-1]['id'] if listing.get('has_more') and data else ''})

    def confirmations(self):
        with self.db() as c:
            rows = c.execute("SELECT * FROM relay_receipts WHERE status='pending' ORDER BY created_at LIMIT 100").fetchall()
        for event in rows:
            evidence = json.loads(event['evidence_json'])
            with self.db() as c:
                payment = c.execute('SELECT * FROM relay_payments WHERE payment_key=?', (evidence['payment_key'],)).fetchone()
            if not payment:
                continue  # Receipt can arrive before link upload; retain it for the next minute.
            try:
                snap = self.current(payment)
                if snap['amount_cents'] != evidence['amount_cents']:
                    raise ValueError('Receipt amount does not match the order')
                result = self.rpc(payment,'relay_bridge_confirm',snap['request_id'],snap['source_hash'],payment['payment_link'],evidence['amount_cents'],event['email_id'])
                if not result.get('initiated_at'):
                    raise ValueError('Odoo did not confirm payment initiation')
                with self.db() as c:
                    c.execute("UPDATE relay_receipts SET status='confirmed',payment_id=?,reason=NULL WHERE email_id=?", (payment['id'],event['email_id']))
                    c.execute("UPDATE relay_payments SET status='processing',last_error=NULL,updated_at=? WHERE id=?", (now(),payment['id']))
                    c.execute("INSERT INTO relay_email_outbox (payment_id,kind,created_at) VALUES (?,'received',?) ON CONFLICT DO NOTHING", (payment['id'],now()))
                    c.execute("UPDATE after_order_cases SET status='resolved',updated_at=? WHERE id=?", (now(),payment['case_id']))
            except ValueError as exc:
                with self.db() as c:
                    c.execute("UPDATE relay_receipts SET status='review',reason=? WHERE email_id=?", (str(exc),event['email_id']))
            except Exception:
                # RPC/server errors retry; Odoo confirmation is idempotent if an acknowledgement was lost.
                with self.db() as c:
                    c.execute('UPDATE relay_payments SET last_error=? WHERE id=?', ('Receipt confirmation temporarily unavailable',payment['id']))

    def email_payload(self, row, kind):
        kind = 'request' if kind == 'request_branded_v1' else kind
        snap = self.current(row)
        if kind == 'request' and (snap.get('initiated_at') or snap['state'] != 'pending'):
            raise ValueError('Payment is no longer awaiting customer action')
        if kind == 'received' and not snap.get('initiated_at'):
            raise ValueError('Payment initiation is not confirmed')
        website = urlsplit(snap['website_url']).hostname
        if not website or not row['payment_link']:
            raise ValueError('Missing website or payment link')
        from .after_order_email import render_after_order_email
        origin_parts = urlsplit(snap['website_url'])
        if origin_parts.scheme != 'https' or origin_parts.username or origin_parts.password or origin_parts.port:
            raise ValueError('A secure order website domain is required')
        origin = 'https://' + website
        logo_url = origin + '/web/image/website/' + str(int(snap['website_id'])) + '/logo'
        pay_url = self.settings()['public_base_url'].rstrip('/')+'/api/relay/pay/'+row['pay_token']
        case = {'odoo_order_name':snap['order_number'], 'sender_domain':website,
                'context':{'website_name':snap['website_name'],'website_logo_url':logo_url,
                           'website_url':origin,'relay_payment':snap}}
        title, body, plain = render_after_order_email(case,pay_url,actions=[],labels={},
                                                     template_kind='relay_'+kind)
        return {'from':snap['website_name']+' <notifications@'+website.removeprefix('www.')+'>','to':[snap['customer_email']],
                'reply_to':'support@'+website.removeprefix('www.'),'subject':title,'html':body,'text':plain}

    def emails(self):
        if self.settings()['test_mode']:
            return
        if not os.getenv('RESEND_API_KEY'):
            raise ValueError('Resend sending API key is not configured')
        with self.db() as c:
            # A prior process may have died after Resend accepted the request.
            # Reuse its immutable payload/key only inside the provider's 24h window.
            c.execute("UPDATE relay_email_outbox SET state='retry' WHERE state='sending'")
            jobs = c.execute("SELECT * FROM relay_email_outbox WHERE state IN ('queued','retry') ORDER BY id LIMIT 50").fetchall()
        for job in jobs:
            first_attempt = job['attempted_at']
            if first_attempt and (datetime.now(timezone.utc)-datetime.fromisoformat(first_attempt)).total_seconds() >= 23*3600:
                with self.db() as c:
                    c.execute("UPDATE relay_email_outbox SET state='delivery_unknown',error='Safe retry window expired; check provider before resending' WHERE id=?",(job['id'],))
                    c.execute("UPDATE after_order_messages SET status='delivery_unknown',last_error='Safe retry window expired',updated_at=? WHERE id=?",(now(),job['message_id']))
                continue
            with self.db() as c:
                row = c.execute('SELECT * FROM relay_payments WHERE id=?', (job['payment_id'],)).fetchone()
            try:
                verified = self.email_payload(row,job['kind'])
                payload = json.loads(job['payload_json']) if job['payload_json'] else verified
                # Never mutate an already dispatched payload or recipient on retry.
                if payload['to'] != verified['to'] or payload['from'] != verified['from']:
                    raise ValueError('Recipient or sender changed')
            except Exception:
                with self.db() as c:
                    c.execute('UPDATE relay_payments SET last_error=? WHERE id=?', ('Payment email held: current Odoo data needs verification',row['id']))
                continue
            key = 'relay:'+row['request_id']+':'+job['kind']
            with self.db() as c:
                claimed = c.execute("UPDATE relay_email_outbox SET state='sending',payload_json=?,attempted_at=COALESCE(attempted_at,?) WHERE id=? AND state IN ('queued','retry') RETURNING id",(json.dumps(payload),now(),job['id'])).fetchone()
                if not claimed:
                    continue
                if job['message_id']:
                    message = c.execute('SELECT id,attempt_count FROM after_order_messages WHERE id=?',(job['message_id'],)).fetchone()
                else:
                    message = c.execute('''INSERT INTO after_order_messages
                        (case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,payload_json,created_at,updated_at,test_mode,template_kind,attempt_count)
                        VALUES (?,'resend',?,?,?,?,'sending',?,?,?,?,0,?,0) ON CONFLICT(idempotency_key) DO NOTHING RETURNING id,attempt_count''',
                        (row['case_id'],payload['to'][0],payload['from'],payload['subject'],payload['html'],key,json.dumps(payload),now(),now(),'relay_'+job['kind'])).fetchone()
                if not message:
                    c.execute("UPDATE relay_email_outbox SET state='delivery_unknown',error='Existing email reservation requires review' WHERE id=?",(job['id'],))
                    continue
                attempt = int(message['attempt_count'] or 0)+1
                c.execute("UPDATE after_order_messages SET status='sending',attempt_count=?,updated_at=? WHERE id=?",(attempt,now(),message['id']))
                c.execute("INSERT INTO after_order_email_attempts(message_id,attempt_number,status,created_at,updated_at) VALUES(?,?,'sending',?,?)",(message['id'],attempt,now(),now()))
                c.execute('UPDATE relay_email_outbox SET message_id=? WHERE id=?',(message['id'],job['id']))
            provider_id = None
            try:
                if self.settings()['test_mode']:
                    raise ValueError('Test mode enabled before delivery')
                response = requests.post('https://api.resend.com/emails',headers={'Authorization':'Bearer '+os.environ['RESEND_API_KEY'],'Idempotency-Key':key},json=payload,timeout=20)
                response.raise_for_status()
                provider_id = response.json()['id']
                status, error = 'sent', None
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                status = 'retry' if code in (408,429) or code >= 500 else 'delivery_unknown'
                error = 'Email provider returned HTTP '+str(code)+'; '+('retrying with the same key' if status=='retry' else 'review required')
            except Exception:
                status, error = 'retry', 'Delivery acknowledgement unavailable; retrying the same payload and key'
            with self.db() as c:
                c.execute('UPDATE relay_email_outbox SET state=?,provider_id=?,error=? WHERE id=?',(status,provider_id,error,job['id']))
                c.execute('UPDATE after_order_messages SET status=?,provider_message_id=?,last_error=?,updated_at=? WHERE id=?',(status,provider_id,error,now(),message['id']))
                c.execute('UPDATE after_order_email_attempts SET status=?,provider_message_id=?,error=?,updated_at=? WHERE message_id=? AND attempt_number=?',(status,provider_id,error,now(),message['id'],attempt))
                if status=='sent' and job['kind'] in ('request','request_branded_v1'):
                    c.execute("UPDATE relay_payments SET status='email_sent' WHERE id=? AND status='ready'",(row['id'],))

    def cycle(self):
        self.ensure()
        if not self.settings()['enabled']:
            return
        # Cross-process exclusion for the minute worker; held until this cycle commits.
        with self.db() as guard:
            lock = guard.execute('SELECT pg_try_advisory_xact_lock(771905432) AS locked').fetchone()
            if not lock['locked']:
                return
            for name, action in [('sync',self.sync),('refresh',self.refresh_bound),('receiving',self.receive),('confirmation',self.confirmations),('email',self.emails)]:
                try:
                    if name=='confirmation' and (self.settings()['test_mode']):
                        continue
                    action()
                    self.set_settings({'relay_'+name+'_last_ok':now(),'relay_'+name+'_error':''})
                except Exception as exc:
                    self.set_settings({'relay_'+name+'_error':type(exc).__name__+': operation failed; check connection/settings'})

    def loop(self):
        while True:
            start=time.monotonic()
            try:
                self.cycle()
            except Exception:
                pass  # A failed startup migration will be retried, never discard queued work.
            time.sleep(max(1,60-(time.monotonic()-start)))

    def router(self):
        r=APIRouter(prefix='/api/relay',tags=['Relay payments'])
        def staff(request):
            if not self.staff_check(request):
                raise HTTPException(401,'Staff authentication required')
        def extension(request, require_enabled=True):
            expected=self.get_settings().get('relay_extension_token_hash','')
            supplied=request.headers.get('X-Relay-Token','')
            if not expected or not hmac.compare_digest(expected,hashlib.sha256(supplied.encode()).hexdigest()):
                raise HTTPException(401,'Relay extension authentication required')
            if require_enabled and not self.settings()['enabled']:
                raise HTTPException(409,'Relay integration is disabled')
        @r.get('/settings')
        def settings(request:Request):
            staff(request);self.ensure()
            raw=self.get_settings()
            return {**self.settings(),'worker_status':{k:v for k,v in raw.items() if k.startswith('relay_') and (k.endswith('_last_ok') or k.endswith('_error'))}}
        @r.post('/settings')
        def settings_save(request:Request,payload:dict):
            staff(request)
            values={**self.settings(),**{k:v for k,v in payload.items() if k in DEFAULTS}}
            if type(values['enabled']) is not bool or type(values['test_mode']) is not bool:
                raise HTTPException(400,'Invalid boolean setting')
            try:
                values['store_ids']=sorted({int(v) for v in values['store_ids'] if int(v)>0})
                for sid in values['store_ids']:
                    self.get_store(sid)
            except (TypeError,ValueError,KeyError):
                raise HTTPException(400,'Choose valid Odoo stores') from None
            import re
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',values['receiving_address']):
                raise HTTPException(400,'A full receiving email address is required')
            base=urlsplit(values['public_base_url'])
            if values['public_base_url'] and (base.scheme!='https' or not base.hostname or base.username or base.password or base.query or base.fragment or base.path not in ('','/')):
                raise HTTPException(400,'An HTTPS app URL is required')
            if values['enabled'] and (not values['store_ids'] or not values['public_base_url']):
                raise HTTPException(400,'Choose stores and configure the public app URL first')
            self.set_settings({'relay_payment_settings':json.dumps(values)})
            return {'ok':True}
        @r.post('/extension-token')
        def token(request:Request):
            staff(request);value=secrets.token_urlsafe(32)
            self.set_settings({'relay_extension_token_hash':hashlib.sha256(value.encode()).hexdigest()})
            return {'token':value}
        @r.post('/extension/check')
        def check_connection(request:Request):
            extension(request, require_enabled=False)
            settings=self.settings()
            return {'ok':True,'service':'relay-payment-bridge','enabled':settings['enabled'],
                    'test_mode':bool(settings['test_mode'])}
        @r.post('/extension/resolve')
        def resolve(request:Request,payload:dict):
            extension(request)
            try:
                row=self.resolve(payload)
                return {'ok':True,'payment_id':row['id']}
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
        @r.post('/extension/contact')
        def contact(request:Request,payload:dict):
            extension(request)
            try:
                return self.contact(payload)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
        @r.post('/extension/capture')
        def capture(request:Request,payload:dict):
            extension(request)
            try:
                return self.capture(payload)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
        @r.post('/payments/{payment_id}/resend-branded')
        def resend_branded(request:Request,payment_id:int):
            staff(request);self.ensure()
            with self.db() as c:
                row=c.execute('SELECT * FROM relay_payments WHERE id=?',(payment_id,)).fetchone()
            if not row or not row['payment_link']:
                raise HTTPException(409,'A matched payment link is required')
            try:
                self.email_payload(row,'request')  # Revalidate current recipient and unpaid order.
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
            with self.db() as c:
                c.execute("INSERT INTO relay_email_outbox(payment_id,kind,created_at) VALUES (?,'request_branded_v1',?) ON CONFLICT(payment_id,kind) DO NOTHING",(payment_id,now()))
            return {'ok':True,'message':'Branded payment email queued. Repeated clicks do not send duplicates.'}
        @r.post('/payments/{payment_id}/customer-alias')
        def customer_alias(request:Request,payment_id:int,payload:dict):
            staff(request);self.ensure()
            try:
                return self.approve_customer_alias(payment_id,payload)
            except ValueError as exc:
                raise HTTPException(409,str(exc)) from None
        @r.get('/payments')
        def payments(request:Request,store_id:int=0):
            staff(request);self.ensure()
            with self.db() as c:
                rows=c.execute('SELECT * FROM relay_payments WHERE (?=0 OR store_id=?) ORDER BY id DESC LIMIT 200',(store_id,store_id)).fetchall()
                outbox=c.execute('SELECT payment_id,kind,state,error FROM relay_email_outbox').fetchall()
                aliases=c.execute('SELECT * FROM relay_customer_aliases').fetchall()
                receipts=c.execute("SELECT email_id,status,reason,created_at FROM relay_receipts WHERE status='review' ORDER BY created_at DESC LIMIT 30").fetchall()
            result=[]
            for row in rows:
                item=dict(row);item.pop('pay_token',None);item.pop('capture_json',None)
                item['snapshot']=json.loads(item.pop('snapshot_json'));item['snapshot'].pop('portal_url',None)
                item['customer_alias']=next((dict(a) for a in aliases if a['payment_id']==row['id']),None)
                item['emails']=[dict(mail) for mail in outbox if mail['payment_id']==row['id']]
                result.append(item)
            return {'rows':result,'receipts':receipts}
        @r.post('/receipts/{email_id}/recheck')
        def recheck(request:Request,email_id:str):
            staff(request)
            with self.db() as c:
                c.execute("UPDATE relay_receipts SET status='retry' WHERE email_id=? AND status='review'",(email_id,))
            self.set_settings({'relay_receiving_cursor':''})
            return {'ok':True}
        @r.get('/pay/{token}')
        def pay(token:str):
            self.ensure()
            with self.db() as c:
                row=c.execute('SELECT * FROM relay_payments WHERE pay_token=?',(token,)).fetchone()
            if not row:
                raise HTTPException(404,'Payment request unavailable')
            try:
                snap=self.current(row)
            except Exception:
                return HTMLResponse('Payment request needs review. Please contact the shop.',status_code=409,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
            if snap.get('initiated_at') or snap['state']=='done':
                return HTMLResponse('Your payment is processing or already paid. Your order is confirmed.',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
            if not row['payment_link'] or snap.get('payment_link')!=row['payment_link']:
                raise HTTPException(409,'Your payment link is being prepared')
            return RedirectResponse(row['payment_link'],status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
        return r
