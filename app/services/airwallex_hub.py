from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from typing import Any, Iterable


HANDLED_EVENTS = {
    "deposit.pending",
    "deposit.settled",
    "deposit.rejected",
    "deposit.reversed",
}
SIGNATURE_TOLERANCE_SECONDS = 300
ORDER_REFERENCE_RE = re.compile(r"(?<![A-Z0-9])([A-Z]{1,8}\d{3,})", re.IGNORECASE)


def normalize_prefixes(value: Any) -> list[str]:
    """Return unique, uppercase order prefixes in their configured order."""
    parts = re.split(r"[\s,;|]+", str(value or ""))
    result: list[str] = []
    for part in parts:
        prefix = re.sub(r"[^A-Z]", "", part.upper())
        if prefix and prefix not in result:
            result.append(prefix)
    return result


def extract_order_reference(value: Any) -> str:
    match = ORDER_REFERENCE_RE.search(str(value or "").upper())
    return match.group(1) if match else ""


def order_prefix(reference: Any) -> str:
    match = re.match(r"([A-Z]+)", extract_order_reference(reference))
    return match.group(1) if match else ""


def webhook_event_details(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    reference = str(data.get("reference") or "").strip()
    detected = extract_order_reference(reference)
    return {
        "event_id": str(payload.get("id") or "").strip(),
        "event_name": str(payload.get("name") or "").strip(),
        "deposit_id": str(data.get("id") or data.get("deposit_id") or "").strip(),
        "reference": reference,
        "order_reference": detected,
        "order_prefix": order_prefix(detected),
        "amount": data.get("amount"),
        "currency": str(data.get("currency") or "").strip().upper(),
        "status": str(data.get("status") or "").strip().upper(),
        "payload": data,
    }


def verify_webhook_signature(
    *, timestamp: str, signature: str, raw_body: bytes, secrets: Iterable[str], now: float | None = None
) -> bool:
    try:
        timestamp_ms = int(timestamp)
    except (TypeError, ValueError):
        return False
    current_time = time.time() if now is None else now
    if abs(current_time - (timestamp_ms / 1000)) > SIGNATURE_TOLERANCE_SECONDS:
        return False
    message = timestamp.encode("utf-8") + raw_body
    for secret in secrets:
        clean_secret = str(secret or "").strip()
        if not clean_secret:
            continue
        expected = hmac.new(clean_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, str(signature or "")):
            return True
    return False


def json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
