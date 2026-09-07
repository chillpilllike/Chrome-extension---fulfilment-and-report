"""Signed delivery receipts, durable deduplication and recipient suppression."""
import base64
import hashlib
import hmac
import json
import time
from fastapi import APIRouter, HTTPException, Request

SCHEMA = """
CREATE TABLE IF NOT EXISTS after_order_delivery_events (
 event_id TEXT PRIMARY KEY, provider_message_id TEXT NOT NULL, event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, created_at TEXT NOT NULL, processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_care_delivery_pending ON after_order_delivery_events(processed_at,created_at);
CREATE TABLE IF NOT EXISTS after_order_email_suppression (
 recipient TEXT NOT NULL, test_mode INTEGER NOT NULL, reason TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(recipient,test_mode)
);
CREATE TABLE IF NOT EXISTS after_order_case_checks (
 case_id INTEGER PRIMARY KEY REFERENCES after_order_cases(id) ON DELETE CASCADE, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS after_order_email_recovery_checks (
 message_id INTEGER PRIMARY KEY REFERENCES after_order_messages(id) ON DELETE CASCADE,
 checked_at TEXT NOT NULL, block_reason TEXT NOT NULL DEFAULT ''
);
"""
EVENT_STATUS = {'email.sent':'sent', 'email.delivered':'delivered', 'email.bounced':'bounced',
                'email.complained':'complained', 'email.failed':'failed', 'email.delivery_delayed':'delivery_delayed'}
PRIORITY = {'sent':10,'sent_test':10,'delivery_delayed':15,'failed':20,'delivered':30,'bounced':40,'complained':50}


def verify_receipt(body, headers, secret, now=None):
    if not secret or not secret.startswith('whsec_'):
        raise ValueError('Webhook signing secret is not configured.')
    event_id = headers.get('svix-id','')
    stamp = headers.get('svix-timestamp','')
    if not event_id or len(event_id) > 200 or abs((time.time() if now is None else now)-int(stamp)) > 300:
        raise ValueError('Expired or invalid webhook timestamp.')
    key = base64.b64decode(secret[6:], validate=True)
    expected = base64.b64encode(hmac.new(key, event_id.encode()+b'.'+stamp.encode()+b'.'+body, hashlib.sha256).digest()).decode()
    signatures = headers.get('svix-signature','').split()
    if not any(hmac.compare_digest('v1,'+expected, supplied) for supplied in signatures):
        raise ValueError('Invalid webhook signature.')
    payload = json.loads(body)
    if not isinstance(payload,dict) or not isinstance(payload.get('data',{}),dict):
        raise ValueError('Invalid webhook object.')
    return event_id, payload


class Delivery:
    def __init__(self, namespace):
        from app.services.alternative_workflow import Runtime
        self.r = Runtime(namespace)

    def suppressed(self, recipient, test_mode):
        with self.r.db() as conn:
            row = conn.execute('SELECT reason FROM after_order_email_suppression WHERE recipient=? AND test_mode=?',
                (recipient.strip().lower(), int(test_mode))).fetchone()
        return row['reason'] if row else ''

    def recover_sends(self, request):
        from app.services.email_log import automatic_retry_reason
        r = self.r
        if r.after_order_email_test_mode():
            return
        with r.db() as conn:
            rows = conn.execute('''SELECT m.* FROM after_order_messages m
                LEFT JOIN after_order_email_recovery_checks k ON k.message_id=m.id
                WHERE m.provider='resend' AND m.test_mode=0
                AND m.status IN ('failed','delivery_unknown','sending','retrying')
                ORDER BY COALESCE(k.checked_at,''),m.id LIMIT 100''').fetchall()
        for row in rows:
            message = dict(row)
            reason = automatic_retry_reason(message)
            if not reason:
                try:
                    r.retry_after_order_email(message['id'], request, automatic=True)
                except HTTPException as exc:
                    reason = str(exc.detail)
                except Exception as exc:
                    reason = r.clean_error_message(exc)
            with r.db() as conn:
                previous = conn.execute('SELECT block_reason FROM after_order_email_recovery_checks WHERE message_id=?',(message['id'],)).fetchone()
                conn.execute('''INSERT INTO after_order_email_recovery_checks(message_id,checked_at,block_reason)
                    VALUES(?,?,?) ON CONFLICT(message_id) DO UPDATE SET checked_at=excluded.checked_at,block_reason=excluded.block_reason''',
                    (message['id'],r.utc_now(),reason))
                if reason and (not previous or previous['block_reason']!=reason):
                    r.record_after_order_event(conn,message['case_id'],'email_automatic_recovery_status',details={'message_id':message['id'],'reason':reason})

    def reconcile(self):
        r = self.r
        with r.db() as conn:
            rows = conn.execute('''SELECT e.*,m.id AS message_id,m.case_id,m.status,m.recipient,m.test_mode,
                m.provider_message_id AS current_provider_id FROM after_order_delivery_events e
                JOIN after_order_messages m ON m.provider='resend' AND (m.provider_message_id=e.provider_message_id OR EXISTS
                  (SELECT 1 FROM after_order_email_attempts a WHERE a.message_id=m.id AND a.provider_message_id=e.provider_message_id))
                WHERE e.processed_at IS NULL ORDER BY e.created_at LIMIT 100''').fetchall()
            for row in rows:
                # Recheck under lock so simultaneous webhook retries produce one timeline entry.
                event = conn.execute('SELECT * FROM after_order_delivery_events WHERE event_id=? FOR UPDATE',(row['event_id'],)).fetchone()
                if event['processed_at']:
                    continue
                message = conn.execute('SELECT * FROM after_order_messages WHERE id=? FOR UPDATE',(row['message_id'],)).fetchone()
                status = EVENT_STATUS[row['event_type']]
                if message['provider_message_id'] == row['provider_message_id'] and PRIORITY.get(status,0) >= PRIORITY.get(message['status'],0):
                    conn.execute('UPDATE after_order_messages SET status=?,updated_at=? WHERE id=?',(status,r.utc_now(),message['id']))
                conn.execute('UPDATE after_order_delivery_events SET processed_at=? WHERE event_id=?',(r.utc_now(),row['event_id']))
                if status in ('bounced','complained'):
                    conn.execute('''INSERT INTO after_order_email_suppression(recipient,test_mode,reason,created_at)
                        VALUES(?,?,?,?) ON CONFLICT(recipient,test_mode) DO UPDATE SET reason=excluded.reason''',
                        (message['recipient'].strip().lower(),int(message['test_mode']),status,r.utc_now()))
                r.record_after_order_event(conn,message['case_id'],'email_'+status,details={
                    'message_id':message['id'],'provider_message_id':row['provider_message_id'],
                    'occurred_at':row['occurred_at'],'test_mode':bool(message['test_mode'])})

    def router(self):
        router = APIRouter()
        @router.post('/api/public/after-order-webhooks/resend')
        async def receipt(request: Request):
            secret = self.r.os.getenv('RESEND_WEBHOOK_SECRET','')
            if not secret:
                raise HTTPException(503,'Webhook signing secret is not configured.')
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 131072:
                    raise HTTPException(413,'Webhook payload too large.')
            try:
                event_id, payload = verify_receipt(bytes(body),request.headers,secret)
                kind = payload.get('type')
                provider_id = str((payload.get('data') or {}).get('email_id') or '')
                if kind not in EVENT_STATUS:
                    return {'ok':True,'ignored':True}
                if not provider_id or len(provider_id)>200:
                    raise ValueError('Invalid email ID.')
                with self.r.db() as conn:
                    conn.execute('''INSERT INTO after_order_delivery_events
                        (event_id,provider_message_id,event_type,occurred_at,created_at) VALUES(?,?,?,?,?)
                        ON CONFLICT(event_id) DO NOTHING''',
                        (event_id,provider_id,kind,str(payload.get('created_at') or self.r.utc_now()),self.r.utc_now()))
            except (ValueError, TypeError, KeyError) as exc:
                raise HTTPException(400,'Invalid webhook.') from exc
            self.reconcile()
            return {'ok':True}
        return router
