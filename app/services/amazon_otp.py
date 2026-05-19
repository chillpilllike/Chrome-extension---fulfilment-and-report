from __future__ import annotations

import email.utils
import html
import imaplib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


AMAZON_ORDER_ID_RE = re.compile(r"\b\d{3}-\d{7}-\d{7}\b")
OTP_RE = re.compile(r"one-time\s+password\s+is\s*(?:<[^>]+>|\s)*(\d{4,8})", re.IGNORECASE)
TRACK_URL_RE = re.compile(r"https://www\.amazon\.com/[^\s\"'<>()]+", re.IGNORECASE)


@dataclass
class ParsedAmazonEmail:
    amazon_order_id: str = ""
    email_type: str = "other"
    otp: str = ""
    tracking_url: str = ""
    shipment_id: str = ""
    package_index: str = ""
    recipient: str = ""
    product_summary: str = ""
    subject: str = ""
    sender: str = ""
    message_id: str = ""
    email_date: str = ""
    raw_json: str = "{}"


def parse_email_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value or "")


def message_bodies(message: Message) -> tuple[str, str]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                content = part.get_content() if isinstance(part, EmailMessage) else part.get_payload(decode=True)
                if isinstance(content, bytes):
                    content = content.decode(part.get_content_charset() or "utf-8", errors="replace")
                text = str(content or "")
            except Exception:
                continue
            if content_type == "text/html":
                html_parts.append(text)
            else:
                plain_parts.append(text)
    else:
        try:
            content = message.get_content() if isinstance(message, EmailMessage) else message.get_payload(decode=True)
            if isinstance(content, bytes):
                content = content.decode(message.get_content_charset() or "utf-8", errors="replace")
            text = str(content or "")
        except Exception:
            text = ""
        if message.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)
    return "\n".join(plain_parts), "\n".join(html_parts)


def html_to_visible_text(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|td|h\d|li)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text or "")
    if not match:
        return ""
    return clean_text(match.group(1) if match.groups() else match.group(0))


def extract_tracking_url(text: str) -> str:
    for raw_url in TRACK_URL_RE.findall(text or ""):
        url = html.unescape(raw_url).rstrip(".,")
        if "/progress-tracker/package" in url or "orderId=" in url:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            if query.get("U"):
                decoded = unquote(query["U"][0])
                if "amazon.com" in decoded:
                    return decoded
            return url
    return ""


def tracking_query_value(url: str, key: str) -> str:
    if not url:
        return ""
    query = parse_qs(urlparse(url).query)
    return clean_text((query.get(key) or [""])[0])


def extract_product_summary(text: str) -> str:
    for line in text.splitlines():
        stripped = clean_text(line)
        if stripped.startswith("* "):
            return stripped[2:502]
    lines = [clean_text(line.lstrip("* ")) for line in text.splitlines()]
    for line in lines:
        if line and not line.lower().startswith(("quantity:", "track package", "order #")) and len(line) > 12:
            if any(token in line.lower() for token in ("your package", "ordered", "shipped", "delivered", "arriving today", "out for delivery")):
                continue
            return line[:500]
    return ""


def extract_recipient(text: str) -> str:
    lines = [clean_text(line) for line in text.splitlines()]
    for line in lines:
        if " - " in line and not line.lower().startswith(("order", "amazon")):
            return line[:255]
    return ""


def parse_amazon_email(raw_bytes: bytes) -> ParsedAmazonEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    subject = clean_text(message.get("subject") or "")
    sender = clean_text(message.get("from") or "")
    message_id = clean_text(message.get("message-id") or "")
    plain_body, html_body = message_bodies(message)
    visible_html = html_to_visible_text(html_body)
    combined = "\n".join(part for part in (subject, plain_body, visible_html, html_body) if part)
    order_id = first_match(AMAZON_ORDER_ID_RE, combined)
    otp = first_match(OTP_RE, combined)
    tracking_url = extract_tracking_url(combined)
    if not order_id:
        order_id = tracking_query_value(tracking_url, "orderId")

    lower = f"{subject}\n{combined}".lower()
    email_type = "other"
    if otp:
        email_type = "otp"
    elif "shipped" in lower or "track package" in lower or tracking_url:
        email_type = "dispatch"

    return ParsedAmazonEmail(
        amazon_order_id=order_id,
        email_type=email_type,
        otp=otp,
        tracking_url=tracking_url,
        shipment_id=tracking_query_value(tracking_url, "shipmentId"),
        package_index=tracking_query_value(tracking_url, "packageIndex"),
        recipient=extract_recipient(plain_body or visible_html),
        product_summary=extract_product_summary(plain_body or visible_html),
        subject=subject,
        sender=sender,
        message_id=message_id,
        email_date=parse_email_date(message.get("date")),
        raw_json=json.dumps(
            {
                "subject": subject,
                "from": sender,
                "date": message.get("date") or "",
                "message_id": message_id,
            },
            default=str,
        ),
    )


def imap_connect(settings: dict[str, str]) -> imaplib.IMAP4:
    host = clean_text(settings.get("amazon_otp_imap_host"))
    port = int(settings.get("amazon_otp_imap_port") or 993)
    ssl_enabled = str(settings.get("amazon_otp_imap_ssl") or "true").lower() in {"1", "true", "yes", "on"}
    if not host:
        raise RuntimeError("Amazon OTP IMAP host is required.")
    client: imaplib.IMAP4
    if ssl_enabled:
        client = imaplib.IMAP4_SSL(host, port)
    else:
        client = imaplib.IMAP4(host, port)
    username = clean_text(settings.get("amazon_otp_imap_username"))
    password = str(settings.get("amazon_otp_imap_password") or "")
    if not username or not password:
        raise RuntimeError("Amazon OTP IMAP username and password are required.")
    client.login(username, password)
    return client


def imap_search_since(days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    return since.strftime("%d-%b-%Y")
