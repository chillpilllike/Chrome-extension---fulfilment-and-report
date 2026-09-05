from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from typing import Any, Protocol
from urllib.parse import urlparse

import requests


CUSTOMER_DECISIONS = {
    "proceed",
    "exclude_item_and_proceed",
    "cancel_affected_item",
    "offer_alternatives",
    "cancel_order",
    "refund",
    "replacement",
    "received",
    "not_received",
}


def can_remove_affected_items(lines: list[dict[str, Any]], affected: list[dict[str, Any]]) -> bool:
    """Removing an unavailable ASIN must leave another fulfillable product.

    Quantity, shipping charges and multiple rows of the same ASIN do not
    turn a single-product order into a partially removable order.
    """
    affected_ids = {str(item.get("line_id")) for item in affected if item.get("line_id")}
    affected_asins = {str(item.get("asin") or "").strip().upper() for item in affected} - {""}
    affected_asins.update(
        str(line.get("asin") or "").strip().upper()
        for line in lines if str(line.get("id")) in affected_ids
    )
    affected_asins.discard("")
    if not affected_asins:
        return False
    for line in lines:
        asin = str(line.get("asin") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        if str(line.get("id")) in affected_ids or asin in affected_asins:
            continue
        if str(line.get("odoo_status_label") or "").lower() in {"cancelled", "canceled", "refunded"}:
            continue
        if str(line.get("state") or "").lower() in {"missing", "cancelled", "canceled", "refunded"}:
            continue
        if float(line.get("quantity") or 0) > 0:
            return True
    return False


class EmailProvider(Protocol):
    """Provider boundary for Resend today and another email service later."""

    def send(self, message: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]: ...


class ResendEmailProvider:
    """Small provider adapter; callers remain independent of Resend."""

    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str, *, timeout_seconds: int = 20):
        self.api_key = str(api_key or "").strip()
        self.timeout_seconds = max(1, timeout_seconds)

    def send(self, message: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError("RESEND_API_KEY is not configured on the server.")
        response = requests.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            json=message,
            timeout=self.timeout_seconds,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text[:500]}
        if response.status_code >= 400:
            raise RuntimeError(str(payload.get("message") or f"Resend returned HTTP {response.status_code}."))
        return payload


class TrackingProvider(Protocol):
    """Provider boundary shared by EPG, FedEx, UPS, DHL, and future carriers."""

    name: str

    def snapshot(self, tracking_code: str) -> dict[str, Any]: ...


class OrderProvider(Protocol):
    """Provider boundary for one configured Odoo store/database/website."""

    def cancel_order(self, order_id: int) -> dict[str, Any]: ...

    def exclude_line(self, order_id: int, line_id: int) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TrackingRisk:
    state: str
    customer_lost_email_allowed: bool
    reason: str


def parse_provider_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def tracking_risk(
    events: list[dict[str, Any]],
    *,
    status: str = "",
    now: datetime | None = None,
    stale_days: int = 10,
) -> TrackingRisk:
    """Classify risk without treating a blank carrier page as a lost parcel.

    The inactivity clock starts only after a physical carrier event. Manifest,
    label, and data-received records are intentionally not proof of possession.
    """

    now = now or datetime.now(timezone.utc)
    combined_status = " ".join(
        [status, *(str(event.get("Event") or event.get("status") or "") for event in events)]
    ).lower()
    exception_terms = ("damaged", "return to sender", "delivery failed", "not delivered", "undeliverable")
    if any(term in combined_status for term in exception_terms):
        return TrackingRisk("carrier_exception", True, "Carrier reported a delivery exception.")
    if any(word in combined_status for word in ("delivered", "recipient collected", "successfully delivered")):
        return TrackingRisk("delivered", False, "Carrier reports delivery.")

    if not events:
        return TrackingRisk(
            "awaiting_first_scan",
            False,
            "No carrier events received; a blank tracking page is not evidence that a parcel is lost.",
        )

    non_physical_terms = (
        "data received",
        "electronic information",
        "manifest",
        "label created",
        "pre-advised",
        "shipment information",
    )
    physical_events = [
        event
        for event in events
        if not any(
            term in str(event.get("Event") or event.get("status") or "").lower()
            for term in non_physical_terms
        )
    ]
    if not physical_events:
        return TrackingRisk(
            "awaiting_first_scan",
            False,
            "Only electronic or manifest events exist; physical carrier possession is not confirmed.",
        )

    event_times = [
        parse_provider_datetime(event.get("EventDT") or event.get("timestamp") or event.get("date"))
        for event in physical_events
    ]
    latest = max((value for value in event_times if value), default=None)
    if latest and now - latest >= timedelta(days=max(1, stale_days)):
        return TrackingRisk(
            "suspected_lost",
            True,
            f"No meaningful movement for {max(1, stale_days)} days after carrier possession.",
        )
    return TrackingRisk("in_transit", False, "The parcel has recent physical carrier movement.")


def normalize_customer_decision(value: Any) -> str:
    decision = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if decision not in CUSTOMER_DECISIONS:
        raise ValueError("Unsupported customer decision.")
    return decision


def resolve_delivery_recipient(customer_email: str, *, test_mode: bool, test_recipient: str) -> str:
    recipient = str(test_recipient if test_mode else customer_email).strip().lower()
    if not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+", recipient):
        raise ValueError("A valid email recipient is required.")
    return recipient


def trustpilot_review_url(domain: str) -> str:
    """Build a predictable Trustpilot review URL from a bare customer domain."""
    candidate = str(domain or "").strip().lower()
    if "://" in candidate:
        candidate = (urlparse(candidate).hostname or "").lower()
    candidate = candidate.strip("./ ")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    if "." not in candidate or not all(part and part.replace("-", "").isalnum() for part in candidate.split(".")):
        raise ValueError("A valid customer website domain is required.")
    return f"https://www.trustpilot.com/review/{candidate}"
