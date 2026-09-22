"""Email-only approval policy and recovery of never-attempted notifications."""
import json
import time
from datetime import datetime, timedelta, timezone
from app.services.alternative_workflow import Runtime

KEYS = ('after_order_email_bypass_approval', 'after_order_email_bypass_existing', 'after_order_email_bypass_enabled_at')


def moment(value):
    dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def bypassed(message, settings):
    if settings.get(KEYS[0]) != 'true' or message.get('test_mode'):
        return False
    if message.get('provider') not in {'resend','odoo'} or message.get('status')!='awaiting_approval' or int(message.get('attempt_count') or 0):
        return False
    if str(message.get('template_kind') or '').startswith('relay_') or message.get('template_kind')=='refund_confirmed':
        return False  # These have independent verified-event workers, already automatic.
    if message.get('provider')=='resend' and message.get('provider_message_id'):
        return False
    if settings.get(KEYS[1])=='true':
        return True
    try:
        return moment(message.get('created_at')) >= moment(settings.get(KEYS[2]))
    except (TypeError,ValueError):
        return False


def require_current_action_notice(case, message, namespace):
    r=Runtime(namespace)
    kind=message.get('template_kind')
    action_notice = kind in {'item_unavailable','expected_dispatch','delivery_confirmation','package_lost'} or (
        kind=='tracking' and (case.get('context') or {}).get('risk_state')=='suspected_lost')
    if action_notice and (case.get('current_decision') or not r.after_order_allowed_actions(case)):
        raise ValueError('This customer action notice is resolved, answered or no longer eligible.')
    if kind!='expected_dispatch':
        return
    with r.db() as conn:
        rows=conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=?',
                          (case['store_id'],case['odoo_order_id'])).fetchall()
    eligible=[];dates=[]
    for raw in rows:
        line=dict(raw)
        if (line.get('state') in {'fulfilled','shipped','delivered','ignored'} or
                line.get('odoo_status_label') in {'cancelled','refunded'} or
                line.get('order_engine') in {'third_party','manual_amazon','inventory'} or not line.get('amazon_order_id')):
            continue
        eligible.append(line['id'])
        for package in r.parse_tracking_packages(line.get('tracking_payload') or ''):
            try:dates.append(moment(package.get('expected_delivery_date')).date())
            except (TypeError,ValueError):pass
    affected={int(x['line_id']) for x in case.get('affected_items') or []}
    handling=str(r.get_service_settings().get('after_order_dispatch_handling_days') or '')
    if (not affected or not affected.issubset(set(eligible)) or not dates or not handling.isdigit()
            or max(dates)<=(datetime.now(timezone.utc)+timedelta(days=6)).date()
            or (max(dates)+timedelta(days=int(handling))).isoformat()!=(case.get('context') or {}).get('expected_dispatch_date')):
        raise ValueError('Expected dispatch date or fulfilment changed. Prepare a current email.')


class Dispatcher:
    def __init__(self, namespace):
        self.r=Runtime(namespace)
        self.last_check_at=None

    def run(self, request):
        r=self.r
        from app.services.welcome_email import permitted
        with r.db() as guard:
            if not guard.execute('SELECT pg_try_advisory_xact_lock(781905443) AS locked').fetchone()['locked']:
                return
            with r.db() as conn:
                rows=conn.execute('''SELECT m.* FROM after_order_messages m
                    WHERE m.status='awaiting_approval' AND m.attempt_count=0
                    AND NOT EXISTS (SELECT 1 FROM after_order_email_attempts a WHERE a.message_id=m.id)
                    ORDER BY m.created_at,m.id''').fetchall()
            for raw in rows:
                row=dict(raw)
                if not permitted(row,test_mode=r.after_order_email_test_mode(),settings=r.get_email_approval_settings()):
                    continue
                try:
                    # A delayed recovery must never deliver an obsolete action notice.
                    if moment(row['created_at']) < datetime.now(timezone.utc)-timedelta(days=3):
                        raise ValueError('Automatic sending blocked: preview is over three days old. Prepare a current email or review manually.')
                    r.retry_after_order_email(row['id'],request,policy_exception=True)
                    time.sleep(1)  # Keep queued releases within provider request limits.
                except Exception as exc:
                    reason=r.clean_error_message(exc)
                    with r.db() as conn:
                        current=conn.execute('SELECT status,attempt_count,last_error FROM after_order_messages WHERE id=?',(row['id'],)).fetchone()
                        if current and current['status']=='awaiting_approval' and not current['attempt_count'] and current['last_error']!=reason:
                            conn.execute('UPDATE after_order_messages SET last_error=?,updated_at=? WHERE id=?',(reason,r.utc_now(),row['id']))
                            r.record_after_order_event(conn,row['case_id'],'email_automatic_send_blocked',details={'message_id':row['id'],'reason':reason})
                guard.execute('SELECT 1')
            self.last_check_at=r.utc_now()
