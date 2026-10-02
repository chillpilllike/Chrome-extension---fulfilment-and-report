"""Explicit, staff-composed Twilio messages. No automatic send or retry."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, HTTPException
from app.services.care_sms import deliver, Rejected, sms_customer_number
from app.services.notification_i18n import sms_segments

SCHEMA = '''CREATE TABLE IF NOT EXISTS manual_sms (
 id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE,
 store_id INTEGER NOT NULL, website_id INTEGER NOT NULL, odoo_order_id INTEGER,
 odoo_order_name TEXT NOT NULL, sender_domain TEXT NOT NULL, recipient TEXT NOT NULL,
 body TEXT NOT NULL, actor TEXT NOT NULL, snapshot_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft', provider_id TEXT, last_error TEXT,
 attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);'''

def validate_body(value):
    if not isinstance(value,str) or not value.strip() or len(value)>1000:
        raise ValueError('Enter a message between 1 and 1,000 characters.')
    if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',value):
        raise ValueError('Message contains unsupported control characters.')
    return value.strip()

class ManualSMS:
    def __init__(self, sms):
        self.sms=sms
        self.r=sms.r

    def site(self, store, website):
        cfg=self.sms.config()
        if not cfg['enabled'] or cfg['provider']!='twilio':
            raise ValueError('Enable Twilio SMS in Settings first.')
        if self.r.after_order_email_test_mode():
            raise ValueError('Manual SMS is disabled in test mode. No test messages will be sent.')
        mapping=cfg['mappings'].get(f'{store}:{website}',{})
        sender=mapping.get('twilio') or {}
        if not mapping.get('transactional_sms_enabled') or not (sender.get('sender') or sender.get('messaging_service_sid')):
            raise ValueError('SMS is not enabled for this website.')
        client=self.r.OdooClient(self.r.get_store(store))
        rows=client.read('website',[website],['name','domain'])
        if not rows: raise ValueError('Website not found.')
        return client,rows[0],sender

    def target(self, payload):
        store=int(payload['store_id']);website=int(payload.get('website_id') or 0)
        order_id=int(payload.get('order_id') or 0)
        order=None
        if order_id:
            client=self.r.OdooClient(self.r.get_store(store))
            rows=client.read('sale.order',[order_id],['name','partner_id','website_id','amount_total','currency_id'])
            if not rows or not rows[0].get('website_id'):raise ValueError('Website order not found.')
            order=rows[0]
            if website and website!=order['website_id'][0]:raise ValueError('Order website changed.')
            website=order['website_id'][0]
        client,site,sender=self.site(store,website)
        info={'store_id':store,'website_id':website,'odoo_order_id':order_id or None,
              'odoo_order_name':'Manual SMS','sender_domain':site.get('domain') or site['name'],
              'customer_name':'','order_total':'','currency':'','sender':sender}
        if order_id:
            fields=client.existing_fields('res.partner',['name','mobile','phone','phone_blacklisted','country_id'])
            if 'phone_blacklisted' not in fields: raise ValueError('Cannot verify customer SMS opt-out.')
            partner=client.read('res.partner',[order['partner_id'][0]],fields)[0]
            if partner.get('phone_blacklisted'): raise ValueError('Customer has opted out of SMS.')
            country=partner.get('country_id');code=None
            if country: code=client.read('res.country',[country[0]],['code'])[0]['code']
            recipient=sms_customer_number([partner.get('mobile'),partner.get('phone')],code)
            info.update(odoo_order_name=order['name'],customer_name=partner.get('name') or '',
                        order_total=str(order['amount_total']),currency=order['currency_id'][1])
        else:
            raw=str(payload.get('recipient') or '')
            if not raw.startswith('+'):raise ValueError('Include the international country code, starting with +.')
            recipient=sms_customer_number([raw])
        # Also covers manually entered numbers. Fail closed if suppression lookup fails.
        blocked=client.execute('phone.blacklist','search',[[('number','=',recipient),('active','=',True)]],{'limit':1})
        if blocked: raise ValueError('This mobile number is on the SMS opt-out list.')
        info['recipient']=recipient
        return info

    def preview(self,payload):
        body=validate_body(payload.get('body'))
        actor=str(payload.get('actor') or '').strip()
        if not actor or len(actor)>100: raise ValueError('Enter the sending team member’s name (up to 100 characters).')
        if payload.get('consent_confirmed') is not True: raise ValueError('Confirm this is an authorized service message, not unsolicited marketing.')
        key=str(payload.get('request_key') or '')
        if not re.fullmatch(r'[a-zA-Z0-9-]{20,80}',key): raise ValueError('Invalid request ID.')
        info=self.target(payload)
        with self.r.db() as conn:
            existing=conn.execute('SELECT * FROM manual_sms WHERE request_key=?',(key,)).fetchone()
            if existing:
                if existing['body']!=body or existing['actor']!=actor or json.loads(existing['snapshot_json'])!=info:
                    raise ValueError('Request ID already belongs to another message. Start a new preview.')
                return self.view(existing['id'])
            now=self.r.utc_now()
            conn.execute('''INSERT INTO manual_sms(request_key,store_id,website_id,odoo_order_id,odoo_order_name,sender_domain,
                recipient,body,actor,snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key,info['store_id'],info['website_id'],info['odoo_order_id'],info['odoo_order_name'],info['sender_domain'],info['recipient'],body,actor,json.dumps(info),now,now))
            ident=conn.execute('SELECT id FROM manual_sms WHERE request_key=?',(key,)).fetchone()['id']
        return self.view(ident)

    def view(self,ident):
        with self.r.db() as conn:
            raw=conn.execute('SELECT * FROM manual_sms WHERE id=?',(ident,)).fetchone()
        if not raw: raise ValueError('Message not found.')
        row=dict(raw);row.pop('request_key');info=json.loads(row.pop('snapshot_json'))
        row.update(customer_name=info['customer_name'],order_total=info['order_total'],currency=info['currency'],
                   segment_estimate=sms_segments(row['body']),provider='twilio',actor_identity='Staff-entered name; shared admin access')
        return row

    def send(self,ident):
        # Serialize identical messages even across concurrent preview IDs.
        with self.r.db() as conn:
            raw=conn.execute('SELECT * FROM manual_sms WHERE id=? FOR UPDATE',(ident,)).fetchone()
            if not raw: raise ValueError('Message not found.')
            row=dict(raw)
            if row['status']!='draft': return {'id':ident,'status':row['status'],'already_attempted':True}
            if datetime.fromisoformat(row['created_at']) < datetime.now(timezone.utc)-timedelta(minutes=10):
                raise ValueError('Preview expired. Create a fresh preview.')
            before=json.loads(row['snapshot_json'])
            current=self.target({**before,'order_id':before['odoo_order_id']})
            if current!=before: raise ValueError('Customer, order or sender changed. Create a new preview.')
            lock=int.from_bytes(hashlib.sha256((row['recipient']+row['body']).encode()).digest()[:8],'big',signed=True)
            conn.execute('SELECT pg_advisory_xact_lock(?)',(lock,))
            duplicate=conn.execute("SELECT id FROM manual_sms WHERE recipient=? AND body=? AND attempts>0 AND created_at>=? LIMIT 1",
                (row['recipient'],row['body'],(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat())).fetchone()
            if duplicate: raise ValueError('An identical message was attempted within five minutes. Check the SMS log first.')
            conn.execute("UPDATE manual_sms SET status='sending',attempts=1,updated_at=? WHERE id=?",(self.r.utc_now(),ident))
        try:
            # Recheck global test-mode immediately before calling the provider.
            if self.r.after_order_email_test_mode(): raise Rejected('Test mode enabled; manual send blocked.')
            provider_id,status=deliver({**row,'provider':'twilio'},current['sender'])
            error=None
        except Rejected as exc:
            provider_id,status,error=None,'failed',str(exc)
        except Exception:
            provider_id,status,error=None,'delivery_unknown','Provider outcome uncertain. Do not resend; check Twilio.'
        with self.r.db() as conn:
            conn.execute('UPDATE manual_sms SET status=?,provider_id=?,last_error=?,updated_at=? WHERE id=?',
                         (status,provider_id,error,self.r.utc_now(),ident))
        return self.view(ident)

    def refresh(self,ident):
        row=self.view(ident)
        if not row['provider_id']: return row
        account=os.getenv('TWILIO_ACCOUNT_SID','');token=os.getenv('TWILIO_AUTH_TOKEN','')
        if not re.fullmatch(r'AC[0-9a-fA-F]{32}',account) or not re.fullmatch(r'S[MM][0-9a-fA-F]{32}',row['provider_id']):
            raise ValueError('Invalid Twilio reference.')
        response=requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{account}/Messages/{row["provider_id"]}.json',auth=(account,token),timeout=25)
        response.raise_for_status();data=response.json();status=data.get('status')
        if status not in {'accepted','queued','sending','sent','delivered','undelivered','failed','canceled'}:
            raise ValueError('Unrecognized provider status.')
        with self.r.db() as conn:
            conn.execute("UPDATE manual_sms SET status=?,last_error=?,updated_at=? WHERE id=? AND status NOT IN ('delivered','undelivered','failed','canceled')",
                (status,str(data.get('error_code')) if data.get('error_code') else None,self.r.utc_now(),ident))
        return self.view(ident)

    def reconcile(self):
        with self.r.db() as conn:
            rows=conn.execute("SELECT id FROM manual_sms WHERE provider_id IS NOT NULL AND status IN ('accepted','queued','sending','sent') AND created_at>=? ORDER BY updated_at LIMIT 30",((datetime.now(timezone.utc)-timedelta(days=30)).isoformat(),)).fetchall()
        for row in rows:
            try:self.refresh(row['id'])
            except Exception:continue  # Receipt lookup never resends a message.

    def router(self):
        router=APIRouter(prefix='/api/after-order/sms/manual')
        def safe(fn,*args):
            try:return fn(*args)
            except (ValueError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
            except Exception as exc:raise HTTPException(502,'Could not verify customer/provider data. No automatic retry was made.') from exc
        @router.get('/stores')
        def stores():
            enabled={int(key.split(':')[0]) for key,value in self.sms.config()['mappings'].items() if value.get('transactional_sms_enabled') and value.get('twilio')}
            with self.r.db() as conn:
                rows=conn.execute('SELECT id,name FROM stores WHERE active=1 ORDER BY name').fetchall()
            return [dict(row) for row in rows if row['id'] in enabled]
        @router.get('/lookup')
        def lookup(store_id:int,order_number:str):
            reference=order_number.strip()
            if not reference or len(reference)>80:raise HTTPException(400,'Enter the complete order number.')
            websites=[int(key.split(':')[1]) for key,value in self.sms.config()['mappings'].items()
                      if int(key.split(':')[0])==store_id and value.get('transactional_sms_enabled') and value.get('twilio')]
            if not websites:raise HTTPException(409,'SMS is not enabled for this store.')
            def resolve():
                client=self.r.OdooClient(self.r.get_store(store_id))
                pattern=reference.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
                rows=client.search_read('sale.order',[('name','=ilike',pattern),('website_id','in',websites)],['id','name'],limit=2)
                if not rows:raise ValueError('Order not found in the selected store. Check the complete order number.')
                if len(rows)!=1:raise ValueError('More than one order has this reference. Use a unique order number.')
                result=self.target({'store_id':store_id,'order_id':rows[0]['id']});result.pop('sender');return result
            return safe(resolve)
        @router.get('/sites')
        def sites():
            rows=[]
            for key,site in self.sms.config()['mappings'].items():
                if site.get('transactional_sms_enabled') and site.get('twilio'):
                    store,website=map(int,key.split(':'))
                    rows.append({'store_id':store,'website_id':website,'label':f'Store {store} · Website {website}'})
            with self.r.db() as conn:
                names=conn.execute('SELECT store_id,website_id,MAX(sender_domain) AS domain FROM after_order_cases GROUP BY store_id,website_id').fetchall()
            lookup={(x['store_id'],x['website_id']):x['domain'] for x in names}
            for row in rows:row['label']=lookup.get((row['store_id'],row['website_id'])) or row['label']
            return rows
        @router.get('/orders')
        def orders(q:str='',store_id:int=0):
            if len(q.strip())<2:return []
            with self.r.db() as conn:
                rows=conn.execute('''SELECT DISTINCT l.store_id,l.odoo_order_id,l.odoo_order_name
                    FROM order_lines l WHERE LOWER(l.odoo_order_name) LIKE ? AND (?=0 OR l.store_id=?)
                    ORDER BY l.odoo_order_id DESC LIMIT 30''',('%'+q.strip().lower()[:60]+'%',store_id,store_id)).fetchall()
            return [dict(x) for x in rows]
        @router.get('/order')
        def order(store_id:int,order_id:int,website_id:int=0):
            result=safe(self.target,locals());result.pop('sender');return result
        @router.post('/preview')
        def preview(payload:dict):return safe(self.preview,payload)
        @router.get('/{ident}')
        def view(ident:int):return safe(self.view,ident)
        @router.post('/{ident}/send')
        def send(ident:int,payload:dict):
            if payload.get('confirm') is not True:raise HTTPException(409,'Confirm this SMS send.')
            return safe(self.send,ident)
        @router.post('/{ident}/refresh')
        def refresh(ident:int):return safe(self.refresh,ident)
        return router
