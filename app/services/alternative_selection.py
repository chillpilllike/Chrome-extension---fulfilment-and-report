"""Line-level customer selections. No network, database or clock side effects."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re


def moment(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def selection_change(previous, product, now):
    """A re-selection never extends the first selection's 24-hour deadline."""
    if previous and (previous.get("status") != "choosing" or moment(previous["deadline_at"]) <= now):
        raise ValueError("The 24-hour selection window has closed.")
    return {
        "first_selected_at": previous["first_selected_at"] if previous else now.isoformat(),
        "deadline_at": previous["deadline_at"] if previous else (now + timedelta(hours=24)).isoformat(),
        "version": int((previous or {}).get("version") or 0) + 1,
        "product": product,
    }


def price_fingerprint(product):
    return hashlib.sha256(json.dumps({k: product.get(k) for k in (
        "product_id", "quantity", "original_total", "alternative_total", "difference", "currency", "pricing_signature"
    )}, sort_keys=True).encode()).hexdigest()


def money_difference(original, alternative, rounding="0.01"):
    try:
        old, new, step = Decimal(str(original)), Decimal(str(alternative)), Decimal(str(rounding))
        if not all(v.is_finite() for v in (old, new, step)) or min(old, new) < 0 or step <= 0:
            raise ValueError("Invalid monetary amount")
        return float(((new-old) / step).quantize(Decimal("1")) * step)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Invalid monetary amount") from exc


def resolved_asin(product, extract, decode, normalize):
    notes = normalize(extract(product.get("description") or "") or "")
    reference = normalize(decode(product.get("default_code") or "") or "")
    direct = normalize(product.get("default_code") or "")
    candidates = {value for value in (notes, reference, direct) if value}
    candidates.update(value for value in (normalize(match) for match in re.findall(
        r'Amazon\s+ASIN\s*:\s*([A-Z0-9]{10})', product.get('description') or '', re.IGNORECASE)) if value)
    if len(candidates) > 1:
        raise ValueError("Internal notes and product reference resolve to conflicting ASINs. Team review required.")
    return next(iter(candidates), "")
