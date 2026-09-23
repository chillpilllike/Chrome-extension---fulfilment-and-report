"""Acknowledgements of recorded customer requests, never evidence of a refund."""
import json
from app.services.alternative_workflow import Runtime

KIND = 'refund_request_received'


class Notices:
    def __init__(self, namespace):
        self.r = Runtime(namespace)

    def validate(self, case):
        r = self.r
        r.require_after_order_case_in_scope(case)
        if case.get('confirmed_at') or case.get('status') == 'resolved':
            raise ValueError('Refund request was already resolved or confirmed.')
        if case.get('current_decision') in {'refund','cancel_order','exclude_item_and_proceed'}:
            return
        with r.db() as conn:
            removal = conn.execute("SELECT 1 FROM after_order_line_removals WHERE case_id=? AND test_mode=0 AND origin='customer' AND status='needs_approval'", (case['id'],)).fetchone()
        if not removal:
            raise ValueError('No current customer refund/removal request exists.')

    def run(self, request):
        r = self.r
        with r.db() as conn:
            rows = conn.execute("""SELECT c.id FROM after_order_cases c
                WHERE c.confirmed_at IS NULL AND c.status!='resolved'
                AND (c.current_decision IN ('refund','cancel_order','exclude_item_and_proceed')
                  OR EXISTS(SELECT 1 FROM after_order_line_removals x WHERE x.case_id=c.id
                    AND x.test_mode=0 AND x.origin='customer' AND x.status='needs_approval'))
                AND NOT EXISTS(SELECT 1 FROM after_order_messages m WHERE m.case_id=c.id
                    AND m.template_kind='refund_request_received' AND m.test_mode=0)
                ORDER BY c.id""").fetchall()
        for row in rows:
            try:
                case = r.after_order_case_by_id(row['id'])
                self.validate(case)
                r.send_after_order_email(row['id'],request,template_kind=KIND)
            except Exception as exc:
                with r.db() as conn:
                    details={'reason':r.clean_error_message(exc)}
                    previous=conn.execute("SELECT details_json FROM after_order_case_events WHERE case_id=? AND event_type='refund_acknowledgement_blocked' ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
                    if not previous or json.loads(previous['details_json'])!=details:
                        r.record_after_order_event(conn,row['id'],'refund_acknowledgement_blocked',details=details)
