"""Deterministic public facts. Never copy raw source dictionaries into this contract."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re


@dataclass(frozen=True)
class Evidence:
    observed_at: datetime
    expected_dates: tuple[date | None, ...] = ()
    received_dates: tuple[date | None, ...] = ()
    inbound_delivered: bool = False
    outbound_state: str = "unknown"
    conflict: bool = False
    payment_issue: bool = False


def public_status(state: str, evidence: Evidence, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    result = {"status": "preparing", "estimated_dispatch": None, "needs_human": False,
              "reply": "Your order is being prepared. We do not have a confirmed dispatch estimate yet."}
    def set_status(status, reply, human=False):
        result.update(status=status, reply=reply, needs_human=human)
        return result
    if state in {"draft", "sent"}:
        return set_status("not_confirmed", "Your order is not confirmed yet. A dispatch estimate is not available.")
    if state == "cancel":
        return set_status("cancelled", "Your order is cancelled. Our team can help with any questions.", True)
    if state not in {"sale", "done"}:
        return set_status("review", "Our team needs to check the latest status of your order.", True)
    if evidence.observed_at.tzinfo is None or not timedelta(0) <= now - evidence.observed_at <= timedelta(minutes=5):
        return set_status("review", "We could not confirm the latest order status. Our team will check it for you.", True)
    if evidence.conflict or evidence.payment_issue:
        return set_status("review", "Our team needs to check your order before confirming the next update.", True)
    outbound = {
        "delivered": ("delivered", "Your shipment has been delivered."),
        "handed_over": ("dispatched", "Your order has been dispatched."),
        "moving": ("dispatched", "Your order has been dispatched."),
        "partial": ("partially_dispatched", "Part of your order has been dispatched. Our team can confirm the remaining items."),
        "label": ("awaiting_collection", "Your tracking details are ready. We are awaiting carrier collection or confirmation."),
    }
    if evidence.outbound_state in outbound:
        status, reply = outbound[evidence.outbound_state]
        return set_status(status, reply, evidence.outbound_state == "partial")
    if evidence.received_dates and all(evidence.received_dates):
        basis = max(evidence.received_dates)
        if basis > now.date():
            return set_status("review", "Our team needs to check the latest status of your order.", True)
    elif evidence.inbound_delivered:
        return set_status("processing", "Your order is awaiting warehouse processing.", True)
    elif evidence.expected_dates and all(evidence.expected_dates):
        # Any overdue required package invalidates the combined promise.
        if any(day < now.date() for day in evidence.expected_dates):
            return set_status("review", "We are checking an updated dispatch estimate for your order.", True)
        basis = max(evidence.expected_dates)
    else:
        result["needs_human"] = True
        return result
    estimate = basis + timedelta(days=1)
    if estimate < now.date():
        return set_status("review", "We are checking an updated dispatch estimate for your order.", True)
    result.update(estimated_dispatch=estimate.isoformat(),
                  reply=f"Your order is being prepared. We currently estimate dispatch on {estimate.strftime('%d %B %Y')}. We will share tracking details when available.")
    return result


def safe_reference(value):
    value = str(value or "")
    # References, free-text titles, notes and URLs are not interchangeable.
    return value if re.fullmatch(r"(?:#[0-9]{1,20}|[A-Za-z]{1,12}[0-9][A-Za-z0-9/-]{0,35})", value) and not re.search(r"amazon|asin|drop", value, re.I) else "Your order"


def amount(value):
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def public_order(order: dict, evidence: Evidence) -> dict:
    currency = order.get("currency_id")
    code = currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else ""
    return {"reference": safe_reference(order.get("name")),
            "total": amount(order.get("amount_total")),
            "currency": code if re.fullmatch(r"[A-Z]{3}", str(code)) else None,
            **public_status(order.get("state", ""), evidence)}
