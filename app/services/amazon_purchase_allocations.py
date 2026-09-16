"""Quantity allocations for several Amazon purchases against one Odoo line."""
import json
import math
import re

ORDER_ID = re.compile(r'\d{3}-\d{7}-\d{7}')


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS amazon_purchase_allocations (
        id BIGSERIAL PRIMARY KEY,
        line_id INTEGER NOT NULL REFERENCES order_lines(id) ON DELETE CASCADE,
        amazon_order_id TEXT NOT NULL,
        quantity DOUBLE PRECISION NOT NULL CHECK(quantity>0),
        asin TEXT NOT NULL,
        amazon_account_name TEXT NOT NULL DEFAULT '',
        amazon_account_type TEXT NOT NULL DEFAULT '',
        total_cost DOUBLE PRECISION,
        state TEXT NOT NULL DEFAULT 'ordered',
        tracking_status TEXT NOT NULL DEFAULT '',
        tracking_payload TEXT NOT NULL DEFAULT '[]',
        tracking_checked_at TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(line_id,amazon_order_id)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_purchase_allocations_order ON amazon_purchase_allocations(amazon_order_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS amazon_purchase_allocation_audit (
        id BIGSERIAL PRIMARY KEY, line_id INTEGER NOT NULL, before_json TEXT NOT NULL,
        after_json TEXT NOT NULL, created_at TEXT NOT NULL
    )''')
    # Keep all legacy columns available without duplicating Odoo line records.
    # One source row is exposed per actual Amazon purchase for tracking/dispatch.
    conn.execute('''CREATE OR REPLACE VIEW amazon_purchase_tracking_lines AS
        SELECT l.* FROM order_lines l WHERE NOT EXISTS
            (SELECT 1 FROM amazon_purchase_allocations a WHERE a.line_id=l.id)
        UNION ALL
        SELECT r.* FROM amazon_purchase_allocations a JOIN order_lines l ON a.line_id=l.id
        CROSS JOIN LATERAL jsonb_populate_record(NULL::order_lines,
          to_jsonb(l) || jsonb_build_object(
            'amazon_order_id',a.amazon_order_id,
            'amazon_order_url','https://www.amazon.com/your-orders/order-details?orderID=' || a.amazon_order_id,
            'quantity',a.quantity,'asin',a.asin,
            'replacement_asin',CASE WHEN COALESCE(l.replacement_asin,'')!='' THEN a.asin ELSE NULL END,
            'state',a.state,'amazon_status',a.state,
            'amazon_cancelled_at',CASE WHEN a.state='cancelled' THEN a.updated_at ELSE NULL END,
            'amazon_cancelled_order_id',CASE WHEN a.state='cancelled' THEN a.amazon_order_id ELSE NULL END,
            'amazon_account_name',a.amazon_account_name,'amazon_account_type',a.amazon_account_type,
            'tracking_status',a.tracking_status,'tracking_payload',a.tracking_payload,
            'tracking_checked_at',a.tracking_checked_at,'updated_at',a.updated_at,
            'amazon_total_price',a.total_cost,
            'amazon_unit_price',a.total_cost/a.quantity
          )) r''')


def validate_allocations(line, values):
    if not isinstance(values, list) or not values or len(values)>100:
        raise ValueError('Enter between 1 and 100 Amazon purchases for this product.')
    demand=float(line.get('quantity') or 0)
    asin=str(line.get('replacement_asin') or line.get('asin') or '').strip().upper()
    if not re.fullmatch('[A-Z0-9]{10}',asin):
        raise ValueError('Assign a valid ASIN to the product first.')
    seen=set();result=[]
    for value in values:
        if not isinstance(value,dict):raise ValueError('Every purchase must contain an Amazon order ID and quantity.')
        oid=str(value.get('amazon_order_id') or '').strip()
        if not ORDER_ID.fullmatch(oid):raise ValueError('Enter a complete Amazon order ID for every purchase.')
        if oid in seen:raise ValueError('Combine units from the same Amazon order into one purchase row.')
        seen.add(oid)
        try:q=float(value.get('quantity') or 0)
        except (TypeError,ValueError):raise ValueError('Purchase quantity must be a positive whole number.')
        if not math.isfinite(q) or q<=0 or not q.is_integer():raise ValueError('Purchase quantity must be a positive whole number.')
        cost=value.get('total_cost')
        if cost not in (None,''):
            try:cost=float(cost)
            except (ValueError,TypeError):raise ValueError('Purchase cost must be a non-negative number.')
            if not math.isfinite(cost) or cost<0:raise ValueError('Purchase cost must be a non-negative number.')
        else:cost=None
        result.append(dict(amazon_order_id=oid,quantity=q,asin=asin,total_cost=cost,
                           amazon_account_name=str(value.get('amazon_account_name') or '').strip(),
                           amazon_account_type=str(value.get('amazon_account_type') or '').strip()))
    if sum(r['quantity'] for r in result if r['amazon_order_id'] not in line.get('_cancelled_purchase_ids',set()))>demand+1e-6:
        raise ValueError(f'Allocated units exceed the required quantity ({demand:g}).')
    return result


def summary(line, allocations):
    active=[a for a in allocations if a['state']!='cancelled']
    required=float(line.get('quantity') or 0)
    assigned=sum(float(a['quantity']) for a in active)
    delivered=sum(float(a['quantity']) for a in active if a['state']=='delivered')
    complete=required>0 and abs(assigned-required)<1e-6 and abs(delivered-required)<1e-6
    return dict(required_quantity=required,allocated_quantity=assigned,delivered_quantity=delivered,
                unallocated_quantity=max(0,required-assigned),complete=complete)


def refresh_parent(m, conn, line_id):
    line=dict(conn.execute('SELECT * FROM order_lines WHERE id=? FOR UPDATE',(line_id,)).fetchone())
    allocations=[dict(a) for a in conn.execute('SELECT * FROM amazon_purchase_allocations WHERE line_id=? ORDER BY id',(line_id,)).fetchall()]
    if not allocations:return line
    totals=summary(line,allocations)
    packages=[]
    for allocation in allocations:
        if allocation['state']=='cancelled':continue
        packages.extend(dict(p,amazon_order_id=allocation['amazon_order_id']) for p in m.parse_tracking_packages(allocation['tracking_payload']))
    if not totals['complete']:
        packages.append(dict(status='Awaiting remaining purchase units',status_only=True,
                             promise=f"{totals['required_quantity']-totals['delivered_quantity']:g} unit(s) not yet confirmed complete"))
    state='delivered' if totals['complete'] else 'ordered'
    status='Delivered' if totals['complete'] else f"Awaiting remaining units ({totals['delivered_quantity']:g}/{totals['required_quantity']:g} delivered)"
    # Do not classify partial delivery as delivered in substring-based consumers.
    if not totals['complete']:status=f"Partial purchase progress: {totals['delivered_quantity']:g}/{totals['required_quantity']:g} units complete"
    total_cost=sum(float(a['total_cost']) for a in allocations) if all(a.get('total_cost') is not None for a in allocations) else None
    conn.execute('''UPDATE order_lines SET amazon_order_id=?,amazon_order_url=?,order_engine='manual_amazon',
        state=?,amazon_status=?,tracking_status=?,tracking_payload=?,tracking_checked_at=?,updated_at=?,
        amazon_group_key=NULL,chrome_claimed_by=NULL,chrome_claimed_at=NULL,chrome_claim_expires_at=NULL,
        amazon_total_price=?,amazon_unit_price=?,chrome_profit_total=?, last_error=NULL WHERE id=?''',
        (allocations[0]['amazon_order_id'],m.order_line_amazon_url(allocations[0]['amazon_order_id']),state,state,status,
         m.tracking_payload_json_for_storage(packages),max((a.get('tracking_checked_at') or '' for a in allocations),default='') or None,m.utc_now(),total_cost,(total_cost/float(line['quantity'])) if total_cost is not None and line['quantity'] else None,
         (m.order_line_store_total(line)-total_cost) if total_cost is not None else None,line_id))
    return dict(conn.execute('SELECT * FROM order_lines WHERE id=?',(line_id,)).fetchone())


def save(m, store_id, line_id, values):
    with m.db() as conn:
        line=conn.execute('SELECT * FROM order_lines WHERE id=? AND store_id=? FOR UPDATE',(line_id,store_id)).fetchone()
        if not line:raise ValueError('Order line not found in this store.')
        line=dict(line)
        if line.get('state') in {'cancelled','refunded','ignored'} or line.get('odoo_status_label') in {'cancelled','refunded'} or line.get('order_engine')=='third_party':
            raise ValueError('This line cannot receive Amazon purchase allocations.')
        if not line.get('amazon_order_id') and line.get('state') in {'submitted','processing','running'}:
            raise ValueError('Wait for the active purchase job to finish before assigning manually purchased units.')
        if float(line.get('inventory_allocated_quantity') or 0)>0:
            raise ValueError('This line already uses local inventory. Reconcile its inventory allocation before splitting Amazon purchases.')
        if m.amazon_bundles.line_components(line):raise ValueError('Allocate each replacement component separately; virtual multipacks need component reconciliation first.')
        previous=[dict(a) for a in conn.execute('SELECT * FROM amazon_purchase_allocations WHERE line_id=? ORDER BY id',(line_id,)).fetchall()]
        line['_cancelled_purchase_ids']={a['amazon_order_id'] for a in previous if a['state']=='cancelled'}
        checked=validate_allocations(line,values)
        before={a['amazon_order_id']:a for a in previous};now=m.utc_now()
        # Existing purchases cannot silently disappear when enabling splits.
        existing=str(line.get('amazon_order_id') or '').strip()
        if existing and not previous and existing not in {a['amazon_order_id'] for a in checked}:
            raise ValueError('Include the currently assigned Amazon order and its actual quantity.')
        for old in previous:
            new=next((a for a in checked if a['amazon_order_id']==old['amazon_order_id']),None)
            if not new:raise ValueError('Existing purchases cannot be removed here. Record cancellations through tracking, then add the replacement purchase.')
            if old['state']!='cancelled' and (old['quantity']!=new['quantity'] or old['asin']!=new['asin']) and m.parse_tracking_packages(old['tracking_payload']):
                raise ValueError('A tracked purchase quantity/ASIN cannot be changed; reconcile its shipment evidence first.')
        # Cancelled allocations are retained for audit but do not consume demand.
        for value in checked:
            oid=value['amazon_order_id'];old=before.get(oid)
            history=conn.execute('SELECT * FROM amazon_order_history_unmatched WHERE amazon_order_id=?',(oid,)).fetchone()
            account=value['amazon_account_name'] or (old or {}).get('amazon_account_name') or (dict(history).get('amazon_account_name') if history else '') or (line.get('amazon_account_name') if oid==existing else '') or ''
            account_type=m.normalize_amazon_account_type(value['amazon_account_type'] or (old or {}).get('amazon_account_type') or (dict(history).get('amazon_account_type') if history else '') or (line.get('amazon_account_type') if oid==existing else ''))
            initial=m.parse_tracking_packages(line.get('tracking_payload')) if not previous and oid==existing else []
            initial=[p for p in initial if not m.package_direct_amazon_order_ids(p) or oid in m.package_direct_amazon_order_ids(p)]
            conn.execute('''INSERT INTO amazon_purchase_allocations(line_id,amazon_order_id,quantity,asin,amazon_account_name,amazon_account_type,total_cost,tracking_payload,created_at,updated_at)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(line_id,amazon_order_id) DO UPDATE SET
              quantity=excluded.quantity,asin=excluded.asin,amazon_account_name=excluded.amazon_account_name,
              amazon_account_type=excluded.amazon_account_type,total_cost=excluded.total_cost,updated_at=excluded.updated_at''',
              (line_id,oid,value['quantity'],value['asin'],account,account_type,value['total_cost'],m.tracking_payload_json_for_storage(initial),now,now))
        for a in conn.execute('SELECT * FROM amazon_purchase_allocations WHERE line_id=?',(line_id,)).fetchall():
            if a['state']=='cancelled':continue
            captured=m.parse_tracking_packages(a['tracking_payload'])
            delivered=[p for p in captured if m.tracking_package_delivered(p) and a['asin'] in m.replacement_tracking.package_asins(p)]
            if captured and len(delivered)==len(captured) and m.replacement_tracking.delivered_quantity_complete(dict(asin=a['asin'],quantity=a['quantity']),delivered):
                conn.execute("UPDATE amazon_purchase_allocations SET state='delivered',tracking_status='Delivered' WHERE id=?",(a['id'],))
        conn.execute('INSERT INTO amazon_purchase_allocation_audit(line_id,before_json,after_json,created_at) VALUES(?,?,?,?)',(line_id,json.dumps(previous or [line],default=str),json.dumps(checked),now))
        updated=refresh_parent(m,conn,line_id)
        for value in checked:
            m.sync_dispatch_packages_for_order(conn,value['amazon_order_id'])
            conn.execute("UPDATE amazon_order_history_unmatched SET resolved_at=COALESCE(resolved_at,?) WHERE amazon_order_id=?",(now,value['amazon_order_id']))
    m.index_order_line(updated);m.clear_order_progress_caches()
    m.fast_page_cache_clear_matching({'orders','tracking-orders','package-pickups','dispatch-related-parts','fulfilment-pending'})
    if abs(sum(v['quantity'] for v in checked if v['amazon_order_id'] not in line['_cancelled_purchase_ids'])-float(line['quantity']))<1e-6:m.enqueue_shopify_fulfilment_for_rows([updated])
    return get(m,store_id,line_id)


def get(m,store_id,line_id):
    with m.db() as conn:
        line=conn.execute('SELECT * FROM order_lines WHERE id=? AND store_id=?',(line_id,store_id)).fetchone()
        if not line:raise ValueError('Order line not found.')
        line=dict(line);allocations=[dict(a) for a in conn.execute('SELECT * FROM amazon_purchase_allocations WHERE line_id=? ORDER BY id',(line_id,)).fetchall()]
    return dict(line_id=line_id,order_name=line['odoo_order_name'],product_name=line['product_name'],asin=line.get('replacement_asin') or line.get('asin'),
                existing_order_id=line.get('amazon_order_id'),allocations=allocations,**summary(line,allocations))


def update_tracking(m,payload):
    """Return None for legacy orders; isolate each split purchase's shipment state."""
    oid=m.clean_text(payload.amazon_order_id)
    with m.db() as conn:
        if not conn.execute('SELECT id FROM amazon_purchase_allocations WHERE amazon_order_id=? LIMIT 1',(oid,)).fetchone():return None
        products=m.order_level_products_from_tracking_payload(payload)
        packages=m.enforce_tracking_payload_order_guard(oid,m.merge_order_products_into_tracking_packages(m.sanitize_tracking_packages(payload.packages or []),products))
        cancelled=bool(payload.order_cancelled)
        payment=m.payload_has_payment_revision(payload)
        if not packages and not cancelled and not payment:raise ValueError('Empty tracking update rejected; purchase data preserved.')
        # Other ordinary lines in this same Amazon checkout retain their full
        # quantity; convert their existing single-purchase association in place.
        ordinary=conn.execute('''SELECT l.* FROM order_lines l WHERE amazon_order_id=? AND NOT EXISTS
            (SELECT 1 FROM amazon_purchase_allocations a WHERE a.line_id=l.id) FOR UPDATE''',(oid,)).fetchall()
        for row in ordinary:
            if row.get('order_engine')=='third_party':continue
            conn.execute('''INSERT INTO amazon_purchase_allocations(line_id,amazon_order_id,quantity,asin,amazon_account_name,amazon_account_type,total_cost,state,tracking_status,tracking_payload,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(row['id'],oid,row['quantity'],row.get('replacement_asin') or row['asin'],row.get('amazon_account_name') or '',row.get('amazon_account_type') or '',row.get('amazon_total_price'),row.get('state') or 'ordered',row.get('tracking_status') or '',row.get('tracking_payload') or '[]',m.utc_now(),m.utc_now()))
        allocations=[dict(a) for a in conn.execute('SELECT * FROM amazon_purchase_allocations WHERE amazon_order_id=? ORDER BY line_id FOR UPDATE',(oid,)).fetchall()]
        error=m.tracking_account_identity_error(allocations,m.clean_text(payload.amazon_account_name),m.normalize_amazon_account_type(payload.amazon_account_type))
        if error:raise ValueError(error)
        for a in allocations:
            conn.execute('SELECT id FROM order_lines WHERE id=? FOR UPDATE',(a['line_id'],))
            # Strict ASIN evidence per package; never infer one purchase from an
            # unrelated product on the same Amazon checkout.
            matched=[p for p in packages if a['asin'] in m.replacement_tracking.package_asins(p)]
            if not matched and not cancelled and not payment:
                if not any(r['asin'] in m.replacement_tracking.package_asins(p) for r in allocations for p in packages):
                    raise ValueError('Purchase tracking requires an exact shipment ASIN match; re-scan its Amazon order and package.')
                continue
            old=m.parse_tracking_packages(a['tracking_payload'])
            if any(m.tracking_package_physical_id(p) for p in old):
                matched=[p for p in matched if not p.get('status_only') or m.tracking_package_physical_id(p)]
            # A partial package-page scan must retain other known shipments.
            by_key={m.tracking_package_physical_id(p) or m.tracking_package_shipment_key(p) or json.dumps(p,sort_keys=True):p for p in old}
            for p in matched:
                incoming_shipment=m.tracking_package_shipment_key(p)
                for old_key, old_package in list(by_key.items()):
                    if incoming_shipment and incoming_shipment==m.tracking_package_shipment_key(old_package) and not m.tracking_package_physical_id(old_package):
                        by_key.pop(old_key)
                key=m.tracking_package_physical_id(p) or m.tracking_package_shipment_key(p) or json.dumps(p,sort_keys=True)
                by_key[key]=p
            combined=m.canonical_tracking_packages(list(by_key.values()))
            # Reuse the exact item/shipment alias rules used by pickup scanning.
            alias_rows=[dict(p,id=i+1,amazon_order_id=oid,
                canonical_scan_code=m.tracking_package_physical_id(p) or '',
                asins_json=json.dumps(sorted(m.replacement_tracking.package_asins(p))),
                order_line_ids_json=json.dumps([a['line_id']])) for i,p in enumerate(combined)]
            obsolete={source for source,_ in m.dispatch_shipment_alias_pairs(alias_rows)}
            combined=[p for i,p in enumerate(combined) if i+1 not in obsolete]
            # Status-only placeholders are superseded once shipment evidence arrives.
            physical=[p for p in combined if m.tracking_package_physical_id(p)]
            if physical:combined=[p for p in combined if m.tracking_package_physical_id(p) or not p.get('status_only')]
            delivered=[p for p in combined if m.tracking_package_delivered(p)]
            complete=bool(combined) and len(delivered)==len(combined) and m.replacement_tracking.delivered_quantity_complete(dict(asin=a['asin'],quantity=a['quantity']),delivered)
            state='cancelled' if cancelled else 'ordered' if payment else 'delivered' if complete else 'ordered'
            status='Cancelled' if cancelled else 'Payment revision needed' if payment else 'Delivered' if complete else m.tracking_status_from_packages(combined)
            if not complete and status=='Delivered':status='Awaiting remaining purchase units'
            conn.execute('''UPDATE amazon_purchase_allocations SET state=?,tracking_status=?,tracking_payload=?,tracking_checked_at=?,updated_at=?,
                amazon_account_name=COALESCE(NULLIF(?,''),amazon_account_name),amazon_account_type=COALESCE(NULLIF(?,''),amazon_account_type)
                WHERE id=?''',(state,status,m.tracking_payload_json_for_storage(combined),m.utc_now(),m.utc_now(),m.clean_text(payload.amazon_account_name),m.normalize_amazon_account_type(payload.amazon_account_type),a['id']))
        updated=[refresh_parent(m,conn,a['line_id']) for a in allocations]
        if cancelled:
            # Keep historical scans; exclude cancelled purchase from readiness.
            conn.execute("UPDATE amazon_dispatch_packages SET package_status='Cancelled',promise='Cancelled',updated_at=? WHERE amazon_order_id=?",(m.utc_now(),oid))
        elif not payment:
            m.sync_dispatch_packages_for_order(conn,oid)
            m.refresh_dispatch_packages_from_tracking(conn,oid,m.order_line_amazon_url(oid),packages)
            m.mark_history_tracking_order_status(conn,oid,m.order_line_amazon_url(oid),packages)
        m.upsert_amazon_otp_from_tracking_payload(conn,oid,m.order_line_amazon_url(oid),m.tracking_payload_otp(payload,packages),packages)
    for row in updated:
        m.ensure_inventory_for_line(row)
        m.index_order_line(row)
    m.fast_page_cache_clear_matching({'orders','tracking-orders','package-pickups','dispatch-related-parts','dispatch-sorting-summary','dispatch-status','fulfilment-pending'})
    return dict(ok=True,updated=len(updated),split_purchases=True,tracking_status='Purchase tracking updated')


def readiness_parts(m,conn,package,parts):
    """Require quantity coverage for every allocated purchase, even before tracking."""
    rows=conn.execute('''SELECT a.*,l.quantity AS required_quantity FROM amazon_purchase_allocations a
        JOIN order_lines l ON l.id=a.line_id WHERE l.store_id=? AND (l.odoo_order_id=? OR (? IS NULL AND UPPER(l.odoo_order_name)=UPPER(?)))''',
        (package.get('store_id'),package.get('odoo_order_id'),package.get('odoo_order_id'),package.get('odoo_order_name'))).fetchall()
    if not rows:return parts
    rows=[dict(r) for r in rows];managed={r['line_id'] for r in rows}
    parts=[p for p in parts if not (p.get('unresolved_line_id') in managed)]
    cancelled={r['amazon_order_id'] for r in rows if r['state']=='cancelled'}
    parts=[p for p in parts if p.get('amazon_order_id') not in cancelled]
    active=[r for r in rows if r['state']!='cancelled']
    gaps=[]
    for a in active:
        matching=[p for p in parts if p.get('amazon_order_id')==a['amazon_order_id'] and a['line_id'] in m.parse_json_list_value(p.get('order_line_ids_json'))]
        if not matching or a['state']!='delivered':
            gaps.append((a['line_id'],a['amazon_order_id'],f"Amazon purchase {a['amazon_order_id']}: {a['quantity']:g} unit(s) awaiting complete delivery/tracking"))
    for line_id in managed:
        required=next(r['required_quantity'] for r in rows if r['line_id']==line_id)
        qty=sum(r['quantity'] for r in active if r['line_id']==line_id)
        if abs(qty-required)>1e-6:gaps.append((line_id,'',f'{max(0,required-qty):g} unit(s) still need a purchase allocation; required {required:g}, allocated {qty:g}'))
    for i,(line_id,oid,message) in enumerate(gaps):
        parts.append(dict(id=-(10**12+i),unresolved_line_id=line_id,amazon_order_id=oid,
                          order_line_ids_json=json.dumps([line_id]),package_status=message,delivery_label=message,received=False))
    return parts


def attach(conn,rows):
    if not rows:return
    ids=[int(r['id']) for r in rows]
    allocations=conn.execute(f"SELECT line_id,amazon_order_id,quantity,state,tracking_status FROM amazon_purchase_allocations WHERE line_id IN ({','.join('?' for _ in ids)}) ORDER BY id",ids).fetchall()
    grouped={}
    for a in allocations:grouped.setdefault(a['line_id'],[]).append(dict(a))
    for r in rows:r['amazon_purchases']=grouped.get(r['id'],[])


def guard_changes(m,conn,line_ids):
    if line_ids and conn.execute(f"SELECT id FROM amazon_purchase_allocations WHERE line_id IN ({','.join('?' for _ in line_ids)}) LIMIT 1",line_ids).fetchone():
        raise m.HTTPException(409,'This product has Amazon purchase allocations. Manage its purchases instead of resetting or replacing the whole line.')

def assert_export_coverage(m, job):
    with m.db() as conn:
        gaps = conn.execute('''SELECT l.id FROM order_lines l JOIN amazon_purchase_allocations a ON a.line_id=l.id
            WHERE l.store_id=? AND l.odoo_order_name=? GROUP BY l.id,l.quantity
            HAVING ABS(COALESCE(SUM(CASE WHEN a.state!='cancelled' THEN a.quantity ELSE 0 END),0)-l.quantity)>0.000001''',
            (job['store_id'],job['odoo_order_name'])).fetchall()
        if gaps:
            raise ValueError('Amazon purchase quantities are incomplete. Finish the purchase allocations before sending to Shopify.')
