"""Delivery follow-ups, independent of financial and general email automation.

Eligibility is rechecked at dispatch, not just when a draft was prepared.
Channel reservations survive cancellations, restarts and competing workers.
"""
import json
from datetime import datetime, timedelta, timezone

from app.services.alternative_workflow import Runtime
from app.services.delivery_checkin import carrier_moment, delivery_details

REVIEW = 'trustpilot_review'
ISSUE = 'delivery_issue_received'
KINDS = {REVIEW, ISSUE}


def receipt_correction(case):
    """A fresh portal link may correct non-delivery; never reopen financial choices."""
    return case.get('case_type') == 'delivery_confirmation' and case.get('current_decision') == 'not_received'


SCHEMA = '''CREATE TABLE IF NOT EXISTS after_order_followup_channels (
 notification_key TEXT PRIMARY KEY, message_id INTEGER NOT NULL,
 created_at TEXT NOT NULL
);'''


def moment(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except (ValueError, TypeError):
        return None


def eligible_kind(decision, decided_at, delivered_at, now=None):
    now = now or datetime.now(timezone.utc)
    if decision == 'not_received':
        return ISSUE
    if decision == 'received':
        decided = moment(decided_at)
        return REVIEW if decided and decided <= now else None
    if decision:
        return None
    delivered = carrier_moment(delivered_at) if delivered_at else None
    return REVIEW if delivered and delivered + timedelta(days=5) <= now else None


class Monitor:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def state(self, case):
        r = self.r
        with r.db() as conn:
            rows = [dict(x) for x in conn.execute('''SELECT * FROM after_order_cases
                WHERE store_id=? AND odoo_order_id=? AND case_type IN ('delivery_confirmation','tracking')
                ORDER BY decision_updated_at DESC NULLS LAST, id DESC''',
                (case['store_id'], case['odoo_order_id'])).fetchall()]
            tracking = conn.execute('SELECT events_json,status FROM epost_global_tracking WHERE store_id=? AND tracking_code=?',
                                    (case['store_id'], case.get('tracking_code') or '')).fetchone()
        # A negative response on another parcel must never be hidden by a positive one.
        latest_parcels = {}
        latest_answers = {}
        for row in rows:  # Query is ordered by decision time, not carrier case creation.
            if row.get('current_decision'):
                latest_answers.setdefault(row.get('tracking_code') or str(row['id']), row)
        for row in sorted(rows, key=lambda x: x['id'], reverse=True):
            latest_parcels.setdefault(row.get('tracking_code') or str(row['id']), row)
        rows = [row for row in rows if row['id'] in {x['id'] for x in latest_parcels.values()}]
        answers = list(latest_answers.values())
        negative = next((x for x in answers if x.get('current_decision') == 'not_received'), None)
        answer = negative or next(iter(answers), rows[0] if rows else case)
        if negative:
            return ISSUE, answer
        delivered_at = ''
        if tracking and r.tracking_risk(r.after_order_json_list(tracking['events_json']), status=tracking['status']).state == 'delivered':
            delivered_at = delivery_details(r.after_order_json_list(tracking['events_json'])).get('delivery_datetime')
        kind = eligible_kind(answer.get('current_decision'), answer.get('decision_updated_at'), delivered_at)
        # Do not infer whole-order delivery from just one of several known parcels.
        if kind == REVIEW and not answer.get('current_decision'):
            for row in rows:
                if (json.loads(row.get('context_json') or '{}')).get('risk_state') != 'delivered':
                    return None, answer
                with r.db() as conn:
                    parcel = conn.execute('SELECT events_json,status FROM epost_global_tracking WHERE store_id=? AND tracking_code=?',
                                         (case['store_id'], row.get('tracking_code') or '')).fetchone()
                if not parcel or r.tracking_risk(r.after_order_json_list(parcel['events_json']), status=parcel['status']).state != 'delivered':
                    return None, answer
                date = delivery_details(r.after_order_json_list(parcel['events_json'])).get('delivery_datetime')
                if eligible_kind('', None, date) != REVIEW:
                    return None, answer
        return kind, answer

    def validate(self, case, kind):
        self.r.require_after_order_case_in_scope(case)
        if case.get('case_type') != 'delivery_confirmation' or kind not in KINDS:
            raise ValueError('Delivery follow-up requires a delivery confirmation case.')
        eligible, _ = self.state(case)
        if eligible != kind:
            raise ValueError('Delivery response changed or the five-day review deadline has not elapsed.')
        if kind == REVIEW and self.r.after_order_tracking_updates_opted_out(case, case.get('customer_email') or ''):
            raise ValueError('Customer opted out of review notifications.')

    def key(self, case, kind, channel):
        return f"delivery-followup:{case['store_id']}:{case['odoo_order_id']}:{kind}:{channel}"

    def reserve(self, conn, case, kind, channel, message_id):
        """Called in the transaction which marks an outbox row as sending."""
        self.validate(case, kind)
        key = self.key(case, kind, channel)
        table, attempts = ('after_order_messages', 'attempt_count') if channel == 'email' else ('after_order_sms', 'attempts')
        conn.execute('INSERT INTO after_order_followup_channels(notification_key,message_id,created_at) VALUES(?,?,?) ON CONFLICT(notification_key) DO NOTHING',
                     (key, message_id, self.r.utc_now()))
        owner = conn.execute('SELECT message_id FROM after_order_followup_channels WHERE notification_key=? FOR UPDATE', (key,)).fetchone()
        if owner['message_id'] != message_id:
            old = conn.execute(f'SELECT {attempts} AS attempts,status FROM {table} WHERE id=?', (owner['message_id'],)).fetchone()
            if not old or old['attempts'] or old['status'] != 'cancelled':
                raise ValueError('This order already has a reserved or attempted delivery follow-up.')
            conn.execute('UPDATE after_order_followup_channels SET message_id=? WHERE notification_key=?', (message_id, key))
        # Include invitations sent before this feature was installed.
        if channel == 'email':
            prior = conn.execute('''SELECT 1 FROM after_order_messages m JOIN after_order_cases c ON c.id=m.case_id
                WHERE c.store_id=? AND c.odoo_order_id=? AND m.template_kind=? AND m.test_mode=0
                  AND m.attempt_count>0 AND m.id<>? LIMIT 1''', (case['store_id'], case['odoo_order_id'], kind, message_id)).fetchone()
            if prior:
                raise ValueError('A delivery follow-up was already attempted for this order; inspect its log before retrying.')

    def cancel_pending(self, conn, case):
        now = self.r.utc_now()
        ids = [row['id'] for row in conn.execute('''SELECT m.id FROM after_order_messages m
            JOIN after_order_cases c ON c.id=m.case_id WHERE c.store_id=? AND c.odoo_order_id=?
              AND m.template_kind=? AND m.test_mode=0''', (case['store_id'], case['odoo_order_id'], REVIEW)).fetchall()]
        for ident in ids:
            conn.execute("UPDATE after_order_messages SET status='cancelled',updated_at=? WHERE id=? AND status IN ('awaiting_approval','failed') AND attempt_count=0", (now, ident))
            conn.execute("UPDATE after_order_sms SET status='cancelled',updated_at=? WHERE email_id=? AND status='awaiting_approval' AND attempts=0", (now, ident))
        self.r.record_after_order_event(conn, case['id'], 'delivery_issue_team_notification',
            details={'message': 'Customer reports non-delivery. Investigate and contact the customer.', 'pending_review_cancelled': True})

    def run_checks(self, request):
        r = self.r
        if r.after_order_email_test_mode():
            return  # Never turn an automatic historical scan into owner-test spam.
        r.sync_after_order_cases()
        with r.db() as conn:
            ids = [x['id'] for x in conn.execute("SELECT id FROM after_order_cases WHERE case_type='delivery_confirmation' ORDER BY id").fetchall()]
        seen = set()
        for cid in ids:
            if r.after_order_email_test_mode():
                return
            try:
                case = r.after_order_case_by_id(cid)
                key = (case['store_id'], case['odoo_order_id'])
                if key in seen:
                    continue
                kind, answer = self.state(case)
                if not kind:
                    continue
                seen.add(key)
                case = r.after_order_case_by_id(answer['id'])
                self.validate(case, kind)
                with r.db() as conn:
                    prior = conn.execute('''SELECT m.* FROM after_order_messages m JOIN after_order_cases c ON c.id=m.case_id
                        WHERE c.store_id=? AND c.odoo_order_id=? AND m.template_kind=? AND m.test_mode=0
                          AND m.status<>'cancelled' ORDER BY m.id DESC LIMIT 1''', (*key, kind)).fetchone()
                if prior:
                    prior = dict(prior)
                    if prior['status'] == 'awaiting_approval' and not prior.get('attempt_count'):
                        r.retry_after_order_email(prior['id'], request, policy_exception=True)
                    r.care_sms.companion(prior['id'])
                else:
                    r.send_after_order_email(case['id'], request, template_kind=kind)
            except Exception as exc:
                with r.db() as conn:
                    details = {'reason': r.clean_error_message(exc)}
                    previous = conn.execute("SELECT details_json FROM after_order_case_events WHERE case_id=? AND event_type='delivery_followup_blocked' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
                    if not previous or json.loads(previous['details_json'] or '{}') != details:
                        r.record_after_order_event(conn, cid, 'delivery_followup_blocked', details=details)
