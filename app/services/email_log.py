"""Email-log presentation and fail-closed retry eligibility."""
import json


STATUS_LABELS = {
    "sent": "Sent", "sent_test": "Sent · test", "failed": "Failed",
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
    try:
        payload = json.loads(message.get("payload_json") or "{}")
    except (ValueError, TypeError):
        return "The saved email payload is unavailable."
    if not isinstance(payload, dict) or not payload.get("html") or not payload.get("to"):
        return "The saved email payload is unavailable."
    recipient = str(message.get("recipient") or "").strip().lower()
    if payload.get("to") != [message.get("recipient")]:
        return "Saved recipients do not match the log record."
    if (test_mode or bool(message.get("test_mode"))) and (not message.get("test_mode") or recipient != str(test_recipient).strip().lower()):
        return "Test mode permits only stored test emails to the configured test address."
    return ""
