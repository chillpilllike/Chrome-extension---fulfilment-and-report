"""Customer-requested resends and staff-reviewed contact changes."""
import json, re, hashlib, os
from datetime import datetime,timezone,timedelta
from app.support.live import stamp
from fastapi import HTTPException

def change_eligibility(conn,store,order,now=None):
    now=now or datetime.now(timezone.utc)
    rows=[dict(x) for x in conn.execute('SELECT fulfillment_status,cancelled_at,synced_at,shopify_order_id FROM shopify_order_status_cache WHERE store_id=? AND odoo_order_name=?',(store,order['name'])).fetchall()]
    known=bool(rows) and all(x.get('shopify_order_id') and timedelta(0)<=now-stamp(x.get('synced_at'))<=timedelta(minutes=5) for x in rows)
    allowed=known and all(not x.get('cancelled_at') and str(x.get('fulfillment_status') or '').lower() in {'','unfulfilled','null'} for x in rows)
    return {'can_collect_change':bool(allowed),'instruction':'Ask for recipient name, full street address, city, state/region, postal code and country for an address change; for phone changes ask for the new number including country code. Then submit for team review, never claim the order was changed.' if allowed else 'Current eligibility for a change is not confirmed. Offer team review; do not say the order is unfulfilled or promise a change.'}

def resend_action_email(db,store,website,order,email,uuid,message_id):
    from app import main as app
    from app.services.after_order import request_fingerprint,create_email_provider,EmailRejected
    with db() as conn:
        row=conn.execute('SELECT m.* FROM after_order_messages m JOIN after_order_cases c ON c.id=m.case_id WHERE m.id=? AND c.store_id=? AND c.website_id=? AND c.odoo_order_id=? AND lower(trim(c.customer_email))=? AND lower(trim(m.recipient))=?',(message_id,store,website,order['id'],email,email)).fetchone()
    if not row:raise HTTPException(404,'No matching email is available for resending.')
    message=dict(row);case=app.after_order_case_by_id(message['case_id'])
    if message.get('status')!='sent' or message.get('test_mode') or app.after_order_email_test_mode():raise HTTPException(409,'A live resend is not available. The team must review this email.')
    if message.get('template_kind') in {'package_movement','trustpilot_review'} or not case or case.get('current_decision') or case.get('confirmed_at') or not app.after_order_allowed_actions(case):raise HTTPException(409,'This email no longer requires a customer choice or is a movement notification. It cannot be resent here.')
    if message.get('request_fingerprint')!=request_fingerprint(case) or not app.after_order_tracking_is_current(case):raise HTTPException(409,'The order information changed. The team must prepare a current update.')
    try:payload=json.loads(message.get('payload_json') or '{}')
    except ValueError:payload={}
    if payload.get('to')!=[message['recipient']] or payload.get('cc') or payload.get('bcc') or not payload.get('html'):raise HTTPException(409,'The stored email cannot be safely resent.')
    # Require the actual stored action link to remain valid, not merely another link for this case.
    tokens=set(re.findall(r'/order-update/([a-f0-9]{64})',payload['html']))
    if not tokens:raise HTTPException(409,'The original email has no current website action link. Ask the team for a new update.')
    now=datetime.now(timezone.utc)
    with db() as conn:
        for token in tokens:
            link=conn.execute('SELECT * FROM after_order_action_links WHERE case_id=? AND token_hash=?',(case['id'],hashlib.sha256(token.encode()).hexdigest())).fetchone()
            if not link or link['invalidated_at'] or link['test_mode'] or stamp(link['expires_at'])<=now or link['request_fingerprint']!=request_fingerprint(case):raise HTTPException(409,'The email choices have expired or changed. Ask the team for a current update.')
        key='support-resend:'+uuid+':'+str(message_id)+':'+now.strftime('%Y-%m-%dT%H')
        conn.execute('SELECT pg_advisory_xact_lock(81910415)')
        old=conn.execute('SELECT status FROM after_order_messages WHERE idempotency_key=?',(key,)).fetchone()
        if old:
            if old['status']=='sent':return {'sent':True,'already_sent':True,'message':'The email has already been resent. Please check your inbox and spam folder.'}
            raise HTTPException(409,'A resend was already attempted. Its delivery needs team review; do not resend again.')
        ident=conn.execute("INSERT INTO after_order_messages(case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,payload_json,created_at,updated_at,test_mode,request_fingerprint,template_kind) VALUES(?,?,?,?,?,?,'sending',?,?,?,?,0,?,?) RETURNING id",(case['id'],message['provider'],message['recipient'],message['sender'],message['subject'],payload['html'],key,json.dumps(payload),now.isoformat(),now.isoformat(),message['request_fingerprint'],message.get('template_kind'))).fetchone()['id']
    try:
        result=create_email_provider(message['provider'],{'api_key':os.getenv('RESEND_API_KEY','')}).send(payload,idempotency_key=key)
    except Exception as exc:
        with db() as conn:conn.execute('UPDATE after_order_messages SET status=?,updated_at=? WHERE id=?',('failed' if isinstance(exc,(EmailRejected,ValueError)) else 'delivery_unknown',datetime.now(timezone.utc).isoformat(),ident))
        raise HTTPException(503,'The resend could not be confirmed. The team needs to check it before another attempt.') from None
    with db() as conn:conn.execute("UPDATE after_order_messages SET status='sent',provider_message_id=?,updated_at=? WHERE id=?",(result['id'],datetime.now(timezone.utc).isoformat(),ident))
    return {'sent':True,'message':'We have resent the email. Please check your inbox and spam folder and use the email to share your choice.'}
