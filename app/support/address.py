"""Verified, order-specific delivery edits with live dispatch checks and durable outcomes."""
import hashlib,json,html,time
from fastapi import HTTPException

ADDRESS_FIELDS=['id','name','street','street2','city','zip','state_id','country_id','phone','mobile','email','write_date']
SHOP_QUERY='''query($id:ID!){order(id:$id){id name email cancelledAt displayFulfillmentStatus updatedAt tags phone shippingAddress{firstName lastName company address1 address2 city provinceCode countryCodeV2 zip phone}}}'''

def gid(value):return str(value) if str(value).startswith('gid://shopify/Order/') else 'gid://shopify/Order/'+str(value)

def live_context(db,client,store,order):
    from app import main as app
    raw=client.search_read('sale.order',[['id','=',order['id']],['website_id','=',1]],['id','name','state','partner_id','partner_shipping_id','write_date'],limit=1)
    if not raw:raise HTTPException(404,'Order not found.')
    raw=raw[0]
    if raw['state']!='sale':raise HTTPException(409,'This order is not eligible for an automatic delivery-detail change.')
    picks=client.search_read('stock.picking',[['sale_id','=',order['id']],['picking_type_id.code','=','outgoing'],['state','!=','cancel']],['id','state'],limit=101)
    if len(picks)>100:raise HTTPException(409,'Delivery status needs team review.')
    if any(x['state']=='done' for x in picks):raise HTTPException(409,'The address cannot be changed because the order has already been dispatched.')
    shipping=raw.get('partner_shipping_id')
    if not shipping:raise HTTPException(409,'The delivery address needs team review.')
    current=client.search_read('res.partner',[['id','=',shipping[0]]],ADDRESS_FIELDS,limit=1)
    if not current:raise HTTPException(409,'The delivery address could not be read.')
    with db() as c:
        source=app.get_store(store).odoo_db+':'+str(order['name']).upper()
        mappings=[dict(x) for x in c.execute("SELECT state_scope,dest_name,dest_order_id FROM shopify_export_order_map WHERE src_order_key=? AND COALESCE(dest_order_id,'')<>''",(source,)).fetchall()]
        cached=[dict(x) for x in c.execute('SELECT route,dest_name,shopify_order_id FROM shopify_order_status_cache WHERE store_id=? AND odoo_order_name=?',(store,order['name'])).fetchall()]
        outbound=c.execute('SELECT id FROM epost_global_tracking WHERE store_id=? AND odoo_order_id=? AND archived_at IS NULL LIMIT 1',(store,order['id'])).fetchone()
    # Existing customer-leg tracking can race Shopify fulfilment; fail closed on conflicting evidence.
    if outbound:raise HTTPException(409,'Shipment processing is already recorded. Our team needs to check whether a delivery change is still possible.')
    targets={(str(x['state_scope']),str(x['dest_name']),gid(x['dest_order_id'])) for x in mappings}
    if any((str(x['route']),str(x['dest_name']),gid(x['shopify_order_id'])) not in targets for x in cached if x.get('shopify_order_id')):raise HTTPException(409,'The linked delivery order mapping needs team verification.')
    shops=[];clients={}
    for route,name,ident in sorted(targets):
        if (route,name) not in clients:
            module,_,scope=app.shopify_route_script_config(route);dest=next((d for d in module.DESTS if d['name']==name),None);state=module.StateDB(scope)
            if not dest or not app.shopify_dest_has_access(module,state,dest):raise HTTPException(503,'The delivery system connection needs team attention.')
            clients[route,name]=module.ShopifyClient(dest['name'],dest['shop'],module.get_shopify_access_token(dest,state),dest.get('api_version') or '2025-10')
        shop=clients[route,name];record=shop.graphql(SHOP_QUERY,{'id':ident}).get('order')
        if not record or record['id']!=ident:raise HTTPException(409,'The linked delivery order could not be verified.')
        if record.get('cancelledAt'):raise HTTPException(409,'This order was cancelled; delivery details cannot be changed automatically.')
        if record.get('displayFulfillmentStatus') in {'FULFILLED','PARTIALLY_FULFILLED'}:raise HTTPException(409,'The address cannot be changed because the order has already been dispatched.')
        if record.get('displayFulfillmentStatus')!='UNFULFILLED':raise HTTPException(409,'The delivery state needs team review before a change.')
        shops.append((shop,record))
    return raw,current[0],picks,shops

def public_current(current):
    return {'name':current.get('name'),'street_address':current.get('street'),'address_line_2':current.get('street2'),'city':current.get('city'),'state_region':(current.get('state_id') or [None,None])[1],'postal_code':current.get('zip'),'country':(current.get('country_id') or [None,None])[1],'phone':current.get('phone') or current.get('mobile')}

def snapshot(raw,current,shops):
    return hashlib.sha256(json.dumps([raw,current,[(x['id'],x.get('updatedAt')) for _,x in shops]],sort_keys=True,default=str).encode()).hexdigest()

def update_delivery(db,client,store,order,uuid,email,payload,expected):
    raw,current,picks,shops=live_context(db,client,store,order)
    if snapshot(raw,current,shops)!=expected:raise HTTPException(409,'The order changed since its details were displayed. Please retrieve the latest address before applying the change.')
    values={k:current.get(k) or False for k in ['name','street','street2','city','zip','phone','mobile','email']}
    values.update(country_id=(current.get('country_id') or [False])[0],state_id=(current.get('state_id') or [False])[0],type='delivery',parent_id=raw['partner_id'][0])
    if payload['kind']=='phone':values.update(phone=payload['phone'],mobile=payload['phone'])
    else:
        country=client.search_read('res.country',['|',['code','=ilike',payload['country']],['name','=ilike',payload['country']]],['id','code','name'],limit=2)
        if len(country)!=1:raise HTTPException(422,'Please provide an unambiguous country name or two-letter country code.')
        region=client.search_read('res.country.state',[['country_id','=',country[0]['id']],'|',['code','=ilike',payload['state_region']],['name','=ilike',payload['state_region']]],['id','code'],limit=2)
        if len(region)!=1:raise HTTPException(422,'Please provide the state/region name or code for this country.')
        values.update(name=payload['recipient_name'],street=payload['street_address'],street2=payload.get('address_line_2') or False,city=payload['city'],zip=payload['postal_code'],country_id=country[0]['id'],state_id=region[0]['id'])
        if payload.get('phone'):values.update(phone=payload['phone'],mobile=payload['phone'])
    request_key=hashlib.sha256(json.dumps([uuid,order['id'],payload],sort_keys=True).encode()).hexdigest()
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS support_delivery_changes (request_key TEXT PRIMARY KEY,order_id BIGINT,conversation_uuid TEXT,state TEXT,details_json TEXT,created_at DOUBLE PRECISION)')
        c.execute('SELECT pg_advisory_xact_lock(81910417)')
        previous=c.execute("SELECT state FROM support_delivery_changes WHERE order_id=? AND state IN ('applying','needs_review')",(order['id'],)).fetchone()
        if previous:raise HTTPException(409,'A delivery change is already being processed or needs review.')
        previous=c.execute('SELECT state FROM support_delivery_changes WHERE request_key=?',(request_key,)).fetchone()
        if previous:raise HTTPException(409,'This change was already attempted. Check the current order details before requesting another change.')
        c.execute("INSERT INTO support_delivery_changes VALUES(?,?,?,'applying',?,?)",(request_key,order['id'],uuid,json.dumps({'old':public_current(current),'requested':payload}),time.time()))
    completed=[];odoo_updated=False
    try:
        # Recheck immediately before writes. Never use a cached UNFULFILLED flag for mutation.
        fresh_raw,fresh_current,_,fresh_shops=live_context(db,client,store,order)
        if snapshot(fresh_raw,fresh_current,fresh_shops)!=expected:raise ValueError('Order changed before write')
        for shop,before in shops:
            latest=shop.graphql(SHOP_QUERY,{'id':before['id']}).get('order')
            if not latest or latest.get('cancelledAt') or latest.get('displayFulfillmentStatus')!='UNFULFILLED' or latest.get('updatedAt')!=before.get('updatedAt'):raise ValueError('Delivery state changed before write')
            address=dict(before.get('shippingAddress') or {})
            if payload['kind']=='phone':address['phone']=payload['phone']
            else:
                names=payload['recipient_name'].split(' ',1)
                address.update(firstName=names[0],lastName=names[1] if len(names)>1 else '',address1=payload['street_address'],address2=payload.get('address_line_2') or '',city=payload['city'],provinceCode=region[0]['code'],countryCodeV2=country[0]['code'],zip=payload['postal_code'])
                if payload.get('phone'):address['phone']=payload['phone']
            # MailingAddressInput names countryCode, unlike the output's countryCodeV2.
            address['countryCode']=address.pop('countryCodeV2',None)
            inp={'id':before['id'],'shippingAddress':address}
            if payload.get('phone'):inp['phone']=payload['phone']
            result=shop.graphql('mutation($input:OrderInput!){orderUpdate(input:$input){order{id} userErrors{field message}}}',{'input':inp})['orderUpdate']
            if result.get('userErrors'):raise ValueError('Delivery-system update rejected')
            after=shop.graphql(SHOP_QUERY,{'id':before['id']})['order'];check=dict(address);check['countryCodeV2']=check.pop('countryCode')
            if any(str((after.get('shippingAddress') or {}).get(k) or '')!=str(v or '') for k,v in check.items()):raise ValueError('Delivery-system readback differs')
            if payload.get('phone') and str(after.get('phone') or '')!=payload['phone']:raise ValueError('Order phone readback differs')
            completed.append(before['id'])
        partner=client.execute('res.partner','create',[values])
        client.execute('sale.order','write',[[order['id']],{'partner_shipping_id':partner}]);odoo_updated=True
        if picks:client.execute('stock.picking','write',[[x['id'] for x in picks],{'partner_id':partner}])
        saved=client.search_read('res.partner',[['id','=',partner]],ADDRESS_FIELDS,limit=1)[0]
        if any(str(saved.get(k) or '')!=str(values.get(k) or '') for k in ['name','street','street2','city','zip','phone','mobile']):raise ValueError('Order-address readback differs')
        if any((saved.get(k) or [False])[0]!=values[k] for k in ['country_id','state_id']):raise ValueError('Order region readback differs')
        read_order=client.search_read('sale.order',[['id','=',order['id']]],['partner_shipping_id'],limit=1)[0]
        if read_order['partner_shipping_id'][0]!=partner:raise ValueError('Order link readback differs')
        body='<p>Customer delivery '+html.escape(payload['kind'])+' change through verified support chat.</p><p>Previous: '+html.escape(json.dumps(public_current(current)))+'</p><p>Updated: '+html.escape(json.dumps(public_current(saved)))+'</p><p>Linked Shopify orders verified updated: '+str(len(completed))+'. Conversation: '+html.escape(uuid)+'</p>'
        client.execute('sale.order','message_post',[[order['id']]],{'body':body,'message_type':'comment','subtype_xmlid':'mail.mt_note'})
        with db() as c:c.execute("UPDATE support_delivery_changes SET state='complete' WHERE request_key=?",(request_key,))
        return {'updated':True,'odoo_updated':True,'shopify_exists':bool(shops),'shopify_updated':len(completed)==len(shops) if shops else False,'current_details':public_current(saved),'instruction':'Confirm the order details were updated. Confirm the linked delivery order was also updated only if shopify_exists and shopify_updated are true. Do not mention internal system names to the customer.'}
    except Exception:
        details={'odoo_updated':odoo_updated,'shopify_verified_updated_count':len(completed),'needs_human':True}
        with db() as c:c.execute("UPDATE support_delivery_changes SET state='needs_review',details_json=? WHERE request_key=?",(json.dumps({'old':public_current(current),'requested':payload,**details}),request_key))
        try:client.execute('sale.order','message_post',[[order['id']]],{'body':'Support delivery change needs review. '+html.escape(json.dumps(details))+' Conversation '+html.escape(uuid),'message_type':'comment','subtype_xmlid':'mail.mt_note'})
        except Exception:pass
        return {'updated':False,**details,'instruction':'Do not claim the change fully succeeded. Explain that synchronization needs team review and hand off. Do not retry automatically.'}
