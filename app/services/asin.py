from __future__ import annotations

import base64
import html
import re


ASIN_SECRET = b"NUTRICITY-KEY"
ASIN_RE = re.compile(r"\b[A-Z0-9]{10}\b")


def normalize_asin(value: str) -> str:
    value = str(value or "").strip().upper()
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
        return normalize_asin(match)
    return ""


def strip_html(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))).strip()
