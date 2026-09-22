"""Narrow, explicitly authorized automatic email exceptions."""
import json
from datetime import datetime, timedelta, timezone
from app.services.alternative_workflow import Runtime

TEST_RECIPIENT = 'sonianuj1284@gmail.com'
KIND = 'new_order_welcome'


def moment(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def delay_minutes(placed, completed=None):
    return max(0, ((moment(completed) if completed else datetime.now(timezone.utc))-moment(placed)).total_seconds()/60)


def permitted(message, *, test_mode):
    payload = json.loads(message.get('payload_json') or '{}')
    if message.get('provider') != 'resend' or message.get('status') != 'awaiting_approval' or int(message.get('attempt_count') or 0):
        return False
    if payload.get('cc') or payload.get('bcc'):
        return False
    if message.get('test_mode'):
        return (str(message.get('recipient') or '').lower() == TEST_RECIPIENT
                and payload.get('to') == [TEST_RECIPIENT])
    return not test_mode and message.get('template_kind') in {KIND, 'shopify_dispatch', 'trustpilot_review', 'delivery_issue_received', 'manual_refund_completed'}


def recent_confirmed(order, started_at):
    try:
        def moment(value):
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
        date = moment(order.get('date_order'))
        return order.get('state') in {'sale', 'done'} and bool(order.get('website_id')) and moment(started_at) <= date <= datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


class Monitor:
    def __init__(self, namespace):
        self.r = Runtime(namespace)
        self.last_check_at = None

    def validate(self, case):
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_started_at')
        orders = r.OdooClient(r.get_store(case['store_id'])).read('sale.order', [case['odoo_order_id']], ['state','date_order','website_id'])
        if not orders or not recent_confirmed(orders[0], started):
            raise ValueError('Welcome email requires a newly confirmed website order after activation.')

    def run_checks(self, request):
        # One welcome dispatcher across concurrent requests and app replicas.
        with self.r.db() as guard:
            if not guard.execute('SELECT pg_try_advisory_xact_lock(781905439) AS locked').fetchone()['locked']:
                return
            result = self._run_checks(request, guard=guard)
            self.r.care_sms.pending_welcomes()
            self.last_check_at = self.r.utc_now()
            return result

    def _run_checks(self, request, guard=None):
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_started_at')
        if not started:
            # First deployment establishes the boundary; never backfill old orders.
            with r.db() as conn:
                conn.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('after_order_welcome_started_at',?,?) ON CONFLICT(key) DO NOTHING", (r.utc_now(),r.utc_now()))
            return
        with r.db() as conn:
            # Filter completed/cancelled notification owners in SQL, instead of
            # repeatedly visiting every historical order before reaching new ones.
            orders = conn.execute("""SELECT l.store_id,l.odoo_order_id,MIN(l.odoo_order_date) AS placed_at
                FROM order_lines l JOIN stores s ON s.id=l.store_id
                WHERE l.odoo_order_date>=? AND s.active=1
                AND NOT EXISTS (SELECT 1 FROM after_order_cases c JOIN after_order_messages m ON m.case_id=c.id
                    WHERE c.case_key='new-order-welcome:' || CAST(l.store_id AS TEXT) || ':' || CAST(l.odoo_order_id AS TEXT))
                GROUP BY l.store_id,l.odoo_order_id ORDER BY placed_at DESC,l.store_id,l.odoo_order_id""",
                (max(str(started)[:10],r.after_order_cutoff_date()),)).fetchall()
        for order in orders:
            if guard:
                guard.execute('SELECT 1')  # Keep the dispatcher lock connection alive during larger batches.
            cid = None
            try:
                sid, oid = order['store_id'],order['odoo_order_id']
                key = f'new-order-welcome:{sid}:{oid}'
                with r.db() as conn:
                    existing = conn.execute('SELECT id FROM after_order_cases WHERE case_key=?',(key,)).fetchone()
                    if existing and conn.execute('SELECT 1 FROM after_order_messages WHERE case_id=? LIMIT 1',(existing['id'],)).fetchone():
                        continue
                    lines = [dict(x) for x in conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=?',(sid,oid)).fetchall()]
                self.validate({'store_id':sid,'odoo_order_id':oid})
                first = lines[0]
                items = [{'line_id':x['id'],'asin':x.get('asin'),'product_name':x.get('product_name'),'quantity':x.get('quantity')} for x in lines]
                with r.db() as conn:
                    created = conn.execute("""INSERT INTO after_order_cases(case_key,store_id,odoo_order_id,odoo_order_name,case_type,status,severity,title,affected_items_json,context_json,created_at,updated_at)
                        VALUES(?,?,?,?,'new_order_welcome','resolved','low','New order welcome',?,'{}',?,?)
                        ON CONFLICT(case_key) DO NOTHING RETURNING id""",(key,sid,oid,first['odoo_order_name'],json.dumps(items),r.utc_now(),r.utc_now())).fetchone()
                    cid = created['id'] if created else conn.execute('SELECT id FROM after_order_cases WHERE case_key=?',(key,)).fetchone()['id']
                r.send_after_order_email(cid,request)
                if delay_minutes(order['placed_at']) > 15:
                    with r.db() as conn:
                        if not conn.execute("SELECT 1 FROM after_order_case_events WHERE case_id=? AND event_type='welcome_email_sla_exceeded'",(cid,)).fetchone():
                            r.record_after_order_event(conn,cid,'welcome_email_sla_exceeded',details={
                                'target_minutes':15,'elapsed_minutes':round(delay_minutes(order['placed_at']),1),
                                'order_date':order['placed_at'],'message':'New-order welcome exceeded the 15-minute target. Check import and email logs.'})
            except Exception as exc:
                if cid:
                    with r.db() as conn:
                        r.record_after_order_event(conn,cid,'welcome_email_blocked',details={'reason':r.clean_error_message(exc)})

    def timing(self, store_id=None):
        """Read-only operational warning. Does not retry or send any messages."""
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_started_at')
        if not started:
            return {'target_minutes':15,'pending_overdue':0,'sent_late':0,'orders':[], 'last_check_at':self.last_check_at}
        since = max(moment(started),moment(r.after_order_cutoff_date()),datetime.now(timezone.utc)-timedelta(hours=24))
        with r.db() as conn:
            import_job = conn.execute("SELECT MIN(created_at) AS started_at FROM pull_jobs WHERE status IN ('queued','running') AND (? IS NULL OR store_id=?)",(store_id,store_id)).fetchone()
            rows = conn.execute("""SELECT l.store_id,l.odoo_order_id,MIN(l.odoo_order_name) AS order_name,
                MIN(l.odoo_order_date) AS placed_at,
                (SELECT MIN(a.updated_at) FROM after_order_cases c JOIN after_order_messages m ON m.case_id=c.id
                    JOIN after_order_email_attempts a ON a.message_id=m.id
                    WHERE c.store_id=l.store_id AND c.odoo_order_id=l.odoo_order_id
                    AND m.template_kind='new_order_welcome' AND m.test_mode=0
                    AND a.provider_message_id IS NOT NULL AND a.provider_message_id!='') AS sent_at,
                COALESCE((SELECT MIN(m.status) FROM after_order_cases c JOIN after_order_messages m ON m.case_id=c.id
                    WHERE c.store_id=l.store_id AND c.odoo_order_id=l.odoo_order_id
                    AND m.template_kind='new_order_welcome' AND m.test_mode=0),
                    CASE WHEN EXISTS (SELECT 1 FROM after_order_cases c JOIN after_order_messages m ON m.case_id=c.id
                        WHERE c.store_id=l.store_id AND c.odoo_order_id=l.odoo_order_id
                        AND m.template_kind='new_order_welcome' AND m.test_mode=1) THEN 'test_only' END) AS email_status
                FROM order_lines l JOIN stores s ON s.id=l.store_id
                WHERE l.odoo_order_date>=? AND s.active=1 AND l.odoo_order_state IN ('sale','done')
                    AND (? IS NULL OR l.store_id=?)
                GROUP BY l.store_id,l.odoo_order_id ORDER BY placed_at DESC""",
                (since.strftime('%Y-%m-%d %H:%M:%S'),store_id,store_id)).fetchall()
        overdue=[];late=0
        for row in rows:
            elapsed=delay_minutes(row['placed_at'],row['sent_at'])
            if elapsed<=15:
                continue
            if row['sent_at']:
                late+=1
            elif row['email_status'] not in {'cancelled','superseded'}:
                overdue.append({'store_id':row['store_id'],'order_id':row['odoo_order_id'],
                    'order_name':row['order_name'],'minutes':round(elapsed,1),'status':row['email_status'] or 'not_prepared'})
        overdue.sort(key=lambda row:row['minutes'],reverse=True)
        return {'target_minutes':15,'window_hours':24,'pending_overdue':len(overdue),'sent_late':late,
            'orders':overdue[:20],'last_check_at':self.last_check_at,
            'import_backlog_minutes':round(delay_minutes(import_job['started_at']),1) if import_job['started_at'] else 0,
            'import_interval_minutes':r.get_service_settings().get('autosync_interval_minutes')}
