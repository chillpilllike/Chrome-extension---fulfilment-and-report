"""Secretgreen verified customer entry and LibreDesk conversation bridge.

All order/customer scope is resolved server-side. Public data is an allowlist.
No procurement data or raw upstream errors reach customer endpoints.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
from typing import Optional
import re
import secrets
import threading
import time
from datetime import datetime, timezone
import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from app.support.odoo import SupportOrders
from app.support.policy import Evidence, public_order

STORE, WEBSITE, INBOX = 8, 1, 1
ORIGIN = 'https://secretgreen.com.au'
LIBRE = 'https://libredesk.185.194.236.161.sslip.io'
INBOX_UUID = '339d24ef-d7ab-4d1a-83ad-f2d0212335e0'
PREFIX = '/api/public/secretgreen-support'
ASSETS = Path(__file__).parent / 'assets'
_lock = threading.Lock()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def secret(name):
    value = os.getenv(name, '')
    if len(value) < 24:
        raise HTTPException(503, 'Support is temporarily unavailable. Please try again later.')
    return value


def code_digest(challenge, code):
    return hmac.new(secret('SUPPORT_SESSION_KEY').encode(), (challenge+':'+code).encode(), hashlib.sha256).hexdigest()


def sign_jwt(payload, key):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).rstrip(b'=').decode()
    body=enc({'alg':'HS256','typ':'JWT'})+'.'+enc(payload)
    sig=base64.urlsafe_b64encode(hmac.new(key.encode(),body.encode(),hashlib.sha256).digest()).rstrip(b'=').decode()
    return body+'.'+sig


def email_value(value):
    value=value.strip().lower()
    if len(value)>254 or not re.fullmatch(r"[a-z0-9.!#$&'*+/=?^`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+",value):
        raise HTTPException(422,'Enter a valid email address.')
    return value


def libre(path, *, data=None, token=None, method=None):
    headers={'X-Libredesk-Inbox-ID':INBOX_UUID, 'Origin':ORIGIN}
    if token:
        headers['Authorization']='Bearer '+token
    elif not path.startswith('/widget/'):
        headers['Authorization']='token '+os.getenv('SUPPORT_LIBREDESK_API_KEY','')+':'+secret('SUPPORT_LIBREDESK_API_SECRET')
    try:
        response=requests.request(method or ('POST' if data is not None else 'GET'), LIBRE+'/api/v1'+path,
                                  headers=headers,json=data,timeout=20)
        if not response.ok: raise ValueError()
        return response.json()['data']
    except Exception:
        raise HTTPException(503,'Chat is temporarily unavailable. Please try again later.') from None


class StartVerification(BaseModel):
    email: str = Field(max_length=254)

class Verify(BaseModel):
    challenge: str = Field(min_length=30,max_length=100)
    code: str = Field(pattern=r'^\d{6}$')

class BeginChat(BaseModel):
    order_id: Optional[int] = Field(default=None,gt=0)
    message: str = Field(min_length=1,max_length=4000)


class NativeOrderSelection(BaseModel):
    order_id: int = Field(gt=0)


class PrivateResponseRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def handle(request):
            response=await original(request)
            response.headers['Cache-Control']='no-store'
            response.headers['Referrer-Policy']='no-referrer'
            response.headers['X-Content-Type-Options']='nosniff'
            return response
        return handle


def create_portal_router(*, db, get_store, client_factory):
    router=APIRouter(route_class=PrivateResponseRoute)
    ready=False
    def ensure():
        nonlocal ready
        if ready:return
        with _lock:
            if ready:return
            with db() as c:
                c.execute('''CREATE TABLE IF NOT EXISTS support_portal_challenges (
                  id TEXT PRIMARY KEY, email TEXT NOT NULL, ip_hash TEXT NOT NULL, code_hash TEXT NOT NULL,
                  created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
                  attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0,
                  delivery_state TEXT NOT NULL DEFAULT 'pending')''')
                c.execute('''CREATE TABLE IF NOT EXISTS support_portal_sessions (
                  token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
                  created_at DOUBLE PRECISION NOT NULL, conversation_uuid TEXT UNIQUE, order_id BIGINT,
                  state TEXT NOT NULL DEFAULT 'ready', link_version INTEGER NOT NULL DEFAULT 1,
                  widget_token TEXT, status_card_state TEXT NOT NULL DEFAULT 'pending')''')
                c.execute('CREATE INDEX IF NOT EXISTS support_challenge_created ON support_portal_challenges(created_at)')
            ready=True
    def active():
        if os.getenv('SUPPORT_SECRETGREEN_ENABLED','false').lower()!='true':
            raise HTTPException(503,'Support chat is not available yet.')
        secret('SUPPORT_SESSION_KEY');ensure()
    def client():return client_factory(get_store(STORE))
    def session(request):
        active()
        token=request.headers.get('Authorization','').removeprefix('Bearer ')
        if not 30<=len(token)<=100:raise HTTPException(401,'Please verify your email again.')
        with db() as c:
            row=c.execute('SELECT * FROM support_portal_sessions WHERE token_hash=? AND expires_at>?',(digest(token),time.time())).fetchone()
        if not row:raise HTTPException(401,'Please verify your email again.')
        return dict(row)
    def partners(c,email):
        rows=c.search_read('res.partner',[['email_normalized','=',email]],['id','email'],limit=101)
        if len(rows)>100:raise HTTPException(409,'Our team needs to help locate your order.')
        return [r['id'] for r in rows if str(r.get('email') or '').strip().lower()==email]
    def owned_order(c,email,order_id):
        ids=partners(c,email)
        rows=c.search_read('sale.order',[['id','=',order_id],['website_id','=',WEBSITE],['partner_id','in',ids]],['id'],limit=1)
        if not rows:raise HTTPException(404,'No matching order was found on this website.')
        return SupportOrders(c,WEBSITE).detail(order_id)
    def card(order):
        result=public_order(order,Evidence(observed_at=datetime.now(timezone.utc)))
        if order.get('state') in {'sale','done'}:
            result.update(status='confirmed',reply='Your order is confirmed. Our team will check the latest dispatch and tracking details for you.')
        return result
    def no_store(response):
        response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @router.get('/public/secretgreen-support')
    def page():
        response=HTMLResponse((ASSETS/'customer.html').read_text())
        response.headers['Content-Security-Policy']="frame-ancestors 'self' https://secretgreen.com.au"
        return no_store(response)

    @router.get('/public/secretgreen-support/launcher.js')
    def launcher():
        return no_store(Response((ASSETS/'launcher.js').read_text(),media_type='application/javascript'))

    @router.post(PREFIX+'/request-code')
    def request_code(payload:StartVerification,request:Request):
        active();email=email_value(payload.email);now=time.time()
        # The reverse proxy supplies the client address to Uvicorn. Do not trust an arbitrary XFF list here.
        ip=digest((request.client.host if request.client else 'unknown')+secret('SUPPORT_SESSION_KEY'))
        challenge=secrets.token_urlsafe(32);code=f'{secrets.randbelow(1000000):06d}'
        with db() as c:
            c.execute('SELECT pg_advisory_xact_lock(81910412)')
            rows=c.execute('SELECT email,ip_hash FROM support_portal_challenges WHERE created_at>?',(now-3600,)).fetchall()
            if len(rows)>=60 or sum(r['email']==email for r in rows)>=3 or sum(r['ip_hash']==ip for r in rows)>=20:
                raise HTTPException(429,'Too many code requests. Please try again later.')
            c.execute('INSERT INTO support_portal_challenges(id,email,ip_hash,code_hash,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                      (challenge,email,ip,code_digest(challenge,code),now,now+600))
        # Do not look up an order before verifying email: the response does not enumerate customers.
        try:
            odoo=client();mail_id=odoo.execute('mail.mail','create',[{
              'subject':'Your Secretgreen support verification code', 'email_to':email,
              'body_html':f'<p>Your Secretgreen support verification code is <strong>{code}</strong>.</p><p>It expires in 10 minutes. If you did not request this code, you can ignore this email.</p>',
              'auto_delete':False}])
            # Some Odoo deployments send successfully but return an XML-RPC
            # serialization fault for the method's None result. Check the persisted
            # delivery state before deciding whether the email failed. Never resend.
            try:
                odoo.execute('mail.mail','send',[[mail_id]])
            except Exception:
                pass
            sent=odoo.read('mail.mail',[mail_id],['state'])
            if not sent or sent[0]['state']!='sent':raise ValueError()
            with db() as c:c.execute("UPDATE support_portal_challenges SET delivery_state='sent' WHERE id=?",(challenge,))
        except Exception:
            with db() as c:c.execute("UPDATE support_portal_challenges SET delivery_state='failed',consumed=1 WHERE id=?",(challenge,))
            raise HTTPException(503,'We could not send your verification code. Please try again later.') from None
        return {'challenge':challenge,'message':'Check your email for a six-digit verification code.'}

    @router.post(PREFIX+'/verify')
    def verify(payload:Verify):
        active();token=secrets.token_urlsafe(32);now=time.time();valid=False
        with db() as c:
            row=c.execute('SELECT * FROM support_portal_challenges WHERE id=? FOR UPDATE',(payload.challenge,)).fetchone()
            if row and not row['consumed'] and row['attempts']<5 and row['expires_at']>now and row['delivery_state']=='sent':
                c.execute('UPDATE support_portal_challenges SET attempts=attempts+1 WHERE id=?',(payload.challenge,))
                valid=hmac.compare_digest(row['code_hash'],code_digest(payload.challenge,payload.code))
                if valid:
                    c.execute('UPDATE support_portal_challenges SET consumed=1 WHERE id=?',(payload.challenge,))
                    c.execute('INSERT INTO support_portal_sessions(token_hash,email,expires_at,created_at) VALUES(?,?,?,?)',(digest(token),row['email'],now+3600,now))
        if not valid:raise HTTPException(401,'That code is invalid or expired. Request a new code if needed.')
        return {'token':token,'expires_in':3600}

    @router.get(PREFIX+'/orders')
    def orders(request:Request,page:int=1,q:str=''):
        user=session(request)
        if page<1 or page>1000 or len(q)>80:raise HTTPException(422,'Invalid search.')
        try:
            c=client();ids=partners(c,user['email'])
            domain=[['website_id','=',WEBSITE],['partner_id','in',ids],['order_line','!=',False]]
            if q:domain.append(['name','ilike',q.replace('%','\\%').replace('_','\\_')])
            rows=c.search_read('sale.order',domain,['id','name','state','amount_total','currency_id','date_order'],limit=26,offset=(page-1)*25,order='date_order desc,id desc')
            return {'orders':[{'id':r['id'],'date':r['date_order'],**card(r)} for r in rows[:25]],'has_more':len(rows)>25,'page':page}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'We could not load your orders. You can still start a chat without selecting an order.') from None

    @router.post(PREFIX+'/start')
    def start(payload:BeginChat,request:Request):
        user=session(request)
        if not payload.message.strip():raise HTTPException(422,'Enter a message for the team.')
        c=client();order=owned_order(c,user['email'],payload.order_id) if payload.order_id else None
        preview=card(order) if order else {'reply':'No order is linked. Our support team can help with your question.'}
        with db() as conn:
            current=conn.execute('SELECT * FROM support_portal_sessions WHERE token_hash=? FOR UPDATE',(user['token_hash'],)).fetchone()
            if current['state']=='linked':
                if current['order_id'] != payload.order_id:raise HTTPException(409,'This chat is already linked to another selection.')
                return {'session_token':current['widget_token'],'conversation_uuid':current['conversation_uuid'],'reply':preview['reply'],'libredesk_url':LIBRE,'inbox_uuid':INBOX_UUID}
            if current['state']!='ready':raise HTTPException(409,'Your chat request is being checked. Please do not start another copy.')
            conn.execute("UPDATE support_portal_sessions SET state='starting',order_id=? WHERE token_hash=?",(payload.order_id,user['token_hash']))
        # Stable per-brand identity; email verification precedes signing.
        jwt=sign_jwt({'external_user_id':'secretgreen:'+digest(user['email']), 'email':user['email'],
                      'first_name':'Customer','iat':int(time.time()),'exp':int(time.time())+120},secret('SUPPORT_LIBREDESK_INBOX_SECRET'))
        try:
            auth=libre('/widget/chat/auth/exchange',data={'jwt':jwt});widget=auth['session_token']
            message=payload.message.strip()
            if order:message='Order '+preview['reference']+'\n'+message
            conversation=libre('/widget/chat/conversations/init',data={'message':message,'form_data':{}},token=widget)['conversation']
            uuid=conversation['uuid']
            with db() as conn:conn.execute("UPDATE support_portal_sessions SET state='linked',conversation_uuid=?,widget_token=? WHERE token_hash=?",(uuid,widget,user['token_hash']))
        except Exception:
            with db() as conn:conn.execute("UPDATE support_portal_sessions SET state='uncertain' WHERE token_hash=?",(user['token_hash'],))
            raise HTTPException(503,'We could not confirm that your chat started. Our team will need to check before another attempt.') from None
        # One attempt only: transport uncertainty must never blindly replay a customer message.
        with db() as conn:conn.execute("UPDATE support_portal_sessions SET status_card_state='sending' WHERE token_hash=?",(user['token_hash'],))
        try:
            libre('/conversations/'+uuid+'/messages',data={'message':'<p>'+html.escape(preview['reply'])+'</p>','private':False,'sender_type':'agent'})
            state='sent'
        except Exception:state='uncertain'
        with db() as conn:conn.execute('UPDATE support_portal_sessions SET status_card_state=? WHERE token_hash=?',(state,user['token_hash']))
        return {'session_token':widget,'conversation_uuid':uuid,'reply':preview['reply'],'libredesk_url':LIBRE,'inbox_uuid':INBOX_UUID}

    def tool_identity(request, verified=False):
        active()
        supplied=request.headers.get('X-Secretgreen-Tool-Key','')
        if not hmac.compare_digest(supplied,secret('SUPPORT_LIBREDESK_TOOL_KEY')):
            raise HTTPException(403,'Tool authentication required.')
        if request.headers.get('X-Libredesk-Inbox-Id')!=str(INBOX):
            raise HTTPException(403,'Wrong support inbox.')
        uuid=request.headers.get('X-Libredesk-Conversation-UUID','')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}',uuid):
            raise HTTPException(403,'Conversation identity required.')
        if verified and request.headers.get('X-Libredesk-Contact-Verified')!='true':
            raise HTTPException(403,'Email verification is required before accessing orders.')
        return uuid,email_value(request.headers.get('X-Libredesk-Contact-Email',''))

    def customer_domain(c,email):
        return [['website_id','=',WEBSITE],['partner_id','in',partners(c,email)],['order_line','!=',False]]

    @router.post(PREFIX+'/tools/customer-match')
    def customer_match(request:Request):
        # Native LibreDesk injects contact identity separately from model arguments.
        _,email=tool_identity(request)
        try:
            c=client();found=bool(c.search_read('sale.order',customer_domain(c,email),['id'],limit=1))
            return {'has_orders':found,'next_step':'send_email_verification' if found else 'continue_general_chat',
                    'message':'Verify email before retrieving any order details.' if found else 'No orders found with this email on Secretgreen. Continue general chat.'}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Order lookup is unavailable; offer human assistance.') from None

    @router.post(PREFIX+'/tools/orders')
    def native_orders(request:Request):
        _,email=tool_identity(request,verified=True)
        try:
            c=client();rows=c.search_read('sale.order',customer_domain(c,email),['id','name','state','amount_total','currency_id','date_order'],limit=26,order='date_order desc,id desc')
            return {'orders':[{'id':r['id'],'date':r['date_order'],**card(r)} for r in rows[:25]],
                    'has_more':len(rows)>25,'instruction':'Ask which order the customer means. Only use the listed customer-safe fields. If there are more orders and none match, offer human assistance.'}
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Orders are unavailable; offer human assistance.') from None

    @router.post(PREFIX+'/tools/link-order')
    def native_link(payload:NativeOrderSelection,request:Request):
        uuid,email=tool_identity(request,verified=True)
        if not payload.order_id:raise HTTPException(422,'Choose an order first.')
        try:order=owned_order(client(),email,payload.order_id)
        except HTTPException:raise
        except Exception:raise HTTPException(503,'Order lookup is unavailable; offer human assistance.') from None
        # Conversation binding also powers the official secured agent context link.
        with db() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(81910413)')
            row=conn.execute('SELECT * FROM support_portal_sessions WHERE conversation_uuid=?',(uuid,)).fetchone()
            if row:
                conn.execute("UPDATE support_portal_sessions SET email=?,order_id=?,link_version=link_version+1 WHERE conversation_uuid=?",(email,payload.order_id,uuid))
            else:
                conn.execute("INSERT INTO support_portal_sessions(token_hash,email,expires_at,created_at,conversation_uuid,order_id,state,status_card_state) VALUES(?,?,?,?,?,?,'linked','native_ai')",
                             (digest(secrets.token_urlsafe(32)),email,time.time()+1800,time.time(),uuid,payload.order_id))
        return {'linked':True,'order_id':payload.order_id,**card(order)}

    @router.get('/public/secretgreen-support/agent')
    def agent_page():
        return no_store(HTMLResponse((ASSETS/'agent.html').read_text()))

    @router.get(PREFIX+'/agent-context')
    def agent_context(request:Request):
        active()
        # LibreDesk grants this encrypted token only after enforcing conversation access.
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            raw=base64.b64decode(request.headers.get('X-Support-Context',''),validate=True)
            claims=json.loads(AESGCM(secret('SUPPORT_CONTEXT_KEY').encode()).decrypt(raw[:12],raw[12:],None))
            if claims['exp']<time.time() or claims['iat']>time.time()+30 or claims['exp']-claims['iat']>1200 or int(claims['agent_id'])<=0:raise ValueError()
            uuid=claims['conversation_uuid']
            with db() as conn:row=conn.execute('SELECT * FROM support_portal_sessions WHERE conversation_uuid=?',(uuid,)).fetchone()
            if not row or row['email'].lower()!=claims['email'].lower():raise ValueError()
        except Exception:raise HTTPException(403,'Open the order panel again from the linked LibreDesk conversation.') from None
        if not row['order_id']:return {'order':None,'message':'No order linked to this conversation.'}
        c=client();order=owned_order(c,row['email'],row['order_id']);history=SupportOrders(c,WEBSITE).timeline(row['order_id'])
        with db() as conn:
            procurement=[dict(r) for r in conn.execute('SELECT id,asin,quantity,state,ordered_at,amazon_order_id,amazon_status FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id LIMIT 200',(STORE,row['order_id'])).fetchall()]
        return {'order':order,'history':history,'internal_fulfilment':procurement,'customer_preview':card(order),'link_version':row['link_version']}
    return router
