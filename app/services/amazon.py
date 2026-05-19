from __future__ import annotations

from datetime import datetime, timezone


AMAZON_ENDPOINT_ALIASES = {
    "https://api.business.amazon.com": "https://na.business-api.amazon.com",
}


def amz_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def normalize_amazon_endpoint(value: str, default: str = "https://na.business-api.amazon.com") -> str:
    endpoint = str(value or default).strip().rstrip("/")
    return AMAZON_ENDPOINT_ALIASES.get(endpoint, endpoint)
