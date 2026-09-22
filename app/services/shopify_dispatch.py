"""Verified DTC fulfilment notifications, separate from carrier movement/loss.

The export ledger supplies the original Odoo mapping; Shopify supplies fresh
fulfilment evidence. Never use the DTC shop's generic customer as recipient.
"""
import hashlib
import ipaddress
import json
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
from app.services.alternative_workflow import Runtime
from app.services.welcome_email import moment

KIND = 'shopify_dispatch'
SETTING = 'after_order_shopify_dispatch_started_at'
QUERY = '''query($id:ID!) { node(id:$id) { ... on Fulfillment {
 id status createdAt trackingInfo { number company url }
 order { id cancelledAt tags }
} } }'''
ORDERS_QUERY = '''query($query:String!, $after:String) {
 orders(first:50, after:$after, query:$query, sortKey:UPDATED_AT) {
  pageInfo { hasNextPage endCursor }
  nodes { id cancelledAt tags fulfillments(first:250) {
   id status createdAt trackingInfo { number company url }
  } }
 }
}'''


def gid(value, resource):
    value = str(value or '')
    if value.startswith('gid://shopify/'+resource+'/') and value.rsplit('/',1)[-1].isdigit():
        return value
    if value.isdigit():
        return 'gid://shopify/'+resource+'/'+value
    raise ValueError('Invalid Shopify source identifier.')


def carrier_url(value):
    value = str(value or '').strip()
    p = urlsplit(value)
    host = (p.hostname or '').lower()
    if (p.scheme != 'https' or not host or p.username or p.password or p.port not in (None,443)
            or re.search(r'[\s<>"\x00-\x1f]',value) or '.' not in host
            or host.endswith(('.local','.internal','.myshopify.com'))
            or host in {'admin.shopify.com','fulfilment.gofinch.com'}
            or host.startswith(('admin.','backend.')) or p.path.startswith(('/admin','/web'))):
        raise ValueError('A public HTTPS customer tracking URL is required.')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return value
    raise ValueError('IP-address tracking URLs are not customer links.')


def evidence(node, order_id, started, now=None):
    if not node or node.get('status') != 'SUCCESS' or not node.get('order') or node['order'].get('cancelledAt'):
        raise ValueError('Shopify fulfilment is not successful or its order was cancelled.')
    if node['order'].get('id') != gid(order_id,'Order'):
        raise ValueError('Shopify fulfilment does not belong to the mapped order.')
    if not started or not moment(started) <= moment(node.get('createdAt')) <= (now or datetime.now(timezone.utc)):
        raise ValueError('Fulfilment is outside the new-notification activation window.')
    parcels = []
    for info in node.get('trackingInfo') or []:
        code = str(info.get('number') or '').strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _-]{0,99}',code):
            raise ValueError('A valid tracking number is required.')
        item = {'number':code,'url':carrier_url(info.get('url')),'company':str(info.get('company') or '')}
        if item not in parcels:
            parcels.append(item)
    if not parcels:
        raise ValueError('Shopify has not supplied tracking details yet.')
    return sorted(parcels,key=lambda x:(x['number'],x['url']))


def case_key(source):
    key = json.dumps([source['src_shop'],gid(source['src_fulfillment_id'],'Fulfillment')])
    return 'shopify-dispatch:'+hashlib.sha256(key.encode()).hexdigest()


def sms_link(case, link):
    # Existing approved tracking template has one dynamic tracking-details field.
    # Its fixed branded/translated text remains unchanged.
    return ' '.join(x['number']+' '+carrier_url(x['url']) for x in case['context']['dispatch_parcels'])


class Monitor:
    def __init__(self, namespace):
        self.r = Runtime(namespace)
        self.last_check_at = None
        self.last_error = None

    def clients(self):
        return {x.shop.lower():x for x in self.r.shopify_clients_for_route('dtc')}

    def validate(self, case, clients=None):
        if case.get('case_type') != KIND or case.get('tracking_provider') != 'shopify_dtc':
            raise ValueError('This is not a verified DTC dispatch case.')
        context = case.get('context') or {}
        source = context['dispatch_source']
        if case.get('case_key') != case_key(source):
            raise ValueError('Dispatch source identity changed.')
        with self.r.db() as conn:
            mapped = conn.execute('''SELECT s.odoo_db FROM shopify_export_order_map m JOIN stores s
                ON m.src_order_key=(s.odoo_db || ':' || ?)
                WHERE m.state_scope='dtc' AND m.dest_name=? AND m.dest_order_id IN (?,?) AND s.id=? AND s.active=1''',
                (case['odoo_order_name'],source['dest_name'],source['src_order_id'],
                 source['src_order_id'].rsplit('/',1)[-1],case['store_id'])).fetchone()
        if not mapped:
            raise ValueError('Original Odoo order mapping is no longer verified.')
        client = (clients if clients is not None else self.clients()).get(source['src_shop'].lower())
        if not client:
            raise ValueError('Source shop is not an active DTC destination.')
        node = client.graphql(QUERY,{'id':gid(source['src_fulfillment_id'],'Fulfillment')}).get('node')
        tags=(node or {}).get('order',{}).get('tags',[])
        if not {'SRC_ODOO_DB:'+mapped['odoo_db'],'SRC_ODOO_ORDER:'+case['odoo_order_name']}.issubset(tags):
            raise ValueError('Shopify original-order tags no longer match the export ledger.')
        parcels = evidence(node,source['src_order_id'],self.r.get_service_settings().get(SETTING))
        if parcels != context.get('dispatch_parcels'):
            raise ValueError('Tracking details changed. Review the dispatch notification before sending.')
        return True

    def run_checks(self, request):
        r = self.r
        with r.db() as guard:
            if not guard.execute('SELECT pg_try_advisory_xact_lock(781905442) AS locked').fetchone()['locked']:
                return
            started = r.get_service_settings().get(SETTING)
            if not started:
                with r.db() as conn:
                    conn.execute('INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO NOTHING',
                                 (SETTING,r.utc_now(),r.utc_now()))
                return
            self.last_error = None
            self.resume_pending(request)
            clients = self.clients()
            for shop,client in clients.items():
                cursor_key='shopify_dispatch_cursor:'+shop
                with r.db() as conn:
                    saved=conn.execute('SELECT value FROM app_settings WHERE key=?',(cursor_key,)).fetchone()
                since=max(moment(started),moment(saved['value'])-timedelta(minutes=5)) if saved else moment(started)
                through=datetime.now(timezone.utc)
                query=f'updated_at:>={since.isoformat()} updated_at:<={through.isoformat()}'
                cursor=None; complete=True
                while True:
                    page=client.graphql(ORDERS_QUERY,{'query':query,'after':cursor})['orders']
                    for remote in page['nodes']:
                        if remote.get('cancelledAt'):
                            continue
                        for fulfilment in remote.get('fulfillments') or []:
                            if (fulfilment.get('status')!='SUCCESS' or not fulfilment.get('trackingInfo')
                                    or moment(fulfilment['createdAt'])<moment(started)):
                                continue
                            node={**fulfilment,'order':remote}
                            source={'src_shop':shop,'dest_name':client.name,'src_order_id':remote['id'],
                                    'src_fulfillment_id':fulfilment['id']}
                            if not self.prepare(source,node,started,request):
                                complete=False
                        guard.execute('SELECT 1')
                    if not page['pageInfo']['hasNextPage']:
                        break
                    next_cursor=page['pageInfo']['endCursor']
                    if not next_cursor or next_cursor==cursor:
                        raise ValueError('Shopify pagination did not advance; cursor was not saved.')
                    cursor=next_cursor
                if complete:
                    with r.db() as conn:
                        conn.execute('''INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?)
                            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at''',
                            (cursor_key,through.isoformat(),r.utc_now()))
            self.last_check_at=r.utc_now()

    def resume_pending(self, request):
        """Recover a crash between reservation, email and companion creation.

        Only unattempted automatic sends are resumed. Provider failures and
        uncertain outcomes retain the existing explicit retry/review policy.
        """
        r=self.r
        with r.db() as conn:
            pending=conn.execute('''SELECT c.id FROM after_order_cases c
                WHERE c.case_type='shopify_dispatch' AND NOT EXISTS
                (SELECT 1 FROM after_order_messages m WHERE m.case_id=c.id)''').fetchall()
        for row in pending:
            try:
                r.send_after_order_email(row['id'],request)
            except Exception as exc:
                self.last_error=r.clean_error_message(exc)
        with r.db() as conn:
            rows=conn.execute('''SELECT m.id,m.status,m.attempt_count FROM after_order_messages m
                LEFT JOIN after_order_sms s ON s.email_id=m.id
                WHERE m.template_kind='shopify_dispatch' AND m.status NOT IN ('cancelled','superseded')
                AND m.created_at>=? AND ((m.status='awaiting_approval' AND m.attempt_count=0)
                    OR s.id IS NULL OR (s.status='awaiting_approval' AND s.attempts=0))''',
                ((datetime.now(timezone.utc)-timedelta(days=3)).isoformat(),)).fetchall()
        for row in rows:
            try:
                r.care_sms.companion(row['id'])
                if row['status']=='awaiting_approval' and not row['attempt_count']:
                    r.retry_after_order_email(row['id'],request,policy_exception=True)
            except Exception as exc:
                self.last_error=r.clean_error_message(exc)

    def prepare(self, source, node, started, request):
        r=self.r; key=case_key(source);cid=None
        try:
            with r.db() as conn:
                existing=conn.execute('SELECT id FROM after_order_cases WHERE case_key=?',(key,)).fetchone()
                if existing and conn.execute('SELECT 1 FROM after_order_messages WHERE case_id=? LIMIT 1',(existing['id'],)).fetchone():
                    return True
                mappings=conn.execute('''SELECT src_order_key FROM shopify_export_order_map
                    WHERE state_scope='dtc' AND dest_name=? AND dest_order_id IN (?,?)''',
                    (source['dest_name'],source['src_order_id'],source['src_order_id'].rsplit('/',1)[-1])).fetchall()
                if not mappings:
                    return True  # Not an order exported by this app.
                if len(mappings)!=1:
                    raise ValueError('Ambiguous DTC export mapping.')
                orders=conn.execute('''SELECT DISTINCT l.store_id,l.odoo_order_id,l.odoo_order_name,s.odoo_db
                    FROM order_lines l JOIN stores s ON s.id=l.store_id
                    WHERE (s.odoo_db || ':' || l.odoo_order_name)=? AND s.active=1''',
                    (mappings[0]['src_order_key'],)).fetchall()
            if len(orders)!=1:
                return False  # Retry after import; never guess an order or skip it via cursor advancement.
            order=dict(orders[0])
            if not {'SRC_ODOO_DB:'+order['odoo_db'],'SRC_ODOO_ORDER:'+order['odoo_order_name']}.issubset(node['order']['tags']):
                raise ValueError('Original-order tags do not match the export ledger.')
            parcels=evidence(node,source['src_order_id'],started)
            context={'dispatch_source':source,'dispatch_parcels':parcels,'dispatch_created_at':node['createdAt']}
            with r.db() as conn:
                created=conn.execute('''INSERT INTO after_order_cases(case_key,store_id,odoo_order_id,odoo_order_name,
                    case_type,status,severity,title,tracking_provider,tracking_code,affected_items_json,context_json,created_at,updated_at)
                    VALUES(?,?,?,?,'shopify_dispatch','resolved','low','Shopify DTC dispatch','shopify_dtc',?,'[]',?,?,?)
                    ON CONFLICT(case_key) DO NOTHING RETURNING id''',
                    (key,order['store_id'],order['odoo_order_id'],order['odoo_order_name'],parcels[0]['number'],json.dumps(context),r.utc_now(),r.utc_now())).fetchone()
                cid=created['id'] if created else existing['id']
                if not created:
                    conn.execute('UPDATE after_order_cases SET context_json=?,tracking_code=?,updated_at=? WHERE id=?',
                                 (json.dumps(context),parcels[0]['number'],r.utc_now(),cid))
            r.send_after_order_email(cid,request)
            return True
        except Exception as exc:
            self.last_error=r.clean_error_message(exc)
            if cid:
                with r.db() as conn:
                    r.record_after_order_event(conn,cid,'shopify_dispatch_blocked',details={'reason':r.clean_error_message(exc)})
            return False
