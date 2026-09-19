"""Narrow, explicitly authorized automatic email exceptions."""
import json
from datetime import datetime, timezone
from app.services.alternative_workflow import Runtime

TEST_RECIPIENT = 'sonianuj1284@gmail.com'
KIND = 'new_order_welcome'


def permitted(message, *, test_mode):
    payload = json.loads(message.get('payload_json') or '{}')
    if message.get('provider') != 'resend' or message.get('status') != 'awaiting_approval' or int(message.get('attempt_count') or 0):
        return False
    if payload.get('cc') or payload.get('bcc'):
        return False
    if message.get('test_mode'):
        return (str(message.get('recipient') or '').lower() == TEST_RECIPIENT
                and payload.get('to') == [TEST_RECIPIENT])
    return not test_mode and message.get('template_kind') in {KIND, 'trustpilot_review', 'delivery_issue_received'}


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

    def validate(self, case):
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_started_at')
        orders = r.OdooClient(r.get_store(case['store_id'])).read('sale.order', [case['odoo_order_id']], ['state','date_order','website_id'])
        if not orders or not recent_confirmed(orders[0], started):
            raise ValueError('Welcome email requires a newly confirmed website order after activation.')

    def run_checks(self, request):
        r = self.r
        started = r.get_service_settings().get('after_order_welcome_started_at')
        if not started:
            # First deployment establishes the boundary; never backfill old orders.
            with r.db() as conn:
                conn.execute("INSERT INTO app_settings(key,value,updated_at) VALUES('after_order_welcome_started_at',?,?) ON CONFLICT(key) DO NOTHING", (r.utc_now(),r.utc_now()))
            return
        with r.db() as conn:
            orders = conn.execute("SELECT DISTINCT store_id,odoo_order_id FROM order_lines WHERE odoo_order_date>=? ORDER BY store_id,odoo_order_id", (str(started)[:10],)).fetchall()
        for order in orders:
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
            except Exception as exc:
                if cid:
                    with r.db() as conn:
                        r.record_after_order_event(conn,cid,'welcome_email_blocked',details={'reason':r.clean_error_message(exc)})
