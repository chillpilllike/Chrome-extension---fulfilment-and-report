"""Verified order journey: explicit public fields, never raw email bodies or sourcing notes."""
import json
import re
import os
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import requests
from app.support.live import stamp, objects

DECISIONS = {'proceed':'proceed with the order','exclude_item_and_proceed':'remove the affected item and proceed','cancel_affected_item':'cancel the affected item','offer_alternatives':'request an alternative','cancel_order':'cancel the order','refund':'request a refund','replacement':'request a replacement','received':'confirm receipt','not_received':'report non-receipt'}
EVENTS = {'sent','delivered','delivery_delayed','bounced','complained','opened','clicked','failed','suppressed'}

def public_text(value):
    text=str(value or '').strip()[:300]
    if re.search(r'amazon|\basin\b|dropship|supplier|procurement|https?://|[A-Za-z]*B0[A-Z0-9]{8}',text,re.I):return ''
    return text

def tracking_url(value):
    try:
        u=urlsplit(str(value or ''))
        # Only known customer-carrier hosts, never arbitrary imported/internal URLs.
        hosts=('epgtrack.com','epostglobalshipping.com','auspost.com.au','usps.com','ups.com','fedex.com','dhl.com','dhl.de','dhlparcel.com','parcelsapp.com','17track.net')
        if u.scheme=='https' and not u.username and not u.password and u.port in (None,443) and any(u.hostname==h or (u.hostname or '').endswith('.'+h) for h in hosts):return u.geturl()
    except ValueError:pass
    return None

def shipment_history(conn,store,order_id,website=1,now=None):
    now=now or datetime.now(timezone.utc)
    rows=conn.execute('SELECT tracking_code,tracking_url,status,last_update_at,location,final_mile_tracking_number,final_mile_tracking_url,final_mile_carrier,events_json,last_checked_at FROM epost_global_tracking WHERE store_id=? AND odoo_order_id=? AND (website_id=? OR website_id IS NULL) AND archived_at IS NULL ORDER BY last_checked_at DESC LIMIT 21',(store,order_id,website)).fetchall()
    result=[]
    for row in rows[:20]:
        r=dict(row);events=[]
        for event in objects(r.get('events_json')):
            if not isinstance(event,dict):continue
            date=event.get('EventDT') or event.get('timestamp') or event.get('date')
            if stamp(date).year<2000:continue
            events.append({'at':str(date)[:50],'status':public_text(event.get('status') or event.get('EventDescription') or event.get('description')),'location':public_text(event.get('location') or event.get('EventLocation'))})
        events.sort(key=lambda e:stamp(e['at']),reverse=True)
        fresh=timedelta(0)<=now-stamp(r.get('last_checked_at'))<=timedelta(minutes=5)
        result.append({'tracking_number':public_text(r.get('tracking_code')),'tracking_url':tracking_url(r.get('tracking_url')),'last_recorded_status':public_text(r.get('status')),'last_recorded_location':public_text(r.get('location')),'last_movement_at':r.get('last_update_at'),'checked_at':r.get('last_checked_at'),'fresh':fresh,'recent_movements':events[:5],'final_mile_tracking_number':public_text(r.get('final_mile_tracking_number')),'final_mile_tracking_url':tracking_url(r.get('final_mile_tracking_url')),'final_mile_carrier':public_text(r.get('final_mile_carrier'))})
    return {'shipments':result,'has_more':len(rows)>20,'instruction':'Describe each package separately. Give the recorded timestamp, location, tracking number and available tracking link. If stale, call it the last recorded update, not the current location. Do not claim all order items shipped or delivered. Missing records do not prove no dispatch.'}

def email_history(conn,store,order_id,email,website=1,now=None,provider_key=None):
    now=now or datetime.now(timezone.utc)
    cases=conn.execute('SELECT id,case_type,current_decision,decision_updated_at,confirmed_at,status FROM after_order_cases WHERE store_id=? AND odoo_order_id=? AND website_id=? AND lower(trim(customer_email))=? ORDER BY updated_at DESC LIMIT 21',(store,order_id,website,email)).fetchall()
    result=[];budget=3
    for row in cases[:20]:
        case=dict(row);messages=[]
        rows=conn.execute("SELECT id,provider,provider_message_id,recipient,status,created_at FROM after_order_messages WHERE case_id=? AND lower(trim(recipient))=? AND status='sent' ORDER BY created_at DESC LIMIT 5",(case['id'],email)).fetchall()
        for record in rows:
            msg=dict(record);observation=None
            saved=conn.execute("SELECT details_json,created_at FROM after_order_case_events WHERE case_id=? AND event_type='support_email_provider_observed' ORDER BY created_at DESC LIMIT 30",(case['id'],)).fetchall()
            for event in saved:
                try:data=json.loads(event['details_json'])
                except (ValueError,TypeError):continue
                if data.get('message_id')==msg['id'] and data.get('event') in EVENTS:
                    observation={'event':data['event'],'observed_at':event['created_at']};break
            key=provider_key if provider_key is not None else os.getenv('RESEND_API_KEY','')
            if budget and key and msg['provider']=='resend' and re.fullmatch(r'[a-fA-F0-9-]{36}',msg.get('provider_message_id') or '') and (not observation or now-stamp(observation['observed_at'])>timedelta(minutes=5)):
                budget-=1
                try:
                    response=requests.get('https://api.resend.com/emails/'+msg['provider_message_id'],headers={'Authorization':'Bearer '+key},timeout=4)
                    response.raise_for_status();data=response.json()
                    if data.get('id')==msg['provider_message_id'] and [str(v).strip().lower() for v in data.get('to',[])]==[email] and data.get('last_event') in EVENTS:
                        observation={'event':data['last_event'],'observed_at':now.isoformat()}
                        conn.execute("INSERT INTO after_order_case_events(case_id,event_type,actor_type,details_json,created_at) VALUES(?,'support_email_provider_observed','system',?,?)",(case['id'],json.dumps({'message_id':msg['id'],'event':observation['event']}),now.isoformat()))
                except (requests.RequestException,ValueError,TypeError):pass
            messages.append({'sent_at':msg['created_at'] if msg['status']=='sent' else None,'send_status':msg['status'],'provider_observation':observation})
        decision=DECISIONS.get(case.get('current_decision'))
        result.append({'topic':{'item_unavailable':'item availability','delivery_confirmation':'delivery confirmation','tracking':'tracking update'}.get(case['case_type'],'order follow-up'),'emails':messages,'recorded_request':decision,'request_recorded_at':case.get('decision_updated_at') if decision else None,'team_confirmed_at':case.get('confirmed_at') if decision else None,'response_state':'request_recorded' if decision else 'no_current_choice_recorded'})
    return {'cases':result,'has_more':len(cases)>20,'instruction':'Mention only real sent emails, not test/failed/uncertain attempts. Sent is provider acceptance, delivered means recipient mail server acceptance. Open/click signals do not prove reading or consent. No current choice means no choice recorded in After-order care, NOT that the customer never replied by email/chat. Never blame the customer. A recorded refund/cancellation request or team confirmation does not prove payment refunded or order cancelled. Do not expose raw subjects, email bodies, action tokens or internal notes.'}
