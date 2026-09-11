"""Receipt-to-dispatch monitoring. Only reserves approval-held notifications."""
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
from app.services.alternative_workflow import Runtime

NY = ZoneInfo('America/New_York')
KIND = 'warehouse_dispatch_delay'


@lru_cache(maxsize=32)
def holidays(year):
    def nth(month, weekday, n):
        first = date(year, month, 1)
        return first + timedelta(days=(weekday-first.weekday()) % 7 + 7*(n-1))
    days = {nth(1, 0, 3), nth(2, 0, 3), nth(9, 0, 1), nth(10, 0, 2), nth(11, 3, 4)}
    last = date(year, 5, 31)
    days.add(last - timedelta(days=last.weekday()))
    # Include adjacent New Year's observations (e.g. 31 December).
    for y in (year-1, year, year+1):
        for month, day in ((1, 1), (6, 19), (7, 4), (11, 11), (12, 25)):
            actual = date(y, month, day)
            observed = actual + timedelta(days=-1 if actual.weekday()==5 else 1 if actual.weekday()==6 else 0)
            if observed.year == year:
                days.add(observed)
    return frozenset(days)


def business_day(day):
    return day.weekday() < 5 and day not in holidays(day.year)


def due_at(received):
    """Exclude receipt day; wait two complete local business days."""
    day = received.astimezone(NY).date()
    count = 0
    while count < 2:
        day += timedelta(days=1)
        count += int(business_day(day))
    day += timedelta(days=1)
    while not business_day(day):
        day += timedelta(days=1)
    return datetime.combine(day, time.min, NY)


def delivery_day(package, checked_at):
    """Only explicit delivered evidence; never use ETA or a polling timestamp."""
    texts = [str(package.get(k) or '') for k in ('status', 'order_status', 'delivery_status', 'promise')]
    status = ' '.join(texts).lower()
    if re.search(r'not delivered|not yet delivered|undelivered|arriving|in transit|out for delivery|return|cancel|unable|attempt', status):
        return None
    if not re.search(r'\bdelivered\b', status):
        return None
    events = list(package.get('events') or [])
    if isinstance(package.get('latest_event'), dict):
        events.append(package['latest_event'])
    raw_dates = [str(e.get('date') or '') for e in events if isinstance(e, dict) and re.search(r'\bdelivered\b', str(e.get('message') or ''), re.I) and not re.search(r'not|unable|attempt', str(e.get('message') or ''), re.I)]
    raw_dates += [re.sub(r'^.*?\bdelivered(?:\s+on)?\s+', '', t, flags=re.I) for t in texts if re.search(r'\bdelivered\s+', t, re.I)]
    try:
        reference = datetime.fromisoformat(str(checked_at).replace('Z', '+00:00'))
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        reference = reference.astimezone(NY).date()
    except (ValueError, TypeError):
        return None
    found = []
    for raw in raw_dates:
        raw = re.sub(r'^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+', '', raw.strip(), flags=re.I)
        for fmt in ('%Y-%m-%d', '%B %d, %Y', '%b %d, %Y', '%m/%d/%Y', '%B %d', '%b %d'):
            try:
                value = datetime.strptime(raw, fmt).date()
                if '%Y' not in fmt:
                    value = value.replace(year=reference.year)
                    if value > reference:
                        value = value.replace(year=reference.year-1)
                # Missing years may cross New Year, but must not turn a future
                # September date into an alleged delivery a year ago.
                if value <= reference and ('%Y' in fmt or (reference-value).days <= 90):
                    found.append(value)
                break
            except ValueError:
                pass
    return datetime.combine(max(found), time.min, NY) if found else None


class Monitor:
    def __init__(self, namespace):
        self.r = Runtime(namespace)
        self.last_run = None
        self.last_result = {'checked':0, 'queued':0, 'blocked':0}

    def evidence(self, store_id, order_id, *, verify_odoo=False):
        r = self.r
        with r.db() as conn:
            lines = [dict(x) for x in conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=?', (store_id, order_id)).fetchall()]
            if not lines or any(not x.get('odoo_order_date') or x['odoo_order_date'][:10] < r.after_order_cutoff_date() for x in lines):
                return None
            name = lines[0]['odoo_order_name']
            if conn.execute("SELECT 1 FROM epost_global_tracking WHERE store_id=? AND odoo_order_id=? LIMIT 1", (store_id, order_id)).fetchone():
                return None
            if conn.execute("SELECT 1 FROM shopify_tracking_sync_log l JOIN stores s ON s.odoo_db=l.odoo_db WHERE s.id=? AND l.odoo_order=? LIMIT 1", (store_id, name)).fetchone():
                return None
            if conn.execute("SELECT 1 FROM shopify_order_status_cache WHERE store_id=? AND UPPER(odoo_order_name)=UPPER(?) AND (COALESCE(cancelled_at,'')!='' OR UPPER(COALESCE(fulfillment_status,'')) IN ('FULFILLED','PARTIALLY_FULFILLED','PARTIAL')) LIMIT 1", (store_id, name)).fetchone():
                return None
        receipts = []
        for line in lines:
            if (not line.get('asin') or not line.get('amazon_order_id') or str(line.get('state') or '').lower() in {'fulfilled','shipped','ignored','missing','cancelled','refunded'}
                or str(line.get('odoo_status_label') or '').lower() in {'cancelled','refunded'} or line.get('odoo_order_state') == 'cancel'
                or line.get('order_engine') in {'third_party','manual_amazon','inventory'}):
                return None
            packages = r.parse_tracking_packages(line.get('tracking_payload') or '')
            if not packages:
                return None
            for package in packages:
                receipt = delivery_day(package, line.get('tracking_checked_at'))
                if receipt is None or receipt.date().isoformat() < str(line['odoo_order_date'])[:10]:
                    return None
                receipts.append(receipt)
        deadline = due_at(max(receipts))
        if datetime.now(timezone.utc) < deadline:
            return None
        if verify_odoo:
            client = r.OdooClient(r.get_store(store_id))
            orders = client.read('sale.order', [order_id], ['state', 'order_line'])
            if not orders or orders[0].get('state') not in {'sale','done'}:
                return None
            # Imported lines alone cannot prove that the whole order arrived.
            sale_lines = client.read('sale.order.line', orders[0]['order_line'], ['display_type','product_id','product_uom_qty','qty_delivered'])
            active = [x for x in sale_lines if not x.get('display_type') and x.get('product_id') and x.get('product_uom_qty', 0) > 0]
            products = client.read('product.product', list({x['product_id'][0] for x in active}), ['type']) if active else []
            types = {x['id']:x.get('type') for x in products}
            if any(not types.get(x['product_id'][0]) for x in active):
                return None
            active = [x for x in active if types[x['product_id'][0]] != 'service']
            if (not active or any(x.get('qty_delivered', 0) > 0 for x in active)
                or not {x['id'] for x in active}.issubset({x.get('odoo_line_id') for x in lines})):
                return None
            pickings = client.execute('stock.picking', 'search_read', [[('sale_id','=',order_id),('picking_type_code','=','outgoing')]], {'fields':['state','carrier_tracking_ref']})
            if not pickings or any(p.get('state') in {'done','cancel'} or p.get('carrier_tracking_ref') for p in pickings):
                return None
        return {'lines':lines, 'receipt_date':max(receipts).date().isoformat(), 'due_at':deadline.isoformat()}

    def validate(self, case):
        if not self.evidence(case['store_id'], case['odoo_order_id'], verify_odoo=True):
            raise ValueError('Dispatch-delay notice is no longer eligible, or current warehouse/dispatch evidence is incomplete.')

    def run_checks(self, request):
        r = self.r
        if (r.after_order_email_test_mode() or not r.after_order_approval_only_live()
            or r.clean_text(r.get_service_settings().get('after_order_warehouse_delay_enabled')) != 'true'):
            return {'checked':0, 'queued':0}
        now = datetime.now(timezone.utc)
        if self.last_run and (now-self.last_run).total_seconds() < 300:
            return {**self.last_result, 'throttled':True}
        self.last_run = now
        with r.db() as conn:
            orders = conn.execute("SELECT DISTINCT store_id,odoo_order_id FROM order_lines WHERE COALESCE(odoo_order_date,'')>=? AND COALESCE(amazon_order_id,'')!='' AND LOWER(COALESCE(tracking_payload,'')) LIKE '%delivered%' AND COALESCE(state,'') NOT IN ('fulfilled','shipped','ignored','missing','cancelled','refunded') ORDER BY store_id,odoo_order_id", (r.after_order_cutoff_date(),)).fetchall()
        checked = queued = blocked = 0
        for order in orders:
            sid, oid = order['store_id'], order['odoo_order_id']
            key = f'warehouse-dispatch-delay:{sid}:{oid}'
            cid = None
            try:
                with r.db() as conn:
                    existing = conn.execute('SELECT id FROM after_order_cases WHERE case_key=?', (key,)).fetchone()
                    if existing and conn.execute("SELECT 1 FROM after_order_messages WHERE case_id=? AND test_mode=0 LIMIT 1", (existing['id'],)).fetchone():
                        continue
                evidence = self.evidence(sid, oid)
                checked += 1
                if not evidence:
                    continue
                if not self.evidence(sid, oid, verify_odoo=True):
                    blocked += 1
                    continue
                first = evidence['lines'][0]
                items = [{'line_id':x['id'],'asin':x['asin'],'product_name':x.get('product_name'),'quantity':x.get('quantity')} for x in evidence['lines']]
                context = {'warehouse_received_date':evidence['receipt_date'], 'dispatch_delay_due_at':evidence['due_at'], 'latest_status':'Warehouse dispatch overdue — team review required'}
                with r.db() as conn:
                    created = conn.execute("""INSERT INTO after_order_cases(case_key,store_id,odoo_order_id,odoo_order_name,case_type,status,severity,title,affected_items_json,context_json,created_at,updated_at)
                        VALUES(?,?,?,?,'warehouse_dispatch_delay','needs_attention','high','Warehouse dispatch delay',?,?,?,?)
                        ON CONFLICT(case_key) DO NOTHING RETURNING id""", (key,sid,oid,first['odoo_order_name'],json.dumps(items),json.dumps(context),r.utc_now(),r.utc_now())).fetchone()
                    cid = created['id'] if created else existing['id'] if existing else conn.execute('SELECT id FROM after_order_cases WHERE case_key=?',(key,)).fetchone()['id']
                    if created:
                        r.record_after_order_event(conn,cid,'warehouse_dispatch_delay_team_notified',details={'receipt_date':evidence['receipt_date'],'due_at':evidence['due_at'],'channel':'After-order care needs-attention queue'})
                result = r.send_after_order_email(cid,request)
                queued += int(result.get('status') == 'awaiting_approval')
            except Exception as exc:
                blocked += 1
                # Do not stop other stores; the case remains visible for review.
                if cid:
                    with r.db() as conn:
                        r.record_after_order_event(conn,cid,'warehouse_dispatch_notice_blocked',details={'reason':r.clean_error_message(exc)})
                else:
                    print('Warehouse dispatch check failed; no email sent: ' + r.clean_error_message(exc), flush=True)
        self.last_result = {'checked':checked,'queued':queued,'blocked':blocked}
        return self.last_result
