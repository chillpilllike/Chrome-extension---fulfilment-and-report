"""Delivered-notification reminders; never an email-open or payment timer."""
import json
from datetime import datetime, timezone
from app.services.alternative_selection import moment
from app.services.alternative_workflow import Runtime

SCHEMA = '''
CREATE TABLE IF NOT EXISTS after_order_reminder_checks (
 message_id INTEGER PRIMARY KEY REFERENCES after_order_messages(id) ON DELETE CASCADE,
 checked_at TEXT NOT NULL, last_slot INTEGER NOT NULL DEFAULT 0
);
'''


def reminder_slot(message, case, delivered_at, now=None):
    """A late worker sends only the latest due reminder, not a catch-up burst."""
    payload = json.loads(message.get('payload_json') or '{}')
    if (message.get('test_mode') or message.get('status') != 'delivered'
            or not payload.get('_care_reminders') or payload.get('_care_reminder_parent')
            or case.get('confirmed_at') or case.get('current_decision')
            or case.get('status') in ('resolved', 'execution_needs_review')):
        return 0
    kind = message.get('template_kind')
    if kind not in ('expected_dispatch', 'item_unavailable', 'delivery_confirmation', 'package_lost', 'tracking'):
        return 0
    if kind == 'tracking' and (case.get('context') or {}).get('risk_state') != 'suspected_lost':
        return 0
    elapsed = ((now or datetime.now(timezone.utc)) - moment(delivered_at)).total_seconds()/3600
    # The final notice precedes the three-day cutoff. Never restart that clock.
    if elapsed < 24 or elapsed >= 72:
        return 0
    return 3 if elapsed >= 60 else 2 if elapsed >= 48 else 1


class Reminders:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def validate(self, message_id, slot, case):
        r = self.r
        if r.after_order_email_test_mode():
            raise ValueError('Live reminders are disabled in test mode.')
        with r.db() as conn:
            row = conn.execute('''SELECT m.*,MIN(e.occurred_at) AS delivered_at
                FROM after_order_messages m JOIN after_order_delivery_events e
                ON e.provider_message_id=m.provider_message_id
                WHERE m.id=? AND e.event_type='email.delivered' GROUP BY m.id''',(message_id,)).fetchone()
        message = dict(row) if row else None
        if (not message or message['case_id'] != case['id']
                or reminder_slot(message,case,message['delivered_at']) != slot
                or message['request_fingerprint'] != r.request_fingerprint(case)
                or message['recipient'].strip().lower() != case.get('customer_email','').strip().lower()
                or message['sender'] != r.after_order_sender(case)[0]):
            raise ValueError('Reminder is no longer current or due.')
        return message

    def run_due(self, request):
        r = self.r
        if r.after_order_email_test_mode():
            return
        with r.db() as conn:
            rows = conn.execute('''SELECT m.*,MIN(e.occurred_at) AS delivered_at,
                COALESCE(k.last_slot,0) AS last_slot FROM after_order_messages m
                JOIN after_order_delivery_events e ON e.provider_message_id=m.provider_message_id
                LEFT JOIN after_order_reminder_checks k ON k.message_id=m.id
                WHERE m.test_mode=0 AND m.provider='resend' AND m.status='delivered'
                AND e.event_type='email.delivered' AND m.payload_json LIKE '%_care_reminders%'
                GROUP BY m.id,k.last_slot,k.checked_at
                ORDER BY COALESCE(k.checked_at,''),m.id LIMIT 100''').fetchall()
        for row in rows:
            message = dict(row)
            case = r.after_order_case_by_id(message['case_id'])
            slot = reminder_slot(message,case,message['delivered_at']) if case else 0
            completed = message['last_slot']
            try:
                if slot > completed:
                    r.send_after_order_email(message['case_id'],request,
                        reminder_parent=message['id'],reminder_number=slot)
                    completed = slot
            except Exception as exc:
                with r.db() as conn:
                    r.record_after_order_event(conn,message['case_id'],'email_reminder_blocked',
                        details={'message_id':message['id'],'reminder':slot,'error':r.clean_error_message(exc)})
            finally:
                with r.db() as conn:
                    conn.execute('''INSERT INTO after_order_reminder_checks(message_id,checked_at,last_slot)
                        VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET checked_at=excluded.checked_at,
                        last_slot=CASE WHEN after_order_reminder_checks.last_slot>excluded.last_slot
                        THEN after_order_reminder_checks.last_slot ELSE excluded.last_slot END''',
                        (message['id'],r.utc_now(),completed))
