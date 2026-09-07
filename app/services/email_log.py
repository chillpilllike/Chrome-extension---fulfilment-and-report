"""Email-log presentation and fail-closed retry eligibility."""
import json
from datetime import datetime, timedelta, timezone


def automatic_retry_reason(message, now=None):
    """Only retry uncertain acceptance inside Resend's deduplication window."""
    now = now or datetime.now(timezone.utc)
    if message.get('provider') != 'resend' or message.get('test_mode'):
        return 'Automatic recovery is limited to live Resend messages.'
    if message.get('status') not in ('failed', 'delivery_unknown', 'sending', 'retrying'):
        return 'No send failure to recover.'
    if int(message.get('attempt_count') or 1) >= 4:
        return 'Three automatic retries exhausted; team attention required.'
    try:
        updated = datetime.fromisoformat(message['updated_at'].replace('Z', '+00:00'))
        created = datetime.fromisoformat(message['created_at'].replace('Z', '+00:00'))
        if updated.tzinfo is None or created.tzinfo is None:
            return 'Missing timezone; reconciliation required.'
    except (ValueError, TypeError, KeyError):
        return 'Missing send timestamp; reconciliation required.'
    if now < updated + timedelta(hours=1):
        return 'Retry is scheduled one hour after the last attempt.'
    if now >= created + timedelta(hours=23):
        return 'Provider deduplication window is closing; reconciliation required.'
    if not message.get('idempotency_key'):
        return 'Missing provider deduplication key.'
    return ''


STATUS_LABELS = {
    "sent": "Accepted by provider", "sent_test": "Accepted · test", "failed": "Failed",
    "delivered": "Delivered to mail server", "bounced": "Bounced · suppressed",
    "complained": "Complaint · suppressed", "delivery_delayed": "Delivery delayed",
    "sending": "Sending", "retrying": "Retrying", "delivery_unknown": "Check delivery",
    "test_preview": "Preview only",
}


def retry_block_reason(message, *, test_mode, test_recipient):
    if message.get("status") != "failed":
        return "Only confirmed failures can be retried. Check uncertain sends with the provider first."
    if int(message.get("attempt_count") or 1) >= 5:
        return "Five attempts reached. Resolve the underlying issue before creating a new notification."
    if not message.get("request_fingerprint"):
        return "Legacy record has no request snapshot. Use the order's current email action instead."
    if message.get('provider') == 'odoo':
        return 'Odoo quotation emails cannot be retried in test mode.' if test_mode or message.get('test_mode') else ''
    try:
        payload = json.loads(message.get("payload_json") or "{}")
    except (ValueError, TypeError):
        return "The saved email payload is unavailable."
    if not isinstance(payload, dict) or not payload.get("html") or not payload.get("to"):
        return "The saved email payload is unavailable."
    if payload.get('_care_reminder_expires_at'):
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(payload['_care_reminder_expires_at']):
                return 'The original reminder window has expired.'
        except (ValueError,TypeError):
            return 'Invalid reminder deadline; reconciliation required.'
    recipient = str(message.get("recipient") or "").strip().lower()
    if payload.get("to") != [message.get("recipient")]:
        return "Saved recipients do not match the log record."
    if (test_mode or bool(message.get("test_mode"))) and (not message.get("test_mode") or recipient != str(test_recipient).strip().lower()):
        return "Test mode permits only stored test emails to the configured test address."
    return ""
