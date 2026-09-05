"""After-order line recommendations and durable, deadline-driven processing.

Runtime dependencies are injected by main, keeping tests away from production config.
All external writes are behind the live-mode guard. Odoo owns transactional quote
creation and the accounting/payment checks; an RPC retry uses the same operation key.
"""
import json
import hashlib
from datetime import datetime, timezone
from email.utils import parseaddr

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.services.alternative_selection import moment, price_fingerprint, resolved_asin, selection_change


SCHEMA = """
CREATE TABLE IF NOT EXISTS after_order_line_offers (
    case_id INTEGER NOT NULL REFERENCES after_order_cases(id) ON DELETE CASCADE,
    line_id INTEGER NOT NULL REFERENCES order_lines(id) ON DELETE CASCADE,
    recommendations_json TEXT NOT NULL DEFAULT '[]',
    issue_fingerprint TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY (case_id, line_id)
);
CREATE TABLE IF NOT EXISTS after_order_line_selections (
    case_id INTEGER NOT NULL REFERENCES after_order_cases(id) ON DELETE CASCADE,
    line_id INTEGER NOT NULL REFERENCES order_lines(id) ON DELETE CASCADE,
    test_mode INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    product_json TEXT NOT NULL,
    first_selected_at TEXT NOT NULL,
    deadline_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'choosing',
    issue_fingerprint TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    refund_status TEXT NOT NULL DEFAULT '',
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (case_id, line_id, test_mode)
);
CREATE INDEX IF NOT EXISTS idx_after_order_selections_due
ON after_order_line_selections(test_mode, status, deadline_at);
CREATE TABLE IF NOT EXISTS after_order_line_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL,
    odoo_order_id INTEGER NOT NULL,
    line_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_after_order_line_activity_order
ON after_order_line_activity(store_id,odoo_order_id,created_at);
"""


class Recommendations(BaseModel):
    references: list[str] = Field(min_length=1, max_length=12)
    sourcing_checked: bool = False


class Runtime:
    def __init__(self, namespace):
        self.namespace = namespace

    def __getattr__(self, name):
        try:
            return self.namespace[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


class Workflow:
    def __init__(self, namespace):
        self.namespace = namespace
        self.r = Runtime(namespace)

    def event(self, conn, case, kind, line_id=None, **details):
        self.r.record_after_order_event(conn, case['id'], kind, actor_type=details.pop('actor','system'), details={
            'order_number': case['odoo_order_name'], 'line_id': line_id, **details,
        })

    def case_line(self, case_id, line_id):
        r = self.r
        case = r.after_order_case_by_id(case_id)
        if not case or case['case_type'] != 'item_unavailable':
            raise HTTPException(404, 'Unavailable-item case not found.')
        r.require_after_order_case_in_scope(case)
        if line_id not in {int(i['line_id']) for i in case['affected_items']}:
            raise HTTPException(409, 'This line is no longer an affected item in this case.')
        review = r.after_order_unavailable_review(case)
        if review['blocked']:
            raise HTTPException(409, review['reason'])
        with r.db() as conn:
            line = conn.execute('SELECT * FROM order_lines WHERE id=? AND store_id=? AND odoo_order_id=?',
                (line_id, case['store_id'], case['odoo_order_id'])).fetchone()
        if not line:
            raise HTTPException(404, 'Order line not found in this store.')
        if int(dict(line).get('source_line_count') or 1) != 1:
            raise HTTPException(409, 'This app row combines multiple Odoo lines. Split or review it manually before offering an alternative.')
        return case, dict(line)

    def rows(self, case_id, test_mode=None):
        r = self.r
        test_mode = r.after_order_email_test_mode() if test_mode is None else test_mode
        with r.db() as conn:
            offers = [dict(row) for row in conn.execute('SELECT * FROM after_order_line_offers WHERE case_id=? ORDER BY line_id', (case_id,)).fetchall()]
            selections = {row['line_id']: dict(row) for row in conn.execute(
                'SELECT * FROM after_order_line_selections WHERE case_id=? AND test_mode=?', (case_id, int(test_mode))).fetchall()}
        result = []
        for offer in offers:
            selection = selections.get(offer['line_id'])
            if selection:
                selection['product'] = json.loads(selection.pop('product_json'))
                selection['result'] = json.loads(selection.pop('result_json'))
                selection['locked'] = selection['status'] != 'choosing' or moment(selection['deadline_at']) <= datetime.now(timezone.utc)
            result.append({**offer, 'recommendations': json.loads(offer['recommendations_json']), 'selection': selection})
        return result

    def product(self, case, line, template_id=0, reference=''):
        r = self.r
        odoo = r.OdooClient(r.get_store(case['store_id']))
        try:
            return odoo.execute('sale.order', 'after_order_alternative_info',
                [[case['odoo_order_id']], int(line['odoo_line_id']), int(case['website_id']), int(template_id or 0), reference])
        except Exception as exc:
            raise HTTPException(409, f'Could not verify the alternative and its price: {r.clean_error_message(exc)}') from exc

    def publish(self, case_id, line_id, payload, request):
        r = self.r
        case, line = self.case_line(case_id, line_id)
        if not payload.sourcing_checked:
            raise HTTPException(400, 'Confirm that third-party and manual sourcing have been checked.')
        references = list(dict.fromkeys(ref.strip() for ref in payload.references if ref.strip()))
        if not references:
            raise HTTPException(400, 'Enter at least one exact Odoo Internal Reference.')
        products = [self.product(case, line, reference=ref) for ref in references]
        if len({p['product_tmpl_id'] for p in products}) != len(products):
            raise HTTPException(400, 'Recommend only one exact variant of each product for this line.')
        with r.db() as conn:
            # Same lock order as customer decisions, confirmation and the worker.
            conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE', (case_id,)).fetchone()
            if conn.execute('SELECT 1 FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=?',
                    (case_id, line_id, int(r.after_order_email_test_mode()))).fetchone():
                raise HTTPException(409, 'A customer has already selected for this line. Recommendations cannot be changed during processing.')
            fresh, _ = self.case_line(case_id, line_id)
            fingerprint = r.request_fingerprint(fresh)
            conn.execute('''INSERT INTO after_order_line_offers
                (case_id,line_id,recommendations_json,issue_fingerprint,published_at) VALUES(?,?,?,?,?)
                ON CONFLICT(case_id,line_id) DO UPDATE SET recommendations_json=excluded.recommendations_json,
                issue_fingerprint=excluded.issue_fingerprint,published_at=excluded.published_at''',
                (case_id,line_id,json.dumps(products),fingerprint,r.utc_now()))
            review = r.after_order_unavailable_review(fresh)
            r.record_after_order_event(conn, case_id, 'unavailable_sourcing_review_approved', actor_type='team',
                actor_label='Team selected alternatives after sourcing review', details={'signature': review['signature']})
            self.event(conn, case, 'line_alternatives_published', line_id, actor='team', products=[{'reference': p['default_code'], 'name': p['name']} for p in products])
        # No email until every currently affected line has a prepared recommendation.
        if not self.ready(case):
            return {'ok': True, 'message': 'Alternatives saved. Prepare the remaining affected lines before the customer is notified.'}
        result = r.send_after_order_email(case_id, request)
        return {'ok': True, 'message': result.get('message') or 'Alternatives saved and notification submitted.', 'email': result}

    def ready(self, case):
        rows = self.rows(case['id'])
        current = self.r.request_fingerprint(case)
        ready = {row['line_id'] for row in rows if row['issue_fingerprint'] == current and row['recommendations']}
        affected = {int(i['line_id']) for i in case['affected_items']}
        return bool(affected and affected.issubset(ready))

    def notification_revision(self, case):
        return hashlib.sha256(json.dumps([{'line_id':row['line_id'], 'recommendations':row['recommendations']}
            for row in self.rows(case['id'])],sort_keys=True).encode()).hexdigest()

    def log_quote_email(self, conn, case, line_id, result):
        mail = result.get('mail') or {}
        if not mail.get('id'):
            return
        r = self.r
        status = {'outgoing':'sending','sent':'sent','exception':'failed','cancel':'failed'}.get(mail['state'],'delivery_unknown')
        key = f"odoo-quote:{case['store_id']}:{result['quote_id']}"
        conn.execute('''INSERT INTO after_order_messages
            (case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,provider_message_id,
            payload_json,created_at,updated_at,test_mode,request_fingerprint,template_kind,related_items_json,last_error)
            VALUES(?,'odoo',?,?,?,'<p>This quotation email is managed by Odoo. Open its quotation in Odoo to view the document.</p>',?,?,?,'{}',?,?,0,?,'price_difference',?,?)
            ON CONFLICT(idempotency_key) DO UPDATE SET status=excluded.status,last_error=excluded.last_error,updated_at=excluded.updated_at''',
            (case['id'],mail['recipient'],mail['sender'],mail['subject'],status,key,str(mail['id']),r.utc_now(),r.utc_now(),
             r.request_fingerprint(case),json.dumps([item for item in case['affected_items'] if item['line_id']==line_id]),mail.get('error') or None))
        message = conn.execute('SELECT id,attempt_count FROM after_order_messages WHERE idempotency_key=?',(key,)).fetchone()
        conn.execute('''INSERT INTO after_order_email_attempts
            (message_id,attempt_number,status,error,provider_message_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(message_id,attempt_number) DO UPDATE SET status=excluded.status,error=excluded.error,updated_at=excluded.updated_at''',
            (message['id'],message['attempt_count'],status,mail.get('error') or None,str(mail['id']),r.utc_now(),r.utc_now()))

    def retry_quote_email(self, message):
        r = self.r
        if r.after_order_email_test_mode():
            raise HTTPException(409,'Odoo quotation emails cannot be retried in test mode.')
        case = r.after_order_case_by_id(message['case_id'])
        r.require_after_order_case_in_scope(case)
        case = r.hydrate_after_order_recipient_and_domain(case,strict=True)
        if r.after_order_unavailable_review(case)['blocked'] or r.request_fingerprint(case) != message['request_fingerprint']:
            raise HTTPException(409,'The affected fulfilment changed. Review the quotation before retrying its email.')
        if (parseaddr(message.get('recipient',''))[1].lower() != case.get('customer_email','').lower()
                or parseaddr(message.get('sender',''))[1].lower() != parseaddr(r.after_order_sender(case)[0])[1].lower()):
            raise HTTPException(409,'The customer email or website sender changed. Review the quotation in Odoo before sending.')
        with r.db() as conn:
            conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE',(case['id'],)).fetchone()
            latest = conn.execute('SELECT * FROM after_order_messages WHERE id=? FOR UPDATE',(message['id'],)).fetchone()
            if latest['status'] != 'failed' or latest['attempt_count'] >= 5:
                raise HTTPException(409,'Only a failed quotation email with fewer than five attempts may be retried.')
            row = next((offer for offer in self.rows(case['id'],False) if
                str((offer.get('selection') or {}).get('result',{}).get('mail',{}).get('id')) == str(message['provider_message_id'])),None)
            if not row or row['selection']['status'] != 'waiting_payment' or case.get('current_decision') != 'offer_alternatives':
                raise HTTPException(409,'The quotation is no longer awaiting payment for this customer choice.')
            try:
                r.OdooClient(r.get_store(case['store_id'])).execute('sale.order','after_order_retry_quote_email',
                    [[case['odoo_order_id']],row['selection']['result']['quote_id']])
            except Exception as exc:
                raise HTTPException(409,r.clean_error_message(exc)) from exc
            conn.execute("UPDATE after_order_messages SET status='retrying',attempt_count=attempt_count+1,updated_at=? WHERE id=?",(r.utc_now(),message['id']))
            self.event(conn,case,'quotation_email_retry_queued',row['line_id'],actor='team',message_id=message['id'])
        return {'ok':True,'message':'Odoo quotation email retry queued. Its mail worker will send it.'}

    def choose(self, case, link, line_id, template_id):
        r = self.r
        case, line = self.case_line(case['id'], line_id)
        test_mode = bool(link.get('test_mode')) or r.after_order_email_test_mode()
        if not self.ready(case):
            raise HTTPException(409, 'Our team is preparing the current alternatives.')
        recommended = [p for offer in self.rows(case['id'],test_mode) if offer['line_id'] == line_id
                       for p in offer['recommendations'] if p['product_tmpl_id'] == template_id]
        product = self.product(case, line, reference=recommended[0]['default_code']) if recommended else self.product(case,line,template_id=template_id)
        if product['product_tmpl_id'] != template_id or (recommended and product['product_id'] != recommended[0]['product_id']):
            raise HTTPException(409,'The recommended reference now points to a different product. Please ask our team to refresh the alternatives.')
        now = datetime.now(timezone.utc)
        with r.db() as conn:
            current = conn.execute('SELECT * FROM after_order_cases WHERE id=? FOR UPDATE', (case['id'],)).fetchone()
            if current['confirmed_at']:
                raise HTTPException(410, 'This request has already been confirmed.')
            fresh_link = conn.execute('SELECT * FROM after_order_action_links WHERE id=?', (link['id'],)).fetchone()
            if not fresh_link or fresh_link['invalidated_at']:
                raise HTTPException(410, 'This link has expired.')
            fresh, _ = self.case_line(case['id'], line_id)
            if r.request_fingerprint(fresh) != r.request_fingerprint(case):
                raise HTTPException(409, 'This order changed. Reload before choosing.')
            prior = conn.execute('SELECT * FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=?',
                (case['id'],line_id,int(test_mode))).fetchone()
            try:
                change = selection_change(dict(prior) if prior else None, product, now)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            conn.execute('''INSERT INTO after_order_line_selections
                (case_id,line_id,test_mode,version,product_json,first_selected_at,deadline_at,status,issue_fingerprint,updated_at)
                VALUES(?,?,?,?,?,?,?,'choosing',?,?) ON CONFLICT(case_id,line_id,test_mode) DO UPDATE SET
                version=excluded.version,product_json=excluded.product_json,updated_at=excluded.updated_at''',
                (case['id'],line_id,int(test_mode),change['version'],json.dumps(product),change['first_selected_at'],change['deadline_at'],r.request_fingerprint(case),r.utc_now()))
            if not test_mode:
                conn.execute("UPDATE after_order_cases SET current_decision='offer_alternatives', decision_version=decision_version+1, decision_updated_at=?, status='needs_confirmation', decision_fingerprint=?, updated_at=? WHERE id=?",
                    (r.utc_now(),r.request_fingerprint(case),r.utc_now(),case['id']))
            self.event(conn, case, 'alternative_changed' if prior else 'alternative_selected', line_id,
                actor='customer', test_mode=test_mode, product_name=product['name'], product_id=product['product_id'],
                previous_product=json.loads(prior['product_json'])['name'] if prior else None,
                deadline_at=change['deadline_at'], difference=product.get('difference'), currency=product['currency'],pricing_error=product.get('pricing_error'))
        return True, case, ('Test selection saved. No Odoo order, payment or fulfilment changes. ' if test_mode else '') + 'Your selection is saved. You can change it until ' + change['deadline_at'] + '.'

    def process(self, case_id, line_id):
        r = self.r
        if r.after_order_email_test_mode():
            return {'status': 'test_mode', 'message': 'No financial or fulfilment actions in test mode.'}
        # Hold a cross-worker lock throughout this bounded operation. Odoo commits
        # independently; its unique operation key makes a lost RPC response safe.
        with r.db() as conn:
            conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE', (case_id,)).fetchone()
            row = conn.execute('SELECT * FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=0 FOR UPDATE', (case_id,line_id)).fetchone()
            if not row or row['status'] not in ('choosing','waiting_payment','processing'):
                return
            if moment(row['deadline_at']) > datetime.now(timezone.utc):
                return
            case = r.after_order_case_by_id(case_id)
            try:
                case, line = self.case_line(case_id, line_id)
                if case['current_decision'] != 'offer_alternatives' or r.request_fingerprint(case) != row['issue_fingerprint']:
                    raise ValueError('Order or customer request changed. Team review required.')
                product = json.loads(row['product_json'])
                if row['status'] == 'choosing':
                    self.event(conn,case,'selection_window_closed',line_id,deadline_at=row['deadline_at'],product_name=product['name'])
                current = self.product(case,line,reference=product['default_code'])
                if product.get('pricing_error') or current.get('pricing_error'):
                    raise ValueError(product.get('pricing_error') or current['pricing_error'])
                if price_fingerprint(current) != price_fingerprint(product):
                    raise ValueError('The product price or paid amount changed after selection. Team review required.')
                asin = resolved_asin(current,r.extract_asin_from_notes,r.decode_asin_reference,r.normalize_asin)
                odoo = r.OdooClient(r.get_store(case['store_id']))
                if r.after_order_email_test_mode():
                    return
                result = odoo.execute('sale.order','after_order_process_alternative', [[case['odoo_order_id']],
                    int(line['odoo_line_id']),int(case['website_id']), product['product_id'],
                    f'care:{case_id}:line:{line_id}:v:{row["version"]}',row['deadline_at'],product['pricing_signature']])
                self.log_quote_email(conn,case,line_id,result)
                previous_result = json.loads(row['result_json'])
                status = result['status']
                if status == 'ready':
                    status = 'ready_to_release'
                    result['asin'] = asin
                if result != previous_result or row['status'] != status:
                    self.event(conn,case,'alternative_' + status,line_id,**result)
                conn.execute('''UPDATE after_order_line_selections SET status=?, result_json=?, refund_status=?, last_error=NULL,updated_at=?
                    WHERE case_id=? AND line_id=? AND test_mode=0''',
                    (status,json.dumps(result),'pending_review' if product['difference'] < 0 else '',r.utc_now(),case_id,line_id))
            except Exception as exc:
                error = r.clean_error_message(exc)
                conn.execute("UPDATE after_order_line_selections SET status='needs_review',last_error=?,updated_at=? WHERE case_id=? AND line_id=? AND test_mode=0", (error,r.utc_now(),case_id,line_id))
                self.event(conn,case,'alternative_processing_failed',line_id,error=error)
                try:
                    r.OdooClient(r.get_store(case['store_id'])).post_order_note(case['odoo_order_id'],
                        'After-order alternative / refund review error for line %s: %s' % (line_id,r.html.escape(error)))
                except Exception as note_error:
                    self.event(conn,case,'odoo_chatter_failed',line_id,error=r.clean_error_message(note_error))

    def run_due(self):
        r = self.r
        if r.after_order_email_test_mode():
            return
        with r.db() as conn:
            due = [dict(row) for row in conn.execute("SELECT case_id,line_id FROM after_order_line_selections WHERE test_mode=0 AND status IN ('choosing','waiting_payment','processing') AND deadline_at<=? ORDER BY updated_at LIMIT 50",(r.utc_now(),)).fetchall()]
            ready = [dict(row) for row in conn.execute("SELECT DISTINCT case_id FROM after_order_line_selections WHERE test_mode=0 AND status='ready_to_release' LIMIT 200").fetchall()]
        for row in due:
            self.process(row['case_id'],row['line_id'])
        for case_id in {row['case_id'] for row in due+ready}:
            self.release(case_id)
        self.refresh_quote_emails()

    def refresh_quote_emails(self):
        r = self.r
        with r.db() as conn:
            messages = [dict(row) for row in conn.execute("SELECT * FROM after_order_messages WHERE provider='odoo' AND status IN ('sending','retrying','delivery_unknown') ORDER BY updated_at LIMIT 50").fetchall()]
        for message in messages:
            case = r.after_order_case_by_id(message['case_id'])
            try:
                mails = r.OdooClient(r.get_store(case['store_id'])).read('mail.mail',[int(message['provider_message_id'])],['state','failure_reason'])
                if not mails:
                    continue
                mail = mails[0]
                snapshot = json.loads(message.get('related_items_json') or '[]')
                line_id = snapshot[0]['line_id'] if snapshot else None
                result = {'quote_id':int(message['idempotency_key'].rsplit(':',1)[-1]),'mail':{
                    'id':int(message['provider_message_id']),'recipient':message['recipient'],'sender':message['sender'],
                    'subject':message['subject'],'state':mail['state'],'error':mail.get('failure_reason') or ''}}
                with r.db() as conn:
                    self.log_quote_email(conn,case,line_id,result)
                    status = {'outgoing':'sending','sent':'sent','exception':'failed','cancel':'failed'}.get(mail['state'],'delivery_unknown')
                    if status != message['status']:
                        self.event(conn,case,'quotation_email_'+status,line_id,message_id=message['id'],error=mail.get('failure_reason') or '')
            except Exception:
                # Unknown delivery is not a confirmed failure and cannot trigger a resend.
                continue

    def release(self, case_id):
        r = self.r
        if r.after_order_email_test_mode():
            return
        with r.db() as conn:
            conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE',(case_id,)).fetchone()
            case = r.after_order_case_by_id(case_id)
            if not case or case['current_decision'] != 'offer_alternatives' or case.get('confirmed_at'):
                return
            selections = [dict(row) for row in conn.execute('SELECT * FROM after_order_line_selections WHERE case_id=? AND test_mode=0',(case_id,)).fetchall()]
            affected = {int(item['line_id']) for item in case['affected_items']}
            if not affected or {s['line_id'] for s in selections} != affected or any(s['status'] != 'ready_to_release' for s in selections):
                return
            if r.after_order_unavailable_review(case)['blocked'] or any(s['issue_fingerprint'] != r.request_fingerprint(case) for s in selections):
                return
            conn.execute('SELECT id FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id FOR UPDATE',
                (case['store_id'],case['odoo_order_id'])).fetchall()
            if r.after_order_unavailable_review(case)['blocked']:
                return
            # A payment can be reversed while another line is still being chosen.
            # Recheck every quote immediately before releasing the order together.
            for selection in selections:
                product = json.loads(selection['product_json'])
                _, line = self.case_line(case_id,selection['line_id'])
                try:
                    verified = r.OdooClient(r.get_store(case['store_id'])).execute('sale.order','after_order_process_alternative',
                        [[case['odoo_order_id']],int(line['odoo_line_id']),int(case['website_id']),product['product_id'],
                         f'care:{case_id}:line:{selection["line_id"]}:v:{selection["version"]}',selection['deadline_at'],product['pricing_signature']])
                    if verified['status'] != 'ready':
                        conn.execute("UPDATE after_order_line_selections SET status='waiting_payment',result_json=? WHERE case_id=? AND line_id=? AND test_mode=0",(json.dumps(verified),case_id,selection['line_id']))
                        return
                except Exception as exc:
                    self.event(conn,case,'release_check_failed',selection['line_id'],error=r.clean_error_message(exc))
                    return
            if r.after_order_email_test_mode():
                return
            for selection in selections:
                product = json.loads(selection['product_json'])
                result = json.loads(selection['result_json'])
                asin = result.get('asin','')
                conn.execute('''UPDATE order_lines SET original_asin=COALESCE(NULLIF(original_asin,''),asin),
                    original_product_name=COALESCE(NULLIF(original_product_name,''),product_name),
                    replacement_asin=?,replacement_product_name=?,replacement_note=?,replacement_assigned_at=?,
                    asin=?,product_name=?,state=?,missing_asin=NULL,amazon_status=NULL,amazon_group_key=NULL,
                    chrome_claimed_by=NULL,chrome_claimed_at=NULL,chrome_claim_expires_at=NULL,
                    last_error=?,updated_at=?,order_engine=CASE WHEN ?='' THEN 'manual_amazon' ELSE order_engine END WHERE id=? AND store_id=?''',
                    (asin,product['name'],'Customer alternative: 24-hour window complete',r.utc_now(),asin,product['name'],
                    'pulled' if asin else 'missing',None if asin else 'Customer alternative needs manual fulfilment: no ASIN found.',r.utc_now(),asin,selection['line_id'],case['store_id']))
                status = 'processed' if asin else 'manual_fulfilment'
                conn.execute('UPDATE after_order_line_selections SET status=?,updated_at=? WHERE case_id=? AND line_id=? AND test_mode=0',
                    (status,r.utc_now(),case_id,selection['line_id']))
                self.event(conn,case,'alternative_'+status,selection['line_id'],product_name=product['name'],asin=asin,
                    refund_amount=abs(min(product['difference'],0)))
            attention = any(not json.loads(s['result_json']).get('asin') or json.loads(s['product_json'])['difference'] < 0 for s in selections)
            conn.execute("UPDATE after_order_cases SET status=?,confirmed_at=?,confirmed_by='24-hour alternative processing',decision_locked_at=?,updated_at=? WHERE id=?",('execution_needs_review' if attention else 'resolved',r.utc_now(),r.utc_now(),r.utc_now(),case_id))
            conn.execute('UPDATE after_order_action_links SET invalidated_at=?,updated_at=? WHERE case_id=?',(r.utc_now(),r.utc_now(),case_id))
        r.fast_page_cache_clear_matching({'orders','missing','dashboard','bulk'})
        try:
            with r.db() as conn:
                lines = conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=?',(case['store_id'],case['odoo_order_id'])).fetchall()
            for line in lines:
                r.index_order_line(line)
            if not r.after_order_email_test_mode() and r.auto_chrome_ordering_enabled() and all(json.loads(s['result_json']).get('asin') for s in selections):
                count, message = r.auto_queue_ready_missing_order(case['store_id'],case['odoo_order_id'])
                with r.db() as conn:
                    self.event(conn,case,'fulfilment_queue_checked',queued=count,message=message)
        except Exception as exc:
            with r.db() as conn:
                self.event(conn,case,'fulfilment_queue_needs_review',error=r.clean_error_message(exc))
                conn.execute("UPDATE after_order_cases SET status='execution_needs_review' WHERE id=?",(case_id,))

    def router(self):
        router = APIRouter()

        @router.get('/api/after-order/alternative-readiness')
        def readiness(store_id: int):
            r = self.r
            try:
                odoo = r.OdooClient(r.get_store(store_id))
                modules = odoo.search_read('ir.module.module',[('name','=','after_order_portal')],['state','installed_version'],limit=1)
                fields = odoo.fields_get('sale.order')
                schema_ready = 'after_order_operation_key' in fields
                if schema_ready:
                    odoo.search_read('sale.order',[],['after_order_operation_key','after_order_pricing_signature'],limit=1)
                return {'ok':True,'module':modules[0] if modules else {},'schema_ready':schema_ready,
                    'app_test_mode':r.after_order_email_test_mode(),
                    'odoo_live_enabled':odoo.execute('ir.config_parameter','get_param',['after_order_portal.live_alternatives_enabled','false']) == 'true',
                    'message':'Schema detected; isolated payment and outgoing-mail tests are still required.' if schema_ready else 'Upgrade the Odoo addon to 18.0.2.0.0 before using line alternatives.'}
            except Exception as exc:
                return {'ok':False,'schema_ready':False,'app_test_mode':r.after_order_email_test_mode(),
                    'message':r.clean_error_message(exc)}

        @router.get('/api/after-order/cases/{case_id}/line-alternatives')
        def offers(case_id: int):
            case = self.r.after_order_case_by_id(case_id)
            if not case:
                raise HTTPException(404,'Case not found.')
            self.r.require_after_order_case_in_scope(case)
            return {'rows': self.rows(case_id)}

        @router.get('/api/after-order/lines/{line_id}/case')
        def line_case(line_id: int):
            with self.r.db() as conn:
                line = conn.execute('SELECT store_id,odoo_order_id FROM order_lines WHERE id=?',(line_id,)).fetchone()
            if not line:
                raise HTTPException(404,'Order line not found.')
            self.r.sync_after_order_cases(line['store_id'])
            with self.r.db() as conn:
                case = conn.execute("SELECT id FROM after_order_cases WHERE store_id=? AND odoo_order_id=? AND case_type='item_unavailable' AND confirmed_at IS NULL ORDER BY id DESC LIMIT 1",(line['store_id'],line['odoo_order_id'])).fetchone()
            if not case:
                raise HTTPException(409,'No active unavailable-item case. Check the line status and order cutoff.')
            self.case_line(case['id'],line_id)
            return {'case_id':case['id'],'rows':self.rows(case['id'])}

        @router.post('/api/after-order/cases/{case_id}/lines/{line_id}/alternatives')
        def publish(case_id: int,line_id: int,payload: Recommendations,request: Request):
            return self.publish(case_id,line_id,payload,request)

        return router
