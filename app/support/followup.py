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
    payload=resend_eligibility(app,message,case)
    # Require the actual stored action link to remain valid, not merely another link for this case.
    tokens=set(re.findall(r'/order-update/([a-f0-9]{64})',payload['html']))
    if not tokens:raise HTTPException(409,'The original email has no current website link. Ask the team for a new update.')
    now=datetime.now(timezone.utc)
    with db() as conn:
        conn.execute('SELECT pg_advisory_xact_lock(81910415)')
        # Re-read eligibility after serializing competing resends.
        case=app.after_order_case_by_id(message['case_id'])
        resend_eligibility(app,message,case)
        for token in tokens:
            link=conn.execute('SELECT * FROM after_order_action_links WHERE case_id=? AND token_hash=?',(case['id'],hashlib.sha256(token.encode()).hexdigest())).fetchone()
            if not link or link['invalidated_at'] or link['test_mode'] or stamp(link['expires_at'])<=now or link['request_fingerprint']!=request_fingerprint(case):raise HTTPException(409,'The email choices have expired or changed. Ask the team for a current update.')
        # Same content across chats and resend copies shares one rolling cooldown.
        digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        prefix='support-resend-v2:'+str(case['id'])+':'+digest+':'
        key=prefix+now.strftime('%Y-%m-%dT%H')
        conn.execute('SELECT pg_advisory_xact_lock(81910415)')
        recent=conn.execute("SELECT status,created_at FROM after_order_messages WHERE idempotency_key LIKE ? ORDER BY id DESC LIMIT 4",(prefix+'%',)).fetchall()
        old=next((r for r in recent if stamp(r['created_at'])>now-timedelta(hours=1) or r['status'] in {'sending','delivery_unknown'}),None)
        if sum(stamp(r['created_at'])>now-timedelta(days=1) for r in recent)>=3 and not old:
            raise HTTPException(429,'The resend limit has been reached. Please ask the team for help.')
        if old:
            if old['status']=='sent':return {'sent':True,'already_sent':True,'message':'The email has already been resent. Please check your inbox and spam folder.'}
            raise HTTPException(409,'A resend was already attempted. Its delivery needs team review; do not resend again.')
        ident=conn.execute("INSERT INTO after_order_messages(case_id,provider,recipient,sender,subject,html_preview,status,idempotency_key,payload_json,created_at,updated_at,test_mode,request_fingerprint,template_kind) VALUES(?,?,?,?,?,?,'sending',?,?,?,?,0,?,?) RETURNING id",(case['id'],message['provider'],message['recipient'],message['sender'],message['subject'],payload['html'],key,json.dumps(payload),now.isoformat(),now.isoformat(),message['request_fingerprint'],message.get('template_kind'))).fetchone()['id']
    try:
        if app.after_order_email_test_mode():raise ValueError('Live sending is disabled.')
        result=create_email_provider(message['provider'],{'api_key':os.getenv('RESEND_API_KEY','')}).send(payload,idempotency_key=key)
        if not isinstance(result,dict) or not result.get('id'):raise RuntimeError('Provider acceptance not confirmed')
    except Exception as exc:
        with db() as conn:conn.execute('UPDATE after_order_messages SET status=?,updated_at=? WHERE id=?',('failed' if isinstance(exc,(EmailRejected,ValueError)) else 'delivery_unknown',datetime.now(timezone.utc).isoformat(),ident))
        raise HTTPException(503,'The resend could not be confirmed. The team needs to check it before another attempt.') from None
    with db() as conn:conn.execute("UPDATE after_order_messages SET status='sent',provider_message_id=?,updated_at=? WHERE id=?",(result['id'],datetime.now(timezone.utc).isoformat(),ident))
    return {'sent':True,'message':'We have resent the email. Please check your inbox and spam folder and use the email to share your choice.'}


def resend_eligibility(app,message,case):
    """A resend reuses prior approval only while the exact customer request is valid."""
    from app.services.after_order import request_fingerprint
    def block(text):raise HTTPException(409,text)
    if message.get('status')!='sent' or message.get('test_mode') or app.after_order_email_test_mode():block('A confirmed live email is required for resending.')
    if not case or case.get('status') in {'resolved','closed'} or case.get('confirmed_at') or case.get('current_decision'):block('This request is no longer awaiting a response. The team can check its progress.')
    app.require_after_order_case_in_scope(case)
    case=app.hydrate_after_order_recipient_and_domain(case,strict=True)
    if str(case.get('customer_email','')).strip().lower()!=str(message.get('recipient','')).strip().lower() or app.after_order_sender(case)[0]!=message.get('sender'):block('The customer address or website sender changed.')
    if message.get('template_kind')=='trustpilot_review':block('Review invitations are not resent by support chat.')
    if message.get('request_fingerprint')!=request_fingerprint(case) or not app.after_order_tracking_is_current(case):block('The order update changed. The team must prepare a current email.')
    if case.get('case_type')=='item_unavailable':
        review=app.after_order_unavailable_review(case)
        if review['blocked'] or not review['approved']:block('The item availability update needs team review.')
    movement=case.get('case_type')=='tracking' and (case.get('context') or {}).get('risk_state')=='in_transit'
    if message.get('template_kind')=='package_movement' and not movement:block('This movement email no longer matches the current case.')
    if movement:
        if app.after_order_tracking_updates_opted_out(case,message['recipient']):block('These email updates are disabled for this customer.')
        context=case.get('context') or {}
        revision=hashlib.sha256(json.dumps({k:context.get(k) for k in ('latest_status','latest_location','last_update_at')},sort_keys=True).encode()).hexdigest()
        if not str(message.get('idempotency_key','')).endswith(revision):block('The movement email cannot be verified against the latest tracking event.')
    elif not app.after_order_allowed_actions(case):block('No current customer choice is available for this email.')
    try:payload=json.loads(message.get('payload_json') or '{}')
    except (ValueError,TypeError):payload={}
    if not isinstance(payload,dict) or payload.get('to')!=[message.get('recipient')] or payload.get('cc') or payload.get('bcc') or not payload.get('html') or payload.get('from')!=message.get('sender'):block('The saved email cannot be safely resent.')
    tokens=set(re.findall(r'/order-update/([a-f0-9]{64})',payload['html']))
    if not tokens:block('The saved website link is unavailable. Ask the team for a current email.')
    with app.db() as conn:
        for token in tokens:
            link=conn.execute('SELECT * FROM after_order_action_links WHERE case_id=? AND token_hash=?',(case['id'],hashlib.sha256(token.encode()).hexdigest())).fetchone()
            if not link or link['invalidated_at'] or link['test_mode'] or stamp(link['expires_at'])<=datetime.now(timezone.utc) or link['request_fingerprint']!=request_fingerprint(case):block('The original email choices have expired or changed.')
    return payload
