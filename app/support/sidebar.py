"""Order summaries in LibreDesk native conversation Information attributes."""

def sync_sidebar_order(client,libre,uuid,email,order):
    conv=libre('/conversations/'+uuid)
    contact_id=conv.get('contact_id')
    if not contact_id:return False
    contact=libre('/contacts/'+str(contact_id))
    if str(contact.get('email') or '').strip().lower()!=email:return False
    ids=list({line['product_id'][0] for line in order.get('items',[]) if isinstance(line.get('product_id'),(list,tuple)) and line['product_id']})[:100]
    links={}
    if ids:
        fields=client.fields_get('product.product')
        wanted=[f for f in ['id','website_url','website_id','is_published','active'] if f in fields]
        for p in client.search_read('product.product',[['id','in',ids]],wanted,limit=100):
            site=p.get('website_id');site=site[0] if isinstance(site,(list,tuple)) and site else site
            path=p.get('website_url')
            if p.get('active') and p.get('is_published') and site in (False,1) and isinstance(path,str) and path.startswith('/shop/') and not any(x in path for x in ('\\','\n','\r')):
                links[p['id']]='https://secretgreen.com.au'+path
    currency=(order.get('currency_id') or ['', ''])[1]
    items=[];pages=[]
    for index,line in enumerate(order.get('items',[])[:100],1):
        name=str(line.get('name') or '')
        product=line.get('product_id')
        link=links.get(product[0]) if isinstance(product,(list,tuple)) and product else None
        items.append(str(index)+'. '+name+' | Qty: '+str(line.get('product_uom_qty') or 0)+' | Total: '+str(line.get('price_total') or 0)+' '+currency)
        if product:pages.append(str(index)+'. '+name+' | '+(link or 'No published product page'))
    attributes=dict(conv.get('custom_attributes') or {})
    attributes.update(sg_order_items=' ; '.join(items),sg_product_urls=' ; '.join(pages),sg_order_total=str(order.get('amount_total') or 0)+' '+currency)
    libre('/conversations/'+uuid+'/custom-attributes',method='PUT',data=attributes)
    return True
