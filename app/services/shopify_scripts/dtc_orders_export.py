
from __future__ import annotations
# --------------------------------------------------------------------------------------
# IGNORE ODOO "DELIVERY / SHIPPING AS PRODUCT" LINES
#
# Odoo can represent shipping charges as a normal sale.order.line with a "delivery" product.
# These should NOT become Shopify line_items (Shopify shipping should be represented via shipping_lines).
#
# Matching rules:
# - Exact match is case-insensitive.
# - Prefer matching by SKU (Odoo product.product.default_code) because it's stable across languages.
# - Title match is a fallback.
# - Optional prefix matching helps future-proof when delivery SKUs follow a pattern (e.g. Delivery_001, Delivery_ABC).
#
# Add/extend these sets as needed (use lowercase values; matching is normalized to lowercase).
IGNORE_LINEITEM_SKUS_EXACT = {
    "delivery_007",     # Standard delivery (example)
}

IGNORE_LINEITEM_TITLES_EXACT = {
    "standard delivery",  # fallback title match
}

# Optional prefixes to ignore (case-insensitive). Leave empty tuple () to disable prefix matching.
IGNORE_LINEITEM_SKU_PREFIXES = (
    "delivery_",
)

def _should_ignore_odoo_line_item(sku: str | None, title: str | None) -> bool:
    sku_norm = (sku or "").strip().lower()
    title_norm = (title or "").strip().lower()

    if sku_norm and sku_norm in IGNORE_LINEITEM_SKUS_EXACT:
        return True
    if title_norm and title_norm in IGNORE_LINEITEM_TITLES_EXACT:
        return True
    if sku_norm and IGNORE_LINEITEM_SKU_PREFIXES:
        for pref in IGNORE_LINEITEM_SKU_PREFIXES:
            if sku_norm.startswith((pref or "").strip().lower()):
                return True
    return False
# --------------------------------------------------------------------------------------
def odoo_effective_unit_price(line: dict, qty_raw: float) -> float:
    """Compute unit price to use on Shopify order line from Odoo sale.order.line.

    Primary: price_total/qty (discounted, tax-included).
    Fallbacks:
    - price_subtotal/qty (discounted, excl. tax) if total missing
    - price_unit adjusted by discount%
    """
    q = qty_raw if qty_raw and qty_raw > 0 else 1.0

    # Primary: total / qty (tax included)
    pt = line.get("price_total", None)
    try:
        if pt is not None:
            ptf = float(pt)
            return ptf / q
    except Exception:
        pass

    # Fallback: subtotal / qty (tax excluded)
    ps = line.get("price_subtotal", None)
    try:
        if ps is not None:
            psf = float(ps)
            return psf / q
    except Exception:
        pass

    # Fallback: unit * (1 - discount)
    try:
        pu = float(line.get("price_unit") or 0.0)
    except Exception:
        pu = 0.0
    try:
        disc = float(line.get("discount") or 0.0)
    except Exception:
        disc = 0.0
    if disc:
        pu = pu * max(0.0, (1.0 - (disc / 100.0)))
    return pu or 0.0
def money_str(value: float) -> str:
    """Format money with 2 decimals for Shopify fields like discount_codes.amount."""
    try:
        return f"{float(value):.2f}"
    except Exception:
        return "0.00"

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Odoo 18 -> Shopify Order+Customer+Product Sync (CSV list of Odoo order numbers, NO HEADERS)

What this script does (high-level):
1) Reads a CSV/TXT file that contains *only* Odoo order numbers (one per line, no headers).
2) For each listed Odoo sale.order, it pulls ALL details from Odoo (customer, shipping/billing, lines, prices, images).
3) For each destination Shopify store:
   - Creates/updates customer (or assigns to generic customer if enabled)
   - Ensures each line-item product exists in Shopify (by SKU), cloning product info + image if missing
   - Creates the order in Shopify, with shipping/billing addresses from Odoo.
   - Remembers synced orders/SKUs/customers in a local SQLite DB to skip duplicates.

Important:
- Odoo is the source of truth for customer data (unlike Shopify restrictions).
- CSV is just a list of Odoo order numbers, nothing else.
- Country rule: If shipping/billing country is AU/Australia or NZ/New Zealand, the script will NOT override prices on the order
  (it will rely on Shopify variant price). For all other countries, if OVERRIDE_PRICES=True, it will set line item prices from Odoo.

Requirements:
    pip install requests

Edit the CONFIG section below and run:
    python odoo18_to_shopify_orders_sync_by_csv_noheader.py
"""
import datetime as dt
import hashlib
import base64
import random
import os
import re
import sqlite3
import sys
import time
import xmlrpc.client
from typing import List
from urllib.parse import urljoin

import requests

# =========================
# CONFIG (EDIT ME)
# =========================

# --- ODOO SOURCES (predefined; choose one at runtime) ---
ODOO_SOURCES = [
    {
        "name": "nutricity-usa",
        "url": "https://nutricityusa.com",  # no trailing slash preferred
        "db": "supplee",
        "username": "admin@nutricityusa.com",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "boostgo",
        "url": "https://boostgo.com.au",  # no trailing slash preferred
        "db": "boostgo",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "espot",
        "url": "https://espot.com.au",  # no trailing slash preferred
        "db": "espot",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "suppcity",
        "url": "https://suppcity.com.au",  # no trailing slash preferred
        "db": "suppcity",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "vitagen",
        "url": "https://vitagen.com.au",  # no trailing slash preferred
        "db": "vitagen",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "vitashop",
        "url": "https://vitashop.co.nz",  # no trailing slash preferred
        "db": "vitashop",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "nutrihub",
        "url": "https://nutrihub.ca",  # no trailing slash preferred
        "db": "nutrihub",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "Secretgreen",
        "url": "https://secretgreen.com.au",  # no trailing slash preferred
        "db": "secretgreen",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "wildkart",
        "url": "https://wildkart.com.au",  # no trailing slash preferred
        "db": "wildkart",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
]

# Runtime-selected source values (set in main() after prompt)
ODOO_URL = ODOO_SOURCES[0]["url"]
ODOO_DB = ODOO_SOURCES[0]["db"]
ODOO_USERNAME = ODOO_SOURCES[0]["username"]
ODOO_PASSWORD = ODOO_SOURCES[0]["password"]

# Odoo model names (Odoo 18 standard)
ODOO_ORDER_MODEL = "sale.order"
ODOO_ORDER_LINE_MODEL = "sale.order.line"
ODOO_PARTNER_MODEL = "res.partner"
ODOO_PRODUCT_MODEL = "product.product"
ODOO_TEMPLATE_MODEL = "product.template"
ODOO_COUNTRY_MODEL = "res.country"
ODOO_CURRENCY_MODEL = "res.currency"
ODOO_TAG_MODEL = "crm.tag"

# --- SHOPIFY DESTINATIONS ---
# Destination tokens are obtained automatically via OAuth (expiring offline tokens + refresh).
# For each destination store, provide the OAuth app credentials (client_id/client_secret) and scopes.
#
# On first run, the script prints an authorization URL. Open it in a browser, approve, and the script
# will capture the redirect (local server) to obtain and store tokens in STATE_DB.
#
# On later runs, tokens are refreshed automatically using the stored refresh token.
#
# Shopify docs (Dec 2025): expiring offline access tokens + refresh tokens.
DESTS = [
    {
        "name": "gofinch1-usa",
        "shop": "gofinch1-usa.myshopify.com",
        "auth": "oauth_expiring_offline",  # required
        "client_id": "195db1cdb832e35000d5dd2080b8fee3",
        "client_secret": os.getenv("SHOPIFY_DTC_CLIENT_SECRET", ""),
        "scopes": "read_orders,write_orders,read_customers,write_customers,read_products,write_products",
        "redirect_uri": "http://localhost:8080/callback",
        "api_version": "2025-10",
        "force_reauth": False,
    },
]

# --- STATE/DEDUP ---
STATE_DB = "sync_state.sqlite3"

# --- ORDER BEHAVIOR ---
# If True, sends Odoo order currency in Shopify order payload.
# Many stores reject unsupported currencies -> keep False unless you are sure destination supports the currency.
SET_SHOPIFY_ORDER_CURRENCY = False

# Disable ALL customer notifications (emails/SMS) that Shopify endpoints support.
# Order create: send_receipt/send_fulfillment_receipt
# Order cancel: email
# Fulfillment create: notify_customer
DISABLE_SHOPIFY_NOTIFICATIONS = True
UPDATE_EXISTING_SKU_PRODUCTS = True


ASSIGN_TO_GENERIC_CUSTOMER = False
GENERIC_CUSTOMER_EMAIL = "am-it@outlook.com"
GENERIC_CUSTOMER_FIRST_NAME = "Amit"
GENERIC_CUSTOMER_LAST_NAME = "Soni"


GENERIC_CUSTOMER_PHONE = "+61435323530"  # Generic customer phone (E.164 preferred)
GENERIC_OVERWRITE_CUSTOMER_EACH_RUN = True  # Overwrite generic customer names/phone/address each run

GENERIC_DEFAULT_ADDRESS = {
    "first_name": "Amit",
    "last_name": "Soni",
    "company": "",
    "address1": "31 Indura Dr",
    "address2": "",
    "city": "Werribee",
    "province": "VIC",
    "province_code": "VIC",
    "country": "Australia",
    "country_code": "AU",
    "zip": "3030",
    "phone": "+61435323530",
}

def odoo_unit_price_no_discount(line: dict) -> float:
    """Return Odoo's *base* unit price, ignoring any discount (%) field.
    - price_unit is the pre-discount unit price in Odoo sale.order.line
    """
    try:
        return float(line.get("price_unit") or 0.0)
    except Exception:
        return 0.0


def get_generic_default_address() -> dict:
    """Return a cleaned copy of GENERIC_DEFAULT_ADDRESS for Shopify payloads."""
    addr = dict(GENERIC_DEFAULT_ADDRESS or {})
    addr.setdefault("first_name", GENERIC_CUSTOMER_FIRST_NAME)
    addr.setdefault("last_name", GENERIC_CUSTOMER_LAST_NAME)
    out = {}
    for k, v in addr.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[k] = v
    return out


# If True, will override prices for non-AU/NZ orders (see OVERRIDE_PRICE_MODE).
OVERRIDE_PRICES = True

# Price override mode for non-AU/NZ orders:
#   - 'odoo'   : use Odoo line price_unit (previous behavior)
#   - 'random' : use a random integer price chosen from RANDOM_PRICE_CHOICES (no decimals)
OVERRIDE_PRICE_MODE = "random"  # 'odoo' or 'random'

# When OVERRIDE_PRICE_MODE='random', choose prices only from this integer list (no decimals).
# Example: [2,3,4,5,6,7,8]
RANDOM_PRICE_CHOICES = [1, 2, 3, 4, 5]
# Minimum allowed line item price when overriding (Shopify total/line prices cannot be negative)
MIN_LINE_PRICE = 0


# If True, discounts in Odoo are completely ignored:
# - sale.order.line.discount (%) is ignored
# - negative adjustment/discount lines are skipped
# - no Shopify discount_codes are created
IGNORE_ODOO_DISCOUNTS = True

# When OVERRIDE_PRICES=False (or for AU/NZ orders), Shopify *should* use the variant price automatically.
# Some stores/API cases return 0.00 when price is not provided, so we explicitly send the current Shopify variant price.
USE_SHOPIFY_VARIANT_PRICE_ON_ORDER_WHEN_OVERRIDES_OFF = True

# When OVERRIDE_PRICES=False and a SKU is missing in Shopify, the script may create the product.
# Shopify defaults newly-created variant prices to 0.00 unless we seed a price.
USE_ODOO_PRICE_FOR_NEW_PRODUCTS_WHEN_OVERRIDES_OFF = True

# AU/NZ duty cap rule:
# If an AU or NZ order's Odoo amount_total exceeds AUNZ_DUTY_THRESHOLD,
# reduce ALL *product* line-item prices proportionally so imported value
# does not exceed AUNZ_MAX_ORDER_VALUE (does NOT change catalog prices).
AUNZ_DUTY_THRESHOLD = 700.0
AUNZ_MAX_ORDER_VALUE = 899.99


# =========================
# SHIPPING METHOD RULES (applied at SHOPIFY ORDER CREATION)
# =========================
# This script creates Shopify orders via Admin API. Shopify won't "attach a CarrierService" to an already-created order
# the way checkout does, but we *can* set the order's `shipping_lines` label during creation.
#
# Your Shopify shipping method labels:
#   - DHL Shipping
#   - Fedex Shipping
#   - USPS Shipping
#   - UPS Shipping
#
# Rules are evaluated top-to-bottom (first match wins).
#
SHIPPING_METHOD_CATALOG = [
    "DHL Shipping",
    "Fedex Shipping",
    "USPS Shipping",
    "UPS Shipping",
]

SHIPPING_RULES = [
]

# If True, include Odoo order name + notes in keyword matching (no extra API calls).
SHIP_RULE_INCLUDE_NOTES = True


# If True, the 'random' price is deterministic per SKU (stable across runs).
# If False, a new random value can be picked each run.
RANDOM_PRICE_DETERMINISTIC = True

# If True, will set financial_status="paid" (best effort).
MARK_AS_PAID = True

# Products: strip words from titles/descriptions when cloning into Shopify
STRIP_WORDS: List[str] = [
    "Melatonin", "Dhea", "Dha", "Phenibut", "DMAA", "dimethylamylamine",
    "DMHA", "dimethylhexylamine", "Oxedrine", "Synephrine", "Tadalafil",
    "BPC-157", "Ibutamoren", "MK-677", "Yohimbine", "Levodopa",
    "Tryptophan", "5-HTP", "cat", "dog", "horse", "animal", "dogs", "cats", "horses", "animals", "sleep" ,
    "poultry", "chicken", "chickens", "Turkeys", "AID", "AIDS", "goat", "goats", "BPC-157", "BPC157", "BPC", 
    "157", "Lions", "Mane", "Asleep", "Drug-free", "drug", "drugs", "Asleep", "Mushroom", "libido", "Diphenhydramine", "Orlistat", "Pregnenolone", "Colloidal silver", "Alcohol tinctures", "sheep", "sheeps", "Honey Weed", "Maca", "fenbendazole ", "puppy", "pet", "pets", "NAD", "NAD+", "fat", "burner", "weightloss"
]



def pick_random_price_for_sku(sku: str) -> int:
    """Pick an integer price from RANDOM_PRICE_CHOICES for a given SKU.
    If RANDOM_PRICE_DETERMINISTIC=True, the choice is stable across runs.
    """
    choices = [int(x) for x in (RANDOM_PRICE_CHOICES or []) if int(x) > 0]
    if not choices:
        choices = [1, 2, 3, 4, 5]
    if RANDOM_PRICE_DETERMINISTIC:
        h = hashlib.sha256((sku or "").encode("utf-8", errors="ignore")).hexdigest()
        idx = int(h[:8], 16) % len(choices)
        return int(choices[idx])
    return int(random.choice(choices))


def price_str_no_decimals(value: int | float) -> str:
    """Format a price as an integer string (no decimals) for Shopify."""
    try:
        return str(int(round(float(value))))
    except Exception:
        return "0"

DECODE_ASIN = True  # Decode Nutricity-encoded ASIN SKUs back to original ASIN when possible.

# Safety toggles
DRY_RUN = False  # if True, does not create anything in Shopify.

# Rate limit handling
HTTP_TIMEOUT = 60
MAX_RETRIES = 6
RETRY_BACKOFF_BASE = 1.5

# =========================
# END CONFIG
# =========================


def log(level_or_msg: str, msg: str | None = None) -> None:
    """Lightweight logger.

    Supports both:
      - log("INFO", "message")
      - log("message")  # defaults to INFO
    """
    if msg is None:
        level = "INFO"
        msg = level_or_msg
    else:
        level = level_or_msg

    ts = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e if "@" in e else None


def normalize_phone(phone: str | None) -> str | None:
    """Return phone in a Shopify-friendly E.164-like form or None.
    Shopify often rejects non-E.164 values. We only keep numbers that start with '+' and have 8-15 digits.
    Anything else is dropped to avoid 422 'phone is invalid'.
    """
    if not phone:
        return None
    p = re.sub(r"[^\d+]", "", phone.strip())
    if not p:
        return None
    if p.startswith("00"):
        p = "+" + p[2:]
    if not p.startswith("+"):
        return None
    digits = re.sub(r"\D", "", p)
    if len(digits) < 8 or len(digits) > 15:
        return None
    return "+" + digits



def _clean_dict(d: dict) -> dict:
    """Remove keys with None/empty-string values (helps avoid Shopify 400 validation errors)."""
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        out[k] = v
    return out


def sanitize_sku(value: str | None, max_len: int = 39) -> str | None:
    """Keep SKUs simple for Shopify/admin search: letters, digits, and spaces only."""
    s = (value or "").strip()
    if not s:
        return None
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    return s[:max_len].rstrip() or None

def qty_to_int_or_none(qty: float, eps: float = 1e-6) -> int | None:
    """Shopify order line_items.quantity must be an integer.
    Returns int if qty is effectively whole; otherwise None (fractional).
    """
    if qty is None:
        return 1
    try:
        q = float(qty)
    except Exception:
        return 1
    r = round(q)
    if abs(q - r) <= eps:
        return int(r)
    return None


# === ASIN decode helpers (must match the encoding used during Odoo product import) ===
_ASIN_SECRET = b"NUTRICITY-KEY"  # reversible obfuscation key used in your import script

def _xor_bytes(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    klen = len(key)
    return bytes([b ^ key[i % klen] for i, b in enumerate(data)])

def decode_asin(encoded: str) -> str:
    """Decode encoded ASIN back to original (if produced by encode_asin in your import script)."""
    try:
        s = (encoded or "").strip().upper()
        if not s:
            return encoded
        pad = "=" * ((8 - (len(s) % 8)) % 8)
        x = base64.b32decode(s + pad, casefold=True)
        raw = _xor_bytes(x, _ASIN_SECRET)
        return raw.decode("utf-8", errors="ignore")
    except Exception:
        return encoded

_ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")

def maybe_decode_asin_sku(sku: str) -> str:
    """If DECODE_ASIN and sku looks like our encoded format, decode and return original ASIN when plausible."""
    if not DECODE_ASIN:
        return sku
    s = (sku or "").strip()
    if not s:
        return s
    dec = decode_asin(s).strip().upper()
    if dec and _ASIN_RE.match(dec):
        return dec
    return s


def strip_words(text: str, words: list[str]) -> str:
    out = text or ""
    for w in words:
        if not w:
            continue
        out = re.sub(re.escape(w), "", out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


class StateDB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()

        # --- Auto-migrate very old state DBs (created by older script versions) ---
        # If tables exist without the expected 'dest_name' column, rename them out of the way so the new schema can be created.
        def _has_col(table: str, col: str) -> bool:
            try:
                rows = cur.execute(f"PRAGMA table_info({table});").fetchall()
                return any((r[1] == col) for r in rows)
            except Exception:
                return False

        def _table_exists(table: str) -> bool:
            try:
                r = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table,)).fetchone()
                return bool(r)
            except Exception:
                return False

        for _tbl in ("order_map", "sku_map", "customer_map", "oauth_tokens"):
            if _table_exists(_tbl) and (not _has_col(_tbl, "dest_name")):
                ts = int(time.time())
                legacy = f"{_tbl}_legacy_{ts}"
                log("WARN", f"STATE_DB: {_tbl} missing dest_name column; renaming to {legacy} and recreating schema")
                try:
                    cur.execute(f"ALTER TABLE {_tbl} RENAME TO {legacy};")
                except Exception:
                    # If rename fails, drop to avoid hard crash.
                    cur.execute(f"DROP TABLE IF EXISTS {_tbl};")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS order_map (
            dest_name TEXT NOT NULL,
            src_order_key TEXT NOT NULL,
            dest_order_id TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (dest_name, src_order_key)
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS sku_map (
            dest_name TEXT NOT NULL,
            sku TEXT NOT NULL,
            variant_id INTEGER,
            product_id INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dest_name, sku)
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS customer_map (
            dest_name TEXT NOT NULL,
            key TEXT NOT NULL,
            customer_id INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dest_name, key)
        );
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            dest_name TEXT NOT NULL,
            shop TEXT NOT NULL,
            access_token TEXT,
            expires_at TEXT,              -- ISO UTC, nullable for non-expiring
            refresh_token TEXT,
            refresh_expires_at TEXT,      -- ISO UTC, nullable
            scope TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dest_name)
        );
        """)

        self.conn.commit()

    def is_order_synced(self, dest_name: str, src_order_key: str) -> bool:
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM order_map WHERE dest_name=? AND src_order_key=? LIMIT 1", (dest_name, src_order_key))
        return cur.fetchone() is not None

    def mark_order_synced(self, dest_name: str, src_order_key: str, dest_order_id: str | None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO order_map(dest_name, src_order_key, dest_order_id, created_at) VALUES (?,?,?,?)",
            (dest_name, src_order_key, dest_order_id, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_variant_for_sku(self, dest_name: str, sku: str) -> tuple[int | None, int | None]:
        cur = self.conn.cursor()
        cur.execute("SELECT variant_id, product_id FROM sku_map WHERE dest_name=? AND sku=? LIMIT 1", (dest_name, sku))
        row = cur.fetchone()
        if not row:
            return None, None
        return row[0], row[1]

    def set_variant_for_sku(self, dest_name: str, sku: str, variant_id: int | None, product_id: int | None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO sku_map(dest_name, sku, variant_id, product_id, updated_at) VALUES (?,?,?,?,?)",
            (dest_name, sku, variant_id, product_id, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_customer_id(self, dest_name: str, key: str) -> int | None:
        cur = self.conn.cursor()
        cur.execute("SELECT customer_id FROM customer_map WHERE dest_name=? AND key=? LIMIT 1", (dest_name, key))
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def set_customer_id(self, dest_name: str, key: str, customer_id: int | None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO customer_map(dest_name, key, customer_id, updated_at) VALUES (?,?,?,?)",
            (dest_name, key, customer_id, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_oauth_tokens(self, dest_name: str) -> dict | None:
        cur = self.conn.cursor()
        cur.execute("SELECT shop, access_token, expires_at, refresh_token, refresh_expires_at, scope, updated_at FROM oauth_tokens WHERE dest_name=? LIMIT 1", (dest_name,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "shop": row[0],
            "access_token": row[1],
            "expires_at": row[2],
            "refresh_token": row[3],
            "refresh_expires_at": row[4],
            "scope": row[5],
            "updated_at": row[6],
        }

    def upsert_oauth_tokens(self, dest_name: str, shop: str, access_token: str | None, expires_at: str | None,
                            refresh_token: str | None, refresh_expires_at: str | None, scope: str | None):
        cur = self.conn.cursor()
        cur.execute(
            "INSERT OR REPLACE INTO oauth_tokens(dest_name, shop, access_token, expires_at, refresh_token, refresh_expires_at, scope, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (dest_name, shop, access_token, expires_at, refresh_token, refresh_expires_at, scope, dt.datetime.now(dt.timezone.utc).isoformat()),
        )
        self.conn.commit()


class OdooClient:
    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password

        common_url = urljoin(self.url + "/", "xmlrpc/2/common")
        object_url = urljoin(self.url + "/", "xmlrpc/2/object")

        self.common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
        self.object = xmlrpc.client.ServerProxy(object_url, allow_none=True)
        self.uid: int | None = None

    def connect(self):
        uid = self.common.authenticate(self.db, self.username, self.password, {})
        if not uid:
            raise RuntimeError("Odoo authentication failed (check URL/DB/username/password).")
        self.uid = int(uid)
        log("INFO", f"Odoo connected: uid={uid}")

    def _exec(self, model: str, method: str, args: list, kwargs: dict | None = None):
        assert self.uid is not None
        return self.object.execute_kw(self.db, self.uid, self.password, model, method, args, kwargs or {})

    def search_read(self, model: str, domain: list, fields: list[str], limit: int = 0):
        return self._exec(model, "search_read", [domain], {"fields": fields, "limit": limit})

    def read(self, model: str, ids: list[int], fields: list[str]):
        if not ids:
            return []
        return self._exec(model, "read", [ids], {"fields": fields})

    def get_order_by_number(self, order_number: str) -> dict | None:
        fields = [
            "id", "name", "date_order", "state",
            "partner_id", "partner_invoice_id", "partner_shipping_id",
            "order_line", "currency_id",
            "amount_total", "amount_tax", "amount_untaxed",
            "client_order_ref", "note", "tag_ids",
        ]
        try:
            rows = self.search_read(ODOO_ORDER_MODEL, [["name", "=", order_number]], fields, limit=1)
        except Exception:
            rows = self.search_read(ODOO_ORDER_MODEL, [["name", "=", order_number]], [f for f in fields if f != "tag_ids"], limit=1)
        if rows:
            return rows[0]
        if order_number.isdigit():
            try:
                rows = self.read(ODOO_ORDER_MODEL, [int(order_number)], fields)
            except Exception:
                rows = self.read(ODOO_ORDER_MODEL, [int(order_number)], [f for f in fields if f != "tag_ids"])
            return rows[0] if rows else None
        return None

    def get_partner(self, partner_id: int) -> dict | None:
        rows = self.read(ODOO_PARTNER_MODEL, [partner_id], [
            "id", "name", "email", "phone", "mobile",
            "street", "street2", "city", "zip",
            "state_id", "country_id",
        ])
        return rows[0] if rows else None

    def get_country(self, country_id: int) -> dict | None:
        rows = self.read(ODOO_COUNTRY_MODEL, [country_id], ["id", "name", "code"])
        return rows[0] if rows else None

    def get_currency(self, currency_id: int) -> dict | None:
        rows = self.read(ODOO_CURRENCY_MODEL, [currency_id], ["id", "name", "symbol"])
        return rows[0] if rows else None

    def get_tag_names(self, tag_ids: list[int]) -> list[str]:
        if not tag_ids:
            return []
        try:
            rows = self.read(ODOO_TAG_MODEL, [int(x) for x in tag_ids], ["id", "name"])
        except Exception:
            return []
        out: list[str] = []
        for r in rows or []:
            name = str(r.get("name") or "").strip()
            if name:
                out.append(name)
        return out

    def get_order_lines(self, line_ids: list[int]) -> list[dict]:
        return self.read(ODOO_ORDER_LINE_MODEL, line_ids, [
            "id", "name", "product_id", "product_uom_qty",
            "price_unit", "price_total", "price_subtotal", "discount",
            "display_type",
        ])

    def get_product_product(self, product_id: int) -> dict | None:
        rows = self.read(ODOO_PRODUCT_MODEL, [product_id], [
            "id", "product_tmpl_id",
            "default_code", "barcode",
            "name", "description", "description_sale",
            "image_1920",
        ])
        return rows[0] if rows else None

    def get_product_template(self, tmpl_id: int) -> dict | None:
        rows = self.read(ODOO_TEMPLATE_MODEL, [tmpl_id], [
            "id", "name", "website_description", "description_sale",
            "list_price", "image_1920",
        ])
        return rows[0] if rows else None

def get_state_from_odoo(odoo: OdooClient, state_id):
    """Fetch state name + code from Odoo safely"""
    if not state_id:
        return None, None

    try:
        sid = state_id[0] if isinstance(state_id, (list, tuple)) else state_id
        rows = odoo.read("res.country.state", [int(sid)], ["name", "code"])
        if rows:
            return rows[0].get("name"), rows[0].get("code")
    except Exception:
        pass

    return None, None



# -------------------------
# Shopify OAuth (expiring offline tokens + refresh)
# -------------------------
# Shopify docs (Dec 2025): expiring offline tokens can be obtained by including expiring=1 when exchanging
# an authorization code for an offline token, and refreshed using grant_type=refresh_token at:
#   POST https://{shop}.myshopify.com/admin/oauth/access_token
#
# This script implements:
# - First run: authorization code grant -> expiring offline token + refresh token
# - Later runs: refresh_token grant to rotate tokens automatically
#
# Security:
# - Tokens are stored in STATE_DB. Protect this file; treat it like a password vault.

import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso_utc(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def parse_iso_utc(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    ss = s.strip()
    if ss.endswith("Z"):
        ss = ss[:-1]
    try:
        return dt.datetime.fromisoformat(ss).replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    result = {"code": None, "state": None, "error": None}

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        OAuthCallbackHandler.result["code"] = (qs.get("code") or [None])[0]
        OAuthCallbackHandler.result["state"] = (qs.get("state") or [None])[0]
        OAuthCallbackHandler.result["error"] = (qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OAuth received. You can close this tab and return to the script.\n")

    def log_message(self, format, *args):
        return


def run_local_oauth_server(redirect_uri: str, timeout_s: int = 600) -> dict:
    u = urlparse(redirect_uri)
    host = u.hostname or "localhost"
    port = u.port or (443 if u.scheme == "https" else 80)

    OAuthCallbackHandler.result = {"code": None, "state": None, "error": None}
    httpd = HTTPServer((host, port), OAuthCallbackHandler)

    def serve():
        httpd.timeout = 1
        start = time.time()
        while time.time() - start < timeout_s:
            httpd.handle_request()
            if OAuthCallbackHandler.result.get("code") or OAuthCallbackHandler.result.get("error"):
                break
        try:
            httpd.server_close()
        except Exception:
            pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()

    start = time.time()
    while time.time() - start < timeout_s:
        if OAuthCallbackHandler.result.get("code") or OAuthCallbackHandler.result.get("error"):
            break
        time.sleep(0.25)

    return OAuthCallbackHandler.result


def shopify_authorize_url(shop: str, client_id: str, scopes: str, redirect_uri: str, state: str) -> str:
    params = {"client_id": client_id, "scope": scopes, "redirect_uri": redirect_uri, "state": state}
    return f"https://{shop}/admin/oauth/authorize?{urlencode(params)}"


def shopify_exchange_code_for_tokens(shop: str, client_id: str, client_secret: str, code: str, expiring: int = 1) -> dict:
    url = f"https://{shop}/admin/oauth/access_token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"client_id": client_id, "client_secret": client_secret, "code": code, "expiring": str(expiring)}
    resp = requests.post(url, data=data, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def shopify_refresh_tokens(shop: str, client_id: str, client_secret: str, refresh_token: str) -> dict:
    url = f"https://{shop}/admin/oauth/access_token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    data = {"client_id": client_id, "client_secret": client_secret, "grant_type": "refresh_token", "refresh_token": refresh_token}
    resp = requests.post(url, data=data, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_shopify_access_token(dest_cfg: dict, state: StateDB) -> str:
    auth = dest_cfg.get("auth")
    if auth != "oauth_expiring_offline":
        raise RuntimeError(f"DEST {dest_cfg.get('name')} must use auth='oauth_expiring_offline' (access_token method is disabled).")

    dest_name = dest_cfg["name"]
    shop = dest_cfg["shop"]
    client_id = dest_cfg["client_id"]
    client_secret = dest_cfg["client_secret"]
    scopes = dest_cfg["scopes"]
    redirect_uri = dest_cfg["redirect_uri"]
    force_reauth = bool(dest_cfg.get("force_reauth"))

    tok = None if force_reauth else state.get_oauth_tokens(dest_name)
    now = utc_now()

    if tok and tok.get("access_token"):
        expires_at = parse_iso_utc(tok.get("expires_at"))
        refresh_token = tok.get("refresh_token")
        refresh_expires_at = parse_iso_utc(tok.get("refresh_expires_at"))

        if expires_at and expires_at > (now + dt.timedelta(minutes=2)):
            return tok["access_token"]
        if not expires_at:
            return tok["access_token"]

        if refresh_token and (not refresh_expires_at or refresh_expires_at > now):
            log("INFO", f"{dest_name}: refreshing Shopify offline token for {shop}")
            data = shopify_refresh_tokens(shop, client_id, client_secret, refresh_token)
            access_token = data.get("access_token")
            expires_in = int(data.get("expires_in") or 0) or 3600
            new_refresh = data.get("refresh_token")
            rt_expires_in = int(data.get("refresh_token_expires_in") or 0) or 7776000
            scope = data.get("scope")

            expires_at_new = iso_utc(now + dt.timedelta(seconds=expires_in))
            refresh_expires_at_new = iso_utc(now + dt.timedelta(seconds=rt_expires_in))

            state.upsert_oauth_tokens(dest_name, shop, access_token, expires_at_new, new_refresh, refresh_expires_at_new, scope)
            return access_token

        log("WARN", f"{dest_name}: stored token expired and cannot refresh; re-authorizing is required.")

    oauth_state = secrets.token_urlsafe(16)
    url = shopify_authorize_url(shop, client_id, scopes, redirect_uri, oauth_state)
    log("INFO", f"{dest_name}: Open this URL in your browser to authorize:\n{url}")
    log("INFO", f"{dest_name}: Waiting for OAuth redirect at {redirect_uri} ...")

    result = run_local_oauth_server(redirect_uri, timeout_s=600)
    if result.get("error"):
        raise RuntimeError(f"{dest_name}: OAuth error: {result.get('error')}")
    code = result.get("code")
    got_state = result.get("state")
    if not code:
        raise RuntimeError(f"{dest_name}: OAuth did not return a code (timeout?).")
    if got_state != oauth_state:
        raise RuntimeError(f"{dest_name}: OAuth state mismatch (potential CSRF).")

    data = shopify_exchange_code_for_tokens(shop, client_id, client_secret, code, expiring=1)
    access_token = data.get("access_token")
    expires_in = int(data.get("expires_in") or 0) or 3600
    refresh_token = data.get("refresh_token")
    rt_expires_in = int(data.get("refresh_token_expires_in") or 0) or 7776000
    scope = data.get("scope")

    expires_at = iso_utc(now + dt.timedelta(seconds=expires_in)) if expires_in else None
    refresh_expires_at = iso_utc(now + dt.timedelta(seconds=rt_expires_in)) if rt_expires_in else None

    state.upsert_oauth_tokens(dest_name, shop, access_token, expires_at, refresh_token, refresh_expires_at, scope)
    log("INFO", f"{dest_name}: OAuth token stored (expires_in={expires_in}s).")
    return access_token


class ShopifyClient:
    def __init__(self, name: str, shop: str, access_token: str, api_version: str):
        self.name = name
        self.shop = shop
        self.access_token = access_token
        self.api_version = api_version
        self.rest_base = f"https://{shop}/admin/api/{api_version}/"
        self.graphql_url = f"https://{shop}/admin/api/{api_version}/graphql.json"

        self.session = requests.Session()
        self.session.headers.update({
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })


    def _format_shopify_error(self, resp: requests.Response) -> str:
        try:
            j = resp.json()
            # Shopify errors can be {"errors": "..."} or {"errors": {"field": ["msg"]}}
            return str(j)
        except Exception:
            return resp.text.strip()
    def _request(self, method: str, url: str, json_body: dict | None = None) -> dict:
        for attempt in range(1, MAX_RETRIES + 1):
            resp = None
            try:
                resp = self.session.request(method, url, json=json_body, timeout=HTTP_TIMEOUT)
            except requests.RequestException as e:
                if attempt == MAX_RETRIES:
                    raise
                sleep_s = (RETRY_BACKOFF_BASE ** attempt)
                log("WARN", f"{self.name} HTTP exception: {e}. retry in {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                if attempt == MAX_RETRIES:
                    resp.raise_for_status()
                sleep_s = (RETRY_BACKOFF_BASE ** attempt)
                ra = resp.headers.get("Retry-After")
                if ra and ra.isdigit():
                    sleep_s = max(sleep_s, float(ra))
                log("WARN", f"{self.name} {resp.status_code} retry in {sleep_s:.1f}s: {resp.text[:200]}")
                time.sleep(sleep_s)
                continue

            if resp.status_code >= 400:


                details = self._format_shopify_error(resp)


                raise RuntimeError(f"{self.name} HTTP {resp.status_code} {resp.request.method} {resp.url} -> {details}")


            return resp.json() if resp.text else {}

        raise RuntimeError("unreachable")

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        payload = {"query": query, "variables": variables or {}}
        data = self._request("POST", self.graphql_url, payload)
        if "errors" in data and data["errors"]:
            raise RuntimeError(f"{self.name} GraphQL errors: {data['errors']}")
        return data.get("data") or {}

    def find_variant_by_sku(self, sku: str) -> tuple[int | None, int | None]:
        q = """
        query($q: String!) {
          productVariants(first: 1, query: $q) { nodes { id product { id } } }
        }"""
        data = self.graphql(q, {"q": f"sku:{sku}"})
        nodes = ((data.get("productVariants") or {}).get("nodes") or [])
        if not nodes:
            return None, None
        return gid_to_int(nodes[0]["id"]), gid_to_int(nodes[0]["product"]["id"])

    def get_variant_price(self, variant_id: int) -> float | None:
        """Fetch the current Shopify variant price (does not modify anything)."""
        try:
            gid = f"gid://shopify/ProductVariant/{int(variant_id)}"
        except Exception:
            return None
        q = """query($id: ID!) { productVariant(id: $id) { price } }"""
        try:
            data = self.graphql(q, {"id": gid})
            pv = data.get("productVariant") if isinstance(data, dict) else None
            if not pv:
                return None
            p = pv.get("price")
            if p is None:
                return None
            return float(p)
        except Exception:
            return None


    def find_customer_by_email(self, email: str) -> int | None:
        q = """query($q: String!) { customers(first: 1, query: $q) { nodes { id } } }"""
        data = self.graphql(q, {"q": f'email:"{email}"'})
        nodes = ((data.get("customers") or {}).get("nodes") or [])
        return gid_to_int(nodes[0]["id"]) if nodes else None

    def find_customer_by_phone(self, phone: str) -> int | None:
        q = """query($q: String!) { customers(first: 1, query: $q) { nodes { id } } }"""
        data = self.graphql(q, {"q": f"phone:{phone}"})
        nodes = ((data.get("customers") or {}).get("nodes") or [])
        return gid_to_int(nodes[0]["id"]) if nodes else None

    def create_or_update_customer(
        self,
        state: StateDB,
        *,
        odoo_partner_id: int | None,
        first_name: str,
        last_name: str,
        email: str | None,
        phone: str | None,
        raw_phone: str | None = None,
        default_address: dict | None,
    ) -> int | None:
        if odoo_partner_id:
            cached = state.get_customer_id(self.name, f"odoo:{odoo_partner_id}")
            if cached:
                return cached

        cid: int | None = None
        if email:
            cached = state.get_customer_id(self.name, f"email:{email}")
            if cached:
                return cached
            cid = self.find_customer_by_email(email)
        if not cid and phone:
            cached = state.get_customer_id(self.name, f"phone:{phone}")
            if cached:
                return cached
            cid = self.find_customer_by_phone(phone)

        if cid:
            if not DRY_RUN:
                payload = {"customer": {"id": cid}}
                # Add raw phone into customer note so it appears in Shopify customer timeline/details
                if raw_phone:
                    note_line = f"Imported phone (raw): {raw_phone}"
                    # Append note without overwriting existing note (Shopify will replace note field if set)
                    payload["customer"]["note"] = note_line
                if phone and raw_phone and phone != raw_phone:
                    payload["customer"]["note"] = (payload["customer"].get("note","") + f" | E164: {phone}").strip()
                if first_name:
                    payload["customer"]["first_name"] = first_name
                if last_name:
                    payload["customer"]["last_name"] = last_name
                if email:
                    payload["customer"]["email"] = email
                if phone:
                    payload["customer"]["phone"] = phone
                if default_address:
                    payload["customer"]["addresses"] = [default_address]
                try:
                    self._request("PUT", self.rest_base + f"customers/{cid}.json", payload)
                except RuntimeError as e:
                    msg = str(e)
                    if "addresses.province" in msg or ("province" in msg and "not valid" in msg):
                        # Retry once by dropping province fields
                        if payload.get("customer", {}).get("addresses"):
                            for a in payload["customer"]["addresses"]:
                                a.pop("province", None)
                        self._request("PUT", self.rest_base + f"customers/{cid}.json", payload)
                    else:
                        raise
            if odoo_partner_id:
                state.set_customer_id(self.name, f"odoo:{odoo_partner_id}", cid)
            if email:
                state.set_customer_id(self.name, f"email:{email}", cid)
            if phone:
                state.set_customer_id(self.name, f"phone:{phone}", cid)
            return cid

        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would create customer {first_name} {last_name} {email or ''} {phone or ''}")
            return None

        payload = {"customer": {}}
        if raw_phone:
            payload["customer"]["note"] = f"Imported phone (raw): {raw_phone}"
            if phone and phone != raw_phone:
                payload["customer"]["note"] += f" | E164: {phone}"
        if first_name:
            payload["customer"]["first_name"] = first_name
        if last_name:
            payload["customer"]["last_name"] = last_name
        if email:
            payload["customer"]["email"] = email
        if phone:
            payload["customer"]["phone"] = phone
        if default_address:
            payload["customer"]["addresses"] = [default_address]

        try:
            resp = self._request("POST", self.rest_base + "customers.json", payload)
        except RuntimeError as e:
            msg = str(e)
            if "addresses.province" in msg or ("province" in msg and "not valid" in msg):
                if payload.get("customer", {}).get("addresses"):
                    for a in payload["customer"]["addresses"]:
                        a.pop("province", None)
                resp = self._request("POST", self.rest_base + "customers.json", payload)
            else:
                raise
        cid = int(resp["customer"]["id"])
        if odoo_partner_id:
            state.set_customer_id(self.name, f"odoo:{odoo_partner_id}", cid)
        if email:
            state.set_customer_id(self.name, f"email:{email}", cid)
        if phone:
            state.set_customer_id(self.name, f"phone:{phone}", cid)
        return cid


    def _overwrite_generic_customer(self, cid: int) -> None:
        """Overwrite generic customer name/phone/address each run."""
        payload = {"customer": {
            "id": int(cid),
            "first_name": GENERIC_CUSTOMER_FIRST_NAME,
            "last_name": GENERIC_CUSTOMER_LAST_NAME,
            "email": normalize_email(GENERIC_CUSTOMER_EMAIL) or GENERIC_CUSTOMER_EMAIL,
            "phone": normalize_phone(GENERIC_CUSTOMER_PHONE),
            "addresses": [get_generic_default_address()],
        }}
        try:
            self._request("PUT", self.rest_base + f"customers/{cid}.json", payload)
        except RuntimeError as e:
            msg = str(e)
            if "addresses.province" in msg or ("province" in msg and "not valid" in msg):
                if payload.get("customer", {}).get("addresses"):
                    for a in payload["customer"]["addresses"]:
                        a.pop("province", None)
                        a.pop("province_code", None)
                self._request("PUT", self.rest_base + f"customers/{cid}.json", payload)
            else:
                raise

    def get_or_create_generic_customer(self, state: StateDB) -> int | None:
        email = normalize_email(GENERIC_CUSTOMER_EMAIL)
        if not email:
            raise RuntimeError("GENERIC_CUSTOMER_EMAIL invalid")
        cached = state.get_customer_id(self.name, f"email:{email}")
        if cached:
            if GENERIC_OVERWRITE_CUSTOMER_EACH_RUN and not DRY_RUN:
                self._overwrite_generic_customer(cached)
            return cached
        cid = self.find_customer_by_email(email)
        if cid:
            if GENERIC_OVERWRITE_CUSTOMER_EACH_RUN and not DRY_RUN:
                self._overwrite_generic_customer(cid)
            state.set_customer_id(self.name, f"email:{email}", cid)
            return cid
        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would create generic customer email={email}")
            return None
        payload = {"customer": {
            "first_name": GENERIC_CUSTOMER_FIRST_NAME,
            "last_name": GENERIC_CUSTOMER_LAST_NAME,
            "email": email,
            "phone": normalize_phone(GENERIC_CUSTOMER_PHONE),
            "addresses": [get_generic_default_address()],
        }}
        try:
            resp = self._request("POST", self.rest_base + "customers.json", payload)
        except RuntimeError as e:
            msg = str(e)
            if "addresses.province" in msg or ("province" in msg and "not valid" in msg):
                if payload.get("customer", {}).get("addresses"):
                    for a in payload["customer"]["addresses"]:
                        a.pop("province", None)
                resp = self._request("POST", self.rest_base + "customers.json", payload)
            else:
                raise
        cid = int(resp["customer"]["id"])
        state.set_customer_id(self.name, f"email:{email}", cid)
        return cid

    def create_product_with_variant(self, *, title: str, body_html: str | None, sku: str, price: str | None, barcode: str | None, image_b64: str | None) -> tuple[int, int]:
        product = {
            "title": title,
            "status": "active",
            "variants": [{"sku": sku, "inventory_management": None}],
        }
        if body_html:
            product["body_html"] = body_html
        if price is not None:
            product["variants"][0]["price"] = price
        if barcode:
            product["variants"][0]["barcode"] = barcode
        if image_b64:
            product["images"] = [{"attachment": image_b64}]
        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would create product sku={sku} title={title}")
            return 0, 0
        resp = self._request("POST", self.rest_base + "products.json", {"product": product})
        pid = int(resp["product"]["id"])
        vid = int(resp["product"]["variants"][0]["id"])
        return pid, vid

    
    def update_variant(self, variant_id: int, *, price: str | None = None, sku: str | None = None, barcode: str | None = None) -> None:
        """Update an existing variant (used to enforce order pricing via variant price, and to set SKU/barcode)."""
        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would update variant {variant_id} price={price} sku={sku} barcode={barcode}")
            return
        v = {"id": int(variant_id)}
        if price is not None:
            v["price"] = price
        if sku is not None:
            v["sku"] = sku
        if barcode is not None:
            v["barcode"] = barcode
        self._request("PUT", self.rest_base + f"variants/{variant_id}.json", {"variant": v})

    def update_product(self, product_id: int, *, title: str | None = None, body_html: str | None = None, image_b64: str | None = None) -> None:
        """Update an existing product title/body/image when SKU already exists."""
        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would update product {product_id} title={title!r}")
            return
        product = {"id": int(product_id)}
        if title is not None:
            product["title"] = title
        if body_html is not None:
            product["body_html"] = body_html
        if image_b64:
            product["images"] = [{"attachment": image_b64}]
        if len(product) == 1:
            return
        self._request("PUT", self.rest_base + f"products/{int(product_id)}.json", {"product": product})


    def cancel_order(self, order_id: int, *, restock: bool = False, refund: bool = False, reason: str | None = None) -> dict:
        """Cancel an order. Ensures no customer notification when DISABLE_SHOPIFY_NOTIFICATIONS=True."""
        payload = {
            "email": (False if DISABLE_SHOPIFY_NOTIFICATIONS else True),
            "restock": bool(restock),
            "refund": bool(refund),
        }
        if reason:
            payload["reason"] = reason
        return self._request("POST", self.rest_base + f"orders/{int(order_id)}/cancel.json", payload)

    def create_fulfillment(self, fulfillment_payload: dict) -> dict:
        """Create a fulfillment. Ensures no customer notification when DISABLE_SHOPIFY_NOTIFICATIONS=True.
        Caller should pass Shopify 'fulfillment' payload dict; we will force notify_customer.
        """
        if not isinstance(fulfillment_payload, dict):
            raise ValueError("fulfillment_payload must be dict")
        # Shopify expects {"fulfillment": {...}}
        if "fulfillment" in fulfillment_payload and isinstance(fulfillment_payload["fulfillment"], dict):
            fulfillment_payload["fulfillment"]["notify_customer"] = (False if DISABLE_SHOPIFY_NOTIFICATIONS else True)
        return self._request("POST", self.rest_base + "fulfillments.json", fulfillment_payload)

    def create_order(self, payload: dict) -> int | None:
        """Create an order (no customer notifications). Retries once by dropping province if Shopify rejects it."""
        if DISABLE_SHOPIFY_NOTIFICATIONS:
            try:
                o = payload.get("order", {})
                o["send_receipt"] = False
                o["send_fulfillment_receipt"] = False
            except Exception:
                pass

        if DRY_RUN:
            log("INFO", f"[DRY_RUN] Would create order lines={len(payload.get('order', {}).get('line_items', []))}")
            return None

        try:
            resp = self._request("POST", self.rest_base + "orders.json", payload)
        except RuntimeError as e:
            msg = str(e)
            if "province" in msg and "not valid" in msg:
                # Drop province fields and retry once
                try:
                    o = payload.get("order", {})
                    if isinstance(o.get("shipping_address"), dict):
                        o["shipping_address"].pop("province", None)
                    if isinstance(o.get("billing_address"), dict):
                        o["billing_address"].pop("province", None)
                except Exception:
                    pass
                resp = self._request("POST", self.rest_base + "orders.json", payload)
            else:
                raise
        created = resp["order"]
        desired_name = str((payload.get("order") or {}).get("name") or "").strip()
        actual_name = str(created.get("name") or "").strip()
        if desired_name and actual_name and actual_name != desired_name:
            log("WARN", f"{self.name}: Shopify created order name {actual_name!r}, expected Odoo order name {desired_name!r}")
        return int(created["id"])

def gid_to_int(gid: str) -> int:
    m = re.search(r"/(\d+)$", gid)
    if not m:
        raise ValueError(f"Cannot parse gid: {gid}")
    return int(m.group(1))


AU_NAMES = {"australia", "au"}
NZ_NAMES = {"new zealand", "nz", "newzealand"}

def is_au_nz(country_code: str | None, country_name: str | None) -> bool:
    cc = (country_code or "").strip().upper()
    cn = (country_name or "").strip().lower()
    if cc in {"AU", "NZ"}:
        return True
    if cn in AU_NAMES or cn in NZ_NAMES:
        return True
    if "australia" in cn:
        return True
    if "new zealand" in cn:
        return True
    return False


def split_name(full_name: str) -> tuple[str, str]:
    full = (full_name or "").strip()
    if not full:
        return "", ""
    parts = re.split(r"\s+", full)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def odoo_partner_to_shopify_address(partner: dict | None, country: dict | None, odoo: OdooClient = None) -> dict | None:
    if not partner:
        return None

    cc = ((country.get("code") if country else "") or "").strip().upper()

    addr = {
        "name": partner.get("name") or "",
        "address1": partner.get("street") or "",
        "address2": partner.get("street2") or "",
        "city": partner.get("city") or "",
        "zip": partner.get("zip") or "",
        "country": (country.get("name") if country else "") or "",
        "country_code": cc,
    }

    st = partner.get("state_id")

    state_name = None
    state_code = None

    # ✅ BEST: fetch from Odoo
    if odoo and st:
        state_name, state_code = get_state_from_odoo(odoo, st)

    # ✅ fallback if Odoo fetch fails
    if not state_name and isinstance(st, (list, tuple)) and len(st) >= 2:
        state_name = st[1]

    if state_name:
        addr["province"] = state_name

    # ✅ CRITICAL: Shopify needs province_code for AU/NZ
    if cc in {"AU", "NZ"}:
        if not state_code:
            # fallback mapping
            state_map = {
                "Victoria": "VIC",
                "New South Wales": "NSW",
                "Queensland": "QLD",
                "Western Australia": "WA",
                "South Australia": "SA",
                "Tasmania": "TAS",
                "Northern Territory": "NT",
                "Australian Capital Territory": "ACT",
            }
            state_code = state_map.get(state_name)

        if state_code:
            addr["province_code"] = state_code

    return _clean_dict(addr)


def html_from_odoo(product_pp: dict | None, product_tmpl: dict | None) -> str | None:
    if product_tmpl and product_tmpl.get("website_description"):
        return product_tmpl.get("website_description")
    if product_tmpl and product_tmpl.get("description_sale"):
        return product_tmpl.get("description_sale")
    if product_pp and product_pp.get("description_sale"):
        return product_pp.get("description_sale")
    if product_pp and product_pp.get("description"):
        return (product_pp.get("description") or "").replace("\n", "<br>")
    return None


def _prompt_yes_no(prompt: str, default: str | None = None) -> bool:
    d = (default or "").strip().lower()
    suffix = " [y/n]: "
    if d == "y":
        suffix = " [Y/n]: "
    elif d == "n":
        suffix = " [y/N]: "
    while True:
        v = input(prompt + suffix).strip().lower()
        if not v and d in {"y", "n"}:
            return d == "y"
        if v in {"y", "yes"}:
            return True
        if v in {"n", "no"}:
            return False
        print("Please enter y or n.")


def choose_odoo_source() -> dict:
    if not ODOO_SOURCES:
        raise RuntimeError("ODOO_SOURCES is empty. Add at least one Odoo source.")
    print("\nSelect source Odoo store:")
    for i, s in enumerate(ODOO_SOURCES, start=1):
        print(f"  {i}. {s.get('name', f'source-{i}')} ({s.get('url', '')} / db={s.get('db', '')})")
    while True:
        raw = input("Enter source number: ").strip()
        try:
            idx = int(raw)
        except Exception:
            idx = -1
        if 1 <= idx <= len(ODOO_SOURCES):
            return ODOO_SOURCES[idx - 1]
        print("Invalid selection.")


def read_order_numbers_from_input() -> list[str]:
    print("\nPaste Odoo order numbers (one per line).")
    print("Press Enter on an empty line to start import.")
    nums: list[str] = []
    while True:
        line = input().strip()
        if not line:
            break
        first = line.split(",")[0].strip()
        if not first:
            continue
        nums.append(first)
    seen = set()
    out: list[str] = []
    for n in nums:
        if n not in seen:
            out.append(n)
            seen.add(n)
    return out


class ProductRenameManager:
    def __init__(self, enabled: bool, mode: str = "none", global_name: str | None = None):
        self.enabled = bool(enabled)
        self.mode = mode
        self.global_name = (global_name or "").strip() or None
        self._per_order_cache: dict[tuple[str, str], str] = {}

    def resolve_title(self, *, order_name: str, sku: str | None, original_title: str) -> str:
        orig = (original_title or "").strip() or "Item"
        if not self.enabled:
            return orig
        if self.mode == "all":
            return self.global_name or orig

        key = (order_name, (sku or orig).strip().upper())
        if key in self._per_order_cache:
            return self._per_order_cache[key]

        print(
            f"\nRename product for order {order_name}\n"
            f"SKU: {sku or '-'}\n"
            f"Current title: {orig}"
        )
        renamed = input("New title (leave empty to keep current): ").strip()
        final_name = renamed or orig
        self._per_order_cache[key] = final_name
        return final_name

    def destination_sku(self, *, source_sku: str | None, source_title: str | None) -> str | None:
        src = sanitize_sku(source_sku)
        if not self.enabled:
            return src or None
        # Rename ON behavior:
        # - Keep display title as renamed text
        # - Set SKU to original source product title per line item
        base = sanitize_sku(source_title) or src
        if not base:
            return None
        return base


def ensure_product_variant_for_line(odoo: OdooClient, shop: ShopifyClient, state: StateDB, *, line: dict, order_name: str, rename_manager: ProductRenameManager, override_prices_for_order: bool, qty_raw: float, qty_int: int | None) -> tuple[int | None, dict | None, dict | None]:
    """Return (variant_id, custom_line_item, sku_audit).
    Shopify requires integer quantities. If qty_int is None (fractional), we fall back to a custom item with quantity=1.
    """
    if line.get("display_type"):
        return None, None, None

    prod = line.get("product_id")
    unit_price = float(line.get("price_unit") or 0.0)

    # Decide the price to apply on destination (non-AU/NZ only)
    if OVERRIDE_PRICE_MODE == "random":
        chosen = pick_random_price_for_sku(str(prod or line.get("name") or "custom"))
        target_price_str = price_str_no_decimals(chosen)
    else:
        target_price_str = f"{unit_price:.2f}"

    # Shopify requires integer quantities. If Odoo has fractional qty, fallback to custom item.
    if qty_int is None:
        title = rename_manager.resolve_title(
            order_name=order_name,
            sku=None,
            original_title=(line.get("name") or "Item").strip(),
        )
        # For fractional qty, Shopify cannot accept non-integer quantity. Use custom item.
        if override_prices_for_order:
            return None, {"title": title, "quantity": 1, "price": target_price_str}, None
        price_total = float(line.get("price_total") or 0.0)
        return None, {"title": title, "quantity": 1, "price": f"{price_total:.2f}"}, None

    if not prod or not isinstance(prod, (list, tuple)) or len(prod) < 1:
        cli_title = rename_manager.resolve_title(
            order_name=order_name,
            sku=None,
            original_title=(line.get("name") or "Item").strip(),
        )
        cli = {"title": cli_title, "quantity": int(qty_int or 1)}
        if override_prices_for_order:
            cli["price"] = target_price_str
        return None, cli, None

    pp = odoo.get_product_product(int(prod[0]))
    if not pp:
        cli_title = rename_manager.resolve_title(
            order_name=order_name,
            sku=None,
            original_title=(line.get("name") or "Item").strip(),
        )
        cli = {"title": cli_title, "quantity": int(qty_int or 1)}
        if override_prices_for_order:
            cli["price"] = target_price_str
        return None, cli, None

    sku_raw = (pp.get("default_code") or "").strip()
    sku = maybe_decode_asin_sku(sku_raw)

    # ✅ MUST BE INSIDE FUNCTION
    # Skip Odoo "delivery/shipping as product" lines
    # This prevents shipping charges represented as a product line from being imported into Shopify.
    _title_for_ignore_check = (
        line.get("name") if isinstance(line, dict) else None
    )
    _sku_for_ignore_check = sku or sku_raw or None

    if _should_ignore_odoo_line_item(_sku_for_ignore_check, _title_for_ignore_check):
        log(
            f"🛑 Ignoring Odoo delivery/shipping line item: "
            f"title={_title_for_ignore_check!r} sku={_sku_for_ignore_check!r}"
        )
        return None, None, None

    barcode = (pp.get("barcode") or "").strip() or None

    tmpl = None
    tmpl_ref = pp.get("product_tmpl_id")
    if isinstance(tmpl_ref, (list, tuple)) and tmpl_ref:
        tmpl = odoo.get_product_template(int(tmpl_ref[0]))

    source_title_for_sku = (pp.get("name") or (tmpl.get("name") if tmpl else "") or (line.get("name") or "") or sku or sku_raw or "Item").strip()
    title = source_title_for_sku
    if STRIP_WORDS:
        title = strip_words(title, STRIP_WORDS)
    title = rename_manager.resolve_title(
        order_name=order_name,
        sku=sku or sku_raw,
        original_title=title,
    )
    body_html = html_from_odoo(pp, tmpl)
    if body_html and STRIP_WORDS:
        body_html = strip_words(body_html, STRIP_WORDS)

    img = None
    if tmpl and tmpl.get("image_1920"):
        img = tmpl.get("image_1920")
    elif pp.get("image_1920"):
        img = pp.get("image_1920")
    img_b64 = None
    if img:
        img_b64 = img.decode("utf-8", "ignore") if isinstance(img, bytes) else str(img)

    source_sku = (sku or sku_raw or "").strip() or None
    destination_sku = rename_manager.destination_sku(source_sku=source_sku, source_title=source_title_for_sku)
    sku_audit = None
    if rename_manager.enabled and source_sku and destination_sku:
        sku_audit = {"source_sku": source_sku, "destination_sku": destination_sku}

    if not destination_sku:
        cli = {"title": title, "quantity": int(qty_int or 1)}
        if override_prices_for_order:
            cli["price"] = target_price_str
        return None, cli, sku_audit

    for candidate in [destination_sku, source_sku, sku_raw]:
        if not candidate:
            continue
        cached_vid, cached_pid = state.get_variant_for_sku(shop.name, candidate)
        if cached_vid:
            if UPDATE_EXISTING_SKU_PRODUCTS and cached_pid:
                shop.update_product(int(cached_pid), title=title, body_html=body_html, image_b64=img_b64)
            if override_prices_for_order:
                shop.update_variant(int(cached_vid), price=target_price_str)
            if candidate != destination_sku:
                shop.update_variant(int(cached_vid), sku=destination_sku)
            if barcode:
                shop.update_variant(int(cached_vid), barcode=barcode)
            state.set_variant_for_sku(shop.name, destination_sku, int(cached_vid), cached_pid)
            if source_sku and source_sku != destination_sku:
                state.set_variant_for_sku(shop.name, source_sku, int(cached_vid), cached_pid)
            return int(cached_vid), None, sku_audit

        vid, pid = shop.find_variant_by_sku(candidate)
        if vid:
            if UPDATE_EXISTING_SKU_PRODUCTS and pid:
                shop.update_product(int(pid), title=title, body_html=body_html, image_b64=img_b64)
            if candidate != destination_sku:
                shop.update_variant(int(vid), sku=destination_sku)
            if barcode:
                shop.update_variant(int(vid), barcode=barcode)
            if override_prices_for_order:
                shop.update_variant(int(vid), price=target_price_str)
            state.set_variant_for_sku(shop.name, destination_sku, vid, pid)
            if source_sku and source_sku != destination_sku:
                state.set_variant_for_sku(shop.name, source_sku, vid, pid)
            if sku_raw and sku_raw not in {source_sku, destination_sku}:
                state.set_variant_for_sku(shop.name, sku_raw, vid, pid)
            return vid, None, sku_audit

    create_price = target_price_str if override_prices_for_order else (f"{unit_price:.2f}" if USE_ODOO_PRICE_FOR_NEW_PRODUCTS_WHEN_OVERRIDES_OFF else None)
    pid, vid = shop.create_product_with_variant(title=title, body_html=body_html, sku=destination_sku, price=create_price, barcode=barcode, image_b64=img_b64)
    state.set_variant_for_sku(shop.name, destination_sku, vid, pid)
    if source_sku and source_sku != destination_sku:
        state.set_variant_for_sku(shop.name, source_sku, vid, pid)
    return vid, None, sku_audit




def _normalize_country_code(country: str) -> str:
    if not country:
        return ""
    c = str(country).strip().upper()
    aliases = {
        "AUSTRALIA": "AU", "AUS": "AU", "AU": "AU",
        "CANADA": "CA", "CA": "CA",
        "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US", "USA": "US", "US": "US",
        "UNITED KINGDOM": "GB", "UK": "GB", "GREAT BRITAIN": "GB", "GB": "GB",
        "INDIA": "IN", "IN": "IN",
        "NEW ZEALAND": "NZ", "NZ": "NZ",
    }
    return aliases.get(c, c)


def _order_text_for_ship_rules(order: dict, order_lines: list[dict]) -> str:
    parts: list[str] = []
    # Odoo order reference/name
    if SHIP_RULE_INCLUDE_NOTES:
        nm = (order.get("name") or "").strip()
        if nm:
            parts.append(nm)
        # common note-ish fields
        for k in ("client_order_ref", "note", "x_studio_note", "x_note", "x_customer_note"):
            try:
                v = (order.get(k) or "").strip()
                if v:
                    parts.append(v)
            except Exception:
                pass

    # Odoo order lines: product display name / name / SKU-like refs
    for ln in (order_lines or []):
        for k in ("name", "display_name", "product_name", "default_code", "product_default_code"):
            try:
                v = (ln.get(k) or "").strip()
                if v:
                    parts.append(v)
            except Exception:
                pass

    return " | ".join([p for p in parts if p])


def _pick_title_from_catalog(contains_substr: str) -> str | None:
    if not contains_substr:
        return None
    needle = str(contains_substr).strip().lower()
    for t in SHIPPING_METHOD_CATALOG:
        if needle in str(t).lower():
            return t
    return None


def choose_shipping_lines_for_order(*, ship_country_code: str | None, ship_country_name: str | None, order: dict, order_lines: list[dict]) -> list[dict] | None:
    cc = _normalize_country_code(ship_country_code or ship_country_name or "")
    text_blob = _order_text_for_ship_rules(order, order_lines).lower()

    for rule in SHIPPING_RULES:
        countries = rule.get("countries") or []
        norm_countries = {_normalize_country_code(x) for x in countries}
        if cc and norm_countries and cc not in norm_countries:
            continue

        include_any = [str(x).lower() for x in (rule.get("include_any") or []) if str(x).strip()]
        exclude_any = [str(x).lower() for x in (rule.get("exclude_any") or []) if str(x).strip()]

        if exclude_any and any(k in text_blob for k in exclude_any):
            continue
        if include_any and not any(k in text_blob for k in include_any):
            continue

        title = (rule.get("shipping_title") or "").strip()
        if not title:
            title = _pick_title_from_catalog(rule.get("shipping_title_contains") or "")
        if not title:
            continue

        code_ = (rule.get("shipping_code") or title).strip()
        price = rule.get("shipping_price", "0.00")

        return [{
            "title": title,
            "code": code_,
            "price": str(price),
            "source": "custom",
        }]

    return None



def build_order_payload(odoo: OdooClient, shop: ShopifyClient, state: StateDB, *, order: dict, order_lines: list[dict], billing_partner: dict | None, shipping_partner: dict | None, billing_country: dict | None, shipping_country: dict | None, currency: dict | None, order_tags: list[str] | None, rename_manager: ProductRenameManager) -> dict:
    order_name = order.get("name") or f"odoo:{order.get('id')}"

    cc = (shipping_country.get("code") if shipping_country else None) or (billing_country.get("code") if billing_country else None)
    cn = (shipping_country.get("name") if shipping_country else None) or (billing_country.get("name") if billing_country else None)
    au_nz = is_au_nz(cc, cn)
    override_prices_for_order = bool(OVERRIDE_PRICES) and (not au_nz)

    # AU/NZ duty cap rule (customs-friendly invoice value control)
    try:
        order_total_value = float(order.get("amount_total") or 0.0)
    except Exception:
        order_total_value = 0.0

    order_price_factor = 1.0
    if au_nz and order_total_value > float(AUNZ_DUTY_THRESHOLD):
        cap_value = float(AUNZ_MAX_ORDER_VALUE)
        if cap_value <= 0:
            cap_value = 899.99
        if order_total_value > 0:
            order_price_factor = min(1.0, cap_value / order_total_value)
        log(
            "INFO",
            f"{shop.name}: AU/NZ duty-cap active "
            f"(amount_total={order_total_value:.2f} > {float(AUNZ_DUTY_THRESHOLD):.2f}); "
            f"scaling item prices by factor={order_price_factor:.4f} to target <= {cap_value:.2f}"
        )


    if au_nz and OVERRIDE_PRICES:
        log("INFO", f"{shop.name}: AU/NZ order detected (country={cn or cc}); price override DISABLED for this order")

    customer_id = None
    if ASSIGN_TO_GENERIC_CUSTOMER:
        customer_id = shop.get_or_create_generic_customer(state)
    else:
        partner_main = None
        p_ref = order.get("partner_id")
        if isinstance(p_ref, (list, tuple)) and p_ref:
            partner_main = odoo.get_partner(int(p_ref[0]))

        email = normalize_email((billing_partner or {}).get("email") or (partner_main or {}).get("email"))
        raw_phone = ( (billing_partner or {}).get("phone") or (billing_partner or {}).get("mobile") or (partner_main or {}).get("phone") or (partner_main or {}).get("mobile") )
        raw_phone = (str(raw_phone).strip() if raw_phone is not None else None)
        phone = normalize_phone(raw_phone)
        full_name = ((billing_partner or {}).get("name") or (partner_main or {}).get("name") or "").strip()
        first_name, last_name = split_name(full_name)
        default_addr = odoo_partner_to_shopify_address(billing_partner or partner_main, billing_country, odoo)
        odoo_pid = int((partner_main or {}).get("id")) if partner_main and partner_main.get("id") else None

        customer_id = shop.create_or_update_customer(state, odoo_partner_id=odoo_pid, first_name=first_name, last_name=last_name, email=email, phone=phone, raw_phone=raw_phone, default_address=default_addr)

    line_items: list[dict] = []
    sku_audit_rows: list[dict] = []
    odoo_negative_discounts_total = 0.0  # sum negative Odoo lines as positive amount
    for line in order_lines:
        qty_raw = float(line.get("product_uom_qty") or 1.0)
        qty_int = qty_to_int_or_none(qty_raw)
        base_unit_price = (odoo_unit_price_no_discount(line) if IGNORE_ODOO_DISCOUNTS else odoo_effective_unit_price(line, qty_raw))
        unit_price = float(base_unit_price) * float(order_price_factor)

        # Detect discount/adjustment lines (negative totals). Shopify doesn't allow negative line items.
        try:
            _pt = float(line.get("price_total") or 0.0)
        except Exception:
            _pt = 0.0
        if unit_price < 0.0 or _pt < 0.0:
            # Discounts/adjustments from Odoo are either converted to Shopify order discounts (legacy behavior),
            # or skipped entirely when IGNORE_ODOO_DISCOUNTS=True (user requirement).
            if not IGNORE_ODOO_DISCOUNTS:
                odoo_negative_discounts_total += abs(_pt) if _pt else abs(unit_price)
            continue
        is_free_line = (unit_price == 0.0) or (_pt == 0.0)

        vid, custom, sku_audit = ensure_product_variant_for_line(
            odoo, shop, state,
            line=line,
            order_name=order_name,
            rename_manager=rename_manager,
            override_prices_for_order=override_prices_for_order,
            qty_raw=qty_raw,
            qty_int=qty_int,
        )
        if sku_audit:
            sku_audit_rows.append(sku_audit)

        if vid:
            li = {"variant_id": int(vid), "quantity": int(qty_int or 1)}
            # When price overrides are OFF (or disabled for AU/NZ), explicitly send a price so Shopify doesn't create 0.00 line items.
            # By default we use the current Shopify variant price; if it's missing/0 we fall back to Odoo base unit price.
            if (not override_prices_for_order) and USE_SHOPIFY_VARIANT_PRICE_ON_ORDER_WHEN_OVERRIDES_OFF:
                vp = shop.get_variant_price(int(vid))
                if vp is None or vp <= 0:
                    vp_use = float(unit_price)
                    log("WARN", f"{shop.name}: Variant {vid} has missing/zero price; using fallback price={vp_use:.2f}")
                else:
                    vp_use = float(vp) * float(order_price_factor)
                if (not is_free_line) and vp_use < float(MIN_LINE_PRICE):
                    vp_use = float(MIN_LINE_PRICE)
                li["price"] = f"{vp_use:.2f}"
            if override_prices_for_order and OVERRIDE_PRICE_MODE == "odoo":
                li["price"] = f"{unit_price:.2f}"
            if override_prices_for_order and OVERRIDE_PRICE_MODE == "random":
                key = str(line.get("product_id") or line.get("product_template_id") or vid)
                li["price"] = str(pick_random_price_for_sku(key))
            line_items.append(li)
        elif custom:
            try:
                custom_qty = int(round(float(custom.get("quantity", qty_int or 1))))
            except Exception:
                custom_qty = int(qty_int or 1)
            if custom_qty < 1:
                custom_qty = 1
            custom["quantity"] = custom_qty
            # Ensure custom item price is present and non-negative (Shopify requires price when no variant_id).
            # Base price comes from custom['price'] if set, otherwise from Odoo unit_price.
            try:
                _cp = float(custom.get("price") or unit_price)
            except Exception:
                _cp = unit_price
            # Apply AU/NZ high-value factor to custom-priced items (only when custom['price'] was provided).
            if (not override_prices_for_order) and (custom.get("price") is not None):
                try:
                    _cp = float(_cp) * float(order_price_factor)
                except Exception:
                    pass
            if is_free_line:
                _cp = 0.0
            if _cp < float(MIN_LINE_PRICE):
                _cp = float(MIN_LINE_PRICE)
            if override_prices_for_order and OVERRIDE_PRICE_MODE == "random":
                key = str(line.get("product_id") or line.get("product_template_id") or custom.get("title") or "custom")
                _cp = float(pick_random_price_for_sku(key))
                if _cp < float(MIN_LINE_PRICE):
                    _cp = float(MIN_LINE_PRICE)
                custom["price"] = str(int(round(_cp)))
            else:
                custom["price"] = money_str(max(float(MIN_LINE_PRICE), float(_cp)))
            line_items.append(custom)

    if ASSIGN_TO_GENERIC_CUSTOMER:
        billing_address = get_generic_default_address()
        shipping_address = get_generic_default_address()
    else:
        shipping_address = odoo_partner_to_shopify_address(shipping_partner, shipping_country, odoo)
        billing_address  = odoo_partner_to_shopify_address(billing_partner, billing_country, odoo)

    tags = [f"SRC_ODOO_DB:{ODOO_DB}", f"SRC_ODOO_ORDER:{order_name}"]
    note_attributes = []
    normalized_order_tags: list[str] = []
    seen_tag = set()
    for t in (order_tags or []):
        tt = str(t or "").strip()
        if not tt:
            continue
        k = tt.lower()
        if k in seen_tag:
            continue
        seen_tag.add(k)
        normalized_order_tags.append(tt)
    for idx, tag_name in enumerate(normalized_order_tags, start=1):
        tags.append(tag_name.replace(",", " / "))
        note_attributes.append({"name": f"Odoo Tag {idx}", "value": tag_name})

    unique_audit: list[dict] = []
    seen_audit = set()
    for row in sku_audit_rows:
        src = str(row.get("source_sku") or "").strip()
        dst = str(row.get("destination_sku") or "").strip()
        if not src or not dst:
            continue
        k = (src.lower(), dst.lower())
        if k in seen_audit:
            continue
        seen_audit.add(k)
        unique_audit.append({"source_sku": src, "destination_sku": dst})

    if not line_items:
        raise RuntimeError(f"No line items to create for order {order_name} (all lines may be display_type).")

    note_lines = [f"Imported from Odoo {ODOO_DB} order {order_name}"]
    if normalized_order_tags:
        note_lines.append("Odoo tags:")
        for tag_name in normalized_order_tags:
            note_lines.append(f"- {tag_name}")
    if unique_audit:
        note_lines.append("Original SKU mapping:")
        for i, row in enumerate(unique_audit, start=1):
            note_lines.append(f"- {row['destination_sku']} <= {row['source_sku']}")
            note_attributes.append({"name": f"Original SKU {i}", "value": row["source_sku"]})
            note_attributes.append({"name": f"Renamed SKU {i}", "value": row["destination_sku"]})

    payload = {"order": {
        # Shopify's visible order number is the order "name"; keep it aligned with Odoo sale.order.name.
        "name": order_name,
        "source_identifier": order_name,
        "line_items": line_items,
        "tags": ", ".join(tags),
        "note": "\n".join(note_lines),
        # Match the working Shopify->Shopify script behavior to avoid inventory errors on create
        "inventory_behaviour": "bypass",
        "send_receipt": (False if DISABLE_SHOPIFY_NOTIFICATIONS else True),
        "send_fulfillment_receipt": (False if DISABLE_SHOPIFY_NOTIFICATIONS else True),
    }}
    if note_attributes:
        payload["order"]["note_attributes"] = note_attributes
    if ASSIGN_TO_GENERIC_CUSTOMER:
        payload["order"]["email"] = normalize_email(GENERIC_CUSTOMER_EMAIL) or GENERIC_CUSTOMER_EMAIL

    if customer_id:
        payload["order"]["customer"] = {"id": int(customer_id)}
    if shipping_address:
        payload["order"]["shipping_address"] = shipping_address

    # ✅ MUST BE INSIDE FUNCTION
    # Apply shipping method rules (sets order.shipping_lines during creation)
    sl = choose_shipping_lines_for_order(
        ship_country_code=(shipping_country.get("code") if shipping_country else None),
        ship_country_name=(shipping_country.get("name") if shipping_country else None),
        order=order,
        order_lines=order_lines,
    )

    if sl:
        payload["order"]["shipping_lines"] = sl
        if billing_address:
            payload["order"]["billing_address"] = billing_address
        if SET_SHOPIFY_ORDER_CURRENCY and currency and currency.get("name"):
            payload["order"]["currency"] = currency.get("name")
        if MARK_AS_PAID:
            payload["order"]["financial_status"] = "paid"

        # Convert negative Odoo lines into a Shopify order-level discount
        try:
            if (not IGNORE_ODOO_DISCOUNTS) and odoo_negative_discounts_total > 0:
                subtotal_est = 0.0
                for it in line_items:
                    try:
                        q = int(it.get("quantity") or 0)
                        pr = float(it.get("price") or 0.0)
                    except Exception:
                        q, pr = 0, 0.0
                    subtotal_est += max(0.0, pr) * max(0, q)

                disc = min(float(odoo_negative_discounts_total), max(0.0, subtotal_est))
                if disc > 0:
                    payload["order"]["discount_codes"] = [{
                        "code": "ODOO_DISCOUNT",
                        "amount": money_str(disc),
                        "type": "fixed_amount",
                    }]
        except Exception:
            pass

    return payload


def sync_one_order_to_dest(odoo: OdooClient, shop: ShopifyClient, state: StateDB, order_number: str, rename_manager: ProductRenameManager):
    order = odoo.get_order_by_number(order_number)
    if not order:
        log("ERROR", f"Odoo order not found: {order_number}")
        return

    order_name = order.get("name") or order_number
    src_order_key = f"{ODOO_DB}:{order_name}"

    if state.is_order_synced(shop.name, src_order_key):
        log("INFO", f"{shop.name}: skip already-synced order {order_name}")
        return

    billing_partner = None
    shipping_partner = None

    inv_ref = order.get("partner_invoice_id")
    shp_ref = order.get("partner_shipping_id")
    main_ref = order.get("partner_id")

    if isinstance(inv_ref, (list, tuple)) and inv_ref:
        billing_partner = odoo.get_partner(int(inv_ref[0]))
    if isinstance(shp_ref, (list, tuple)) and shp_ref:
        shipping_partner = odoo.get_partner(int(shp_ref[0]))

    if not billing_partner and isinstance(main_ref, (list, tuple)) and main_ref:
        billing_partner = odoo.get_partner(int(main_ref[0]))
    if not shipping_partner and isinstance(main_ref, (list, tuple)) and main_ref:
        shipping_partner = odoo.get_partner(int(main_ref[0]))

    billing_country = None
    shipping_country = None
    if billing_partner and isinstance(billing_partner.get("country_id"), (list, tuple)) and billing_partner["country_id"]:
        billing_country = odoo.get_country(int(billing_partner["country_id"][0]))
    if shipping_partner and isinstance(shipping_partner.get("country_id"), (list, tuple)) and shipping_partner["country_id"]:
        shipping_country = odoo.get_country(int(shipping_partner["country_id"][0]))

    currency = None
    cur_ref = order.get("currency_id")
    if isinstance(cur_ref, (list, tuple)) and cur_ref:
        currency = odoo.get_currency(int(cur_ref[0]))

    order_tag_names: list[str] = []
    tag_ids = order.get("tag_ids") or []
    if isinstance(tag_ids, list) and tag_ids:
        order_tag_names = odoo.get_tag_names([int(x) for x in tag_ids])

    line_ids = order.get("order_line") or []
    order_lines = odoo.get_order_lines([int(x) for x in line_ids]) if isinstance(line_ids, list) else []
# 👇 ADD HERE (before payload creation)

    shipping_address = None
    billing_address = None

    try:
      shipping_address = odoo_partner_to_shopify_address(
          shipping_partner,
          shipping_country,
          odoo
      )

      billing_address = odoo_partner_to_shopify_address(
          billing_partner,
          billing_country,
          odoo
      )

    except Exception as e:
      log("ERROR", f"Address error: {e}")

# fallback
    if not shipping_address:
       log("WARN", "Missing shipping address → fallback used")
       shipping_address = {
          "first_name": "Customer",
          "address1": "Unknown",
          "city": "Unknown",
          "country": "Australia",
          "country_code": "AU"
       }

    log("DEBUG", f"Shipping Address Final: {shipping_address}")
    payload = build_order_payload(
        odoo, shop, state,
        order=order,
        order_lines=order_lines,
        billing_partner=billing_partner,
        shipping_partner=shipping_partner,
        billing_country=billing_country,
        shipping_country=shipping_country,
        currency=currency,
        order_tags=order_tag_names,
        rename_manager=rename_manager,
    )

    raw_phone = None
    phone = None
    if ASSIGN_TO_GENERIC_CUSTOMER:
        raw_phone = GENERIC_CUSTOMER_PHONE
        phone = normalize_phone(GENERIC_CUSTOMER_PHONE)
    else:
        partner_main = None
        if isinstance(main_ref, (list, tuple)) and main_ref:
            partner_main = odoo.get_partner(int(main_ref[0]))
        raw_phone = (
            (billing_partner or {}).get("phone")
            or (billing_partner or {}).get("mobile")
            or (shipping_partner or {}).get("phone")
            or (shipping_partner or {}).get("mobile")
            or (partner_main or {}).get("phone")
            or (partner_main or {}).get("mobile")
        )
        raw_phone = (str(raw_phone).strip() if raw_phone is not None else None)
        phone = normalize_phone(raw_phone)

    # Add customer phone details into order note/timeline (no customer notifications are sent)
    try:
        if raw_phone or phone:
            o = payload.get("order", {})
            note = (o.get("note") or "").strip()
            extra = []
            if raw_phone:
                extra.append(f"Customer phone (raw): {raw_phone}")
            if phone and phone != raw_phone:
                extra.append(f"Customer phone (E164): {phone}")
            if extra:
                o["note"] = (note + "\n" + "\n".join(extra)).strip() if note else "\n".join(extra)
                na = o.get("note_attributes") or []
                # Keep them visible in order details
                if raw_phone:
                    na.append({"name": "Customer phone (raw)", "value": raw_phone})
                if phone and phone != raw_phone:
                    na.append({"name": "Customer phone (E164)", "value": phone})
                o["note_attributes"] = na
    except Exception:
        pass

    if DRY_RUN:
        log("INFO", f"[DRY_RUN] {shop.name}: would create order {order_name}")
        state.mark_order_synced(shop.name, src_order_key, None)
        return

    dest_order_id = shop.create_order(payload)
    state.mark_order_synced(shop.name, src_order_key, str(dest_order_id) if dest_order_id else None)
    log("INFO", f"{shop.name}: created order {order_name} -> {dest_order_id}")


def main():
    global ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD

    rename_enabled = _prompt_yes_no("Do you want to rename imported products?", default="n")
    if rename_enabled:
        rename_all = _prompt_yes_no("Use one same name for all imported products?", default="y")
        if rename_all:
            global_name = input("Enter product name to use for all imported products: ").strip()
            rename_manager = ProductRenameManager(enabled=True, mode="all", global_name=global_name)
        else:
            rename_manager = ProductRenameManager(enabled=True, mode="per_order")
    else:
        rename_manager = ProductRenameManager(enabled=False, mode="none")

    order_numbers = read_order_numbers_from_input()
    if not order_numbers:
        log("ERROR", "No order numbers were provided.")
        sys.exit(1)
    log("INFO", f"Orders to sync: {len(order_numbers)}")

    src = choose_odoo_source()
    ODOO_URL = str(src.get("url") or "").strip()
    ODOO_DB = str(src.get("db") or "").strip()
    ODOO_USERNAME = str(src.get("username") or "").strip()
    ODOO_PASSWORD = str(src.get("password") or "").strip()

    log("INFO", f"STATE_DB: {STATE_DB}")
    log("INFO", f"Selected Odoo source: {src.get('name')} ({ODOO_URL} / db={ODOO_DB})")
    log("INFO", f"ASSIGN_TO_GENERIC_CUSTOMER={ASSIGN_TO_GENERIC_CUSTOMER} | OVERRIDE_PRICES={OVERRIDE_PRICES} | DRY_RUN={DRY_RUN} | UPDATE_EXISTING_SKU_PRODUCTS={UPDATE_EXISTING_SKU_PRODUCTS}")

    state = StateDB(STATE_DB)

    odoo = OdooClient(ODOO_URL, ODOO_DB, ODOO_USERNAME, ODOO_PASSWORD)
    odoo.connect()

    shops = []
    for d in DESTS:
        access_token = get_shopify_access_token(d, state)
        shops.append(ShopifyClient(d["name"], d["shop"], access_token, d.get("api_version") or "2025-10"))

    for order_number in order_numbers:
        log("INFO", f"== Source Odoo order: {order_number} ==")
        for shop in shops:
            try:
                sync_one_order_to_dest(odoo, shop, state, order_number, rename_manager)
            except Exception as e:
                log("ERROR", f"{shop.name}: failed order {order_number}: {e}")



    log("INFO", "Done.")
    


if __name__ == "__main__":
    main()
