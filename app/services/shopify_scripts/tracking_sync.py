#!/usr/bin/env python3
""" 
Odoo Tracking Sync (SMART v3.3) — STRICT TAG_DIRECT
===================================================

SOURCE: Shopify stores  --->  DESTINATION: Odoo DBs

What this script does
---------------------
It reads fulfillments from Shopify SOURCE stores and writes tracking numbers/carrier
into Odoo stock pickings for the mapped sale order.

STRICT MATCHING (TAG_DIRECT)
----------------------------
Each Shopify SOURCE order MUST contain BOTH tags (case-insensitive):
  - SRC_ODOO_DB:<odoo_db>
  - SRC_ODOO_ORDER:<odoo_order_name>

Example:
  SRC_ODOO_DB:supplee
  SRC_ODOO_ORDER:S00945

This script **only** matches using these tags.
There are **no fallback matching methods**.

Tag validation (exceptions)
---------------------------
If any of these occur, the order is marked EXCEPTION and written to CSV:
  - Multiple SRC_ODOO_DB tags
  - Multiple SRC_ODOO_ORDER tags
  - SRC_ODOO_ORDER is invalid (contains spaces / empty / illegal chars)

Dry-run mode
------------
--dry-run makes the script REPORT-ONLY:
  - Does NOT write anything to Odoo
  - Does NOT write anything to SQLite state DB
  - Still generates the full CSV report with what *would* be updated

Date filtering
--------------
Only Shopify fulfillments whose createdAt timestamp is within [from-date, to-date] are considered.
Orders are fetched by updated_at range, but fulfillments are filtered by createdAt.

Idempotency (SQLite)
--------------------
In non-dry-run, each (src_shop, src_order_id, src_fulfillment_id) is synced once.
In dry-run, idempotency is not enforced (report-only).

Usage
-----
python3 odoo_tracking_sync_smart_v3_1_strict_tag_direct_v1_0.py --from-date "2025-12-25" --to-date "2026-01-19" --dry-run
python3 odoo_tracking_sync_smart_v3_1_strict_tag_direct_v1_0.py --from-date "25 December 2025" --to-date "19 January 2026"
"""

import time
import sqlite3
import argparse
import threading
import webbrowser
import re
import os
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs
import hashlib
import requests
import xmlrpc.client

PROGRESS_CALLBACK = None
CANCEL_CALLBACK = None
CSV_LOCK = threading.Lock()


class TrackingSyncCancelled(BaseException):
    pass


class OdooDestinationUnavailable(Exception):
    pass


def check_cancelled():
    callback = CANCEL_CALLBACK
    if callback and callback():
        raise TrackingSyncCancelled("Tracking sync cancelled by user.")


def emit_progress(**payload):
    check_cancelled()
    callback = PROGRESS_CALLBACK
    if not callback:
        return
    try:
        callback(payload)
    except Exception:
        pass


# ----------------------------
# USER CONFIG
# ----------------------------

ODOO_DESTS = [
    {
        "name": "Nutricity USA",
        "url": "https://nutricityusa.com",
        "db": "supplee",
        "username": "admin@nutricityusa.com",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "Boostgo",
        "url": "https://boostgo.com.au",
        "db": "boostgo",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "Espot",
        "url": "https://espot.com.au",
        "db": "espot",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "Suppcity",
        "url": "https://suppcity.com.au",
        "db": "suppcity",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "nutrihub",
        "url": "https://nutrihub.ca",
        "db": "nutrihub",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "vitagen",
        "url": "https://vitagen.com.au",
        "db": "vitagen",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "vitashop",
        "url": "https://vitashop.co.nz",
        "db": "vitashop",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "secretgreen",
        "url": "https://secretgreen.com.au",
        "db": "secretgreen",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },
    {
        "name": "wildkart",
        "url": "https://wildkart.com.au",
        "db": "wildkart",
        "username": "admin",
        "password": os.getenv("ODOO_SCRIPT_PASSWORD", ""),
    },

]

SHOPIFY_SOURCES = [
    {
        "name": "Fulfilment Center - D2C-worldwide",
        "shop": "7mvpxa-1b",
        "auth_mode": "auto",
        "client_id": "195db1cdb832e35000d5dd2080b8fee3",
        "client_secret": os.getenv("SHOPIFY_TRACKING_CLIENT_SECRET", ""),
        "scopes": ["read_orders"],
        "redirect_uri": "http://localhost:8080/callback",
        "api_version": "2025-01",
    },
]

STATE_DB = "odoo_tracking_sync_state.sqlite3"


# ----------------------------
# CSV reporting
# ----------------------------

REPORT_FIELDS = [
    "ts_utc",

    # source
    "src_store_name", "src_shop",
    "src_order_name", "src_order_id", "src_order_updatedAt",
    "src_tags_all",
    "src_fulfillment_id", "src_fulfillment_createdAt",
    "src_tracking_numbers", "src_tracking_companies", "src_tracking_urls",

    # tag validation
    "match_method",
    "tag_validation_status", "tag_exception_code", "tag_exception_detail",
    "src_odoo_db_tags_found", "src_odoo_order_tags_found",

    # destination
    "odoo_dest_name", "odoo_url", "odoo_db", "odoo_order",

    # pickings snapshot
    "odoo_sale_order_id",
    "odoo_sale_state",
    "odoo_invoice_status",
    "odoo_paid_status",
    "picking_ids_all",
    "picking_states",
    "picking_existing_tracking",
    "picking_update_needed_ids",

    # outcome
    "action", "result", "message",
    "updated_picking_ids",

    # validation
    "validated_picking_ids",
    "validation_result",
    "validation_message",
]


def ensure_csv_header(path: str):
    if not path:
        return
    with CSV_LOCK:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
            w.writeheader()


def append_csv_row(path: str, row: dict):
    if not path:
        return
    with CSV_LOCK:
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
            w.writerow({k: row.get(k, "") for k in REPORT_FIELDS})




# ----------------------------
# Helpers
# ----------------------------

def tracking_matches(existing: str, desired: str) -> bool:
    # Compare existing and desired tracking strings in a forgiving way.
    # Returns True when every desired tracking code is already present in existing.
    desired_parts = tracking_parts(desired)
    if not desired_parts:
        return False
    existing_parts = tracking_parts(existing)
    if not existing_parts:
        return False
    return all(part in existing_parts for part in desired_parts)


def tracking_parts(value: str) -> list[str]:
    parts: list[str] = []
    for item in re.split(r"[,;\n]+", value or ""):
        normalized = re.sub(r"\s+", "", item.strip().lower())
        if normalized and normalized not in parts:
            parts.append(normalized)
    return parts


def merge_tracking_text(existing: str, desired: str) -> str:
    labels: dict[str, str] = {}
    merged: list[str] = []
    for item in re.split(r"[,;\n]+", existing or ""):
        text = item.strip()
        key = re.sub(r"\s+", "", text.lower())
        if key and key not in labels:
            labels[key] = text
            merged.append(key)
    for item in re.split(r"[,;\n]+", desired or ""):
        text = item.strip()
        key = re.sub(r"\s+", "", text.lower())
        if key and key not in labels:
            labels[key] = text
            merged.append(key)
    return ", ".join(labels[key] for key in merged)


def has_tracking_text(value: str) -> bool:
    return bool(tracking_parts(value))
# ----------------------------
# SQLite state (read-only support)
# ----------------------------


class StateDB:
    def __init__(self, path: str, read_only: bool = False):
        self.path = path
        self.read_only = read_only
        self.conn = None
        self.lock = threading.Lock()
        self._open()
        if not self.read_only:
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self._init_schema()

    def _open(self):
        if self.read_only:
            # If DB doesn't exist, fall back to memory to avoid creating files.
            if not os.path.exists(self.path):
                self.conn = sqlite3.connect(":memory:", check_same_thread=False)
                return
            self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
            return
        self.conn = sqlite3.connect(self.path, check_same_thread=False)

    def _init_schema(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS token_cache (
                shop TEXT PRIMARY KEY,
                access_token TEXT,
                expires_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tracking_sync_log (
                src_shop TEXT,
                src_order_id TEXT,
                src_fulfillment_id TEXT,
                odoo_db TEXT,
                odoo_order TEXT,
                synced_at INTEGER,
                PRIMARY KEY(src_shop, src_order_id, src_fulfillment_id)
            )
            """
        )
        self.conn.commit()

    # token cache
    def get_token(self, shop: str):
        try:
            with self.lock:
                cur = self.conn.cursor()
                cur.execute("SELECT access_token, expires_at FROM token_cache WHERE shop=?", (shop,))
                row = cur.fetchone()
            if not row:
                return None
            return {"access_token": row[0], "expires_at": row[1]}
        except Exception:
            return None

    def set_token(self, shop: str, token: str, expires_at: int):
        if self.read_only:
            return
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO token_cache(shop, access_token, expires_at, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(shop) DO UPDATE SET
                  access_token=excluded.access_token,
                  expires_at=excluded.expires_at,
                  updated_at=excluded.updated_at
                """,
                (shop, token, expires_at, int(time.time())),
            )
            self.conn.commit()

    # idempotency
    def already_synced(self, src_shop: str, src_order_id: str, src_fulfillment_id: str) -> bool:
        try:
            with self.lock:
                cur = self.conn.cursor()
                cur.execute(
                    """
                    SELECT 1 FROM tracking_sync_log
                    WHERE src_shop=? AND src_order_id=? AND src_fulfillment_id=?
                    """,
                    (src_shop, src_order_id, src_fulfillment_id),
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def log_sync(self, src_shop: str, src_order_id: str, src_fulfillment_id: str, odoo_db: str, odoo_order: str):
        if self.read_only:
            return
        with self.lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO tracking_sync_log
                (src_shop, src_order_id, src_fulfillment_id, odoo_db, odoo_order, synced_at)
                VALUES (?,?,?,?,?,?)
                """,
                (src_shop, src_order_id, src_fulfillment_id, odoo_db, odoo_order, int(time.time())),
            )
            self.conn.commit()


# ----------------------------
# OAuth Callback Server
# ----------------------------


class OAuthCallbackServer:
    def __init__(self, host="localhost", port=8080):
        self.host = host
        self.port = port
        self._code = None
        self._state = None
        self._error = None
        self._server = None
        self._thread = None

    def start(self, expected_state: str):
        self._state = expected_state
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)

                if "error" in qs:
                    server_ref._error = qs.get("error", ["unknown_error"])[0]
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OAuth failed. You can close this window.")
                    return

                code = qs.get("code", [None])[0]
                state = qs.get("state", [None])[0]
                if not code or not state or state != server_ref._state:
                    server_ref._error = "invalid_state_or_code"
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Invalid callback. You can close this window.")
                    return

                server_ref._code = code
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OAuth success. You can close this window.")

            def log_message(self, format, *args):
                return

        self._server = HTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()

    def wait_for_code(self, timeout=300):
        start = time.time()
        while time.time() - start < timeout:
            if self._code or self._error:
                break
            time.sleep(0.2)
        return self._code, self._error


# ----------------------------
# Shopify Auth
# ----------------------------


def get_access_token_client_credentials(shop: str, client_id: str, client_secret: str) -> dict:
    url = f"https://{shop}.myshopify.com/admin/oauth/access_token"
    payload = {"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"}
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"client_credentials failed for {shop}: {r.status_code} {r.text}")
    return r.json()


def oauth_install_and_get_token(shop: str, client_id: str, client_secret: str, scopes: list, redirect_uri: str) -> dict:
    state = hashlib.sha256(f"{shop}:{time.time()}".encode()).hexdigest()[:24]
    scope_str = ",".join(scopes)
    auth_url = (
        f"https://{shop}.myshopify.com/admin/oauth/authorize?" + urlencode(
            {"client_id": client_id, "scope": scope_str, "redirect_uri": redirect_uri, "state": state}
        )
    )

    print(f"\n[OAUTH] Browser approval required for {shop}")
    print(auth_url)

    server = OAuthCallbackServer(host="localhost", port=8080)
    server.start(expected_state=state)
    try:
        webbrowser.open(auth_url)
        code, err = server.wait_for_code(timeout=300)
        if err:
            raise RuntimeError(f"OAuth error for {shop}: {err}")
        if not code:
            raise RuntimeError(f"OAuth timeout for {shop}. No code received.")

        token_url = f"https://{shop}.myshopify.com/admin/oauth/access_token"
        r = requests.post(
            token_url,
            json={"client_id": client_id, "client_secret": client_secret, "code": code},
            timeout=30,
        )
        if r.status_code != 200:
            raise RuntimeError(f"OAuth token exchange failed for {shop}: {r.status_code} {r.text}")
        return r.json()
    finally:
        server.stop()


def ensure_shopify_access_token(db: StateDB, store: dict) -> str:
    """Auto: client_credentials first, fallback to browser OAuth install."""
    shop = store["shop"]
    if store.get("auth_mode") == "token":
        tok = store.get("access_token")
        if not tok:
            raise ValueError(f"{shop} auth_mode=token but access_token missing")
        return tok

    cached = db.get_token(shop)
    if cached and cached.get("expires_at", 0) > int(time.time()) + 60:
        return cached["access_token"]

    cid = store.get("client_id")
    csec = store.get("client_secret")
    scopes = store.get("scopes", ["read_orders"])
    redirect_uri = store.get("redirect_uri", "http://localhost:8080/callback")
    if not cid or not csec:
        raise ValueError(f"{shop} missing client_id/client_secret")

    try:
        tok = get_access_token_client_credentials(shop, cid, csec)
        access_token = tok["access_token"]
        expires_at = int(time.time()) + int(tok.get("expires_in", 23 * 3600))
        db.set_token(shop, access_token, expires_at)
        return access_token
    except Exception as e:
        print(f"[WARN] client_credentials failed for {shop}: {e}")

    tok = oauth_install_and_get_token(shop, cid, csec, scopes, redirect_uri)
    access_token = tok["access_token"]
    expires_at = int(time.time()) + (365 * 24 * 3600)
    db.set_token(shop, access_token, expires_at)
    return access_token


# ----------------------------
# Shopify GraphQL
# ----------------------------


def gql(shop: str, token: str, api_version: str, query: str, variables=None) -> dict:
    url = f"https://{shop}.myshopify.com/admin/api/{api_version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    r = requests.post(url, headers=headers, json={"query": query, "variables": variables or {}}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"GraphQL {shop} failed: {r.status_code} {r.text}")
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"GraphQL errors {shop}: {data['errors']}")
    return data["data"]


# ----------------------------
# Matching / tag validation
# ----------------------------


RE_DB = re.compile(r"^SRC_ODOO_DB:(.+)$", re.IGNORECASE)
RE_ORDER = re.compile(r"^SRC_ODOO_ORDER:(.+)$", re.IGNORECASE)

# stricter than before: no spaces, allow letters/digits/_- only
RE_VALID_ODOO_ORDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,64}$")


def validate_src_tags_for_odoo(tags: list) -> dict:
    """Strict TAG_DIRECT validation.

    Returns dict with:
      status: OK / MISSING / EXCEPTION
      odoo_db, odoo_order
      exception_code/detail
      db_tags_found/order_tags_found
    """
    db_tags = []
    order_tags = []

    for t in tags or []:
        s = (t or "").strip()
        m = RE_DB.match(s)
        if m:
            val = m.group(1).strip()
            if val:
                db_tags.append(val)
            continue
        m2 = RE_ORDER.match(s)
        if m2:
            val = m2.group(1).strip()
            if val:
                order_tags.append(val)
            continue

    # Missing
    if not db_tags or not order_tags:
        return {
            "status": "MISSING",
            "odoo_db": db_tags[0] if db_tags else "",
            "odoo_order": order_tags[0] if order_tags else "",
            "exception_code": "",
            "exception_detail": "",
            "db_tags_found": db_tags,
            "order_tags_found": order_tags,
        }

    # Multiple tags
    if len(db_tags) > 1:
        return {
            "status": "EXCEPTION",
            "odoo_db": "",
            "odoo_order": "",
            "exception_code": "MULTIPLE_SRC_ODOO_DB",
            "exception_detail": ";".join(db_tags),
            "db_tags_found": db_tags,
            "order_tags_found": order_tags,
        }
    if len(order_tags) > 1:
        return {
            "status": "EXCEPTION",
            "odoo_db": "",
            "odoo_order": "",
            "exception_code": "MULTIPLE_SRC_ODOO_ORDER",
            "exception_detail": ";".join(order_tags),
            "db_tags_found": db_tags,
            "order_tags_found": order_tags,
        }

    odoo_db = db_tags[0]
    odoo_order = order_tags[0]

    # Invalid order tag
    if (not odoo_order) or (" " in odoo_order) or (not RE_VALID_ODOO_ORDER.match(odoo_order)):
        return {
            "status": "EXCEPTION",
            "odoo_db": "",
            "odoo_order": "",
            "exception_code": "INVALID_SRC_ODOO_ORDER",
            "exception_detail": odoo_order,
            "db_tags_found": db_tags,
            "order_tags_found": order_tags,
        }

    return {
        "status": "OK",
        "odoo_db": odoo_db,
        "odoo_order": odoo_order,
        "exception_code": "",
        "exception_detail": "",
        "db_tags_found": db_tags,
        "order_tags_found": order_tags,
    }


# ----------------------------
# Helpers - date parsing
# ----------------------------


def parse_date_to_ymd(date_str: str) -> str:
    date_str = date_str.strip()
    fmts = ["%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d.%m.%Y"]
    for f in fmts:
        try:
            dt = datetime.strptime(date_str, f)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"Could not parse date: {date_str}")


def parse_window_datetime(value: str, end_of_day: bool = False) -> datetime:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Missing tracking sync window value.")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        suffix = "T23:59:59+00:00" if end_of_day else "T00:00:00+00:00"
        return datetime.fromisoformat(cleaned + suffix)
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        ymd = parse_date_to_ymd(cleaned)
        suffix = "T23:59:59+00:00" if end_of_day else "T00:00:00+00:00"
        return datetime.fromisoformat(ymd + suffix)


def shopify_query_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_dt(iso_str: str):
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def iso_in_range(iso_str: str, start_dt: datetime, end_dt: datetime) -> bool:
    dt = parse_iso_dt(iso_str)
    if not dt:
        return False
    return start_dt <= dt <= end_dt


# ----------------------------
# Fetch SOURCE orders (GraphQL compatible)
# ----------------------------


def fetch_source_orders(src_shop: str, token: str, api_version: str, from_query: str, to_query: str, max_pages=250):
    cursor = None
    q = f"updated_at:>={from_query} updated_at:<={to_query} (fulfillment_status:fulfilled OR fulfillment_status:shipped)"
    query = """
    query($first:Int!, $after:String, $q:String!) {
      orders(first:$first, after:$after, query:$q, reverse:true) {
        pageInfo { hasNextPage }
        edges {
          cursor
          node {
            id
            name
            updatedAt
            tags
            fulfillments(first: 20) {
              id
              status
              createdAt
              trackingInfo {
                number
                company
                url
              }
            }
          }
        }
      }
    }
    """
    pages = 0
    while pages < max_pages:
        pages += 1
        data = gql(src_shop, token, api_version, query, {"first": 100, "after": cursor, "q": q})
        edges = data["orders"]["edges"]
        for e in edges:
            yield e["node"]
        if not data["orders"]["pageInfo"]["hasNextPage"]:
            break
        cursor = edges[-1]["cursor"] if edges else None
        if not cursor:
            break


# ----------------------------
# Odoo XML-RPC
# ----------------------------


class OdooClient:
    def __init__(self, url: str, db: str, username: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.uid = None
        self.common = None
        self.models = None

    def connect(self):
        self.common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        self.uid = self.common.authenticate(self.db, self.username, self.password, {})
        if not self.uid:
            raise RuntimeError(f"Odoo auth failed db={self.db} user={self.username}")

    def execute(self, model, method, args, kwargs=None):
        last_error = None
        for attempt in range(1, 5):
            try:
                return self.models.execute_kw(self.db, self.uid, self.password, model, method, args, kwargs or {})
            except xmlrpc.client.ProtocolError as exc:
                last_error = exc
                if exc.errcode not in {429, 500, 502, 503, 504} or attempt >= 4:
                    raise
            except (OSError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                if attempt >= 4:
                    raise
            time.sleep(0.5 * attempt)
        if last_error:
            raise last_error
        raise RuntimeError("Odoo XML-RPC call failed.")

    def find_sale_order_id_by_name(self, order_name: str):
        # NOTE: We intentionally search by exact name here. Eligibility checks happen separately.
        ids = self.execute("sale.order", "search", [[("name", "=", order_name)]], {"limit": 1})
        return ids[0] if ids else None

    def read_sale_order_meta(self, sale_id: int):
        """Return minimal sale.order fields needed to decide if an order is eligible for tracking sync."""
        rows = self.execute(
            "sale.order",
            "read",
            [[sale_id]],
            {"fields": ["id", "name", "state", "invoice_status", "invoice_ids"]},
        )
        return rows[0] if rows else {}

    def _has_paid_invoice_for_sale_order(self, sale_name: str, invoice_ids: list):
        """Check if there is at least one posted customer invoice that is fully paid."""
        inv_ids = invoice_ids or []

        # If invoice_ids is not available/empty, fall back to invoice_origin = sale order name.
        if not inv_ids and sale_name:
            try:
                inv_ids = self.execute(
                    "account.move",
                    "search",
                    [[("invoice_origin", "=", sale_name), ("move_type", "=", "out_invoice"), ("state", "=", "posted")]],
                    {},
                )
            except Exception:
                inv_ids = []

        if not inv_ids:
            return False

        inv_rows = self.execute(
            "account.move",
            "read",
            [inv_ids],
            {"fields": ["id", "state", "move_type", "payment_state", "amount_residual"]},
        )

        for inv in inv_rows or []:
            if (inv.get("state") == "posted") and (inv.get("move_type") == "out_invoice"):
                # Most reliable: payment_state == paid.
                if (inv.get("payment_state") or "").lower() == "paid":
                    return True
                # Fallback check: residual == 0 (some customizations)
                try:
                    if float(inv.get("amount_residual") or 0.0) == 0.0:
                        return True
                except Exception:
                    pass

        return False

    def sale_order_is_eligible(self, sale_id: int):
        """
        Eligibility rule:
        - Must be confirmed (state in sale/done)

        Tracking is a fulfilment signal coming from Shopify. Do not block it
        just because the Odoo invoice is not paid yet; otherwise already
        dispatched Shopify orders can be left without tracking in Odoo.

        Returns: (eligible: bool, meta: dict, reason_code: str)
        """
        meta = self.read_sale_order_meta(sale_id)
        state = (meta.get("state") or "").lower()

        if state not in ("sale", "done"):
            return False, meta, "ODOO_ORDER_NOT_CONFIRMED"

        return True, meta, ""

    def get_pickings_of_sale_order(self, sale_id: int):
        rows = self.execute("sale.order", "read", [[sale_id]], {"fields": ["picking_ids"]})
        if not rows:
            return []
        return rows[0].get("picking_ids") or []

    def read_pickings(self, picking_ids: list):
        if not picking_ids:
            return []
        return self.execute(
            "stock.picking",
            "read",
            [picking_ids],
            {"fields": ["id", "name", "state", "carrier_tracking_ref", "carrier_id", "note"]},
        )

    def find_carrier_id_by_name(self, carrier_name: str):
        if not carrier_name:
            return None
        ids = self.execute("delivery.carrier", "search", [[("name", "ilike", carrier_name)]], {"limit": 1})
        return ids[0] if ids else None

    def write_picking_tracking(self, picking_id: int, tracking_text: str, carrier_id: int = None, append_note: str = ""):
        vals = {}
        if tracking_text:
            vals["carrier_tracking_ref"] = tracking_text
        if carrier_id:
            vals["carrier_id"] = carrier_id
        if append_note:
            vals["note"] = append_note
        if not vals:
            return False
        return bool(self.execute("stock.picking", "write", [[picking_id], vals]))




    def read_picking_state(self, picking_id: int) -> str:
        rows = self.execute(
            "stock.picking",
            "read",
            [[picking_id]],
            {"fields": ["id", "state", "name", "carrier_tracking_ref"]},
        )
        if not rows:
            return ""
        return (rows[0].get("state") or "")

    def validate_picking_safely(self, picking_id: int):
        # Validate (DONE) a stock.picking in Odoo.
        # Returns: (ok: bool, msg: str)
        try:
            st0 = self.read_picking_state(picking_id)
            if st0 in ("done", "cancel"):
                return True, f"Already {st0}" if st0 else "Already done/cancel"

            # Try reserving stock first (safe even if already assigned)
            try:
                self.execute("stock.picking", "action_assign", [[picking_id]])
            except Exception:
                pass

            res = self.execute("stock.picking", "button_validate", [[picking_id]])

            # button_validate may return True/None OR an action dict for a wizard
            if isinstance(res, dict):
                res_model = res.get("res_model")
                res_id = res.get("res_id")

                # Some Odoo versions return a window action without res_id; try to infer via context
                if not res_id and res_model in ("stock.immediate.transfer", "stock.backorder.confirmation"):
                    ctx = res.get("context") or {}
                    # Try to create wizard record
                    try:
                        if res_model == "stock.immediate.transfer":
                            res_id = self.execute(
                                "stock.immediate.transfer",
                                "create",
                                [{"pick_ids": [(6, 0, [picking_id])]}],
                                ctx,
                            )
                        elif res_model == "stock.backorder.confirmation":
                            res_id = self.execute(
                                "stock.backorder.confirmation",
                                "create",
                                [{"pick_ids": [(6, 0, [picking_id])]}],
                                ctx,
                            )
                    except Exception:
                        res_id = None

                if res_model and res_id:
                    # Immediate transfer wizard
                    if res_model == "stock.immediate.transfer":
                        self.execute("stock.immediate.transfer", "process", [[res_id]])
                    # Backorder confirmation wizard
                    elif res_model == "stock.backorder.confirmation":
                        try:
                            self.execute("stock.backorder.confirmation", "process", [[res_id]])
                        except Exception:
                            # If process fails, try cancel backorder (best-effort)
                            self.execute("stock.backorder.confirmation", "process_cancel_backorder", [[res_id]])
                    else:
                        # Unknown wizard; best-effort try common method name
                        try:
                            self.execute(res_model, "process", [[res_id]])
                        except Exception:
                            pass

            # Re-check final state
            st1 = self.read_picking_state(picking_id)
            if st1 == "done":
                return True, "Validated (done)"
            return False, f"Not validated. Final state={st1 or 'unknown'}"

        except Exception as e:
            return False, f"Validation error: {e}"
# ----------------------------
# Main
# ----------------------------


def main():
    ap = argparse.ArgumentParser(
        description="STRICT TAG_DIRECT tracking sync: Shopify SOURCE -> Odoo using SRC_ODOO_DB/SRC_ODOO_ORDER tags only."
    )
    ap.add_argument("--from-date", required=True, help="Only process SOURCE fulfillments created on/after this date.")
    ap.add_argument("--to-date", required=True, help="Only process SOURCE fulfillments created on/before this date.")
    ap.add_argument("--dry-run", action="store_true", help="Report-only: do not write to Odoo or SQLite.")
    ap.add_argument(
        "--skip-done-pickings",
        action="store_true",
        help="Always skip pickings in 'done' state (disable smart update on done).",
    )
    ap.add_argument(
        "--no-validate-deliveries",
        action="store_true",
        help="Do NOT validate (DONE) the delivery after writing tracking (default validates).",
    )
    ap.add_argument("--report-csv", default="", help="CSV report path. Default auto timestamp.")
    ap.add_argument("--ineligible-csv", default="", help="Optional CSV for Odoo-ineligible orders (cancel/unpaid/unconfirmed). Default auto.")
    ap.add_argument("--workers", type=int, default=int(os.getenv("SHOPIFY_TRACKING_WORKERS", "1") or "1"), help="Parallel order workers. Use 1 for serial processing.")
    args = ap.parse_args()

    start_dt = parse_window_datetime(args.from_date)
    end_dt = parse_window_datetime(args.to_date, end_of_day=True)
    from_query = shopify_query_datetime(start_dt)
    to_query = shopify_query_datetime(end_dt)
    dry_run = args.dry_run
    skip_done_pickings = args.skip_done_pickings
    validate_deliveries = (not args.no_validate_deliveries)
    workers = max(1, min(12, int(args.workers or 1)))

    report_csv = args.report_csv.strip() or f"odoo_tracking_sync_report_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    ensure_csv_header(report_csv)
    ineligible_csv = args.ineligible_csv.strip() or report_csv.replace(".csv", "_odoo_ineligible.csv")
    ensure_csv_header(ineligible_csv)

    # In dry-run: do not create/modify SQLite at all.
    st = StateDB(STATE_DB, read_only=dry_run)

    # Auth Shopify SOURCES
    sources_auth = []
    for s in SHOPIFY_SOURCES:
        tok = ensure_shopify_access_token(st, s)
        sources_auth.append(
            {
                "name": s.get("name", s["shop"]),
                "shop": s["shop"],
                "token": tok,
                "api_version": s.get("api_version", "2025-01"),
            }
        )

    # Connect Odoo destinations
    odoo_by_db = {}
    odoo_connect_errors = {}
    odoo_cfg_by_db = {o["db"]: o for o in ODOO_DESTS}
    for o in ODOO_DESTS:
        check_cancelled()
        db_name = o["db"]
        emit_progress(
            status="running",
            source="",
            total=0,
            processed=0,
            current_order="",
            message=f"Connecting Odoo destination {db_name}.",
            counters={},
        )
        oc = OdooClient(o["url"], o["db"], o["username"], o["password"])
        try:
            oc.connect()
            odoo_by_db[db_name] = {"name": o.get("name", db_name), "url": o["url"], "client": oc}
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            odoo_connect_errors[db_name] = message
            print(f"[WARN] Odoo destination unavailable: {db_name} ({o.get('name', db_name)}) -> {message}")
    worker_local = threading.local()

    def dest_meta_for_db(odoo_db: str) -> dict:
        if odoo_db in odoo_connect_errors:
            raise OdooDestinationUnavailable(odoo_connect_errors[odoo_db])
        if workers <= 1:
            return odoo_by_db[odoo_db]
        clients = getattr(worker_local, "odoo_by_db", None)
        if clients is None:
            clients = {}
            worker_local.odoo_by_db = clients
        if odoo_db not in clients:
            cfg = odoo_cfg_by_db[odoo_db]
            oc = OdooClient(cfg["url"], cfg["db"], cfg["username"], cfg["password"])
            try:
                oc.connect()
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                odoo_connect_errors[odoo_db] = message
                raise OdooDestinationUnavailable(message) from exc
            clients[odoo_db] = {"name": cfg.get("name", cfg["db"]), "url": cfg["url"], "client": oc}
        return clients[odoo_db]

    print("\n======================================================")
    print(" Odoo Tracking Sync (SMART v3.3) Shopify -> Odoo")
    print("======================================================")
    print(f"Fulfillment createdAt filter: {start_dt.isoformat()}  ->  {end_dt.isoformat()}")
    print(f"Order updated_at fetch range: {from_query}  ->  {to_query}")
    print(f"Odoo DBs: {len(odoo_by_db)}/{len(ODOO_DESTS)} connected | Shopify SOURCE stores: {len(SHOPIFY_SOURCES)}")
    if odoo_connect_errors:
        print("Unavailable Odoo DBs: " + ", ".join(f"{db}: {err}" for db, err in sorted(odoo_connect_errors.items())))
    print(f"Dry-run (report-only): {dry_run}")
    print(f"Parallel workers: {workers}")
    print(f"Smart update DONE pickings: {'NO' if skip_done_pickings else 'YES'}")
    print(f"CSV report: {report_csv}")
    print(f"Odoo-ineligible CSV: {ineligible_csv}")

    counters = {
        "total_orders": 0,
        "processed_orders": 0,
        "processed_fulfillments": 0,
        "synced": 0,
        "tracking_codes_added": 0,
        "tracking_codes_would_add": 0,
        "tracking_codes_replaced": 0,
        "tracking_codes_would_replace": 0,
        "tracking_pickings_updated": 0,
        "tracking_pickings_would_update": 0,
        "tracking_pickings_replaced": 0,
        "tracking_pickings_would_replace": 0,
        "validated": 0,
        "validation_failed": 0,
        "skipped_already": 0,
        "skipped_out_of_range": 0,
        "skipped_no_tracking": 0,
        "skipped_missing_tags": 0,
        "skipped_exception_tags": 0,
        "skipped_odoo_db_missing": 0,
        "skipped_odoo_connect_failed": 0,
        "skipped_odoo_order_not_found": 0,
        "skipped_odoo_order_ineligible": 0,
        "skipped_no_pickings": 0,
        "skipped_no_update_needed": 0,
        "errors": 0,
    }

    counters_lock = threading.Lock()

    def add_counter(key: str, amount: int = 1):
        with counters_lock:
            counters[key] += amount
            return counters[key]

    def counters_snapshot() -> dict:
        with counters_lock:
            return counters.copy()

    def finish_current_order(src_shop: str, current_order: str, message: str):
        processed = add_counter("processed_orders")
        snapshot = counters_snapshot()
        emit_progress(
            status="running",
            source=src_shop,
            total=snapshot["total_orders"],
            processed=processed,
            current_order=current_order,
            message=message,
            counters=snapshot,
        )

    for src in sources_auth:
        print(f"\n--- SOURCE: {src['name']} ({src['shop']}) ---")
        source_orders = list(fetch_source_orders(src["shop"], src["token"], src["api_version"], from_query, to_query))
        add_counter("total_orders", len(source_orders))
        emit_progress(
            status="running",
            source=src["shop"],
            total=counters_snapshot()["total_orders"],
            processed=counters_snapshot()["processed_orders"],
            current_order="",
            message=f"Loaded {len(source_orders)} Shopify order(s) from {src['shop']}.",
            counters=counters_snapshot(),
        )

        def process_source_order(src_order: dict):
            check_cancelled()
            current_order = src_order.get("name", "") or src_order.get("id", "")
            snapshot = counters_snapshot()
            emit_progress(
                status="running",
                source=src["shop"],
                total=snapshot["total_orders"],
                processed=snapshot["processed_orders"],
                current_order=current_order,
                message=f"Syncing Shopify order {current_order}.",
                counters=snapshot,
            )
            src_tags = src_order.get("tags") or []
            v = validate_src_tags_for_odoo(src_tags)

            base_row = {
                "ts_utc": datetime.utcnow().isoformat(),
                "src_store_name": src["name"],
                "src_shop": src["shop"],
                "src_order_name": src_order.get("name", ""),
                "src_order_id": src_order.get("id", ""),
                "src_order_updatedAt": src_order.get("updatedAt", ""),
                "src_tags_all": ", ".join(src_tags),
                "match_method": "TAG_DIRECT",
                "tag_validation_status": v["status"],
                "tag_exception_code": v.get("exception_code", ""),
                "tag_exception_detail": v.get("exception_detail", ""),
                "src_odoo_db_tags_found": ";".join(v.get("db_tags_found", [])),
                "src_odoo_order_tags_found": ";".join(v.get("order_tags_found", [])),
            }

            # Validate tags first
            if v["status"] == "MISSING":
                add_counter("skipped_missing_tags")
                append_csv_row(
                    report_csv,
                    {
                        **base_row,
                        "action": "SKIP",
                        "result": "MISSING_TAGS",
                        "message": "Order missing SRC_ODOO_DB or SRC_ODOO_ORDER tag",
                    },
                )
                finish_current_order(src["shop"], current_order, f"Skipped Shopify order {current_order}: missing Odoo tags.")
                return

            if v["status"] == "EXCEPTION":
                add_counter("skipped_exception_tags")
                append_csv_row(
                    report_csv,
                    {
                        **base_row,
                        "action": "SKIP",
                        "result": "TAG_EXCEPTION",
                        "message": f"Tag validation exception: {v.get('exception_code','')}",
                    },
                )
                finish_current_order(src["shop"], current_order, f"Skipped Shopify order {current_order}: tag exception.")
                return

            odoo_db = v["odoo_db"]
            odoo_order = v["odoo_order"]
            if odoo_db not in odoo_by_db:
                unavailable_error = odoo_connect_errors.get(odoo_db)
                if unavailable_error:
                    add_counter("skipped_odoo_connect_failed")
                    append_csv_row(
                        report_csv,
                        {
                            **base_row,
                            "odoo_dest_name": odoo_cfg_by_db.get(odoo_db, {}).get("name", odoo_db),
                            "odoo_url": odoo_cfg_by_db.get(odoo_db, {}).get("url", ""),
                            "odoo_db": odoo_db,
                            "odoo_order": odoo_order,
                            "action": "SKIP",
                            "result": "ODOO_CONNECT_FAILED",
                            "message": f"Odoo destination could not be connected: {unavailable_error}",
                        },
                    )
                    finish_current_order(src["shop"], current_order, f"Skipped Shopify order {current_order}: Odoo DB connection failed.")
                    return
                add_counter("skipped_odoo_db_missing")
                append_csv_row(
                    report_csv,
                    {
                        **base_row,
                        "odoo_db": odoo_db,
                        "odoo_order": odoo_order,
                        "action": "SKIP",
                        "result": "ODOO_DB_NOT_CONFIGURED",
                        "message": "SRC_ODOO_DB points to a DB not present in ODOO_DESTS config",
                    },
                )
                finish_current_order(src["shop"], current_order, f"Skipped Shopify order {current_order}: Odoo DB not configured.")
                return

            try:
                dest_meta = dest_meta_for_db(odoo_db)
            except OdooDestinationUnavailable as exc:
                add_counter("skipped_odoo_connect_failed")
                append_csv_row(
                    report_csv,
                    {
                        **base_row,
                        "odoo_dest_name": odoo_cfg_by_db.get(odoo_db, {}).get("name", odoo_db),
                        "odoo_url": odoo_cfg_by_db.get(odoo_db, {}).get("url", ""),
                        "odoo_db": odoo_db,
                        "odoo_order": odoo_order,
                        "action": "SKIP",
                        "result": "ODOO_CONNECT_FAILED",
                        "message": f"Odoo destination could not be connected: {exc}",
                    },
                )
                finish_current_order(src["shop"], current_order, f"Skipped Shopify order {current_order}: Odoo DB connection failed.")
                return
            oc = dest_meta["client"]

            fulfillments = src_order.get("fulfillments") or []
            if isinstance(fulfillments, dict):
                # safety: if someone changes query, normalize
                fulfillments = fulfillments.get("edges", [])

            # Iterate fulfillments
            for f in fulfillments:
                check_cancelled()
                # If it's in edges format, unwrap
                if isinstance(f, dict) and "node" in f:
                    f = f["node"]

                f_id = f.get("id", "")
                f_created = f.get("createdAt") or ""
                add_counter("processed_fulfillments")

                row_common = {
                    **base_row,
                    "src_fulfillment_id": f_id,
                    "src_fulfillment_createdAt": f_created,
                    "odoo_dest_name": dest_meta.get("name", odoo_db),
                    "odoo_url": dest_meta.get("url", ""),
                    "odoo_db": odoo_db,
                    "odoo_order": odoo_order,
                }

                if not iso_in_range(f_created, start_dt, end_dt):
                    add_counter("skipped_out_of_range")
                    append_csv_row(
                        report_csv,
                        {
                            **row_common,
                            "action": "SKIP",
                            "result": "OUT_OF_RANGE",
                            "message": "Fulfillment createdAt outside date range",
                        },
                    )
                    continue

                tracking_list = [
                    t
                    for t in (f.get("trackingInfo") or [])
                    if (t.get("number") or "").strip()
                ]
                if not tracking_list:
                    add_counter("skipped_no_tracking")
                    append_csv_row(
                        report_csv,
                        {
                            **row_common,
                            "action": "SKIP",
                            "result": "NO_TRACKING",
                            "message": "No trackingInfo numbers in source fulfillment",
                        },
                    )
                    continue

                already_synced = False
                if (not dry_run) and st.already_synced(src["shop"], src_order.get("id", ""), f_id):
                    already_synced = True
                    add_counter("skipped_already")

                tracking_numbers = [(t.get("number") or "").strip() for t in tracking_list if (t.get("number") or "").strip()]
                tracking_companies = [(t.get("company") or "").strip() for t in tracking_list]
                tracking_urls = [(t.get("url") or "").strip() for t in tracking_list]

                tracking_text = ", ".join([x for x in tracking_numbers if x])
                carrier_name = (tracking_companies[0] if tracking_companies else "").strip()

                row_common.update(
                    {
                        "src_tracking_numbers": tracking_text,
                        "src_tracking_companies": ", ".join([c for c in tracking_companies if c]),
                        "src_tracking_urls": ", ".join([u for u in tracking_urls if u]),
                    }
                )

                try:
                    check_cancelled()
                    so_id = oc.find_sale_order_id_by_name(odoo_order)
                    if not so_id:
                        add_counter("skipped_odoo_order_not_found")
                        append_csv_row(
                            report_csv,
                            {
                                **row_common,
                                "action": "SKIP",
                                "result": "ODOO_ORDER_NOT_FOUND",
                                "message": "Odoo sale.order not found by name",
                            },
                        )
                        continue

                    # STRICT ODOO FILTERING:
                    # Only process CONFIRMED + FULLY INVOICED + PAID orders.
                    eligible, so_meta, reason = oc.sale_order_is_eligible(so_id)
                    so_state = (so_meta.get("state") or "")
                    so_inv_status = (so_meta.get("invoice_status") or "")
                    so_paid = "YES" if eligible else "NO"

                    if not eligible:
                        add_counter("skipped_odoo_order_ineligible")
                        append_csv_row(
                            ineligible_csv,
                            {
                                **row_common,
                                "odoo_sale_order_id": str(so_id),
                                "odoo_sale_state": so_state,
                                "odoo_invoice_status": so_inv_status,
                                "odoo_paid_status": so_paid,
                                "action": "FILTER_OUT",
                                "result": reason,
                                "message": "Filtered out: Odoo order is NOT confirmed/fully-invoiced/paid (excluded from main report)",
                            },
                        )
                        continue

                    picking_ids = oc.get_pickings_of_sale_order(so_id)
                    if not picking_ids:
                        add_counter("skipped_no_pickings")
                        append_csv_row(
                            report_csv,
                            {
                                **row_common,
                                "odoo_sale_order_id": str(so_id),
                                "odoo_sale_state": so_state,
                                "odoo_invoice_status": so_inv_status,
                                "odoo_paid_status": so_paid,
                                "action": "SKIP",
                                "result": "NO_PICKINGS",
                                "message": "No stock.picking linked to this sale.order",
                            },
                        )
                        continue

                    carrier_id = oc.find_carrier_id_by_name(carrier_name) if carrier_name else None
                    pickings = oc.read_pickings(picking_ids)
                    check_cancelled()

                    picking_states = []
                    picking_existing = []
                    update_needed_ids = []
                    picking_existing_by_id = {}
                    replacement_needed_ids = []
                    for p in pickings:
                        pid = p.get("id")
                        stt = p.get("state")
                        existing = (p.get("carrier_tracking_ref") or "").strip()
                        picking_existing_by_id[str(pid)] = existing
                        picking_states.append(f"{pid}:{stt}")
                        picking_existing.append(f"{pid}:{existing}")

                        if stt == "cancel":
                            continue
                        if stt == "done" and skip_done_pickings:
                            continue
                        if tracking_matches(existing, tracking_text):
                            continue
                        if has_tracking_text(existing):
                            replacement_needed_ids.append(str(pid))
                        update_needed_ids.append(str(pid))

                    if not update_needed_ids:
                        # Nothing to write, but we may still validate the delivery if tracking already matches.
                        validated_ids = []
                        validation_msgs = []

                        if (not dry_run) and validate_deliveries:
                            validate_needed = set()
                            for p in pickings:
                                pid2 = str(p.get("id"))
                                stt2 = (p.get("state") or "")
                                existing2 = (p.get("carrier_tracking_ref") or "").strip()
                                if stt2 in ("done", "cancel"):
                                    continue
                                if tracking_matches(existing2, tracking_text):
                                    validate_needed.add(pid2)

                            for pid2 in sorted(validate_needed, key=lambda x: int(x)):
                                check_cancelled()
                                okv, msgv = oc.validate_picking_safely(int(pid2))
                                if okv:
                                    validated_ids.append(pid2)
                                    add_counter("validated")
                                else:
                                    validation_msgs.append(f"{pid2}:{msgv}")
                                    add_counter("validation_failed")

                        # IMPORTANT: in dry-run we do NOT log sync
                        if not dry_run:
                            st.log_sync(src["shop"], src_order.get("id", ""), f_id, odoo_db, odoo_order)

                        did_validate = bool(validated_ids)
                        if not did_validate:
                            add_counter("skipped_no_update_needed")

                        append_csv_row(
                            report_csv,
                            {
                                **row_common,
                                "odoo_sale_order_id": str(so_id),
                                "odoo_sale_state": so_state,
                                "odoo_invoice_status": so_inv_status,
                                "odoo_paid_status": so_paid,
                                "picking_ids_all": ",".join([str(x) for x in picking_ids]),
                                "picking_states": ";".join(picking_states),
                                "picking_existing_tracking": ";".join(picking_existing),
                                "picking_update_needed_ids": "",
                                "action": "VALIDATE_ONLY" if did_validate else "SKIP",
                                "result": "OK" if did_validate else "NO_UPDATE_NEEDED",
                                "validated_picking_ids": ",".join(validated_ids) if did_validate else "",
                                "validation_result": "OK" if (did_validate and not validation_msgs) else ("PARTIAL" if did_validate else "SKIPPED"),
                                "validation_message": ";".join(validation_msgs)[:1000] if validation_msgs else ("Validated" if did_validate else "No validation performed"),
                                "message": "Pickings already had tracking; validated delivery to trigger notifications" if did_validate else "Pickings already have same tracking or skipped by rules",
                            },
                        )
                        continue

                    updated_ids = []
                    note_append = ""
                    if tracking_urls:
                        note_append = f"Tracking URLs: {', '.join([u for u in tracking_urls if u])}"

                    if dry_run:
                        updated_ids = update_needed_ids[:]  # what would update
                        if updated_ids:
                            replacing_count = len([pid for pid in updated_ids if pid in replacement_needed_ids])
                            adding_count = max(0, len(updated_ids) - replacing_count)
                            add_counter("tracking_codes_would_add", len(tracking_numbers) * adding_count)
                            add_counter("tracking_codes_would_replace", len(tracking_numbers) * replacing_count)
                            add_counter("tracking_pickings_would_update", len(updated_ids))
                            add_counter("tracking_pickings_would_replace", replacing_count)
                    else:
                        for pid in update_needed_ids:
                            check_cancelled()
                            ok = oc.write_picking_tracking(int(pid), tracking_text, carrier_id=carrier_id, append_note=note_append)
                            if ok:
                                updated_ids.append(pid)
                        if updated_ids:
                            replacing_count = len([pid for pid in updated_ids if pid in replacement_needed_ids])
                            adding_count = max(0, len(updated_ids) - replacing_count)
                            add_counter("tracking_codes_added", len(tracking_numbers) * adding_count)
                            add_counter("tracking_codes_replaced", len(tracking_numbers) * replacing_count)
                            add_counter("tracking_pickings_updated", len(updated_ids))
                            add_counter("tracking_pickings_replaced", replacing_count)

                    validated_ids = []
                    validation_msgs = []
                    if (not dry_run) and validate_deliveries:
                        # Validate pickings after writing tracking so customer notifications are triggered
                        # Validate all updated pickings; also validate those already having matching tracking but not done
                        validate_needed = set(updated_ids)
                        for p in pickings:
                            pid = str(p.get("id"))
                            stt = (p.get("state") or "")
                            existing = (p.get("carrier_tracking_ref") or "").strip()
                            if stt in ("done", "cancel"):
                                continue
                            if tracking_matches(existing, tracking_text):
                                validate_needed.add(pid)

                        for pid in sorted(validate_needed, key=lambda x: int(x)):
                            check_cancelled()
                            okv, msgv = oc.validate_picking_safely(int(pid))
                            if okv:
                                validated_ids.append(pid)
                                add_counter("validated")
                            else:
                                validation_msgs.append(f"{pid}:{msgv}")
                                add_counter("validation_failed")
                    else:
                        validated_ids = []
                        validation_msgs = []

                    if not dry_run:
                        st.log_sync(src["shop"], src_order.get("id", ""), f_id, odoo_db, odoo_order)

                    add_counter("synced")
                    action_message = (
                        "Shopify tracking would replace mismatched Odoo tracking (strict TAG_DIRECT)"
                        if dry_run and replacement_needed_ids
                        else "Tracking would be written to Odoo pickings (strict TAG_DIRECT)"
                        if dry_run
                        else "Shopify tracking replaced mismatched Odoo tracking (strict TAG_DIRECT)"
                        if replacement_needed_ids
                        else "Tracking written to Odoo pickings (strict TAG_DIRECT)"
                    )
                    append_csv_row(
                        report_csv,
                        {
                            **row_common,
                            "odoo_sale_order_id": str(so_id),
                            "odoo_sale_state": so_state,
                            "odoo_invoice_status": so_inv_status,
                            "odoo_paid_status": so_paid,
                            "picking_ids_all": ",".join([str(x) for x in picking_ids]),
                            "picking_states": ";".join(picking_states),
                            "picking_existing_tracking": ";".join(picking_existing),
                            "picking_update_needed_ids": ",".join(update_needed_ids),
                            "action": "DRYRUN_UPDATE" if dry_run else "UPDATE_PICKINGS",
                            "result": "OK",
                            "message": action_message,
                            "validated_picking_ids": ",".join(validated_ids) if (not dry_run and validate_deliveries) else "",
                            "validation_result": "OK" if (not dry_run and validate_deliveries and not validation_msgs) else ("PARTIAL" if (not dry_run and validate_deliveries and validation_msgs) else ""),
                            "validation_message": ";".join(validation_msgs)[:1000] if validation_msgs else ("Validated" if (not dry_run and validate_deliveries) else ""),
                            "updated_picking_ids": ",".join(updated_ids),
                        },
                    )

                    print(
                        f"[{'DRY' if dry_run else 'OK'}] {src['shop']} {src_order.get('name')} -> Odoo({odoo_db}) {odoo_order} | tracking={tracking_numbers[:1]} (+{max(0, len(tracking_numbers)-1)} more)"
                    )

                except Exception as e:
                    add_counter("errors")
                    append_csv_row(
                        report_csv,
                        {
                            **row_common,
                            "action": "ERROR",
                            "result": "SYNC_FAILED",
                            "message": str(e),
                        },
                    )
                    print(f"[ERROR] {src['shop']} {src_order.get('name')} -> Odoo({odoo_db}) {odoo_order}: {e}")
            finish_current_order(src["shop"], current_order, f"Finished Shopify order {current_order}.")

        if workers > 1 and len(source_orders) > 1:
            executor = ThreadPoolExecutor(max_workers=min(workers, len(source_orders)), thread_name_prefix="tracking-order")
            futures = [executor.submit(process_source_order, src_order) for src_order in source_orders]
            try:
                for future in as_completed(futures):
                    future.result()
            except TrackingSyncCancelled:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
        else:
            for src_order in source_orders:
                process_source_order(src_order)

    print("\n================ SUMMARY ================")
    for k, v in counters.items():
        print(f"{k:28s}: {v}")
    print(f"CSV report: {report_csv}")
    print(f"Odoo-ineligible CSV: {ineligible_csv}")
    emit_progress(
        status="completed",
        total=counters["total_orders"],
        processed=counters["processed_orders"],
        current_order="",
        message="Shopify tracking sync complete.",
        counters=counters.copy(),
    )


if __name__ == "__main__":
    main()
