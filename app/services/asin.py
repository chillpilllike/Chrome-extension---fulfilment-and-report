from __future__ import annotations

import base64
import html
import re
from urllib.parse import urlsplit


ASIN_SECRET = b"NUTRICITY-KEY"
ASIN_RE = re.compile(r"\b[A-Z0-9]{10}\b")
ASIN_FALSE_POSITIVES = {"CONDITIONS", "SUPPLEMENT"}


def normalize_asin(value: str) -> str:
    value = str(value or "").strip().upper()
    if value in ASIN_FALSE_POSITIVES:
        return ""
    # Real ASINs contain at least one digit. Requiring one prevents ordinary
    # ten-letter words in product/order notes from becoming fulfilment keys.
    if not any(char.isdigit() for char in value):
        return ""
    return value if ASIN_RE.fullmatch(value) else ""


def xor_bytes(data: bytes, key: bytes) -> bytes:
    return bytes([b ^ key[i % len(key)] for i, b in enumerate(data)]) if key else data


def encode_asin(asin: str) -> str:
    raw = str(asin or "").strip().encode("utf-8")
    return base64.b32encode(xor_bytes(raw, ASIN_SECRET)).decode("ascii").rstrip("=")


def decode_asin_reference(reference: str) -> str:
    reference = str(reference or "").strip().upper()
    if normalize_asin(reference):
        return reference
    if not reference:
        return ""
    try:
        padded = reference + ("=" * ((8 - len(reference) % 8) % 8))
        raw = base64.b32decode(padded.encode("ascii"))
        decoded = xor_bytes(raw, ASIN_SECRET).decode("utf-8").strip().upper()
        return normalize_asin(decoded)
    except Exception:
        return ""


def extract_asin_from_notes(*values: str) -> str:
    combined = "\n".join(str(v or "") for v in values)
    explicit = re.search(r"Amazon\s+ASIN\s*:\s*([A-Z0-9]{10})", combined, re.IGNORECASE)
    if explicit:
        return normalize_asin(explicit.group(1))
    for match in ASIN_RE.findall(combined.upper()):
        asin = normalize_asin(match)
        if asin:
            return asin
    return ""


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))).strip()


def amazon_india_product_asins(value: str) -> set[str]:
    """Extract product IDs from links whose actual host belongs to Amazon India."""
    asins = set()
    for link in re.findall(r"""(?:https?://|(?<![/@.\w])(?:www\.)?amazon\.in/)[^\s<>"']+""", html.unescape(str(value or "")), re.IGNORECASE):
        try:
            url = urlsplit(link if "://" in link else "https://" + link)
            host = url.hostname or ""
        except ValueError:
            continue
        if host != "amazon.in" and not host.endswith(".amazon.in"):
            continue
        product = re.search(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN)/([A-Z0-9]{10})(?=/|$|[),.;])", url.path, re.IGNORECASE)
        if product and (asin := normalize_asin(product.group(1))):
            asins.add(asin)
    return asins
