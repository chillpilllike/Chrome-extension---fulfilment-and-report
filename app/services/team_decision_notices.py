"""Transactional staff alerts for saved customer decisions; no order execution."""
import hashlib
import html
import json
import re
import time
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from app.services.after_order import ResendEmailProvider, EmailRejected

SETTING = 'after_order_team_emails'
EVENTS = {'customer_decision_received', 'customer_decision_changed', 'alternative_selected',
          'alternative_changed', 'line_removal_requested'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS after_order_team_notices (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id INTEGER REFERENCES after_order_case_events(id) ON DELETE CASCADE,
 case_id INTEGER REFERENCES after_order_cases(id) ON DELETE CASCADE,
 recipient TEXT NOT NULL,
 snapshot_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0,
 provider_message_id TEXT,
 last_error TEXT,
 next_attempt_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 UNIQUE(event_id,recipient)
);
CREATE INDEX IF NOT EXISTS idx_team_notices_due ON after_order_team_notices(status,next_attempt_at);
"""

def recipients(value):
    """One bare address per line; reject invalid lines instead of partial saves."""
    result = []
    for line in str(value or '').splitlines():
        address = line.strip().lower()
        if not address:
            continue
        if len(address) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", address) or '..' in address:
            raise ValueError('Enter one valid team email address per line, without names, commas or semicolons.')
        if address not in result:
            result.append(address)
    if len(result) > 50:
        raise ValueError('Enter no more than 50 team email addresses.')
    return result


def next_action(decision, detail):
    if decision == 'proceed':
        return 'Review the latest dispatch estimate, confirm the customer’s Proceed request in After-order care, then continue fulfilment under the usual order checks.'
    if decision == 'cancel_order':
        return 'Check current fulfilment and payment status, confirm the cancellation request, and review any refund due. This request has not cancelled or refunded the order.'
    if decision in {'refund', 'cancel_affected_item', 'exclude_item_and_proceed', 'remove_line'}:
        return 'Review the affected items, current fulfilment and refundable amount. Obtain the required approval and record verified refund completion before releasing remaining items. No refund is recorded by this alert.'
    if decision in {'replacement', 'offer_alternatives'}:
        deadline = detail.get('deadline_at')
        return ('The customer may change this selection until ' + str(deadline) + '. ' if deadline else '') + 'Review the selected product, availability and price difference. Complete the required team, payment or refund approvals before replacement fulfilment.'
    if decision == 'not_received':
        return 'Review the carrier delivery evidence and customer report, investigate with the carrier, and contact the customer with the next step. Do not treat the parcel as received.'
    if decision == 'received':
        return 'Review and confirm the delivery acknowledgement. Close the matching delivery follow-up after checking for any outstanding issue.'
    return 'Open the case in After-order care, review the customer’s latest request and complete the required approval before taking action.'


def message(snapshot, recipient, labels):
    decision = snapshot['decision']
    label = labels.get(decision, decision.replace('_', ' ').capitalize())
    case_id = snapshot['case_id']
    lines = [
        'Customer order choice received', '',
        'Website: ' + str(snapshot.get('website') or snapshot.get('domain') or ''),
        'Customer: ' + str(snapshot.get('customer_name') or 'Name unavailable; identify by customer email'),
        'Customer email: ' + str(snapshot.get('customer_email') or 'Not available'),
        'Order: ' + str(snapshot.get('order_name') or snapshot.get('order_id')),
        'Case: ' + str(case_id) + ' — ' + str(snapshot.get('title') or ''),
        'Selected option: ' + label,
        'Recorded at: ' + snapshot['recorded_at'] + ' (UTC)',
        'Previous option: ' + str(labels.get(snapshot.get('previous_decision'), snapshot.get('previous_decision')) or 'None recorded'),
    ]
    if snapshot.get('expected_dispatch_date'):
        lines.append('Current expected dispatch: ' + str(snapshot['expected_dispatch_date']))
    if snapshot.get('tracking_code'):
        lines.append('Tracking reference: ' + str(snapshot['tracking_code']))
    if snapshot.get('order_total'):
        lines.append('Order total: ' + str(snapshot['order_total']))
    lines += ['', 'Affected items:']
    for item in snapshot.get('items') or []:
        lines.append('- ' + str(item.get('product_name') or 'Item') + ' | quantity ' + str(item.get('quantity') or '') +
                     (' | ' + str(item.get('currency') or '') + ' ' + str(item['original_line_total']) if item.get('original_line_total') is not None else ''))
    detail = snapshot.get('details') or {}
    if detail.get('line_id'):
        lines.append('Selected line: ' + str(detail['line_id']))
    if detail.get('product_name'):
        lines.append('Selected item/product: ' + str(detail['product_name']))
    if detail.get('previous_product'):
        lines.append('Previous selected product: ' + str(detail['previous_product']))
    if detail.get('difference') is not None:
        lines.append('Price difference: ' + str(detail.get('currency') or '') + ' ' + str(detail['difference']))
    if snapshot.get('selected_product_id'):
        lines.append('Selected product ID: ' + str(snapshot['selected_product_id']))
    lines += ['', 'Next action:', next_action(decision, detail), '',
              'Open After-order care: https://fulfilment.gofinch.com/after-order-care',
              'Search for order ' + str(snapshot.get('order_name') or snapshot.get('order_id')) + ' and case ' + str(case_id) + '.',
              'This notification confirms a saved request; it does not confirm a completed order action.']
    if snapshot.get('odoo_order_url'):
        lines += ['Order in Odoo: ' + snapshot['odoo_order_url']]
    if snapshot.get('test'):
        lines.insert(0, 'TEST ONLY — no customer decision, cancellation, refund or fulfilment was performed.')
    text = '\n'.join(lines)
    return {'from': snapshot['sender'], 'to': [recipient],
            'subject': ('[TEST] ' if snapshot.get('test') else '') + 'Customer choice: ' + str(snapshot.get('order_name') or 'Order') + ' — ' + label,
            'text': text, 'html': '<div style="font:15px/1.6 Arial,sans-serif;max-width:760px"><h2>Customer order choice received</h2><pre style="font:inherit;white-space:pre-wrap">' + html.escape(text) + '</pre></div>'}


class Runtime:
    def __init__(self, namespace):
        self.namespace = namespace

    def __getattr__(self, name):
        try:
            return self.namespace[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class Notices:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def enqueue(self, conn, event_id, case_id, event_type, actor_type, decision, details):
        if event_type not in EVENTS or actor_type != 'customer' or details.get('test_mode') or self.r.after_order_email_test_mode():
            return
        addresses = recipients(self.r.get_service_settings().get(SETTING))
        if not addresses:
            return
        row = conn.execute('SELECT c.*,s.name AS store_name,s.odoo_url FROM after_order_cases c JOIN stores s ON s.id=c.store_id WHERE c.id=?', (case_id,)).fetchone()
        if not row:
            return
        case = dict(row)
        context = json.loads(case.get('context_json') or '{}')
        selected = decision or ('offer_alternatives' if event_type.startswith('alternative_') else 'remove_line')
        sender, domain = self.r.after_order_sender(dict(case, context=context))
        stamp = self.r.utc_now()
        snapshot = {'case_id':case_id, 'event_id':event_id, 'order_id':case.get('odoo_order_id'),
                    'order_name':case.get('odoo_order_name'), 'store_id':case['store_id'],
                    'domain':domain, 'website':context.get('website_name') or case['store_name'],
                    'customer_email':case.get('customer_email'), 'title':case['title'],
                    'items':json.loads(case.get('affected_items_json') or '[]'),
                    'expected_dispatch_date':context.get('expected_dispatch_date'),
                    'tracking_code':case.get('tracking_code'), 'decision':selected,
                    'previous_decision':details.get('previous_decision'),
                    'selected_product_id':details.get('selected_product_id'),
                    'details':{k:details.get(k) for k in ('line_id','product_name','previous_product','difference','currency','deadline_at')},
                    'recorded_at':stamp, 'sender':sender,
                    'odoo_order_url':str(case.get('odoo_url') or '').rstrip('/') + '/web#id=' + str(case.get('odoo_order_id')) + '&model=sale.order&view_type=form'}
        for address in addresses:
            conn.execute('''INSERT INTO after_order_team_notices(event_id,case_id,recipient,snapshot_json,next_attempt_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id,recipient) DO NOTHING''',
                (event_id,case_id,address,json.dumps(snapshot,default=str),stamp,stamp,stamp))

    def enrich(self, snapshot):
        try:
            odoo = self.r.OdooClient(self.r.get_store(snapshot['store_id']))
            order = odoo.read('sale.order', [snapshot['order_id']], ['partner_id','amount_total','currency_id'])
            if order:
                order = order[0]
                partner = order.get('partner_id')
                snapshot['customer_name'] = partner[1] if isinstance(partner,list) and len(partner)>1 else ''
                currency = order.get('currency_id')
                snapshot['order_total'] = str(currency[1] if isinstance(currency,list) and len(currency)>1 else '') + ' ' + str(order.get('amount_total'))
        except Exception:
            pass  # Still send the saved order/email facts when Odoo is unavailable.
        return snapshot

    def run(self):
        r = self.r
        if r.after_order_email_test_mode():
            return
        # The row is committed as sending before any network call. Ambiguous
        # outcomes remain reserved, so a crash/restart cannot duplicate a send.
        for _ in range(20):
            with r.db() as conn:
                row = conn.execute("SELECT * FROM after_order_team_notices WHERE status='pending' AND next_attempt_at<=? ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED",(r.utc_now(),)).fetchone()
                if not row:
                    return
                row = dict(row)
                if row['recipient'] not in recipients(r.get_service_settings().get(SETTING)):
                    conn.execute("UPDATE after_order_team_notices SET status='suppressed',updated_at=? WHERE id=?",(r.utc_now(),row['id']))
                    continue
                conn.execute("UPDATE after_order_team_notices SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?",(r.utc_now(),row['id']))
            snapshot = self.enrich(json.loads(row['snapshot_json']))
            status, error, provider_id = 'sent', None, None
            try:
                result = ResendEmailProvider(r.os.getenv('RESEND_API_KEY','')).send(message(snapshot,row['recipient'],r.AFTER_ORDER_ACTION_LABELS),idempotency_key='team-decision-'+str(row['id']))
                provider_id = result.get('id')
                if not provider_id:
                    raise RuntimeError('Provider acceptance was not confirmed')
            except EmailRejected:
                status, error = 'failed', 'Email provider rejected the notification. Review sender and recipient configuration.'
            except Exception:
                status, error = 'delivery_unknown', 'Delivery outcome is uncertain. Check the provider before retrying.'
            with r.db() as conn:
                conn.execute('UPDATE after_order_team_notices SET status=?,provider_message_id=?,last_error=?,updated_at=? WHERE id=?', (status,provider_id,error,r.utc_now(),row['id']))
                if row['case_id'] is not None:
                    r.record_after_order_event(conn,row['case_id'],'team_decision_notice_'+status,details={'notice_id':row['id'],'recipient':row['recipient']})
            time.sleep(0.6)  # Keep multi-recipient alerts within the provider's request rate.

    def reconcile(self):
        import requests
        r = self.r
        with r.db() as conn:
            conn.execute("UPDATE after_order_team_notices SET status='delivery_unknown',last_error='Send was interrupted. Check provider records before retrying.',updated_at=? WHERE status='sending' AND updated_at<?",(r.utc_now(),(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()))
            rows = conn.execute("SELECT id,case_id,provider_message_id,status FROM after_order_team_notices WHERE provider_message_id IS NOT NULL AND status IN ('sent','delivery_delayed') AND next_attempt_at<=? ORDER BY id LIMIT 10",(r.utc_now(),)).fetchall()
            for row in rows:
                conn.execute('UPDATE after_order_team_notices SET next_attempt_at=? WHERE id=?',((datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat(),row['id']))
        for row in rows:
            try:
                response = requests.get('https://api.resend.com/emails/'+row['provider_message_id'],headers={'Authorization':'Bearer '+r.os.getenv('RESEND_API_KEY','')},timeout=20)
                response.raise_for_status()
                event = response.json().get('last_event')
                if event not in {'delivered','bounced','complained','suppressed','failed','delivery_delayed'}:
                    continue
                error = {'suppressed':'The email provider suppressed this recipient. Check the mailbox and provider suppression record.',
                         'bounced':'The recipient mail server rejected this email. Check the team address.',
                         'complained':'The recipient reported this email as spam. Review before sending again.',
                         'failed':'Email delivery failed at the provider.',
                         'delivery_delayed':'Delivery is delayed; the provider is still trying.'}.get(event)
                with r.db() as conn:
                    conn.execute('UPDATE after_order_team_notices SET status=?,last_error=?,updated_at=? WHERE id=?',(event,error,r.utc_now(),row['id']))
                    if row['case_id'] is not None and event != row['status']:
                        r.record_after_order_event(conn,row['case_id'],'team_decision_notice_'+event,details={'notice_id':row['id']})
            except Exception:
                continue
            time.sleep(0.6)

    def loop(self):
        while True:
            try:
                self.run()
                self.reconcile()
            except Exception:
                pass  # Persisted pending rows remain available after a DB outage.
            time.sleep(10)

    def router(self):
        router = APIRouter()
        @router.get('/api/after-order/team-notifications')
        def status():
            with self.r.db() as conn:
                rows = conn.execute('SELECT status,COUNT(*) AS count FROM after_order_team_notices GROUP BY status').fetchall()
                recent = conn.execute('SELECT id,case_id,recipient,status,provider_message_id,last_error,created_at,updated_at FROM after_order_team_notices ORDER BY id DESC LIMIT 20').fetchall()
            return {'ok':True,'recipients':recipients(self.r.get_service_settings().get(SETTING)), 'counts':{row['status']:row['count'] for row in rows},'recent':[dict(row) for row in recent]}
        @router.post('/api/after-order/team-notifications/test')
        def test():
            r = self.r
            addresses = recipients(r.get_service_settings().get(SETTING))
            if not addresses:
                raise HTTPException(400,'Save at least one team email address first.')
            # Synthetic email exercises the actual provider without recording a
            # customer decision or creating an executable workflow.
            sender = str(r.get_service_settings().get('email_from_address') or '').strip()
            if not sender:
                sender = 'Gofinch Customer Care <notifications@nutricity.com.au>'
            snapshot = {'test':True,'sender':sender,'case_id':'TEST','order_name':'TEST-DISPATCH','customer_name':'Test customer','customer_email':'test@example.com', 'website':'Team notification test','decision':'proceed','recorded_at':r.utc_now(),'items':[{'product_name':'Test item','quantity':1}], 'details':{}}
            ids=[]
            stamp=r.utc_now()
            with r.db() as conn:
                for address in addresses:
                    row=conn.execute('''INSERT INTO after_order_team_notices(recipient,snapshot_json,next_attempt_at,created_at,updated_at)
                        VALUES(?,?,?,?,?) RETURNING id''',(address,json.dumps(snapshot),stamp,stamp,stamp)).fetchone()
                    ids.append(row['id'])
            return {'ok':True,'notice_ids':ids,'message':'Test notifications queued for the saved team addresses. No customer decision or order action was recorded.'}
        return router
