"""Read current operational evidence; raw procurement facts never leave this module."""
import json
from datetime import datetime, timezone, timedelta
from app.support.policy import Evidence
from app.services.after_order import tracking_risk


def stamp(value):
    try:
        result=datetime.fromisoformat(str(value or '').replace('Z','+00:00'))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def objects(value):
    try:
        result=json.loads(value or '[]')
        return result if isinstance(result,list) else [result] if isinstance(result,dict) else []
    except (ValueError,TypeError):return []


def order_evidence(conn, store, order, now=None):
    now=now or datetime.now(timezone.utc)
    stale=Evidence(observed_at=datetime.min.replace(tzinfo=timezone.utc))
    # Read all required items, including unsourced ones. Filtering only tracked rows can overpromise.
    rows=[dict(r) for r in conn.execute('SELECT * FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id LIMIT 501',(store,order['id'])).fetchall()]
    if not rows or len(rows)>500 or order.get('items_truncated'):return stale
    outbound=[dict(r) for r in conn.execute('SELECT status,events_json,last_checked_at FROM epost_global_tracking WHERE store_id=? AND odoo_order_id=? AND archived_at IS NULL LIMIT 501',(store,order['id'])).fetchall()]
    for shipment in outbound:
        if timedelta(0)<=now-stamp(shipment.get('last_checked_at'))<=timedelta(minutes=5):
            risk=tracking_risk(objects(shipment.get('events_json')),status=shipment.get('status') or '',now=now)
            if risk.state in {'delivered','in_transit'}:
                # A shipment alone does not prove coverage of every order item.
                return Evidence(observed_at=now,outbound_state='partial')
    required={int(x['id']) for x in order.get('items',[]) if not x.get('display_type') and not x.get('is_delivery') and x.get('product_id') and float(x.get('product_uom_qty') or 0)>0}
    covered=set()
    expected=[]
    timestamps=[]
    inbound=False
    for row in rows:
        timestamps.append(stamp(row.get('tracking_checked_at')))
        source=objects(row.get('source_odoo_line_ids'))
        covered.update(int(x) for x in source if isinstance(x,int))
        if row.get('odoo_line_id'):covered.add(int(row['odoo_line_id']))
        packages=objects(row.get('tracking_payload'))
        quantities=0
        dates=[]
        seen=set()
        for package in packages:
            if not isinstance(package,dict):continue
            ident=package.get('tracking_id')
            if not ident or ident in seen:continue
            seen.add(ident)
            products=[p for p in package.get('products',[]) if isinstance(p,dict) and p.get('asin')==row.get('asin') and p.get('quantity_verified') is True]
            quantity=sum(float(p.get('quantity') or 0) for p in products)
            if quantity<=0:continue
            quantities+=quantity
            date=stamp(package.get('expected_delivery_date')).date()
            dates.append(date if date.year>2000 else None)
            inbound=inbound or str(package.get('status') or '').lower()=='delivered'
        if quantities<float(row.get('quantity') or 0) or not dates:expected.append(None)
        else:expected.extend(dates)
    if not required or not required.issubset(covered):return stale
    return Evidence(observed_at=min(timestamps),expected_dates=tuple(expected),inbound_delivered=inbound)
