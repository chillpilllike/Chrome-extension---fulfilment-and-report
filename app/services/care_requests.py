"""Per-line customer requests and notification-based response deadlines.

Timeouts create durable work, not fake customer consent or unverified refunds.
All transitions serialize on the same case lock as alternative choices.
"""
import json
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from app.services.alternative_selection import moment

SCHEMA = """
CREATE TABLE IF NOT EXISTS after_order_response_windows (
 case_id INTEGER NOT NULL REFERENCES after_order_cases(id) ON DELETE CASCADE,
 line_id INTEGER NOT NULL, issue_fingerprint TEXT NOT NULL,
 message_id INTEGER NOT NULL, delivered_at TEXT NOT NULL, deadline_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'open', outcome TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 PRIMARY KEY(case_id,line_id,issue_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_care_response_due ON after_order_response_windows(state,deadline_at);
CREATE TABLE IF NOT EXISTS after_order_deadline_receipt_checks (
 message_id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_order_line_removals (
 case_id INTEGER NOT NULL REFERENCES after_order_cases(id) ON DELETE CASCADE,
 line_id INTEGER NOT NULL, test_mode INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 issue_fingerprint TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL,
 approved_by TEXT, approved_at TEXT, result_json TEXT NOT NULL DEFAULT '{}',
 updated_at TEXT NOT NULL, PRIMARY KEY(case_id,line_id,test_mode)
);
CREATE TABLE IF NOT EXISTS after_order_parcel_mapping (
 case_id INTEGER NOT NULL REFERENCES after_order_cases(id) ON DELETE CASCADE,
 line_id INTEGER NOT NULL, quantity REAL NOT NULL CHECK(quantity>0),
 verified_by TEXT NOT NULL, verified_at TEXT NOT NULL, PRIMARY KEY(case_id,line_id)
);
"""


class ParcelItem(BaseModel):
    line_id: int
    quantity: float = Field(gt=0, allow_inf_nan=False)


class ParcelMapping(BaseModel):
    items: list[ParcelItem] = Field(min_length=1,max_length=100)
    verified_by: str = Field(min_length=1,max_length=100)


class Approval(BaseModel):
    version: int
    approved_by: str = Field(min_length=1,max_length=100)


class Requests:
    def __init__(self, namespace):
        from app.services.alternative_workflow import Runtime
        self.r = Runtime(namespace)

    def rows(self, case_id, test_mode=None):
        r = self.r
        mode = r.after_order_email_test_mode() if test_mode is None else test_mode
        with r.db() as conn:
            case = r.after_order_case_by_id(case_id)
            parcel_items = [dict(x) for x in conn.execute('''SELECT * FROM after_order_parcel_mapping WHERE case_id=(
                SELECT MAX(p.case_id) FROM after_order_parcel_mapping p JOIN after_order_cases c ON c.id=p.case_id
                WHERE c.store_id=? AND c.odoo_order_id=? AND c.tracking_code=?)''',
                (case['store_id'],case['odoo_order_id'],case.get('tracking_code'))).fetchall()] if case and case.get('tracking_code') else []
            return {'removals':[dict(x) for x in conn.execute('SELECT * FROM after_order_line_removals WHERE case_id=? AND test_mode=?',(case_id,int(mode))).fetchall()],
                    'deadlines':[dict(x) for x in conn.execute('SELECT * FROM after_order_response_windows WHERE case_id=?',(case_id,)).fetchall()],
                    'parcel_items':parcel_items}

    def response_closed(self, conn, case, line_id):
        row = conn.execute('SELECT * FROM after_order_response_windows WHERE case_id=? AND line_id=? AND issue_fingerprint=?',
                           (case['id'],line_id,self.r.request_fingerprint(case))).fetchone()
        return bool(row and (row['state']=='expired' or moment(row['deadline_at']) <= datetime.now(timezone.utc)))

    def remove(self, case, link, line_id):
        r = self.r
        mode = bool(link.get('test_mode')) or r.after_order_email_test_mode()
        case, line = r.alternative_workflow.case_line(case['id'],line_id)
        with r.db() as conn:
            locked = conn.execute('SELECT * FROM after_order_cases WHERE id=? FOR UPDATE',(case['id'],)).fetchone()
            fresh = conn.execute('SELECT * FROM after_order_action_links WHERE id=?',(link['id'],)).fetchone()
            if locked['confirmed_at'] or not fresh or fresh['invalidated_at'] or (not mode and fresh['request_fingerprint'] != r.request_fingerprint(case)):
                raise HTTPException(409,'This request changed or was confirmed. Reload the current order.')
            prior = conn.execute('SELECT * FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=?',(case['id'],line_id,int(mode))).fetchone()
            if prior and (prior['status']!='choosing' or moment(prior['deadline_at'])<=datetime.now(timezone.utc)):
                raise HTTPException(409,'This line is locked for processing.')
            if not mode and not prior and self.response_closed(conn,case,line_id):
                raise HTTPException(409,'The response deadline has passed. Contact our team.')
            removal = conn.execute('SELECT * FROM after_order_line_removals WHERE case_id=? AND line_id=? AND test_mode=?',(case['id'],line_id,int(mode))).fetchone()
            if removal and removal['approved_at']:
                raise HTTPException(409,'This removal was already approved.')
            conn.execute('''INSERT INTO after_order_line_removals(case_id,line_id,test_mode,issue_fingerprint,origin,status,updated_at)
                VALUES(?,?,?,?,'customer','needs_approval',?) ON CONFLICT(case_id,line_id,test_mode)
                DO UPDATE SET version=after_order_line_removals.version+1, status='needs_approval',updated_at=excluded.updated_at''',
                (case['id'],line_id,int(mode),r.request_fingerprint(case),r.utc_now()))
            # Preserve the original 24-hour clock if the customer returns to an alternative.
            if prior:
                conn.execute("UPDATE after_order_line_selections SET status='withdrawn',updated_at=? WHERE case_id=? AND line_id=? AND test_mode=?",(r.utc_now(),case['id'],line_id,int(mode)))
            if not mode:
                conn.execute("UPDATE after_order_cases SET current_decision='offer_alternatives',status='needs_confirmation',decision_version=decision_version+1,decision_fingerprint=?,updated_at=? WHERE id=?",(r.request_fingerprint(case),r.utc_now(),case['id']))
            r.alternative_workflow.event(conn,case,'line_removal_requested',line_id,actor='customer',test_mode=mode,product_name=line['product_name'])
        return True,case,'Item removal requested for team approval. No refund has been issued.'

    def delivered(self, message, delivered_at):
        r = self.r
        if message['test_mode'] or message['template_kind']!='item_unavailable':
            return
        # Old emails did not explain this policy and cannot create a deadline.
        payload = json.loads(message.get('payload_json') or '{}')
        if payload.get('_care_policy') != 'three-day-v1':
            return
        stamp = moment(delivered_at)
        if stamp > datetime.now(timezone.utc):
            return
        case = r.after_order_case_by_id(message['case_id'])
        if not case or not r.after_order_case_is_in_scope(case) or message['request_fingerprint']!=r.request_fingerprint(case):
            return
        with r.db() as conn:
            locked = conn.execute('SELECT confirmed_at FROM after_order_cases WHERE id=? FOR UPDATE',(case['id'],)).fetchone()
            if locked['confirmed_at']:
                return
            for item in case['affected_items']:
                cursor = conn.execute('''INSERT INTO after_order_response_windows
                    (case_id,line_id,issue_fingerprint,message_id,delivered_at,deadline_at,updated_at)
                    VALUES(?,?,?,?,?,?,?) ON CONFLICT(case_id,line_id,issue_fingerprint) DO NOTHING''',
                    (case['id'],int(item['line_id']),r.request_fingerprint(case),message['id'],stamp.isoformat(),(stamp+timedelta(days=3)).isoformat(),r.utc_now()))
                if cursor.rowcount:
                    r.alternative_workflow.event(conn,case,'response_deadline_started',int(item['line_id']),deadline_at=(stamp+timedelta(days=3)).isoformat(),message_id=message['id'])

    def run_due(self):
        r = self.r
        if r.after_order_email_test_mode():
            return
        with r.db() as conn:
            due = [dict(x) for x in conn.execute("SELECT * FROM after_order_response_windows WHERE state='open' AND deadline_at<=? ORDER BY deadline_at LIMIT 200",(r.utc_now(),)).fetchall()]
        for window in due:
            case = r.after_order_case_by_id(window['case_id'])
            if not case:
                continue
            with r.db() as conn:
                locked = conn.execute('SELECT * FROM after_order_cases WHERE id=? FOR UPDATE',(case['id'],)).fetchone()
                case = r.after_order_case_by_id(case['id'])
                fresh = conn.execute('SELECT state FROM after_order_response_windows WHERE case_id=? AND line_id=? AND issue_fingerprint=?',(case['id'],window['line_id'],window['issue_fingerprint'])).fetchone()
                if fresh['state']!='open':
                    continue
                outcome = 'no_response'
                selected = conn.execute("SELECT 1 FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=0 AND status!='withdrawn'",(case['id'],window['line_id'])).fetchone()
                removal = conn.execute("SELECT 1 FROM after_order_line_removals WHERE case_id=? AND line_id=? AND test_mode=0 AND status!='withdrawn'",(case['id'],window['line_id'])).fetchone()
                if selected or removal or (locked['current_decision'] and locked['current_decision']!='offer_alternatives'):
                    outcome = 'customer_responded'
                elif locked['confirmed_at'] or window['issue_fingerprint']!=r.request_fingerprint(case) or not r.after_order_case_is_in_scope(case) or r.after_order_unavailable_review(case)['blocked']:
                    outcome = 'issue_changed'
                elif r.care_delivery.suppressed(case.get('customer_email') or '',False):
                    outcome = 'delivery_needs_review'
                else:
                    partial = r.after_order_removal_allowed(case) or bool(conn.execute("SELECT 1 FROM after_order_line_selections WHERE case_id=? AND test_mode=0 AND status NOT IN ('withdrawn','needs_review')",(case['id'],)).fetchone())
                    outcome = 'partial_removal_refund_review' if partial else 'whole_order_cancel_refund_review'
                    conn.execute('''INSERT INTO after_order_line_removals(case_id,line_id,test_mode,issue_fingerprint,origin,status,updated_at)
                        VALUES(?,?,0,?,'no_response',?,?) ON CONFLICT(case_id,line_id,test_mode) DO NOTHING''',
                        (case['id'],window['line_id'],window['issue_fingerprint'],'auto_remove_pending' if partial else 'cancel_review',r.utc_now()))
                    if partial:
                        conn.execute("UPDATE after_order_cases SET current_decision='offer_alternatives',decision_fingerprint=?,status='execution_needs_review',updated_at=? WHERE id=?",(r.request_fingerprint(case),r.utc_now(),case['id']))
                    else:
                        conn.execute("UPDATE after_order_cases SET status='execution_needs_review',updated_at=? WHERE id=?",(r.utc_now(),case['id']))
                conn.execute('UPDATE after_order_response_windows SET state=?,outcome=?,updated_at=? WHERE case_id=? AND line_id=? AND issue_fingerprint=?',
                    ('expired' if outcome.endswith('review') else 'closed',outcome,r.utc_now(),case['id'],window['line_id'],window['issue_fingerprint']))
                r.alternative_workflow.event(conn,case,'response_deadline_closed',window['line_id'],outcome=outcome)
        with r.db() as conn:
            ready = conn.execute("SELECT case_id FROM after_order_line_removals WHERE test_mode=0 AND status IN ('auto_remove_pending','finance_review') GROUP BY case_id ORDER BY MIN(updated_at),case_id LIMIT 200").fetchall()
            for candidate in ready:
                conn.execute("UPDATE after_order_line_removals SET updated_at=? WHERE case_id=? AND test_mode=0 AND status IN ('auto_remove_pending','finance_review')",(r.utc_now(),candidate['case_id']))
        for row in ready:
            r.alternative_workflow.release(row['case_id'])

    def sync_delivered(self):
        r = self.r
        with r.db() as conn:
            rows = [dict(x) for x in conn.execute('''SELECT m.*,MIN(e.occurred_at) AS delivered_at
                FROM after_order_messages m JOIN after_order_delivery_events e ON e.provider_message_id=m.provider_message_id
                WHERE m.test_mode=0 AND m.template_kind='item_unavailable' AND m.status='delivered'
                AND m.payload_json LIKE '%three-day-v1%' AND e.event_type='email.delivered'
                AND NOT EXISTS(SELECT 1 FROM after_order_deadline_receipt_checks c WHERE c.message_id=m.id)
                GROUP BY m.id ORDER BY m.id LIMIT 200''').fetchall()]
        for message in rows:
            self.delivered(message,message['delivered_at'])
            # A stale issue is also a completed check. Otherwise ignored receipts
            # permanently occupy the first batch and starve newer notifications.
            with r.db() as conn:
                conn.execute('INSERT INTO after_order_deadline_receipt_checks(message_id,checked_at) VALUES(?,?) ON CONFLICT(message_id) DO NOTHING',
                             (message['id'],r.utc_now()))

    def router(self):
        router = APIRouter()
        r = self.r
        @router.get('/api/after-order/cases/{case_id}/requests')
        def rows(case_id: int):
            case = r.after_order_case_by_id(case_id)
            if not case:
                raise HTTPException(404,'Case not found.')
            r.require_after_order_case_in_scope(case)
            with r.db() as conn:
                candidates = [dict(x) for x in conn.execute('SELECT id,product_name,quantity FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id',(case['store_id'],case['odoo_order_id'])).fetchall()] if case.get('tracking_code') else []
            return {'ok':True,'tracking_code':case.get('tracking_code'), 'mapping_candidates':candidates,**self.rows(case_id)}
        @router.post('/api/after-order/cases/{case_id}/parcel-items')
        def map_parcel(case_id: int, payload: ParcelMapping):
            case = r.after_order_case_by_id(case_id)
            if not case or not case.get('tracking_code'):
                raise HTTPException(404,'Tracking case not found.')
            r.require_after_order_case_in_scope(case)
            if len({x.line_id for x in payload.items}) != len(payload.items):
                raise HTTPException(400,'Duplicate parcel lines.')
            with r.db() as conn:
                locked = conn.execute('SELECT * FROM after_order_cases WHERE id=? FOR UPDATE',(case_id,)).fetchone()
                if locked['confirmed_at']:
                    raise HTTPException(409,'Confirmed parcel mapping cannot be changed.')
                lines = {x['id']:dict(x) for x in conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id FOR UPDATE',(case['store_id'],case['odoo_order_id'])).fetchall()}
                for item in payload.items:
                    line = lines.get(item.line_id)
                    if not line or item.quantity > float(line['quantity'] or 0):
                        raise HTTPException(409,'Parcel quantity exceeds this order line or belongs to another order.')
                    allocated = conn.execute('''SELECT COALESCE(SUM(p.quantity),0) AS quantity FROM after_order_parcel_mapping p
                        JOIN after_order_cases c ON c.id=p.case_id WHERE c.store_id=? AND c.odoo_order_id=?
                        AND p.line_id=? AND c.tracking_code!=? AND p.case_id=(
                            SELECT MAX(p2.case_id) FROM after_order_parcel_mapping p2 JOIN after_order_cases c2 ON c2.id=p2.case_id
                            WHERE c2.store_id=c.store_id AND c2.odoo_order_id=c.odoo_order_id AND c2.tracking_code=c.tracking_code)''',
                        (case['store_id'],case['odoo_order_id'],item.line_id,case['tracking_code'])).fetchone()['quantity']
                    if float(allocated)+item.quantity > float(line['quantity'] or 0):
                        raise HTTPException(409,'This quantity is already allocated to another parcel.')
                if r.after_order_email_test_mode():
                    return {'ok':True,'test_mode':True,'message':'Test preview: parcel quantities validated. No live mapping or customer decision was changed.'}
                conn.execute('DELETE FROM after_order_parcel_mapping WHERE case_id=?',(case_id,))
                for item in payload.items:
                    conn.execute('INSERT INTO after_order_parcel_mapping(case_id,line_id,quantity,verified_by,verified_at) VALUES(?,?,?,?,?)',
                        (case_id,item.line_id,item.quantity,payload.verified_by,r.utc_now()))
                conn.execute('UPDATE after_order_action_links SET invalidated_at=? WHERE case_id=?',(r.utc_now(),case_id))
                conn.execute("UPDATE after_order_cases SET current_decision=NULL,decision_fingerprint=NULL,decision_version=decision_version+1 WHERE id=?",(case_id,))
                r.alternative_workflow.event(conn,case,'parcel_items_verified',actor='team',verified_by=payload.verified_by,items=[x.model_dump() for x in payload.items])
            r.sync_after_order_cases(case['store_id'])
            return {'ok':True,'message':'Parcel contents verified. A new customer decision is required for the updated parcel.'}
        @router.post('/api/after-order/cases/{case_id}/lines/{line_id}/approve-removal')
        def approve_removal(case_id: int, line_id: int, payload: Approval):
            if r.after_order_email_test_mode():
                return {'ok':True,'message':'Test preview: no approval, refund or fulfilment change was made.'}
            case,_ = r.alternative_workflow.case_line(case_id,line_id)
            with r.db() as conn:
                conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE',(case_id,)).fetchone()
                row = conn.execute('SELECT * FROM after_order_line_removals WHERE case_id=? AND line_id=? AND test_mode=0',(case_id,line_id)).fetchone()
                if not row or row['version']!=payload.version or row['status'] not in ('needs_approval','cancel_review') or row['issue_fingerprint']!=r.request_fingerprint(case):
                    raise HTTPException(409,'The request changed. Refresh before approving.')
                conn.execute("UPDATE after_order_line_removals SET approved_at=?,approved_by=?,status='finance_review',updated_at=? WHERE case_id=? AND line_id=? AND test_mode=0",
                    (r.utc_now(),payload.approved_by,r.utc_now(),case_id,line_id))
                r.alternative_workflow.event(conn,case,'line_removal_approved',line_id,actor='team',approved_by=payload.approved_by,version=payload.version)
            return {'ok':True,'message':'Removal approved. Verified refund completion is still required before partial-order release.'}
        @router.post('/api/after-order/cases/{case_id}/lines/{line_id}/retry-processing')
        def retry_processing(case_id: int, line_id: int, payload: Approval):
            if r.after_order_email_test_mode():
                return {'ok':True,'message':'Test preview: no processing retried.'}
            case,_ = r.alternative_workflow.case_line(case_id,line_id)
            with r.db() as conn:
                conn.execute('SELECT id FROM after_order_cases WHERE id=? FOR UPDATE',(case_id,)).fetchone()
                row = conn.execute('SELECT * FROM after_order_line_selections WHERE case_id=? AND line_id=? AND test_mode=0',(case_id,line_id)).fetchone()
                if not row or row['version']!=payload.version or row['status']!='needs_review' or row['issue_fingerprint']!=r.request_fingerprint(case) or case['current_decision']!='offer_alternatives':
                    raise HTTPException(409,'Only the same unchanged request may be retried. New pricing or products require new customer consent.')
                conn.execute("UPDATE after_order_line_selections SET status='processing',updated_at=? WHERE case_id=? AND line_id=? AND test_mode=0",(r.utc_now(),case_id,line_id))
                r.alternative_workflow.event(conn,case,'alternative_retry_requested',line_id,actor='team',approved_by=payload.approved_by,version=payload.version)
            return {'ok':True,'message':'Retry queued with the original operation key. Pricing, payment and fulfilment will be rechecked.'}
        return router
