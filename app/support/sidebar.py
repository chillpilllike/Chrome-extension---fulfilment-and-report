"""Order summaries rendered in LibreDesk's native sidebar contact notes."""
from html import escape

def sync_sidebar_order(client,libre,uuid,email,order):
    conv=libre('/conversations/'+uuid)
    contact_id=conv.get('contact_id')
    if not contact_id:return False
    contact=libre('/contacts/'+str(contact_id))
    if str(contact.get('email') or '').strip().lower()!=email:return False
    marker='Secretgreen order '+str(order['name'])+' · Odoo '+str(order['id'])
    notes=libre('/contacts/'+str(contact_id)+'/notes')
    if not isinstance(notes,list):return False
    # Notes are contact-wide: label the order explicitly; never claim this is every conversation's selection.
    if any(marker in str(n.get('note','')) for n in notes):return True
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
    esc=lambda x:escape(str(x or ''),quote=True)
    url='https://secretgreen.com.au/web#id='+str(int(order['id']))+'&model=sale.order&view_type=form'
    parts=['<p><strong>'+esc(marker)+'</strong></p>','<p><a href="'+esc(url)+'" target="_blank">Open order '+esc(order['name'])+' in Odoo ↗</a></p>','<p>Total: '+esc(order.get('amount_total'))+' '+esc((order.get('currency_id') or ['', ''])[1])+'</p><ul>']
    for line in order.get('items',[])[:100]:
        name=esc(line.get('name'));product=line.get('product_id');link=links.get(product[0]) if isinstance(product,(list,tuple)) and product else None
        if link:name='<a href="'+esc(link)+'" target="_blank">'+name+'</a>'
        parts.append('<li>'+name+' — Qty: '+esc(line.get('product_uom_qty'))+' · Total: '+esc(line.get('price_total'))+(' (no published product page)' if product and not link else '')+'</li>')
    parts.append('</ul><p>Order snapshot when linked. Open Odoo for the latest values.</p>')
    libre('/contacts/'+str(contact_id)+'/notes',method='POST',data={'note':''.join(parts)})
    return True
