"""Durable, once-per-refund confirmations using the shared customer email log."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit

from app.services.after_order import create_email_provider, EmailRejected
from app.services.after_order_email import render_after_order_email


def now():
    return datetime.now(timezone.utc).isoformat()


def masked_destination(transfer):
    beneficiary = transfer.get('beneficiary') or {}
    bank = beneficiary.get('bank_details') or {}
    account = str(bank.get('iban') or bank.get('account_number') or '').replace(' ', '')
    email = str(beneficiary.get('personal_email') or '')
    destination = 'Account ending ' + account[-4:] if len(account) >= 4 else 'Verified recipient account'
    if not account and '@' in email:
        name, domain = email.rsplit('@', 1)
        destination = name[:1] + '•••@' + domain
    return {'account': destination, 'holder': str(bank.get('account_name') or ''),
            'bank': str(bank.get('bank_name') or ''),
            'method': str(bank.get('local_clearing_system') or transfer.get('transfer_method') or 'Bank transfer')}


class RefundEmails:
    def __init__(self, *, db, get_store, client_factory, test_mode, suppressed, list_stores=None, cutoff_date=None):
        self.db, self.get_store, self.client_factory = db, get_store, client_factory
        self.test_mode, self.suppressed = test_mode, suppressed
        self.refunds = None
        self.list_stores = list_stores or (lambda: [])
        self.cutoff_date = cutoff_date or (lambda: '')

    def init_db(self):
        with self.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS airwallex_refund_emails (
                request_id TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'queued',
                message_id INTEGER, case_id INTEGER, payload_json TEXT,
                website_id INTEGER, attempted_at TEXT, created_at TEXT NOT NULL,
                last_error TEXT NOT NULL DEFAULT '')''')

    def enqueue(self, c, row, result):
        if row.get('notify_customer') and result.get('status') == 'PAID':
            c.execute('''INSERT INTO airwallex_refund_emails(request_id,created_at)
                         VALUES(?,?) ON CONFLICT(request_id) DO NOTHING''', (row['request_id'], now()))

    def identity(self, row):
        store = self.get_store(int(row['store_id']))
        client = self.client_factory(store)
        orders = client.read('sale.order', [int(row['order_id'])], ['name','website_id','partner_id','date_order'])
        order = orders[0] if len(orders) == 1 else {}
        cutoff = self.cutoff_date()
        if cutoff and str(order.get('date_order') or '')[:10] < cutoff:
            raise ValueError('Order is outside the notification rollout date window.')
        website_id = (order.get('website_id') or [None])[0]
        if not website_id or order.get('name') != row['order_name']:
            raise ValueError('The order website could not be verified. Email is held for review.')
        if store.website_id and int(store.website_id) != int(website_id):
            raise ValueError('The order no longer belongs to this website. Email is held for review.')
        sites = client.read('website', [website_id], ['name','domain'])
        people = client.read('res.partner', [order['partner_id'][0]], ['email'])
        site = sites[0] if len(sites) == 1 else {}
        raw = str(site.get('domain') or '').strip()
        parsed = urlsplit(raw if '://' in raw else 'https://' + raw)
        domain = (parsed.hostname or '').lower().removeprefix('www.')
        recipient = str((people[0] if len(people) == 1 else {}).get('email') or '').strip()
        brand = str(site.get('name') or '').strip()
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port
                or not re.fullmatch(r'[a-z0-9.-]+\.[a-z]{2,}', domain)
                or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', recipient)
                or not brand or any(ch in brand for ch in '\r\n<>')):
            raise ValueError('The customer email or website sender is not valid. Email is held for review.')
        # Prefer the registered website connection for log scoping, even when the
        # refund was opened through a database-wide Odoo connection.
        matching = [s for s in self.list_stores()
                    if int(s.get('active',1)) and s.get('website_id') == int(website_id)
                    and s.get('odoo_db') == getattr(store,'odoo_db',None)
                    and str(s.get('odoo_url') or '').rstrip('/') == str(getattr(store,'odoo_url','')).rstrip('/')]
        log_store_id = int(matching[0]['id']) if len(matching) == 1 else int(row['store_id'])
        return {'log_store_id':log_store_id, 'website_id': int(website_id), 'website_name':brand, 'domain':domain,
                'recipient':recipient, 'logo':'https://' + domain + '/web/image/website/' + str(website_id) + '/logo'}

    def payload(self, row, site):
        details = json.loads(row.get('email_destination') or '{}')
        case = {'odoo_order_name':row['order_name'], 'sender_domain':site['domain'],
                'context':{'website_name':site['website_name'],'website_logo_url':site['logo'],
                           'refund':{'amount':str(row['amount']), 'currency':row['currency'],
                                     'reference':row['transfer_id'], **details}}}
        subject, body, plain = render_after_order_email(case, '', actions=[], labels={}, template_kind='refund_confirmed')
        return {'from':site['website_name']+' <notifications@'+site['domain']+'>',
                'to':[site['recipient']], 'reply_to':'support@'+site['domain'],
                'subject':subject,'html':body,'text':plain}

    def hold(self, job, message):
        with self.db() as c:
            c.execute("UPDATE airwallex_refund_emails SET state='held',last_error=? WHERE request_id=? AND state!='cancelled'", (message,job['request_id']))
            if job.get('message_id'):
                c.execute("UPDATE after_order_messages SET status='delivery_unknown',last_error=?,updated_at=? WHERE id=? AND status NOT IN ('sent','delivered','bounced','complained','delivery_delayed')", (message,now(),job['message_id']))

    def process(self, job):
        with self.db() as c:
            current = c.execute('SELECT state FROM airwallex_refund_emails WHERE request_id=?',(job['request_id'],)).fetchone()
            if not current or current['state']=='cancelled':
                return
        if job.get('message_id'):
            with self.db() as c:
                saved = c.execute('SELECT status FROM after_order_messages WHERE id=?',(job['message_id'],)).fetchone()
                if saved and saved['status'] in {'sent','delivered','bounced','complained','delivery_delayed'}:
                    c.execute("UPDATE airwallex_refund_emails SET state='sent',last_error='' WHERE request_id=?",(job['request_id'],))
                    return
        # This is a GET reconciliation, never a payout or retry of the payment.
        self.refunds.refresh(job['request_id'])
        with self.db() as c:
            row = dict(c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (job['request_id'],)).fetchone())
        if row['status'] != 'PAID':
            self.hold(job, 'Refund is not currently confirmed successful. No confirmation email sent.')
            return
        site = self.identity(row)
        fresh = self.payload(row, site)
        payload = json.loads(job['payload_json']) if job.get('payload_json') else fresh
        if job.get('payload_json') and (job['website_id'] != site['website_id'] or payload['to'] != fresh['to'] or payload['from'] != fresh['from']):
            self.hold(job, 'The order website or customer changed. Verify the saved email before retrying.')
            return
        if self.suppressed(site['recipient'], False):
            self.hold(job, 'Email is suppressed after a bounce or complaint. Verify the customer email.')
            return
        if job.get('attempted_at') and datetime.now(timezone.utc)-datetime.fromisoformat(job['attempted_at']) >= timedelta(hours=23):
            self.hold(job, 'Email acceptance is uncertain and the safe retry window expired. Check the email provider before resending.')
            return
        key = 'airwallex-refund:' + job['request_id']
        with self.db() as c:
            case = c.execute('''INSERT INTO after_order_cases
                (case_key,store_id,website_id,odoo_order_id,odoo_order_name,case_type,status,severity,title,
                 customer_email,sender_domain,context_json,created_at,updated_at)
                VALUES(?,?,?,?,?,'refund_confirmed','resolved','low','Refund confirmation',?,?,?,?,?)
                ON CONFLICT(case_key) DO UPDATE SET updated_at=excluded.updated_at RETURNING id''',
                (key,site['log_store_id'],site['website_id'],row['order_id'],row['order_name'],site['recipient'],site['domain'],
                 json.dumps({'website_name':site['website_name'],'website_logo_url':site['logo']}),now(),now())).fetchone()
            c.execute('''INSERT INTO after_order_messages
                (case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,payload_json,
                 created_at,updated_at,test_mode,template_kind,attempt_count)
                VALUES(?,'resend',?,?,?,?,'sending',?,?,?,?,0,'refund_confirmed',0)
                ON CONFLICT(idempotency_key) DO NOTHING''',
                (case['id'],payload['to'][0],payload['from'],payload['subject'],payload['html'],key,json.dumps(payload),now(),now()))
            message = c.execute('SELECT * FROM after_order_messages WHERE idempotency_key=? FOR UPDATE',(key,)).fetchone()
            if message['status'] in {'sent','delivered','bounced','complained','delivery_delayed'}:
                c.execute("UPDATE airwallex_refund_emails SET state='sent',message_id=? WHERE request_id=?",(message['id'],job['request_id']))
                return
            attempt = int(message['attempt_count'] or 0)+1
            c.execute("UPDATE after_order_messages SET status='sending',attempt_count=?,updated_at=? WHERE id=?",(attempt,now(),message['id']))
            c.execute("INSERT INTO after_order_email_attempts(message_id,attempt_number,status,created_at,updated_at) VALUES(?,?,'sending',?,?)",(message['id'],attempt,now(),now()))
            c.execute("UPDATE airwallex_refund_emails SET state='sending',message_id=?,case_id=?,payload_json=?,website_id=?,attempted_at=COALESCE(attempted_at,?),last_error='' WHERE request_id=?",(message['id'],case['id'],json.dumps(payload),site['website_id'],now(),job['request_id']))
        provider_id = None
        try:
            if self.test_mode():
                raise EmailRejected('Live email sending is paused in test mode.')
            provider = create_email_provider('resend', {'api_key':os.environ.get('RESEND_API_KEY','')})
            result = provider.send(payload, idempotency_key=key)
            provider_id = result.get('id')
            if not provider_id:
                raise RuntimeError('No provider acknowledgement')
            state, status, error = 'sent', 'sent', ''
        except (EmailRejected, ValueError):
            state, status, error = 'failed', 'failed', 'The email provider rejected this message. Ask the administrator to check the website sender and recipient.'
        except Exception:
            state, status, error = 'retry', 'delivery_unknown', 'Email acceptance is uncertain. The worker will check again using the same email and duplicate-protection key.'
        with self.db() as c:
            c.execute('UPDATE airwallex_refund_emails SET state=?,last_error=? WHERE request_id=?',(state,error,job['request_id']))
            c.execute("UPDATE after_order_messages SET status=?,provider_message_id=?,last_error=?,updated_at=? WHERE id=? AND status NOT IN ('delivered','bounced','complained')",(status,provider_id,error,now(),message['id']))
            c.execute('UPDATE after_order_email_attempts SET status=?,provider_message_id=?,error=?,updated_at=? WHERE message_id=? AND attempt_number=?',(status,provider_id,error,now(),message['id'],attempt))

    def approve_retry(self, message, digest):
        if self.test_mode():
            raise ValueError('Refund confirmation sending is paused.')
        if hashlib.sha256(str(message.get('payload_json') or '').encode()).hexdigest() != digest:
            raise ValueError('The saved email changed. Review it again.')
        with self.db() as c:
            job = c.execute('SELECT * FROM airwallex_refund_emails WHERE message_id=? FOR UPDATE',(message['id'],)).fetchone()
            if not job or job['state'] != 'failed' or message['status'] != 'failed' or int(message['attempt_count'] or 0) >= 5:
                raise ValueError('This email cannot be retried in its current state.')
            if not job['attempted_at'] or datetime.now(timezone.utc)-datetime.fromisoformat(job['attempted_at']) >= timedelta(hours=23):
                raise ValueError('The safe retry window has expired. Check delivery with the provider first.')
            c.execute("UPDATE airwallex_refund_emails SET state='retry',last_error='' WHERE request_id=?",(job['request_id'],))
            c.execute("UPDATE after_order_messages SET status='retrying',last_error='',updated_at=? WHERE id=?",(now(),message['id']))
        return {'ok':True,'message':'Refund email retry queued. The worker will verify the payment and website again before sending.'}

    def cycle(self):
        if self.test_mode():
            return
        with self.db() as guard:
            if not guard.execute("SELECT pg_try_advisory_xact_lock(781905437) AS locked").fetchone()['locked']:
                return
            with self.db() as c:
                jobs = c.execute("SELECT * FROM airwallex_refund_emails WHERE state IN ('queued','retry','sending','held') ORDER BY created_at LIMIT 30").fetchall()
            for job in jobs:
                try:
                    self.process(dict(job))
                except Exception:
                    self.hold(dict(job), 'Refund email is held: the payment, customer or order website could not be verified. Check the order details.')
