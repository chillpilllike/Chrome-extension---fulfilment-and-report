"""Notification-only maintenance. Never execute payments, refunds or fulfilment."""
import json
from app.services.alternative_workflow import Runtime


class Worker:
    def __init__(self, namespace):
        self.r = Runtime(namespace)
        self.last_check_at = None
        self.errors = {}

    def run(self, request):
        r = self.r
        if r.after_order_email_test_mode() or r.get_service_settings().get('after_order_notifications_enabled') != 'true':
            return
        with r.db() as guard:
            if not guard.execute('SELECT pg_try_advisory_xact_lock(781905444) AS locked').fetchone()['locked']:
                return
            stages = {
                'delivery_receipts': r.care_delivery.reconcile,
                'response_deadlines': r.care_requests.sync_delivered,
                'expired_responses': lambda: r.care_requests.run_due(review_only=True),
                'replacement_review': self.selection_reviews,
                'email_recovery': lambda: r.care_delivery.recover_sends(request),
                'reminders': lambda: r.care_reminders.run_due(request),
                'sms_recovery': r.care_sms.recover_failed,
                'sms_receipts': r.care_sms.reconcile_receipts,
                'refund_acknowledgements': lambda: r.refund_notices.run(request),
                'financial_sms_preparation': self.financial_sms,
                'case_notifications': lambda: self.prepare(request),
                'quotation_status': r.alternative_workflow.refresh_quote_emails,
            }
            for name, action in stages.items():
                try:
                    action()
                    self.errors.pop(name, None)
                except Exception as exc:
                    self.errors[name] = r.clean_error_message(exc)
                guard.execute('SELECT 1')
            self.last_check_at = r.utc_now()

    def financial_sms(self):
        r=self.r
        with r.db() as conn:
            rows=conn.execute("""SELECT m.id FROM after_order_messages m
                LEFT JOIN after_order_sms s ON s.email_id=m.id
                WHERE m.test_mode=0 AND m.template_kind IN ('manual_refund_completed','refund_request_received','price_difference')
                  AND m.status NOT IN ('cancelled','superseded') AND s.id IS NULL
                  AND m.created_at>=? ORDER BY m.id""", (r.after_order_cutoff_date(),)).fetchall()
        for row in rows:
            r.care_sms.companion(row['id'])  # Financial SMS keeps its independent approval.

    def selection_reviews(self):
        r = self.r
        with r.db() as conn:
            rows = conn.execute("""SELECT s.* FROM after_order_line_selections s
                WHERE s.test_mode=0 AND s.status='choosing' AND s.deadline_at<=?
                ORDER BY s.deadline_at LIMIT 200""", (r.utc_now(),)).fetchall()
            for raw in rows:
                row = dict(raw)
                case = r.after_order_case_by_id(row['case_id'])
                if not case or not r.after_order_case_is_in_scope(case):
                    continue
                changed = conn.execute("""UPDATE after_order_line_selections
                    SET status='needs_review',last_error='Selection window closed. Team approval required; no payment, refund or fulfilment was executed.',updated_at=?
                    WHERE case_id=? AND line_id=? AND test_mode=0 AND version=? AND status='choosing'""",
                    (r.utc_now(), row['case_id'], row['line_id'], row['version']))
                if changed.rowcount:
                    conn.execute("UPDATE after_order_cases SET status='needs_confirmation',updated_at=? WHERE id=? AND confirmed_at IS NULL", (r.utc_now(), row['case_id']))
                    r.record_after_order_event(conn, row['case_id'], 'selection_window_closed_for_review', details={'line_id':row['line_id'],'version':row['version']})

    def prepare(self, request):
        r = self.r
        r.sync_after_order_cases()
        with r.db() as conn:
            rows = conn.execute("""SELECT c.id FROM after_order_cases c
                LEFT JOIN after_order_case_checks k ON k.case_id=c.id
                WHERE c.confirmed_at IS NULL AND c.current_decision IS NULL AND c.status!='resolved'
                  AND c.case_type IN ('expected_dispatch','tracking','delivery_confirmation','item_unavailable')
                  AND EXISTS(SELECT 1 FROM order_lines l WHERE l.store_id=c.store_id
                    AND l.odoo_order_id=c.odoo_order_id AND l.odoo_order_date>=?)
                ORDER BY COALESCE(k.checked_at,''),c.id LIMIT 100""", (r.after_order_cutoff_date(),)).fetchall()
        for row in rows:
            try:
                case = r.after_order_case_by_id(row['id'])
                if not r.after_order_case_is_in_scope(case):
                    continue
                context = case.get('context') or {}
                if case['case_type']=='tracking' and context.get('risk_state') not in {'in_transit','suspected_lost'}:
                    continue  # Blank/pre-scan/exception statuses must never become lost notices.
                if case['case_type']=='item_unavailable':
                    review = r.after_order_unavailable_review(case, for_send=True)
                    if review['blocked'] or not review['approved'] or not r.alternative_workflow.ready(case):
                        continue
                r.send_after_order_email(case['id'], request)
            except Exception as exc:
                reason = r.clean_error_message(exc)
                with r.db() as conn:
                    previous = conn.execute("SELECT details_json FROM after_order_case_events WHERE case_id=? AND event_type='notification_preparation_blocked' ORDER BY id DESC LIMIT 1", (row['id'],)).fetchone()
                    if not previous or json.loads(previous['details_json'] or '{}').get('reason') != reason:
                        r.record_after_order_event(conn,row['id'],'notification_preparation_blocked',details={'reason':reason})
            finally:
                with r.db() as conn:
                    conn.execute("""INSERT INTO after_order_case_checks(case_id,checked_at) VALUES(?,?)
                        ON CONFLICT(case_id) DO UPDATE SET checked_at=excluded.checked_at""", (row['id'],r.utc_now()))
