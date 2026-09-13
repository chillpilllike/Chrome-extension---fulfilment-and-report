"""Native verified chat bridge to post-order care. No model-supplied customer/order scope."""
import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
import requests
from app.support.journey import email_history, public_text

PREFIX = '/api/public/support-post-order'
RULES = ('Use dispatch.reply and dispatch.estimated_dispatch for dispatch questions. Never recalculate a date or use an old email/case date if dispatch.estimated_dispatch is absent. The date is an estimate, never a guarantee. Do not reveal the source, supplier, inbound dates or internal handling calculation. Use current facts only. Sent means provider acceptance; delivered means recipient mail server acceptance. '
         'No click is NOT no reply. No recorded selection means only no selection recorded in post-order care. '
         'Never say the customer ignored/read an email. If they replied elsewhere, acknowledge this and offer team review. '
         'Do not expose internal identifiers, supplier data, sourcing, costs, or raw email bodies. '
         'A requested refund/replacement/cancellation is not completed execution. '
         'Explain affected items/options only when customer_details_available=true. A missing case does not prove no issue or no dispatch. Offer only the returned resend candidate, once. After explicit agreement use its offer_token. '
         'Do not ask for another team approval for a valid resend. Say resent only if sent=true.')

def fail(message='This order update needs team review.', status=409):
    raise HTTPException(status, message)

def master():
    key=os.getenv('SUPPORT_LIBREDESK_TOOL_KEY','')
    if len(key)<24:fail('Post-order support is not configured.',503)
    return key

def scope_key(store, website, inbox):
    return hmac.new(master().encode(),f'post-order:{store}:{website}:{inbox}'.encode(),hashlib.sha256).hexdigest()

def issue_offer(scope,message_id,incoming_id,now=None):
    body=base64.urlsafe_b64encode(json.dumps(dict(scope,message_id=message_id,incoming_id=incoming_id,expires=int(now or time.time())+600),sort_keys=True).encode()).decode().rstrip('=')
    return body+'.'+hmac.new(master().encode(),body.encode(),hashlib.sha256).hexdigest()

def read_offer(token,scope,now=None):
    try:
        body,sig=token.split('.')
        if not hmac.compare_digest(sig,hmac.new(master().encode(),body.encode(),hashlib.sha256).hexdigest()):raise ValueError()
        data=json.loads(base64.urlsafe_b64decode(body+'='*(-len(body)%4)))
        if data['expires']<int(now or time.time()) or any(data.get(k)!=v for k,v in scope.items()):raise ValueError()
        return data
    except (ValueError,KeyError,TypeError):fail('Refresh the order update before offering this email again.')

def plain(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>',' ',str(value or '')))).strip().lower()

def explicit_resend(value):
    # Deliberately bounded commands, not quoted emails, complaints, or AI inference.
    value=plain(value).strip(' .!?')
    if len(value)>180:return False
    return bool(re.fullmatch(r"(?:please |yes[, ]*|oui[, ]*|s’il vous plaît |s'il vous plaît )?(?:resend|re-send|send (?:it|the email|that email) again|renvo(?:yez|ie|yer)(?:-moi)?|réexpédiez)(?: (?:it|the email|that email|this email|the update|l[’']e-?mail|le mail|le courriel|le message|cela))?(?: (?:please|again|s[’']il vous plaît|svp))?",value))

def consent(messages,offer):
    rows=sorted(messages,key=lambda m:int(m.get('id') or 0))
    incoming=[m for m in rows if m.get('type')=='incoming' and not m.get('private')]
    if not incoming:fail('Ask the customer whether they want this email resent.')
    latest=incoming[-1];text=plain(latest.get('content'))
    if int(latest['id'])<int(offer['incoming_id']):fail()
    if explicit_resend(text):return int(latest['id'])
    if int(latest['id'])<=int(offer['incoming_id']):fail('Wait for the customer to agree before resending.')
    replies=[m for m in rows if int(offer['incoming_id'])<int(m.get('id') or 0)<int(latest['id']) and m.get('type')=='outgoing' and not m.get('private')]
    offered=bool(replies and re.search(r'resend|send.{0,25}again|renvo|réexpédi',plain(replies[-1].get('content'))))
    if offered and re.fullmatch(r'(?:yes|yes please|please do|go ahead|sure|ok|okay|oui|oui merci|oui svp|oui s[’\']il vous plaît|d[’\']accord|volontiers)[.! ]*',text):return int(latest['id'])
    fail('Ask for a clear confirmation to resend this email.')

def native(path):
    base=os.getenv('SUPPORT_LIBREDESK_BASE_URL','https://support.gofinch.com').rstrip('/')
    headers={'Authorization':'token '+os.getenv('SUPPORT_LIBREDESK_API_KEY','')+':'+os.getenv('SUPPORT_LIBREDESK_API_SECRET','')}
    try:
        r=requests.get(base+'/api/v1'+path,headers=headers,timeout=15);r.raise_for_status();return r.json()['data']
    except (requests.RequestException,ValueError,KeyError):fail('Support history could not be checked. Please ask the team.',503)

def list_messages(uuid):
    value=native('/conversations/'+uuid+'/messages')
    return value.get('results',[]) if isinstance(value,dict) else value

def dispatch_update(db,client,store,website,order):
    """Only derived public status leaves this boundary; source rows remain internal."""
    from app.support.live import order_evidence
    from app.support.policy import Evidence,public_status
    try:
        fields=client.existing_fields('sale.order.line',['id','product_id','display_type','is_delivery','product_uom_qty'])
        items=client.search_read('sale.order.line',[('order_id','=',order['id'])],fields,limit=501)
        detail=dict(order,items=items[:500],items_truncated=len(items)>500)
        with db() as conn:evidence=order_evidence(conn,store,detail,website=website)
        return public_status(order['state'],evidence)
    except Exception:
        return public_status(order['state'],Evidence(observed_at=datetime.min.replace(tzinfo=timezone.utc)))

class Resend(BaseModel):
    offer_token:str=Field(min_length=40,max_length=3000)

def create_router(db,get_store,client_factory):
    router=APIRouter()
    def identity(request,store,website,inbox):
        if not hmac.compare_digest(request.headers.get('X-Postorder-Key',''),scope_key(store,website,inbox)):fail('Tool authentication required.',403)
        if request.headers.get('X-Libredesk-Inbox-Id')!=str(inbox) or request.headers.get('X-Libredesk-Contact-Verified')!='true':fail('Verify your email and select your order first.',403)
        uuid=request.headers.get('X-Libredesk-Conversation-UUID','');email=request.headers.get('X-Libredesk-Contact-Email','').strip().lower()
        if not re.fullmatch(r'[a-fA-F0-9-]{36}',uuid) or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):fail('Conversation identity required.',403)
        conversation=native('/conversations/'+uuid)
        if conversation.get('inbox_id')!=inbox or str((conversation.get('contact') or {}).get('email','')).lower().strip()!=email:fail('Conversation identity changed.',403)
        client=client_factory(get_store(store))
        if inbox==1 and store==8 and website==1:
            with db() as conn:binding=conn.execute('SELECT order_id FROM support_portal_sessions WHERE conversation_uuid=? AND lower(email)=?',(uuid,email)).fetchone()
            order_id=binding['order_id'] if binding else None
        else:
            bindings=client.search_read('libredesk.order.binding',[('conversation_uuid','=',uuid),('website_id','=',website),('email','=',email)],['order_id'],limit=1)
            order_id=bindings[0]['order_id'][0] if bindings and bindings[0].get('order_id') else None
        if not order_id:fail('Select and link an order in this chat first.')
        orders=client.search_read('sale.order',[('id','=',order_id),('website_id','=',website),('partner_id.email_normalized','=',email)],['id','name','state'],limit=1)
        if not orders:fail('The selected order no longer matches this customer and website.',403)
        return dict(store=store,website=website,inbox=inbox,uuid=uuid,email=email,order=order_id),orders[0]

    @router.post(PREFIX+'/{store}/{website}/{inbox}/status')
    def status(request:Request,store:int,website:int,inbox:int):
        scope,order=identity(request,store,website,inbox)
        dispatch=dispatch_update(db,client_factory(get_store(store)),store,website,order)
        from app import main as app
        from app.support.followup import resend_eligibility
        with db() as conn:
            history=email_history(conn,store,order['id'],scope['email'],website)
            rows=conn.execute('SELECT * FROM after_order_cases WHERE store_id=? AND website_id=? AND odoo_order_id=? AND lower(trim(customer_email))=? ORDER BY updated_at DESC LIMIT 20',(store,website,order['id'],scope['email'])).fetchall()
        cases=[];candidate=None
        for row in rows:
            case=app.after_order_case_by_id(row['id'])
            if not app.after_order_case_is_in_scope(case):continue
            current=case.get('status') not in {'resolved','closed'} and not case.get('confirmed_at')
            if case['case_type']=='item_unavailable':
                review=app.after_order_unavailable_review(case)
                publish=current and review['approved'] and not review['blocked']
            else:publish=current and app.after_order_tracking_is_current(case)
            entry={'topic':case['case_type'],'customer_details_available':publish,'current':current,'selection_recorded':bool(case.get('current_decision')),'team_confirmed':bool(case.get('confirmed_at'))}
            if publish:
                entry['affected_items']=[{'name':public_text(x.get('product_name')),'quantity':x.get('quantity')} for x in case.get('affected_items',[]) if public_text(x.get('product_name'))]
                entry['estimated_dispatch']=dispatch.get('estimated_dispatch')
                entry['available_choices']=app.after_order_allowed_actions(case)
            cases.append(entry)
            if candidate is None:
                with db() as conn:messages=conn.execute("SELECT * FROM after_order_messages WHERE case_id=? AND recipient=? AND status='sent' AND test_mode=0 ORDER BY id DESC LIMIT 1",(case['id'],scope['email'])).fetchall()
                for message in messages:
                    try:
                        resend_eligibility(app,dict(message),case)
                        candidate={'message_id':message['id'],'topic':case['case_type'],'sent_at':message['created_at']}
                    except HTTPException:pass
        if candidate:
            incoming=[m for m in list_messages(scope['uuid']) if m.get('type')=='incoming' and not m.get('private')]
            if incoming:candidate['offer_token']=issue_offer(scope,candidate.pop('message_id'),max(int(m['id']) for m in incoming))
            else:candidate=None
        return {'order_reference':public_text(order['name']),'order_status':{'sale':'confirmed','done':'confirmed','draft':'not_confirmed','sent':'not_confirmed','cancel':'cancelled'}.get(order['state'],'needs_review'),'dispatch':dispatch,'post_order':cases,'email_history':history,'resend_offer':candidate,'instruction':RULES}

    @router.post(PREFIX+'/{store}/{website}/{inbox}/resend')
    def resend(payload:Resend,request:Request,store:int,website:int,inbox:int):
        scope,order=identity(request,store,website,inbox)
        offer=read_offer(payload.offer_token,scope)
        incoming_id=consent(list_messages(scope['uuid']),offer)
        from app.support.followup import resend_action_email
        result=resend_action_email(db,store,website,order,scope['email'],scope['uuid'],offer['message_id'])
        # Audit only after the transport result is known; failures stay in email log.
        from app import main as app
        with db() as conn:
            row=conn.execute('SELECT case_id FROM after_order_messages WHERE id=?',(offer['message_id'],)).fetchone()
            app.record_after_order_event(conn,row['case_id'],'customer_requested_chat_resend',actor_type='customer',details={'conversation_uuid':scope['uuid'],'incoming_message_id':incoming_id,'original_message_id':offer['message_id'],'already_sent':result.get('already_sent',False)})
        return result
    return router
