from __future__ import annotations

import csv
import html
import io
import json
import os
import re
import threading
import time
import tempfile
import uuid
import zipfile
import xmlrpc.client
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from urllib.parse import urlencode
from urllib.parse import quote_plus
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from calendar import monthrange
from pathlib import Path
from typing import Any, Optional, Union
from xml.sax.saxutils import escape as xml_escape

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import BASE_DIR, DEFAULT_SERVICE_SETTINGS, FRONTEND_DIST, env_bool
from app.core.time import utc_now
from app.db.session import db
from app.schemas import (
    AddressPayload,
    AdminSettingsPayload,
    AmazonAccountPayload,
    BulkPlacePayload,
    ChromeJobCompletePayload,
    ChromeJobCostlyPayload,
    ChromeJobFailPayload,
    ChromeJobHeartbeatPayload,
    ChromeTrackingUpdatePayload,
    CostlyApprovalPayload,
    DeleteLinesPayload,
    EnginePayload,
    ExportCreatePayload,
    EpostSyncPayload,
    EpostTrackingUpdatePayload,
    InventoryCreatePayload,
    LineSpaidPayload,
    ManualAmazonOrderMatchPayload,
    PlacePayload,
    PullPayload,
    PunchoutReturnUrlPayload,
    ReplacementPayload,
    ServiceSettingsPayload,
    StoreActionPayload,
    StorePayload,
)
from app.services.amazon import amz_date, normalize_amazon_endpoint
from app.services.amazon_otp import imap_connect, imap_search_since, parse_amazon_email
from app.services.asin import decode_asin_reference, extract_asin_from_notes, normalize_asin, strip_html


AMAZON_ORDER_RE = re.compile(r"\b(?:AMAZON|Amazon)\s+order\s*:\s*([A-Z0-9-]+)", re.IGNORECASE)
SKIP_LINE_NAME_RE = re.compile(
    r"\b(delivery|shipping|standard delivery|discount|coupon|promotion|conditions)\b",
    re.IGNORECASE,
)
DELIVERY_REFERENCE_RE = re.compile(r"^delivery_", re.IGNORECASE)
DISCOUNT_LINE_RE = re.compile(r"\bdiscount\b", re.IGNORECASE)
ORDERING_ENGINES = {"rest", "cxml", "chrome"}
CXML_AUTH_MODES = {"header", "basic", "both"}

app = FastAPI(title="Amazon Business Fulfilment Control Panel")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "app" / "static")), name="static")
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend-assets")

_sync_thread_started = False
PUBLIC_PATH_PREFIXES = ("/public", "/api/public", "/assets", "/static", "/health", "/favicon")
MASTER_ADMIN_ACCESS_TOKEN = os.getenv("MASTER_ADMIN_ACCESS_TOKEN", "1284").strip()
_ADMIN_ACCESS_TOKEN_CACHE: tuple[str, float] = ("", 0.0)
_ADMIN_ACCESS_TOKEN_CACHE_LOCK = threading.Lock()
_STORE_CACHE: dict[int, tuple[Store, float]] = {}
_STORE_CACHE_LOCK = threading.Lock()


def effective_admin_access_token() -> str:
    global _ADMIN_ACCESS_TOKEN_CACHE
    now = time.monotonic()
    with _ADMIN_ACCESS_TOKEN_CACHE_LOCK:
        cached_token, expires_at = _ADMIN_ACCESS_TOKEN_CACHE
    if expires_at > now:
        return cached_token
    fallback = os.getenv("ADMIN_ACCESS_TOKEN", "").strip()
    try:
        with db() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", ("admin_access_token",)).fetchone()
        configured = str(row["value"] or "").strip() if row else ""
        token = configured or fallback
    except Exception:
        token = fallback
    with _ADMIN_ACCESS_TOKEN_CACHE_LOCK:
        _ADMIN_ACCESS_TOKEN_CACHE = (token, now + 30)
    return token


@app.middleware("http")
async def admin_access_middleware(request: Request, call_next: Any) -> Response:
    token = effective_admin_access_token()
    path = request.url.path
    allow_frontend_shell = request.method == "GET" and path == "/"
    if token and not allow_frontend_shell and not path.startswith(PUBLIC_PATH_PREFIXES):
        supplied = request.headers.get("X-Admin-Token") or request.query_params.get("admin_token") or ""
        if supplied != token and supplied != MASTER_ADMIN_ACCESS_TOKEN:
            return Response("Admin token required.", status_code=401)
    return await call_next(request)


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                odoo_url TEXT NOT NULL,
                odoo_db TEXT NOT NULL,
                odoo_user TEXT NOT NULL,
                odoo_password TEXT NOT NULL,
                website_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                odoo_order_id INTEGER NOT NULL,
                odoo_order_name TEXT NOT NULL,
                odoo_order_date TEXT,
                odoo_line_id INTEGER NOT NULL,
                product_id INTEGER,
                product_tmpl_id INTEGER,
                product_name TEXT,
                default_code TEXT,
                internal_note TEXT,
                asin_from_reference TEXT,
                asin_from_note TEXT,
                asin TEXT,
                supplier_part_auxiliary_id TEXT,
                quantity REAL NOT NULL DEFAULT 1,
                store_unit_price REAL,
                store_total_price REAL,
                store_currency TEXT,
                store_currency_rate_to_usd REAL,
                store_subtotal_native REAL,
                store_delivery_native REAL,
                store_discount_native REAL,
                store_adjustment_native REAL,
                store_total_native REAL,
                amazon_unit_price REAL,
                amazon_total_price REAL,
                chrome_profit_total REAL,
                fulfilment_note TEXT,
                odoo_order_state TEXT,
                odoo_invoice_status TEXT,
                odoo_status_label TEXT,
                state TEXT NOT NULL DEFAULT 'pulled',
                amazon_order_id TEXT,
                amazon_order_url TEXT,
                amazon_account_id INTEGER,
                amazon_account_name TEXT,
                order_engine TEXT NOT NULL DEFAULT 'rest',
                amazon_group_key TEXT,
                amazon_status TEXT,
                tracking_status TEXT,
                tracking_payload TEXT,
                tracking_checked_at TEXT,
                chrome_claimed_by TEXT,
                chrome_claimed_at TEXT,
                chrome_claim_expires_at TEXT,
                last_error TEXT,
                missing_asin TEXT,
                original_asin TEXT,
                replacement_asin TEXT,
                replacement_product_name TEXT,
                replacement_note TEXT,
                replacement_assigned_at TEXT,
                cost_approved_at TEXT,
                cost_review_loss REAL,
                raw_json TEXT,
                pulled_at TEXT,
                ordered_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, odoo_line_id)
            );

            CREATE TABLE IF NOT EXISTS amazon_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_line_id INTEGER NOT NULL REFERENCES order_lines(id) ON DELETE CASCADE,
                external_id TEXT NOT NULL UNIQUE,
                mode TEXT NOT NULL,
                request_json TEXT NOT NULL,
                response_json TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fulfilment_addresses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                company_name TEXT,
                phone_number TEXT,
                address_line1 TEXT NOT NULL,
                address_line2 TEXT,
                address_line3 TEXT,
                city TEXT NOT NULL,
                state_or_region TEXT,
                postal_code TEXT NOT NULL,
                country_code TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS amazon_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                api_base_url TEXT NOT NULL,
                tracking_api_base_url TEXT NOT NULL,
                lwa_token_url TEXT NOT NULL,
                lwa_client_id TEXT,
                lwa_client_secret TEXT,
                lwa_refresh_token TEXT,
                api_access_token TEXT,
                buyer_email TEXT,
                buying_group_id TEXT,
                product_region TEXT NOT NULL DEFAULT 'US',
                locale TEXT NOT NULL DEFAULT 'en_US',
                cxml_from_identity TEXT,
                cxml_shared_secret TEXT,
                cxml_po_url TEXT,
                cxml_punchout_url TEXT,
                cxml_punchout_test_url TEXT,
                cxml_auth_mode TEXT NOT NULL DEFAULT 'header',
                cxml_cart_session_id TEXT,
                cxml_credential_domain TEXT NOT NULL DEFAULT 'NetworkId',
                cxml_to_identity TEXT NOT NULL DEFAULT 'Amazon',
                dry_run INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS inventory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                order_line_id INTEGER REFERENCES order_lines(id) ON DELETE SET NULL,
                asin TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 1,
                product_name TEXT,
                source_odoo_order_id INTEGER,
                source_odoo_order_name TEXT,
                amazon_order_id TEXT,
                amazon_order_url TEXT,
                amazon_account_name TEXT,
                status TEXT NOT NULL DEFAULT 'incoming',
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, order_line_id)
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS epost_global_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                order_line_id INTEGER REFERENCES order_lines(id) ON DELETE SET NULL,
                odoo_order_id INTEGER,
                odoo_order_name TEXT,
                amazon_order_id TEXT,
                amazon_order_url TEXT,
                picking_id INTEGER,
                picking_name TEXT,
                tracking_code TEXT NOT NULL,
                tracking_url TEXT,
                status TEXT,
                last_update_at TEXT,
                location TEXT,
                destination TEXT,
                awb TEXT,
                events_json TEXT,
                last_checked_at TEXT,
                epost_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(store_id, tracking_code)
            );

            CREATE TABLE IF NOT EXISTS punchout_return_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                url TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS export_jobs (
                id TEXT PRIMARY KEY,
                view TEXT NOT NULL,
                store_id INTEGER,
                status TEXT NOT NULL,
                columns_json TEXT NOT NULL,
                filters_json TEXT,
                selected_ids_json TEXT,
                select_all INTEGER NOT NULL DEFAULT 0,
                total_records INTEGER NOT NULL DEFAULT 0,
                processed_records INTEGER NOT NULL DEFAULT 0,
                part_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                stop_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS pull_jobs (
                id TEXT PRIMARY KEY,
                store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                days INTEGER NOT NULL DEFAULT 7,
                limit_value INTEGER NOT NULL DEFAULT 50,
                inserted_records INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS export_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES export_jobs(id) ON DELETE CASCADE,
                part_number INTEGER NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                filename TEXT NOT NULL,
                local_path TEXT,
                storage_key TEXT,
                storage_url TEXT,
                downloaded_at TEXT,
                expires_at TEXT,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(job_id, part_number)
            );

            CREATE TABLE IF NOT EXISTS shipping_imports (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                month TEXT NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                matched_count INTEGER NOT NULL DEFAULT 0,
                unmatched_count INTEGER NOT NULL DEFAULT 0,
                default_fulfilment_fee REAL NOT NULL DEFAULT 4,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS shipping_charges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                import_id TEXT NOT NULL REFERENCES shipping_imports(id) ON DELETE CASCADE,
                odoo_order_name TEXT NOT NULL,
                shipment_date TEXT,
                tracking_number TEXT,
                carrier TEXT,
                service TEXT,
                quantity REAL NOT NULL DEFAULT 1,
                shipping_fee REAL NOT NULL DEFAULT 0,
                fulfilment_fee REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0,
                matched_line_count INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounting_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_type TEXT NOT NULL,
                odoo_order_name TEXT NOT NULL,
                country_code TEXT NOT NULL DEFAULT '',
                tax_region TEXT NOT NULL,
                invoice_date TEXT,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                content_type TEXT,
                local_path TEXT,
                storage_key TEXT,
                storage_url TEXT,
                file_size INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS amazon_otp_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amazon_order_id TEXT NOT NULL UNIQUE,
                otp TEXT,
                otp_required INTEGER NOT NULL DEFAULT 0,
                tracking_url TEXT,
                shipment_id TEXT,
                package_index TEXT,
                recipient TEXT,
                product_summary TEXT,
                otp_email_subject TEXT,
                otp_email_date TEXT,
                dispatch_email_subject TEXT,
                dispatch_email_date TEXT,
                status TEXT,
                raw_json TEXT,
                last_email_uid TEXT,
                last_synced_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS amazon_otp_email_uids (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folder TEXT NOT NULL,
                uid TEXT NOT NULL,
                message_id TEXT,
                amazon_order_id TEXT,
                email_type TEXT,
                subject TEXT,
                email_date TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(folder, uid)
            );
            """
        )
        for column, ddl in {
            "source_type": "ALTER TABLE inventory_items ADD COLUMN source_type TEXT NOT NULL DEFAULT 'amazon_cancelled'",
            "reserved_order_line_id": "ALTER TABLE inventory_items ADD COLUMN reserved_order_line_id INTEGER",
            "reserved_at": "ALTER TABLE inventory_items ADD COLUMN reserved_at TEXT",
            "used_at": "ALTER TABLE inventory_items ADD COLUMN used_at TEXT",
            "manual_reference": "ALTER TABLE inventory_items ADD COLUMN manual_reference TEXT",
        }.items():
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(inventory_items)").fetchall()}
            if column not in existing_cols:
                conn.execute(ddl)
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_inventory_store_asin_status ON inventory_items(store_id, asin, status)",
            "CREATE INDEX IF NOT EXISTS idx_inventory_reserved_line ON inventory_items(reserved_order_line_id)",
            "CREATE INDEX IF NOT EXISTS idx_export_jobs_status ON export_jobs(status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_export_files_expiry ON export_files(expires_at, deleted_at)",
            "CREATE INDEX IF NOT EXISTS idx_pull_jobs_status ON pull_jobs(status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_pull_jobs_store ON pull_jobs(store_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_shipping_charges_order ON shipping_charges(odoo_order_name)",
            "CREATE INDEX IF NOT EXISTS idx_shipping_imports_month ON shipping_imports(month)",
            "CREATE INDEX IF NOT EXISTS idx_accounting_documents_order ON accounting_documents(odoo_order_name)",
            "CREATE INDEX IF NOT EXISTS idx_accounting_documents_region ON accounting_documents(tax_region, document_type)",
            "CREATE INDEX IF NOT EXISTS idx_amazon_otp_order ON amazon_otp_records(amazon_order_id)",
            "CREATE INDEX IF NOT EXISTS idx_amazon_otp_updated ON amazon_otp_records(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_amazon_otp_email_type ON amazon_otp_email_uids(email_type, created_at)",
        ):
            conn.execute(index_sql)
        for column, ddl in {
            "odoo_order_state": "ALTER TABLE order_lines ADD COLUMN odoo_order_state TEXT",
            "odoo_order_date": "ALTER TABLE order_lines ADD COLUMN odoo_order_date TEXT",
            "odoo_invoice_status": "ALTER TABLE order_lines ADD COLUMN odoo_invoice_status TEXT",
            "odoo_status_label": "ALTER TABLE order_lines ADD COLUMN odoo_status_label TEXT",
            "tracking_status": "ALTER TABLE order_lines ADD COLUMN tracking_status TEXT",
            "tracking_payload": "ALTER TABLE order_lines ADD COLUMN tracking_payload TEXT",
            "tracking_checked_at": "ALTER TABLE order_lines ADD COLUMN tracking_checked_at TEXT",
            "chrome_claimed_by": "ALTER TABLE order_lines ADD COLUMN chrome_claimed_by TEXT",
            "chrome_claimed_at": "ALTER TABLE order_lines ADD COLUMN chrome_claimed_at TEXT",
            "chrome_claim_expires_at": "ALTER TABLE order_lines ADD COLUMN chrome_claim_expires_at TEXT",
            "amazon_account_id": "ALTER TABLE order_lines ADD COLUMN amazon_account_id INTEGER",
            "amazon_account_name": "ALTER TABLE order_lines ADD COLUMN amazon_account_name TEXT",
            "order_engine": "ALTER TABLE order_lines ADD COLUMN order_engine TEXT NOT NULL DEFAULT 'rest'",
            "amazon_group_key": "ALTER TABLE order_lines ADD COLUMN amazon_group_key TEXT",
            "source_odoo_line_ids": "ALTER TABLE order_lines ADD COLUMN source_odoo_line_ids TEXT",
            "source_line_count": "ALTER TABLE order_lines ADD COLUMN source_line_count INTEGER DEFAULT 1",
            "supplier_part_auxiliary_id": "ALTER TABLE order_lines ADD COLUMN supplier_part_auxiliary_id TEXT",
            "store_unit_price": "ALTER TABLE order_lines ADD COLUMN store_unit_price REAL",
            "store_total_price": "ALTER TABLE order_lines ADD COLUMN store_total_price REAL",
            "store_currency": "ALTER TABLE order_lines ADD COLUMN store_currency TEXT",
            "store_currency_rate_to_usd": "ALTER TABLE order_lines ADD COLUMN store_currency_rate_to_usd REAL",
            "store_subtotal_native": "ALTER TABLE order_lines ADD COLUMN store_subtotal_native REAL",
            "store_delivery_native": "ALTER TABLE order_lines ADD COLUMN store_delivery_native REAL",
            "store_discount_native": "ALTER TABLE order_lines ADD COLUMN store_discount_native REAL",
            "store_adjustment_native": "ALTER TABLE order_lines ADD COLUMN store_adjustment_native REAL",
            "store_total_native": "ALTER TABLE order_lines ADD COLUMN store_total_native REAL",
            "amazon_unit_price": "ALTER TABLE order_lines ADD COLUMN amazon_unit_price REAL",
            "amazon_total_price": "ALTER TABLE order_lines ADD COLUMN amazon_total_price REAL",
            "chrome_profit_total": "ALTER TABLE order_lines ADD COLUMN chrome_profit_total REAL",
            "fulfilment_note": "ALTER TABLE order_lines ADD COLUMN fulfilment_note TEXT",
            "pulled_at": "ALTER TABLE order_lines ADD COLUMN pulled_at TEXT",
            "ordered_at": "ALTER TABLE order_lines ADD COLUMN ordered_at TEXT",
            "missing_asin": "ALTER TABLE order_lines ADD COLUMN missing_asin TEXT",
            "original_asin": "ALTER TABLE order_lines ADD COLUMN original_asin TEXT",
            "replacement_asin": "ALTER TABLE order_lines ADD COLUMN replacement_asin TEXT",
            "replacement_product_name": "ALTER TABLE order_lines ADD COLUMN replacement_product_name TEXT",
            "replacement_note": "ALTER TABLE order_lines ADD COLUMN replacement_note TEXT",
            "replacement_assigned_at": "ALTER TABLE order_lines ADD COLUMN replacement_assigned_at TEXT",
            "cost_approved_at": "ALTER TABLE order_lines ADD COLUMN cost_approved_at TEXT",
            "cost_review_loss": "ALTER TABLE order_lines ADD COLUMN cost_review_loss REAL",
        }.items():
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(order_lines)").fetchall()}
            if column not in existing_cols:
                conn.execute(ddl)
        conn.execute("UPDATE order_lines SET pulled_at = COALESCE(NULLIF(pulled_at, ''), created_at)")
        conn.execute(
            """
            UPDATE order_lines
            SET ordered_at = COALESCE(NULLIF(ordered_at, ''), updated_at)
            WHERE COALESCE(amazon_order_id, '') != ''
              AND COALESCE(ordered_at, '') = ''
            """
        )
        for column, ddl in {
            "buyer_email": "ALTER TABLE amazon_accounts ADD COLUMN buyer_email TEXT",
            "buying_group_id": "ALTER TABLE amazon_accounts ADD COLUMN buying_group_id TEXT",
            "product_region": "ALTER TABLE amazon_accounts ADD COLUMN product_region TEXT NOT NULL DEFAULT 'US'",
            "locale": "ALTER TABLE amazon_accounts ADD COLUMN locale TEXT NOT NULL DEFAULT 'en_US'",
            "cxml_from_identity": "ALTER TABLE amazon_accounts ADD COLUMN cxml_from_identity TEXT",
            "cxml_shared_secret": "ALTER TABLE amazon_accounts ADD COLUMN cxml_shared_secret TEXT",
            "cxml_po_url": "ALTER TABLE amazon_accounts ADD COLUMN cxml_po_url TEXT",
            "cxml_punchout_url": "ALTER TABLE amazon_accounts ADD COLUMN cxml_punchout_url TEXT",
            "cxml_punchout_test_url": "ALTER TABLE amazon_accounts ADD COLUMN cxml_punchout_test_url TEXT",
            "cxml_auth_mode": "ALTER TABLE amazon_accounts ADD COLUMN cxml_auth_mode TEXT NOT NULL DEFAULT 'header'",
            "cxml_cart_session_id": "ALTER TABLE amazon_accounts ADD COLUMN cxml_cart_session_id TEXT",
            "cxml_credential_domain": "ALTER TABLE amazon_accounts ADD COLUMN cxml_credential_domain TEXT NOT NULL DEFAULT 'NetworkId'",
            "cxml_to_identity": "ALTER TABLE amazon_accounts ADD COLUMN cxml_to_identity TEXT NOT NULL DEFAULT 'Amazon'",
        }.items():
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(amazon_accounts)").fetchall()}
            if column not in existing_cols:
                conn.execute(ddl)
        conn.execute("UPDATE amazon_accounts SET dry_run = 0")
        row = conn.execute("SELECT COUNT(*) AS count FROM punchout_return_urls").fetchone()
        if int(row["count"]) == 0:
            now = utc_now()
            cursor = conn.execute(
                """
                INSERT INTO punchout_return_urls (label, url, is_default, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
                """,
                (
                    "Local punchout return",
                    os.getenv("DEFAULT_PUNCHOUT_RETURN_URL", "http://127.0.0.1:8000/punchout/cart-return?store_id=1"),
                    now,
                    now,
                ),
            )
        conn.execute(
            """
            UPDATE order_lines
            SET amazon_order_id=NULL,
                amazon_order_url=NULL,
                amazon_account_id=NULL,
                amazon_account_name=NULL,
                amazon_group_key=NULL,
                amazon_status=NULL,
                tracking_status=NULL,
                tracking_payload=NULL,
                tracking_checked_at=NULL,
                state='pulled',
                last_error=NULL,
                ordered_at=NULL,
                updated_at=?
            WHERE COALESCE(amazon_order_id, '') LIKE 'DRYRUN-%'
               OR state='trial_ok'
            """,
            (utc_now(),),
        )
        conn.execute(
            """
            UPDATE amazon_accounts
            SET api_base_url = ?, tracking_api_base_url = ?, updated_at = ?
            WHERE api_base_url = 'https://api.business.amazon.com'
               OR tracking_api_base_url = 'https://api.business.amazon.com'
            """,
            ("https://na.business-api.amazon.com", "https://na.business-api.amazon.com", utc_now()),
        )
        row = conn.execute("SELECT COUNT(*) AS count FROM stores").fetchone()
        if int(row["count"]) == 0:
            conn.execute(
                """
                INSERT INTO stores
                (name, odoo_url, odoo_db, odoo_user, odoo_password, website_id, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    "Nutricity USA",
                    os.getenv("ODOO_URL", "https://backend.nutricityusa.com").rstrip("/"),
                    os.getenv("ODOO_DB", "supplee"),
                    os.getenv("ODOO_USER", "admin@nutricityusa.com"),
                    os.getenv("ODOO_PASSWORD", "Amit@123"),
                    None,
                    utc_now(),
                    utc_now(),
                ),
            )
        amazon_account_count = conn.execute("SELECT COUNT(*) AS count FROM amazon_accounts").fetchone()
        if int(amazon_account_count["count"]) == 0:
            conn.execute(
                """
                INSERT INTO amazon_accounts
                (name, api_base_url, tracking_api_base_url, lwa_token_url, lwa_client_id, lwa_client_secret,
                 lwa_refresh_token, api_access_token, dry_run, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    "Default Amazon Business",
                    normalize_amazon_endpoint(os.getenv("AMAZON_API_BASE_URL", "https://na.business-api.amazon.com")),
                    normalize_amazon_endpoint(os.getenv("AMAZON_TRACKING_API_BASE_URL", "https://na.business-api.amazon.com")),
                    os.getenv("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token"),
                    os.getenv("AMAZON_LWA_CLIENT_ID", ""),
                    os.getenv("AMAZON_LWA_CLIENT_SECRET", ""),
                    os.getenv("AMAZON_LWA_REFRESH_TOKEN", ""),
                    os.getenv("AMAZON_API_ACCESS_TOKEN", ""),
                    0,
                    utc_now(),
                    utc_now(),
                ),
            )
        address_count = conn.execute("SELECT COUNT(*) AS count FROM fulfilment_addresses").fetchone()
        if int(address_count["count"]) == 0:
            conn.execute(
                """
                INSERT INTO fulfilment_addresses
                (label, company_name, phone_number, address_line1, address_line2, address_line3,
                 city, state_or_region, postal_code, country_code, is_default, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    "Default fulfilment address",
                    "Nutricity",
                    "",
                    "Replace with your warehouse address",
                    "",
                    "",
                    "Replace city",
                    "",
                    "00000",
                    "US",
                    utc_now(),
                    utc_now(),
                ),
            )


@dataclass
class Store:
    id: int
    name: str
    odoo_url: str
    odoo_db: str
    odoo_user: str
    odoo_password: str
    website_id: Optional[int] = None


_ODOO_FIELDS_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}
_ODOO_FIELDS_CACHE_LOCK = threading.Lock()
_ODOO_UID_CACHE: dict[tuple[str, str, str, str], int] = {}
_ODOO_UID_CACHE_LOCK = threading.Lock()


class OdooClient:
    def __init__(self, store: Store):
        self.store = store
        self.common = xmlrpc.client.ServerProxy(f"{store.odoo_url}/xmlrpc/2/common", allow_none=True)
        self.object = xmlrpc.client.ServerProxy(f"{store.odoo_url}/xmlrpc/2/object", allow_none=True)
        uid_cache_key = (store.odoo_url, store.odoo_db, store.odoo_user, store.odoo_password)
        with _ODOO_UID_CACHE_LOCK:
            self.uid = _ODOO_UID_CACHE.get(uid_cache_key)
        if not self.uid:
            self.uid = self.common.authenticate(store.odoo_db, store.odoo_user, store.odoo_password, {})
            if self.uid:
                with _ODOO_UID_CACHE_LOCK:
                    _ODOO_UID_CACHE[uid_cache_key] = int(self.uid)
        if not self.uid:
            raise RuntimeError(f"Could not authenticate to Odoo store {store.name}")

    def execute(self, model: str, method: str, args: Optional[list[Any]] = None, kwargs: Optional[dict[str, Any]] = None) -> Any:
        return self.object.execute_kw(
            self.store.odoo_db,
            self.uid,
            self.store.odoo_password,
            model,
            method,
            args or [],
            kwargs or {},
        )

    def search_read(self, model: str, domain: list[Any], fields: list[str], limit: int = 0, order: str = "") -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields}
        if limit:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        return self.execute(model, "search_read", [domain], kwargs)

    def write(self, model: str, ids: list[int], vals: dict[str, Any]) -> bool:
        return bool(self.execute(model, "write", [ids, vals]))

    def read(self, model: str, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        return self.execute(model, "read", [ids], {"fields": fields})

    def fields_get(self, model: str) -> dict[str, Any]:
        return self.execute(model, "fields_get", [], {})

    def has_field(self, model: str, field: str) -> bool:
        try:
            return field in self.fields_get(model)
        except Exception:
            return False

    def existing_fields(self, model: str, fields: list[str]) -> list[str]:
        try:
            cache_key = (self.store.odoo_url, self.store.odoo_db, self.store.odoo_user, model)
            with _ODOO_FIELDS_CACHE_LOCK:
                available = _ODOO_FIELDS_CACHE.get(cache_key)
            if available is None:
                available = self.fields_get(model)
                with _ODOO_FIELDS_CACHE_LOCK:
                    _ODOO_FIELDS_CACHE[cache_key] = available
            return [field for field in fields if field in available]
        except Exception:
            return fields

    def order_url(self, order_id: int) -> str:
        return f"{self.store.odoo_url}/web#id={order_id}&model=sale.order&view_type=form"

    def post_order_note(self, order_id: int, body: str) -> None:
        try:
            self.execute(
                "sale.order",
                "message_post",
                [[order_id]],
                {"body": body, "message_type": "comment", "subtype_xmlid": "mail.mt_note"},
            )
        except Exception:
            existing = self.read("sale.order", [order_id], ["note"])
            current = existing[0].get("note") if existing else ""
            separator = "\n" if current else ""
            self.write("sale.order", [order_id], {"note": f"{current or ''}{separator}{body}"})

    def validate_pickings_for_order(self, order_id: int) -> str:
        rows = self.read("sale.order", [order_id], ["picking_ids"])
        picking_ids = rows[0].get("picking_ids") if rows else []
        if not picking_ids:
            return "No pickings found"
        pickings = self.read("stock.picking", picking_ids, ["id", "state"])
        validated: list[int] = []
        for picking in pickings:
            if picking.get("state") in {"done", "cancel"}:
                continue
            pid = int(picking["id"])
            try:
                self.execute("stock.picking", "action_confirm", [[pid]])
            except Exception:
                pass
            try:
                self.execute("stock.picking", "action_assign", [[pid]])
            except Exception:
                pass
            self.execute("stock.picking", "button_validate", [[pid]])
            validated.append(pid)
        return f"Validated pickings: {validated}" if validated else "Already validated"

    def picking_fulfilment_status_for_order(self, order_id: int) -> dict[str, Any]:
        rows = self.read("sale.order", [order_id], ["id", "name", "state", "invoice_status", "picking_ids"])
        order = rows[0] if rows else {}
        picking_ids = order.get("picking_ids") or []
        if not picking_ids:
            return {
                "odoo_sale_state": order.get("state") or "",
                "odoo_invoice_status": order.get("invoice_status") or "",
                "picking_ids": [],
                "picking_names": [],
                "picking_states": [],
                "open_picking_ids": [],
                "open_picking_names": [],
                "fulfilment_status": "fulfilment pending",
                "message": "No Odoo pickings found",
            }
        fields = self.existing_fields("stock.picking", ["id", "name", "state", "carrier_tracking_ref"])
        pickings = self.read("stock.picking", picking_ids, fields)
        open_pickings = [picking for picking in pickings if picking.get("state") not in {"done", "cancel"}]
        return {
            "odoo_sale_state": order.get("state") or "",
            "odoo_invoice_status": order.get("invoice_status") or "",
            "picking_ids": [picking.get("id") for picking in pickings],
            "picking_names": [picking.get("name") or str(picking.get("id")) for picking in pickings],
            "picking_states": [picking.get("state") or "" for picking in pickings],
            "open_picking_ids": [picking.get("id") for picking in open_pickings],
            "open_picking_names": [picking.get("name") or str(picking.get("id")) for picking in open_pickings],
            "fulfilment_status": "fulfilled" if not open_pickings else "fulfilment pending",
            "message": "All pickings done/cancelled" if not open_pickings else "Open Odoo pickings remain",
        }

    def epost_tracking_for_order(self, order_id: int, since_days: int = 2) -> list[dict[str, Any]]:
        rows = self.read("sale.order", [order_id], ["picking_ids"])
        picking_ids = rows[0].get("picking_ids") if rows else []
        if not picking_ids:
            return []
        fields = self.existing_fields("stock.picking", ["id", "name", "state", "carrier_tracking_ref", "date_done", "write_date"])
        pickings = self.read("stock.picking", picking_ids, fields)
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(since_days or 2)))

        def parse_odoo_datetime(value: Any) -> Optional[datetime]:
            if not value:
                return None
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    parsed = datetime.strptime(text[:19], fmt)
                    return parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None

        found: list[dict[str, Any]] = []
        for picking in pickings:
            if picking.get("state") != "done":
                continue
            completed_at = parse_odoo_datetime(picking.get("date_done") or picking.get("write_date"))
            if since_days and (not completed_at or completed_at < cutoff):
                continue
            tracking_text = str(picking.get("carrier_tracking_ref") or "")
            for code in sorted(set(re.findall(r"\bEPG[0-9A-Z]+\b", tracking_text, re.IGNORECASE))):
                found.append(
                    {
                        "picking_id": picking.get("id"),
                        "picking_name": picking.get("name") or str(picking.get("id")),
                        "tracking_code": code.upper(),
                    }
                )
        return found

    def epost_tracking_since(self, since_days: int = 2, limit: int = 1000) -> list[dict[str, Any]]:
        fields = self.existing_fields("stock.picking", ["id", "name", "state", "carrier_tracking_ref", "date_done", "write_date", "sale_id", "origin", "carrier_id"])
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(since_days or 2)))
        cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        domain: list[Any] = [("state", "=", "done"), ("carrier_tracking_ref", "!=", False)]
        if "date_done" in fields:
            domain.append(("date_done", ">=", cutoff_text))
        elif "write_date" in fields:
            domain.append(("write_date", ">=", cutoff_text))
        pickings = self.search_read("stock.picking", domain, fields, limit=limit, order="write_date desc")
        found: list[dict[str, Any]] = []
        for picking in pickings:
            tracking_text = str(picking.get("carrier_tracking_ref") or "")
            sale = picking.get("sale_id") or []
            carrier = picking.get("carrier_id") or []
            for code in sorted(set(re.findall(r"\bEPG[0-9A-Z]+\b", tracking_text, re.IGNORECASE))):
                found.append(
                    {
                        "odoo_order_id": sale[0] if isinstance(sale, list) and sale else None,
                        "odoo_order_name": sale[1] if isinstance(sale, list) and len(sale) > 1 else picking.get("origin") or "",
                        "picking_id": picking.get("id"),
                        "picking_name": picking.get("name") or str(picking.get("id")),
                        "tracking_code": code.upper(),
                        "carrier_name": carrier[1] if isinstance(carrier, list) and len(carrier) > 1 else "",
                    }
                )
        return found


class AmazonBusinessClient:
    def __init__(self, account: Optional[dict[str, Any]] = None) -> None:
        self.account = account
        self.account_name = account["name"] if account else "Environment Amazon Business"
        self.base_url = normalize_amazon_endpoint(
            account["api_base_url"] if account else os.getenv("AMAZON_API_BASE_URL", "https://na.business-api.amazon.com")
        )
        self.tracking_base_url = normalize_amazon_endpoint(
            account["tracking_api_base_url"] if account else os.getenv("AMAZON_TRACKING_API_BASE_URL", "https://na.business-api.amazon.com")
        )
        self.buyer_email = str(account["buyer_email"] if account and "buyer_email" in account.keys() and account["buyer_email"] else os.getenv("AMAZON_BUYER_EMAIL", "")).strip()
        self.buying_group_id = str(account["buying_group_id"] if account and "buying_group_id" in account.keys() and account["buying_group_id"] else os.getenv("AMAZON_BUYING_GROUP_ID", "")).strip()
        self.product_region = str(account["product_region"] if account and "product_region" in account.keys() and account["product_region"] else os.getenv("AMAZON_PRODUCT_REGION", "US")).strip() or "US"
        self.locale = str(account["locale"] if account and "locale" in account.keys() and account["locale"] else os.getenv("AMAZON_LOCALE", "en_US")).strip() or "en_US"
        self.dry_run = False
        self.product_search_path = os.getenv("AMAZON_PRODUCT_SEARCH_PATH", "/products/2020-08-26/products/getProductsByAsins")
        self.order_create_path = os.getenv("AMAZON_ORDER_CREATE_PATH", "/ordering/2022-10-30/orders")
        self.order_details_path = os.getenv("AMAZON_ORDER_DETAILS_PATH", "/ordering/2022-10-30/orders/{amazon_order_id}")

    @property
    def is_sandbox(self) -> bool:
        return "sandbox." in self.base_url or "sandbox." in self.tracking_base_url

    def _token(self) -> str:
        static_token = (self.account["api_access_token"] if self.account else os.getenv("AMAZON_API_ACCESS_TOKEN", "")).strip()
        if static_token:
            return static_token
        refresh_token = (self.account["lwa_refresh_token"] if self.account else os.getenv("AMAZON_LWA_REFRESH_TOKEN", "")).strip()
        client_id = (self.account["lwa_client_id"] if self.account else os.getenv("AMAZON_LWA_CLIENT_ID", "")).strip()
        client_secret = (self.account["lwa_client_secret"] if self.account else os.getenv("AMAZON_LWA_CLIENT_SECRET", "")).strip()
        if not all([refresh_token, client_id, client_secret]):
            raise RuntimeError(f"Amazon API token or LWA refresh credentials are missing for {self.account_name}")
        res = requests.post(
            (self.account["lwa_token_url"] if self.account else os.getenv("AMAZON_LWA_TOKEN_URL", "https://api.amazon.com/auth/o2/token")),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=30,
        )
        if res.status_code >= 400:
            try:
                body = res.json()
            except Exception:
                body = {"text": res.text[:300]}
            error = body.get("error") or f"HTTP {res.status_code}"
            description = body.get("error_description") or body.get("text") or "Amazon LWA token request failed"
            raise RuntimeError(f"Amazon LWA token failed for {self.account_name}: {error} - {description}")
        return str(res.json()["access_token"])

    def _request(self, method: str, path: str, payload: Optional[dict[str, Any]] = None, base_url: Optional[str] = None) -> dict[str, Any]:
        resolved_base_url = (base_url or self.base_url).rstrip("/")
        headers = {
            "x-amz-access-token": self._token(),
            "x-amz-date": amz_date(),
            "user-agent": "Nutricity Amazon Business Fulfilment/1.0 (Language=Python)",
            "Content-Type": "application/json",
        }
        if self.buyer_email:
            headers["x-amz-user-email"] = self.buyer_email
        res = requests.request(method, f"{resolved_base_url}{path}", headers=headers, json=payload, timeout=60)
        try:
            body = res.json()
        except Exception:
            body = {"text": res.text}
        if res.status_code >= 400:
            raise RuntimeError(f"Amazon API failed: HTTP {res.status_code}: {body}")
        return body

    def search_asin(self, asin: str) -> dict[str, Any]:
        if not self.buyer_email:
            raise RuntimeError("Amazon buyer email is missing. Edit the Amazon account and set the buyer email used for Amazon Business API requests.")
        payload = {
            "productIds": [asin],
            "productRegion": self.product_region,
            "locale": self.locale,
        }
        return self._request("POST", self.product_search_path, payload)

    def verify_asin_for_order(self, asin: str) -> dict[str, Any]:
        if self.is_sandbox:
            return {"sandbox": True, "skippedProductSearch": True, "asin": asin}
        return self.search_asin(asin)

    def create_order(self, external_id: str, asin: str, quantity: float, odoo_order_name: str, address: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._order_payload(external_id, asin, quantity, odoo_order_name, address)
        return payload, self._request("POST", self.order_create_path, payload)

    def create_order_lines(self, external_id: str, items: list[dict[str, Any]], order_names: list[str], address: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._order_payload_multi(external_id, items, order_names, address)
        return payload, self._request("POST", self.order_create_path, payload)

    def order_details(self, amazon_order_id: str) -> dict[str, Any]:
        path = self.order_details_path.format(amazon_order_id=amazon_order_id)
        return self._request("GET", path)

    def package_tracking(self, order_id: str, shipment_id: str, package_id: str) -> dict[str, Any]:
        path_template = os.getenv(
            "AMAZON_PACKAGE_TRACKING_PATH",
            "/ab-tracking/2025-07-02/orders/{orderId}/shipments/{shipmentId}/packages/{packageId}",
        )
        path = path_template.format(orderId=order_id, shipmentId=shipment_id, packageId=package_id)
        return self._request("GET", path, base_url=self.tracking_base_url)

    def _recipient_name(self, order_names: Union[list[str], str]) -> str:
        if isinstance(order_names, str):
            names = [order_names]
        else:
            names = []
            for name in order_names:
                clean = str(name or "").strip()
                if clean and clean not in names:
                    names.append(clean)
        return f"Nutricity {' '.join(names)}".strip()

    def _address_payload(self, odoo_order_name: Union[str, list[str]], address: dict[str, Any]) -> dict[str, Any]:
        physical_address = {
            "addressType": "PhysicalAddress",
            "fullName": self._recipient_name(odoo_order_name),
            "companyName": address["company_name"] or "Nutricity",
            "phoneNumber": address["phone_number"] or "",
            "addressLine1": address["address_line1"],
            "city": address["city"],
            "stateOrRegion": address["state_or_region"] or "",
            "postalCode": address["postal_code"],
            "countryCode": address["country_code"],
        }
        if address["address_line2"]:
            physical_address["addressLine2"] = address["address_line2"]
        if address["address_line3"]:
            physical_address["addressLine3"] = address["address_line3"]
        return physical_address

    def _order_payload(self, external_id: str, asin: str, quantity: float, odoo_order_name: str, address: dict[str, Any]) -> dict[str, Any]:
        return self._order_payload_multi(
            external_id,
            [{"asin": asin, "quantity": quantity, "line_id": "1"}],
            [odoo_order_name],
            address,
        )

    def _order_payload_multi(self, external_id: str, items: list[dict[str, Any]], order_names: list[str], address: dict[str, Any]) -> dict[str, Any]:
        if not self.buyer_email:
            raise RuntimeError("Amazon buyer email is missing. Edit the Amazon account and set the buyer email used for Ordering API.")
        purchase_order_number = " ".join(dict.fromkeys(str(name or "").strip() for name in order_names if str(name or "").strip()))
        line_items: list[dict[str, Any]] = []
        for idx, item in enumerate(items, start=1):
            quantity = float(item.get("quantity") or 1)
            asin = str(item.get("asin") or "").strip().upper()
            line_items.append(
                {
                    "externalId": f"{external_id}-{item.get('line_id') or idx}",
                    "quantity": int(quantity) if quantity.is_integer() else quantity,
                    "attributes": [
                        {
                            "attributeType": "SelectedProductReference",
                            "productReference": {
                                "productReferenceType": "ProductIdentifier",
                                "id": asin,
                            },
                        }
                    ],
                    "expectations": [],
                }
            )
        payload = {
            "externalId": external_id,
            "attributes": [
                {
                    "attributeType": "PurchaseOrderNumber",
                    "purchaseOrderNumber": purchase_order_number,
                },
                {
                    "attributeType": "ShippingAddress",
                    "address": self._address_payload(order_names, address),
                },
                {
                    "attributeType": "BuyerReference",
                    "userReference": {
                        "userReferenceType": "UserEmail",
                        "emailAddress": self.buyer_email,
                    },
                },
                {
                    "attributeType": "Region",
                    "region": self.product_region,
                },
                {
                    "attributeType": "SelectedPaymentMethodReference",
                    "paymentMethodReference": {
                        "paymentMethodReferenceType": "StoredPaymentMethod",
                    },
                },
            ],
            "lineItems": line_items,
            "expectations": [],
        }
        if self.buying_group_id:
            payload["attributes"].append(
                {
                    "attributeType": "BuyingGroupReference",
                    "groupReference": {
                        "groupReferenceType": "GroupIdentity",
                        "identifier": self.buying_group_id,
                    },
                }
            )
        return payload


def get_store(store_id: int) -> Store:
    now = time.monotonic()
    with _STORE_CACHE_LOCK:
        cached = _STORE_CACHE.get(int(store_id))
    if cached and cached[1] > now:
        return cached[0]
    with db() as conn:
        row = conn.execute("SELECT * FROM stores WHERE id = ?", (store_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Store not found")
    store = Store(
        id=row["id"],
        name=row["name"],
        odoo_url=row["odoo_url"],
        odoo_db=row["odoo_db"],
        odoo_user=row["odoo_user"],
        odoo_password=row["odoo_password"],
        website_id=row["website_id"],
    )
    with _STORE_CACHE_LOCK:
        _STORE_CACHE[int(store_id)] = (store, now + 60)
    return store


def list_stores() -> list[dict[str, Any]]:
    with db() as conn:
        return conn.execute("SELECT * FROM stores ORDER BY name").fetchall()


def row_to_dict(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def rows_to_dicts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


def pagination_bounds(page: int = 1, per_page: int = 100) -> tuple[int, int, int]:
    page = max(1, int(page or 1))
    per_page = max(1, min(100, int(per_page or 100)))
    return page, per_page, (page - 1) * per_page


def paginate_values(values: list[Any], page: int = 1, per_page: int = 100) -> tuple[list[Any], int, int, int]:
    page, per_page, offset = pagination_bounds(page, per_page)
    return values[offset:offset + per_page], len(values), page, per_page


def odoo_order_admin_url(store: dict[str, Any], order_id: Any) -> str:
    base = str(store["odoo_url"] or "").rstrip("/")
    if not base or not order_id:
        return ""
    return f"{base}/web#id={order_id}&model=sale.order&view_type=form"


def order_line_amazon_url(order_id: str) -> str:
    order_id = clean_text(order_id)
    return f"https://www.amazon.com/your-orders/order-details?orderID={quote_plus(order_id)}" if order_id else ""


def asin_product_url(asin: str) -> str:
    asin = normalize_asin(asin)
    return f"https://www.amazon.com/dp/{quote_plus(asin)}" if asin else ""


def list_addresses() -> list[dict[str, Any]]:
    with db() as conn:
        return conn.execute("SELECT * FROM fulfilment_addresses ORDER BY is_default DESC, label").fetchall()


def list_amazon_accounts() -> list[dict[str, Any]]:
    with db() as conn:
        return conn.execute("SELECT * FROM amazon_accounts ORDER BY is_default DESC, name").fetchall()


def list_punchout_return_urls() -> list[dict[str, Any]]:
    with db() as conn:
        return conn.execute("SELECT * FROM punchout_return_urls ORDER BY is_default DESC, label").fetchall()


def get_default_punchout_return_url() -> str:
    with db() as conn:
        row = conn.execute("SELECT url FROM punchout_return_urls WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
        if not row:
            row = conn.execute("SELECT url FROM punchout_return_urls ORDER BY id LIMIT 1").fetchone()
    return str(row["url"]) if row else ""


def list_inventory_items(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> tuple[list[dict[str, Any]], int]:
    page = max(1, int(page or 1))
    per_page = max(1, min(100, int(per_page or 100)))
    offset = (page - 1) * per_page
    with db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS count FROM inventory_items WHERE (? IS NULL OR store_id=?)",
            (store_id, store_id),
        ).fetchone()["count"]
        if store_id:
            rows = conn.execute(
                "SELECT * FROM inventory_items WHERE store_id=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (store_id, per_page, offset),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM inventory_items ORDER BY updated_at DESC LIMIT ? OFFSET ?", (per_page, offset)).fetchall()
    return rows, int(total)


def available_inventory_quantity(store_id: int, asin: str) -> float:
    asin = normalize_asin(asin)
    if not asin:
        return 0
    with db() as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS quantity
            FROM inventory_items
            WHERE store_id=? AND asin=? AND status='available'
            """,
            (store_id, asin),
        ).fetchone()
    return float(row["quantity"] or 0)


def reserve_inventory_for_line(line: dict[str, Any]) -> bool:
    asin = normalize_asin(line["asin"])
    quantity_needed = float(line["quantity"] or 1)
    if not asin or quantity_needed <= 0:
        return False
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM inventory_items
            WHERE store_id=? AND asin=? AND status='available'
            ORDER BY updated_at ASC, id ASC
            """,
            (line["store_id"], asin),
        ).fetchall()
        remaining = quantity_needed
        selected: list[dict[str, Any]] = []
        for item in rows:
            selected.append(item)
            remaining -= float(item["quantity"] or 0)
            if remaining <= 0:
                break
        if remaining > 0:
            return False
        for item in selected:
            conn.execute(
                """
                UPDATE inventory_items
                SET status='reserved', reserved_order_line_id=?, reserved_at=?, updated_at=?
                WHERE id=?
                """,
                (line["id"], utc_now(), utc_now(), item["id"]),
            )
        conn.execute(
            """
            UPDATE order_lines
            SET state='inventory',
                fulfilment_note=?,
                last_error=NULL,
                updated_at=?
            WHERE id=?
            """,
            (
                f"Fulfil from local inventory: ASIN {asin}, qty {quantity_needed:g}. Do not auto-buy in Chrome.",
                utc_now(),
                line["id"],
            ),
        )
    return True


def list_missing_order_lines(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> tuple[list[dict[str, Any]], int]:
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        total = int(conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM order_lines
            WHERE state='missing'
              AND (? IS NULL OR store_id=?)
            """,
            (store_id, store_id),
        ).fetchone()["count"] or 0)
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE state='missing'
              AND (? IS NULL OR store_id=?)
            ORDER BY updated_at DESC, odoo_order_id DESC, id ASC
            LIMIT ? OFFSET ?
            """,
            (store_id, store_id, per_page, offset),
        ).fetchall()
    return rows, total


def list_costly_order_lines(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> tuple[list[dict[str, Any]], int]:
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        total = int(conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM order_lines
            WHERE state='costly'
              AND (? IS NULL OR store_id=?)
            """,
            (store_id, store_id),
        ).fetchone()["count"] or 0)
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE state='costly'
              AND (? IS NULL OR store_id=?)
            ORDER BY updated_at DESC, odoo_order_id DESC, id ASC
            LIMIT ? OFFSET ?
            """,
            (store_id, store_id, per_page, offset),
        ).fetchall()
    return rows, total


def bulk_opportunity_groups(store_id: Optional[int] = None, days: int = 2) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE (? IS NULL OR store_id=?)
              AND COALESCE(asin, '') != ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state='pulled'
              AND datetime(COALESCE(pulled_at, created_at)) >= datetime('now', ?)
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
            ORDER BY asin, odoo_order_name
            """,
            (store_id, store_id, f"-{int(days)} days"),
        ).fetchall()
        missing_order_ids = {
            int(row["odoo_order_id"])
            for row in conn.execute(
                """
                SELECT DISTINCT odoo_order_id
                FROM order_lines
                WHERE state='missing'
                  AND (? IS NULL OR store_id=?)
                """,
                (store_id, store_id),
            ).fetchall()
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["asin"]), []).append(row)
    opportunities: list[dict[str, Any]] = []
    for asin, asin_rows in grouped.items():
        order_ids = sorted({int(row["odoo_order_id"]) for row in asin_rows})
        if len(order_ids) < 2:
            continue
        opportunities.append(
            {
                "asin": asin,
                "asin_url": asin_product_url(asin),
                "quantity": sum(float(row["quantity"] or 0) for row in asin_rows),
                "order_names": list(dict.fromkeys(str(row["odoo_order_name"]) for row in asin_rows)),
                "line_ids": [int(row["id"]) for row in asin_rows],
                "product_names": list(dict.fromkeys(str(row["product_name"] or "") for row in asin_rows))[:5],
                "has_missing_order": any(order_id in missing_order_ids for order_id in order_ids),
            }
        )
    return sorted(opportunities, key=lambda item: (-float(item["quantity"]), item["asin"]))


def duplicate_asin_groups(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> tuple[list[dict[str, Any]], int, int, int]:
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM (
                SELECT asin
                FROM order_lines
                WHERE (? IS NULL OR store_id=?)
                  AND COALESCE(asin, '') != ''
                  AND COALESCE(amazon_order_id, '') = ''
                  AND state = 'pulled'
                  AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
                GROUP BY asin
                HAVING COUNT(DISTINCT odoo_order_name) > 1
            ) AS duplicate_groups
            """,
            (store_id, store_id),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT asin,
                   COUNT(*) AS line_count,
                   COUNT(DISTINCT odoo_order_name) AS order_count,
                   SUM(quantity) AS total_quantity,
                   STRING_AGG(DISTINCT odoo_order_name, ',') AS orders
            FROM order_lines
            WHERE (? IS NULL OR store_id=?)
              AND COALESCE(asin, '') != ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state = 'pulled'
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
            GROUP BY asin
            HAVING COUNT(DISTINCT odoo_order_name) > 1
            ORDER BY order_count DESC, total_quantity DESC, asin
            LIMIT ? OFFSET ?
            """,
            (store_id, store_id, per_page, offset),
        ).fetchall()
    return rows_to_dicts(rows), int(total_row["count"] or 0), page, per_page


def consolidate_existing_asin_lines(store_id: Optional[int] = None) -> None:
    with db() as conn:
        groups = conn.execute(
            """
            SELECT store_id, odoo_order_id, asin, COUNT(*) AS row_count
            FROM order_lines
            WHERE (? IS NULL OR store_id = ?)
              AND COALESCE(asin, '') != ''
              AND COALESCE(amazon_order_id, '') = ''
            GROUP BY store_id, odoo_order_id, asin
            HAVING COUNT(*) > 1
            """,
            (store_id, store_id),
        ).fetchall()
        for group in groups:
            rows = conn.execute(
                """
                SELECT *
                FROM order_lines
                WHERE store_id = ? AND odoo_order_id = ? AND asin = ?
                  AND COALESCE(amazon_order_id, '') = ''
                ORDER BY id
                """,
                (group["store_id"], group["odoo_order_id"], group["asin"]),
            ).fetchall()
            if len(rows) < 2:
                continue
            keep = rows[0]
            remove_ids = [row["id"] for row in rows[1:]]
            quantity = sum(float(row["quantity"] or 0) for row in rows)
            source_line_ids: list[int] = []
            for row in rows:
                if "source_odoo_line_ids" in row.keys() and row["source_odoo_line_ids"]:
                    try:
                        source_line_ids.extend(int(value) for value in json.loads(row["source_odoo_line_ids"]))
                    except Exception:
                        source_line_ids.append(int(row["odoo_line_id"]))
                else:
                    source_line_ids.append(int(row["odoo_line_id"]))
            source_line_ids = sorted(set(source_line_ids))
            conn.execute(
                """
                UPDATE order_lines
                SET quantity = ?, source_odoo_line_ids = ?, source_line_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (quantity, json.dumps(source_line_ids), len(source_line_ids), utc_now(), keep["id"]),
            )
            conn.execute(
                f"DELETE FROM order_lines WHERE id IN ({','.join('?' for _ in remove_ids)})",
                remove_ids,
            )


def clear_dry_run_order_markers(store_id: Optional[int] = None) -> int:
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE order_lines
            SET amazon_order_id = NULL,
                amazon_order_url = NULL,
                amazon_status = NULL,
                state = 'pulled',
                last_error = NULL,
                ordered_at = NULL,
                updated_at = ?
            WHERE (? IS NULL OR store_id = ?)
              AND COALESCE(amazon_order_id, '') LIKE 'DRYRUN-%'
            """,
            (utc_now(), store_id, store_id),
        )
        return cursor.rowcount


def selected_line_reasons(store_id: int, line_ids: Optional[list[int]] = None) -> list[str]:
    params: list[Any] = [store_id]
    line_filter = ""
    if line_ids:
        line_filter = f" AND id IN ({','.join('?' for _ in line_ids)})"
        params.extend(line_ids)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT id, odoo_order_name, asin, state, amazon_order_id, odoo_status_label, order_engine,
                   supplier_part_auxiliary_id, last_error
            FROM order_lines
            WHERE store_id = ?
            {line_filter}
            ORDER BY odoo_order_name, id
            """,
            params,
        ).fetchall()
    reasons = []
    for row in rows:
        prefix = f"{row['odoo_order_name']} {row['asin'] or ''}".strip()
        if row["last_error"]:
            reasons.append(f"{prefix}: {clean_error_message(row['last_error'])}")
        elif row["state"] == "submitted" and row["amazon_order_id"]:
            reasons.append(f"{prefix}: cXML request submitted to Amazon; confirmation pending ({row['amazon_order_id']})")
        elif row["amazon_order_id"]:
            reasons.append(f"{prefix}: already has Amazon order {row['amazon_order_id']}")
        elif row["state"] == "submitted" and row["order_engine"] == "chrome":
            reasons.append(f"{prefix}: queued for Chrome extension ordering")
        elif row["order_engine"] == "cxml" and not row["supplier_part_auxiliary_id"]:
            reasons.append(f"{prefix}: missing SupplierPartAuxiliaryID from Amazon Punchout cart")
        elif row["state"] in {"ordered", "delivered", "dispatched"}:
            reasons.append(f"{prefix}: already marked {row['state']}")
        elif row["odoo_status_label"] in {"cancelled", "refunded"}:
            reasons.append(f"{prefix}: Odoo order is {row['odoo_status_label']}")
        elif not row["asin"]:
            reasons.append(f"{prefix}: missing valid ASIN")
    return reasons[:8]


def clean_error_message(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return ""
    host_match = re.search(r"host='([^']+)'", text)
    host = host_match.group(1) if host_match else "Amazon API"
    url_match = re.search(r"url:\s*([^\s]+)", text)
    path = f" ({url_match.group(1)})" if url_match else ""
    if "nodename nor servname provided" in text or "NameResolutionError" in text or "Failed to establish a new connection" in text:
        return (
            f"Amazon API connection failed for {host}{path}. "
            "Check the Amazon account API Base URL/region endpoint and your internet/DNS connection."
        )
    if "Amazon cXML failed" in text:
        if "HTTP 401" in text or "Unauthorized" in text:
            auth_match = re.search(r"Auth mode:\s*([a-z]+)", text, re.IGNORECASE)
            auth_detail = f" Request used cXML auth mode: {auth_match.group(1).lower()}." if auth_match else ""
            cart_detail = " No Punchout Cart/Session ID is saved." if "add the Punchout Cart/Session ID" in text else ""
            return (
                "Amazon cXML Purchase Order endpoint returned HTTP 401 Unauthorized. "
                "Check the cXML From Identity, Shared Secret, and Purchase Order Request URL. "
                f"{auth_detail}{cart_detail} "
                "Try cXML Header Only auth first; use HTTP Basic or Both only if Amazon enabled that for this group. "
                "If Amazon requires punchout-context ordering, add the Punchout Cart/Session ID from the Amazon punchout cart."
            )
        return text[:700]
    if "Unauthorized" in text or "Access to requested resource is denied" in text:
        return (
            "Amazon denied access to this API resource. Check that this Amazon app is authorized for the "
            "Business Product Catalog role for ASIN lookup and AmazonBusinessOrderPlacement for ordering, "
            "and that the selected account is using the correct sandbox/production endpoint and buyer email."
        )
    if "Could not match input arguments" in text:
        return (
            "Amazon static sandbox could not match this request. Amazon Business sandbox is pattern-based and "
            "does not accept arbitrary real Odoo order payloads for Ordering API order placement. Use a production "
            "Amazon Business app/account with AmazonBusinessOrderPlacement enabled for real orders."
        )
    return text[:700]


def fetch_amazon_product_title(asin: str) -> str:
    asin = normalize_asin(asin)
    if not asin:
        return ""
    try:
        response = requests.get(
            asin_product_url(asin),
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"},
            timeout=12,
        )
        text = response.text or ""
        for pattern in (
            r'<span[^>]+id=["\']productTitle["\'][^>]*>(.*?)</span>',
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
            r"<title>(.*?)</title>",
        ):
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                title = strip_html(match.group(1)).replace("Amazon.com:", "").strip()
                if title:
                    return title[:500]
    except Exception:
        return ""
    return ""


def mark_chrome_group_missing(group_key: str, message: str, missing_asin: str = "", missing_line_id: Optional[int] = None) -> int:
    missing_asin = normalize_asin(missing_asin)
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_lines WHERE amazon_group_key=? AND order_engine='chrome'",
            (group_key,),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            is_missing_line = (missing_line_id and int(row["id"]) == int(missing_line_id)) or (missing_asin and str(row["asin"]).upper() == missing_asin)
            line_message = message if is_missing_line else f"Skipped because ASIN {missing_asin or 'in this order'} is missing from the Amazon fulfilment group."
            conn.execute(
                """
                UPDATE order_lines
                SET state='missing',
                    amazon_status='missing',
                    last_error=?,
                    missing_asin=?,
                    updated_at=?
                WHERE id=?
                """,
                (line_message, missing_asin if is_missing_line else "", utc_now(), row["id"]),
            )
        conn.execute(
            """
            UPDATE amazon_attempts
            SET status='missing', error=?
            WHERE external_id=? AND mode='chrome'
            """,
            (message, group_key),
        )
    return len(rows)


def mark_chrome_group_costly(
    group_key: str,
    message: str,
    costly_asin: str = "",
    costly_line_id: Optional[int] = None,
    store_total_price: float = 0,
    amazon_total_price: float = 0,
) -> int:
    costly_asin = normalize_asin(costly_asin)
    loss = max(0.0, float(amazon_total_price or 0) - float(store_total_price or 0))
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_lines WHERE amazon_group_key=? AND order_engine='chrome'",
            (group_key,),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            is_costly_line = (costly_line_id and int(row["id"]) == int(costly_line_id)) or (costly_asin and str(row["asin"]).upper() == costly_asin)
            line_message = message if is_costly_line else f"Skipped because ASIN {costly_asin or 'in this order'} needs costly fulfilment approval."
            conn.execute(
                """
                UPDATE order_lines
                SET state='costly',
                    amazon_status='cost_review',
                    last_error=?,
                    cost_review_loss=?,
                    updated_at=?
                WHERE id=?
                """,
                (line_message, loss if is_costly_line else None, utc_now(), row["id"]),
            )
        conn.execute(
            """
            UPDATE amazon_attempts
            SET status='costly', error=?
            WHERE external_id=? AND mode='chrome'
            """,
            (message, group_key),
        )
    return len(rows)


def get_amazon_account(account_id: Optional[int] = None) -> dict[str, Any]:
    with db() as conn:
        row = None
        if account_id:
            row = conn.execute("SELECT * FROM amazon_accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM amazon_accounts WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        if not row:
            row = conn.execute("SELECT * FROM amazon_accounts ORDER BY id LIMIT 1").fetchone()
    if not row:
        raise HTTPException(400, "Add an Amazon Business account before placing orders.")
    return row


def get_address(address_id: Optional[int] = None) -> dict[str, Any]:
    with db() as conn:
        row = None
        if address_id:
            row = conn.execute("SELECT * FROM fulfilment_addresses WHERE id = ?", (address_id,)).fetchone()
        if not row:
            row = conn.execute("SELECT * FROM fulfilment_addresses WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        if not row:
            row = conn.execute("SELECT * FROM fulfilment_addresses ORDER BY id LIMIT 1").fetchone()
    if not row:
        raise HTTPException(400, "Add a fulfilment address before placing Amazon orders.")
    return row


def should_skip_order_line(line: dict[str, Any], product: dict[str, Any], tmpl: dict[str, Any]) -> bool:
    name = str(line.get("name") or "")
    default_code = str(product.get("default_code") or tmpl.get("default_code") or "")
    display_type = str(line.get("display_type") or "")
    detailed_type = str(product.get("detailed_type") or tmpl.get("detailed_type") or product.get("type") or tmpl.get("type") or "")
    price_unit = float(line.get("price_unit") or 0)
    if display_type:
        return True
    if detailed_type in {"service"}:
        return True
    if price_unit < 0:
        return True
    if SKIP_LINE_NAME_RE.search(name) or SKIP_LINE_NAME_RE.search(default_code):
        return True
    return False


def relational_name(value: Any) -> str:
    if isinstance(value, (list, tuple)) and len(value) > 1:
        return clean_text(value[1])
    if isinstance(value, str):
        return clean_text(value)
    return ""


def order_currency_code(order: dict[str, Any]) -> str:
    currency = relational_name(order.get("currency_id")).upper()
    return currency or "USD"


def line_native_total(line: dict[str, Any]) -> float:
    for key in ("price_subtotal", "price_total"):
        if line.get(key) is not None:
            return float(line.get(key) or 0)
    return float(line.get("product_uom_qty") or 1) * float(line.get("price_unit") or 0)


def is_delivery_line(line: dict[str, Any], default_code: str) -> bool:
    name = clean_text(line.get("name"))
    code = clean_text(default_code)
    return bool(DELIVERY_REFERENCE_RE.search(code) or DELIVERY_REFERENCE_RE.search(name))


def is_discount_line(line: dict[str, Any], default_code: str) -> bool:
    return bool(DISCOUNT_LINE_RE.search(clean_text(line.get("name"))) or DISCOUNT_LINE_RE.search(clean_text(default_code)))


def is_order_adjustment_line(line: dict[str, Any], product: dict[str, Any], tmpl: dict[str, Any]) -> bool:
    default_code = str(product.get("default_code") or tmpl.get("default_code") or "")
    return is_delivery_line(line, default_code) or is_discount_line(line, default_code) or line_native_total(line) < 0


def adjustment_line_kind(line: dict[str, Any], product: dict[str, Any], tmpl: dict[str, Any]) -> str:
    default_code = str(product.get("default_code") or tmpl.get("default_code") or "")
    if is_delivery_line(line, default_code):
        return "delivery"
    if is_discount_line(line, default_code) or line_native_total(line) < 0:
        return "discount"
    return "adjustment"


def conversion_rate_to_usd(currency: str, settings: Optional[dict[str, str]] = None) -> float:
    code = clean_text(currency).upper() or "USD"
    if code == "USD":
        return 1.0
    settings = settings or get_service_settings()
    rates = sync_openexchange_rates_if_due(settings)
    source_rate = float(rates.get(code) or 0)
    usd_rate = float(rates.get("USD") or 1)
    if source_rate <= 0:
        raise RuntimeError(f"OpenExchange rate for {code} is not available.")
    return usd_rate / source_rate


def convert_to_usd(amount: float, currency: str, settings: Optional[dict[str, str]] = None) -> float:
    return float(amount or 0) * conversion_rate_to_usd(currency, settings)


def safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def cached_openexchange_rates() -> dict[str, float]:
    try:
        payload = json.loads(get_setting("openexchange_rates_json", "{}") or "{}")
        rates = payload.get("rates") if isinstance(payload, dict) else {}
        if isinstance(rates, dict):
            return {str(key).upper(): float(value) for key, value in rates.items() if value}
    except Exception:
        pass
    return {"USD": 1.0}


def sync_openexchange_rates_if_due(settings: Optional[dict[str, str]] = None, force: bool = False) -> dict[str, float]:
    settings = settings or get_service_settings()
    interval = max(0, int(float(settings.get("openexchange_sync_interval_minutes") or 2880)))
    last_sync = parse_iso_date(get_setting("openexchange_last_sync_at", ""))
    if not force and last_sync and interval and datetime.now(timezone.utc) - last_sync < timedelta(minutes=interval):
        return cached_openexchange_rates()
    api_key = clean_text(settings.get("openexchange_api_key"))
    if not api_key:
        rates = cached_openexchange_rates()
        if rates:
            return rates
        raise RuntimeError("OpenExchange API key is missing.")
    try:
        response = requests.get(
            "https://openexchangerates.org/api/latest.json",
            params={"app_id": api_key},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        rates = payload.get("rates") or {}
        if not isinstance(rates, dict) or "USD" not in rates:
            raise RuntimeError("OpenExchange response did not include USD rates.")
        set_setting("openexchange_rates_json", json.dumps({"base": payload.get("base") or "USD", "rates": rates, "timestamp": payload.get("timestamp")}, default=str))
        set_setting("openexchange_last_sync_at", utc_now())
        set_setting("openexchange_last_sync_message", f"Synced {len(rates)} currency rates.")
        return {str(key).upper(): float(value) for key, value in rates.items() if value}
    except Exception as exc:
        set_setting("openexchange_last_sync_at", utc_now())
        set_setting("openexchange_last_sync_message", f"Sync failed: {str(exc)[:300]}")
        rates = cached_openexchange_rates()
        if rates:
            return rates
        raise


def derive_odoo_status(order: dict[str, Any], invoice_rows: list[dict[str, Any]]) -> str:
    state = str(order.get("state") or "").strip().lower()
    invoice_status = str(order.get("invoice_status") or "").strip().lower()
    if state == "cancel":
        return "cancelled"
    for invoice in invoice_rows:
        if invoice.get("state") != "cancel" and str(invoice.get("move_type") or "") in {"out_refund", "in_refund"}:
            return "refunded"
    if invoice_status:
        return invoice_status
    return state or "unknown"


def save_combined_order_line(
    store: Store,
    order: dict[str, Any],
    combined: dict[str, Any],
    status_label: str,
    *,
    post_process: bool = True,
    conn: Any = None,
    existing_rows: Optional[list[dict[str, Any]]] = None,
    delete_duplicates: bool = True,
) -> bool:
    source_line_ids = sorted(int(line_id) for line_id in combined["line_ids"])
    if not source_line_ids:
        return False
    canonical_line_id = source_line_ids[0]
    conn_context = db() if conn is None else nullcontext(conn)
    with conn_context as conn:
        if existing_rows is None:
            existing_rows = conn.execute(
                f"""
                SELECT id, odoo_line_id, amazon_order_id, amazon_order_url, amazon_account_id, amazon_account_name,
                       amazon_group_key, amazon_status, tracking_status, tracking_payload, tracking_checked_at, state, last_error,
                       pulled_at, ordered_at
                FROM order_lines
                WHERE store_id = ? AND odoo_line_id IN ({",".join("?" for _ in source_line_ids)})
                ORDER BY COALESCE(amazon_order_id, '') DESC, id ASC
                """,
                [store.id, *source_line_ids],
            ).fetchall()
        preserved = next((row for row in existing_rows if row["amazon_order_id"]), existing_rows[0] if existing_rows else None)
        if delete_duplicates:
            conn.execute(
                f"""
                DELETE FROM order_lines
                WHERE store_id = ? AND odoo_line_id IN ({",".join("?" for _ in source_line_ids)})
                  AND odoo_line_id != ?
                """,
                [store.id, *source_line_ids, canonical_line_id],
            )
        raw = {
            "order": order,
            "combined_source_lines": combined["raw_lines"],
            "order_adjustment_lines": combined.get("order_adjustment_lines") or [],
            "source_line_ids": source_line_ids,
        }
        conn.execute(
            """
            INSERT INTO order_lines
            (store_id, odoo_order_id, odoo_order_name, odoo_order_date, odoo_line_id, product_id, product_tmpl_id,
             product_name, default_code, internal_note, asin_from_reference, asin_from_note, asin,
             supplier_part_auxiliary_id,
             quantity, store_unit_price, store_total_price, store_currency, store_currency_rate_to_usd,
             store_subtotal_native, store_delivery_native, store_discount_native, store_adjustment_native, store_total_native,
             odoo_order_state, odoo_invoice_status, odoo_status_label, amazon_order_id,
             amazon_order_url, amazon_account_id, amazon_account_name, amazon_group_key, amazon_status,
             tracking_status, tracking_payload, tracking_checked_at, state, last_error, source_odoo_line_ids,
             source_line_count, raw_json, pulled_at, ordered_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id, odoo_line_id) DO UPDATE SET
                odoo_order_name=excluded.odoo_order_name,
                odoo_order_date=excluded.odoo_order_date,
                product_id=excluded.product_id,
                product_tmpl_id=excluded.product_tmpl_id,
                product_name=excluded.product_name,
                default_code=excluded.default_code,
                internal_note=excluded.internal_note,
                asin_from_reference=excluded.asin_from_reference,
                asin_from_note=excluded.asin_from_note,
                asin=excluded.asin,
                supplier_part_auxiliary_id=COALESCE(NULLIF(order_lines.supplier_part_auxiliary_id, ''), excluded.supplier_part_auxiliary_id),
                quantity=excluded.quantity,
                store_unit_price=excluded.store_unit_price,
                store_total_price=excluded.store_total_price,
                store_currency=excluded.store_currency,
                store_currency_rate_to_usd=excluded.store_currency_rate_to_usd,
                store_subtotal_native=excluded.store_subtotal_native,
                store_delivery_native=excluded.store_delivery_native,
                store_discount_native=excluded.store_discount_native,
                store_adjustment_native=excluded.store_adjustment_native,
                store_total_native=excluded.store_total_native,
                odoo_order_state=excluded.odoo_order_state,
                odoo_invoice_status=excluded.odoo_invoice_status,
                odoo_status_label=excluded.odoo_status_label,
                amazon_order_id=COALESCE(order_lines.amazon_order_id, excluded.amazon_order_id),
                amazon_order_url=COALESCE(order_lines.amazon_order_url, excluded.amazon_order_url),
                amazon_account_id=COALESCE(order_lines.amazon_account_id, excluded.amazon_account_id),
                amazon_account_name=COALESCE(order_lines.amazon_account_name, excluded.amazon_account_name),
                amazon_group_key=COALESCE(order_lines.amazon_group_key, excluded.amazon_group_key),
                amazon_status=COALESCE(order_lines.amazon_status, excluded.amazon_status),
                tracking_status=COALESCE(order_lines.tracking_status, excluded.tracking_status),
                tracking_payload=COALESCE(order_lines.tracking_payload, excluded.tracking_payload),
                tracking_checked_at=COALESCE(order_lines.tracking_checked_at, excluded.tracking_checked_at),
                state=CASE
                    WHEN COALESCE(order_lines.amazon_order_id, '') != '' THEN order_lines.state
                    ELSE excluded.state
                END,
                last_error=CASE
                    WHEN COALESCE(order_lines.amazon_order_id, '') != '' THEN order_lines.last_error
                    ELSE excluded.last_error
                END,
                source_odoo_line_ids=excluded.source_odoo_line_ids,
                source_line_count=excluded.source_line_count,
                raw_json=excluded.raw_json,
                pulled_at=COALESCE(order_lines.pulled_at, excluded.pulled_at),
                ordered_at=COALESCE(order_lines.ordered_at, excluded.ordered_at),
                updated_at=excluded.updated_at
            """,
            (
                store.id,
                order["id"],
                order["name"],
                order.get("date_order") or "",
                canonical_line_id,
                combined.get("product_id"),
                combined.get("product_tmpl_id"),
                combined.get("product_name") or "",
                combined.get("default_code") or "",
                combined.get("internal_note") or "",
                combined.get("asin_from_reference") or "",
                combined.get("asin_from_note") or "",
                combined["asin"],
                preserved["supplier_part_auxiliary_id"] if preserved and "supplier_part_auxiliary_id" in preserved.keys() else None,
                combined["quantity"],
                combined.get("store_unit_usd") or 0,
                combined.get("store_total_usd") or 0,
                combined.get("store_currency") or order_currency_code(order),
                combined.get("store_currency_rate_to_usd") or 1,
                combined.get("store_subtotal_native") or 0,
                combined.get("store_delivery_native") or 0,
                combined.get("store_discount_native") or 0,
                combined.get("store_adjustment_native") or 0,
                combined.get("store_total_native") or 0,
                order.get("state") or "",
                order.get("invoice_status") or "",
                status_label,
                preserved["amazon_order_id"] if preserved else None,
                preserved["amazon_order_url"] if preserved else None,
                preserved["amazon_account_id"] if preserved else None,
                preserved["amazon_account_name"] if preserved else None,
                preserved["amazon_group_key"] if preserved else None,
                preserved["amazon_status"] if preserved else None,
                preserved["tracking_status"] if preserved else None,
                preserved["tracking_payload"] if preserved else None,
                preserved["tracking_checked_at"] if preserved else None,
                preserved["state"] if preserved and preserved["amazon_order_id"] else "pulled",
                preserved["last_error"] if preserved and preserved["amazon_order_id"] else None,
                ",".join(str(line_id) for line_id in source_line_ids),
                len(source_line_ids),
                json.dumps(raw, default=str),
                preserved["pulled_at"] if preserved and "pulled_at" in preserved.keys() and preserved["pulled_at"] else utc_now(),
                preserved["ordered_at"] if preserved and "ordered_at" in preserved.keys() and preserved["ordered_at"] else None,
                utc_now(),
                utc_now(),
            ),
        )
        saved = None
        if post_process:
            saved = conn.execute(
                "SELECT * FROM order_lines WHERE store_id = ? AND odoo_line_id = ?",
                (store.id, canonical_line_id),
            ).fetchone()
    if post_process and saved:
        ensure_inventory_for_line(saved)
        index_order_line(saved)
    return True


def ensure_inventory_for_line(row: Union[dict[str, Any], dict[str, Any]]) -> None:
    status_label = str(row["odoo_status_label"] or "").lower()
    if status_label not in {"cancelled", "refunded"}:
        return
    if not row["amazon_order_id"]:
        return
    tracking_status = str(row["tracking_status"] or "").lower() if "tracking_status" in row.keys() else ""
    inventory_status = "available" if tracking_status == "delivered" or str(row["state"] or "") == "delivered" else "incoming"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO inventory_items
            (store_id, order_line_id, asin, quantity, product_name, source_odoo_order_id, source_odoo_order_name,
             amazon_order_id, amazon_order_url, amazon_account_name, status, source_type, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id, order_line_id) DO UPDATE SET
                asin=excluded.asin,
                quantity=excluded.quantity,
                product_name=excluded.product_name,
                source_odoo_order_id=excluded.source_odoo_order_id,
                source_odoo_order_name=excluded.source_odoo_order_name,
                amazon_order_id=excluded.amazon_order_id,
                amazon_order_url=excluded.amazon_order_url,
                amazon_account_name=excluded.amazon_account_name,
                status=excluded.status,
                source_type=excluded.source_type,
                notes=excluded.notes,
                updated_at=excluded.updated_at
            """,
            (
                row["store_id"],
                row["id"],
                row["asin"],
                row["quantity"],
                row["product_name"],
                row["odoo_order_id"],
                row["odoo_order_name"],
                row["amazon_order_id"],
                row["amazon_order_url"],
                row["amazon_account_name"],
                inventory_status,
                "amazon_cancelled",
                f"Odoo order is {row['odoo_status_label']}",
                utc_now(),
                utc_now(),
            ),
        )


def fetch_odoo_lines(store: Store, days: int, limit: int) -> int:
    odoo = OdooClient(store)
    domain: list[Any] = [("state", "in", ["sale", "done", "cancel"])]
    if store.website_id:
        domain.append(("website_id", "=", store.website_id))
    if days > 0:
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
        domain.append(("date_order", ">=", cutoff.strftime("%Y-%m-%d %H:%M:%S")))
    orders = odoo.search_read(
        "sale.order",
        domain,
        odoo.existing_fields("sale.order", ["id", "name", "note", "order_line", "state", "invoice_status", "invoice_ids", "currency_id", "date_order"]),
        limit=limit,
        order="date_order desc",
    )
    line_fields = odoo.existing_fields("sale.order.line", ["id", "name", "display_type", "product_id", "product_uom_qty", "price_unit", "price_subtotal", "price_total", "discount"])
    product_fields = odoo.existing_fields("product.product", ["id", "product_tmpl_id", "default_code", "detailed_type", "type"])
    tmpl_fields = odoo.existing_fields("product.template", ["id", "description", "default_code", "detailed_type", "type"])
    invoice_fields = odoo.existing_fields("account.move", ["id", "move_type", "state", "payment_state", "amount_total_signed"])
    all_line_ids = sorted({int(line_id) for order in orders for line_id in (order.get("order_line") or [])})
    all_invoice_ids = sorted({int(invoice_id) for order in orders for invoice_id in (order.get("invoice_ids") or [])})
    with ThreadPoolExecutor(max_workers=2) as executor:
        line_future = executor.submit(lambda: OdooClient(store).read("sale.order.line", all_line_ids, line_fields))
        invoice_future = executor.submit(lambda: OdooClient(store).read("account.move", all_invoice_ids, invoice_fields))
        all_lines = line_future.result()
        invoice_rows = invoice_future.result()
    lines_by_id = {int(line["id"]): line for line in all_lines}
    product_ids = sorted({int(line["product_id"][0]) for line in all_lines if line.get("product_id")})
    all_products = odoo.read("product.product", product_ids, product_fields)
    products = {int(product["id"]): product for product in all_products}
    tmpl_ids = sorted({int(product["product_tmpl_id"][0]) for product in all_products if product.get("product_tmpl_id")})
    templates = {int(template["id"]): template for template in odoo.read("product.template", tmpl_ids, tmpl_fields)}
    invoices = {int(invoice["id"]): invoice for invoice in invoice_rows}
    count = 0
    pending_saves: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    all_invalid_line_ids: set[int] = set()
    service_settings = get_service_settings()
    with db() as conn:
        for order in orders:
            currency = order_currency_code(order)
            rate_to_usd = conversion_rate_to_usd(currency, service_settings)
            line_ids = order.get("order_line") or []
            lines = [lines_by_id[int(line_id)] for line_id in line_ids if int(line_id) in lines_by_id]
            invoice_rows = [invoices[int(invoice_id)] for invoice_id in (order.get("invoice_ids") or []) if int(invoice_id) in invoices]
            status_label = derive_odoo_status(order, invoice_rows)
            combined_by_asin: dict[str, dict[str, Any]] = {}
            invalid_line_ids: list[int] = []
            adjustment_lines: list[dict[str, Any]] = []
            for line in lines:
                product_id = int(line["product_id"][0]) if line.get("product_id") else None
                product = products.get(product_id or 0, {})
                tmpl_id = int(product["product_tmpl_id"][0]) if product.get("product_tmpl_id") else None
                tmpl = templates.get(tmpl_id or 0, {})
                if should_skip_order_line(line, product, tmpl):
                    if is_order_adjustment_line(line, product, tmpl):
                        adjustment_lines.append({"line": line, "kind": adjustment_line_kind(line, product, tmpl)})
                    else:
                        invalid_line_ids.append(int(line["id"]))
                    continue
                if is_order_adjustment_line(line, product, tmpl):
                    adjustment_lines.append({"line": line, "kind": adjustment_line_kind(line, product, tmpl)})
                    invalid_line_ids.append(int(line["id"]))
                    continue
                default_code = product.get("default_code") or tmpl.get("default_code") or ""
                note = strip_html(tmpl.get("description") or "")
                asin_from_reference = decode_asin_reference(default_code)
                asin_from_note = extract_asin_from_notes(note, line.get("name") or "", order.get("note") or "")
                asin = asin_from_reference or asin_from_note
                if not normalize_asin(asin):
                    invalid_line_ids.append(int(line["id"]))
                    continue
                entry = combined_by_asin.setdefault(
                    asin,
                    {
                        "asin": asin,
                        "quantity": 0.0,
                        "line_ids": [],
                        "raw_lines": [],
                        "product_id": product_id,
                        "product_tmpl_id": tmpl_id,
                        "product_name": line.get("name") or "",
                        "default_code": default_code,
                        "internal_note": note,
                        "asin_from_reference": asin_from_reference,
                        "asin_from_note": asin_from_note,
                        "store_subtotal_native": 0.0,
                    },
                )
                quantity = float(line.get("product_uom_qty") or 1)
                native_total = line_native_total(line)
                entry["quantity"] += quantity
                entry["store_subtotal_native"] += native_total
                entry["line_ids"].append(int(line["id"]))
                entry["raw_lines"].append({"line": line, "product": product, "template": tmpl})
                if len(entry["line_ids"]) > 1 and line.get("name"):
                    entry["product_name"] = entry["product_name"] or line.get("name") or ""
            if invalid_line_ids:
                all_invalid_line_ids.update(invalid_line_ids)
            subtotal_native = sum(float(entry.get("store_subtotal_native") or 0) for entry in combined_by_asin.values())
            delivery_native = sum(line_native_total(item["line"]) for item in adjustment_lines if item.get("kind") == "delivery")
            discount_native = sum(line_native_total(item["line"]) for item in adjustment_lines if item.get("kind") == "discount")
            other_adjustment_native = sum(line_native_total(item["line"]) for item in adjustment_lines if item.get("kind") == "adjustment")
            adjustment_native = delivery_native + discount_native + other_adjustment_native
            for combined in combined_by_asin.values():
                line_subtotal_native = float(combined.get("store_subtotal_native") or 0)
                allocation_ratio = (line_subtotal_native / subtotal_native) if subtotal_native else 0.0
                allocated_delivery = delivery_native * allocation_ratio
                allocated_discount = discount_native * allocation_ratio
                allocated_adjustment = adjustment_native * allocation_ratio
                total_native = line_subtotal_native + allocated_adjustment
                combined["store_currency"] = currency
                combined["store_currency_rate_to_usd"] = rate_to_usd
                combined["store_delivery_native"] = allocated_delivery
                combined["store_discount_native"] = allocated_discount
                combined["store_adjustment_native"] = allocated_adjustment
                combined["store_total_native"] = total_native
                combined["store_total_usd"] = total_native * rate_to_usd
                combined["store_unit_usd"] = (combined["store_total_usd"] / float(combined.get("quantity") or 1)) if combined.get("quantity") else 0.0
                combined["order_adjustment_lines"] = adjustment_lines
                pending_saves.append((order, combined, status_label))
        if all_invalid_line_ids:
            invalid_ids = sorted(all_invalid_line_ids)
            conn.execute(
                f"DELETE FROM order_lines WHERE store_id = ? AND odoo_line_id IN ({','.join('?' for _ in invalid_ids)}) AND state IN ('pulled', 'error')",
                [store.id, *invalid_ids],
            )
        all_source_line_ids = sorted({
            int(line_id)
            for _, combined, _ in pending_saves
            for line_id in combined["line_ids"]
        })
        existing_by_line_id: dict[int, list[dict[str, Any]]] = {}
        if all_source_line_ids:
            existing_rows = conn.execute(
                f"""
                SELECT id, odoo_line_id, amazon_order_id, amazon_order_url, amazon_account_id, amazon_account_name,
                       amazon_group_key, amazon_status, tracking_status, tracking_payload, tracking_checked_at, state, last_error,
                       pulled_at, ordered_at
                FROM order_lines
                WHERE store_id = ? AND odoo_line_id IN ({','.join('?' for _ in all_source_line_ids)})
                ORDER BY COALESCE(amazon_order_id, '') DESC, id ASC
                """,
                [store.id, *all_source_line_ids],
            ).fetchall()
            for row in existing_rows:
                existing_by_line_id.setdefault(int(row["odoo_line_id"]), []).append(row)
        duplicate_line_ids = sorted({
            int(line_id)
            for _, combined, _ in pending_saves
            for line_id in sorted(int(value) for value in combined["line_ids"])[1:]
        })
        if duplicate_line_ids:
            conn.execute(
                f"DELETE FROM order_lines WHERE store_id = ? AND odoo_line_id IN ({','.join('?' for _ in duplicate_line_ids)})",
                [store.id, *duplicate_line_ids],
            )
        for order, combined, status_label in pending_saves:
            combined_existing_rows = [
                row
                for line_id in sorted(int(value) for value in combined["line_ids"])
                for row in existing_by_line_id.get(line_id, [])
            ]
            if save_combined_order_line(
                store,
                order,
                combined,
                status_label,
                post_process=False,
                conn=conn,
                existing_rows=combined_existing_rows,
                delete_duplicates=False,
            ):
                count += 1
    return count


def existing_amazon_order_in_odoo(store: Store, order_id: int) -> str:
    try:
        odoo = OdooClient(store)
        rows = odoo.read("sale.order", [order_id], ["note"])
        note = strip_html(rows[0].get("note") if rows else "")
        match = AMAZON_ORDER_RE.search(note)
        return match.group(1) if match else ""
    except Exception:
        return ""


def update_lines_after_order(
    lines: list[dict[str, Any]],
    amazon_order_id: Optional[str],
    amazon_url: Optional[str],
    amazon_account: dict[str, Any],
    status: str,
    group_key: Optional[str],
    order_engine: str = "rest",
) -> None:
    with db() as conn:
        for line in lines:
            now = utc_now()
            resolved_amazon_url = amazon_url or order_line_amazon_url(amazon_order_id or "")
            conn.execute(
                """
                UPDATE order_lines
                SET amazon_order_id=?, amazon_order_url=?, amazon_account_id=?, amazon_account_name=?,
                    order_engine=?, amazon_group_key=?, amazon_status=?, state=?,
                    last_error=NULL, ordered_at=COALESCE(ordered_at, ?), updated_at=?
                WHERE id=?
                """,
                (
                    amazon_order_id,
                    resolved_amazon_url,
                    amazon_account["id"],
                    amazon_account["name"],
                    normalize_ordering_engine(order_engine),
                    group_key,
                    status,
                    status,
                    now,
                    now,
                    line["id"],
                ),
            )
            updated = conn.execute("SELECT * FROM order_lines WHERE id = ?", (line["id"],)).fetchone()
            if updated:
                ensure_inventory_for_line(updated)
                index_order_line(updated)


ORDER_REF_RE = re.compile(r"\bNC\d+\b", re.IGNORECASE)


def manual_order_refs_from_payload(payload: ManualAmazonOrderMatchPayload) -> list[str]:
    refs: list[str] = []
    for value in payload.order_names or []:
        refs.extend(match.group(0).upper() for match in ORDER_REF_RE.finditer(str(value or "")))
    refs.extend(match.group(0).upper() for match in ORDER_REF_RE.finditer(payload.source_text or ""))
    return list(dict.fromkeys(refs))


def note_manual_amazon_match(rows: list[dict[str, Any]], amazon_order_id: str, amazon_order_url: str, amazon_account_name: str) -> None:
    rows_by_store: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_store.setdefault(int(row["store_id"]), []).append(row)
    for store_id, store_rows in rows_by_store.items():
        try:
            odoo = OdooClient(get_store(store_id))
            note = (
                f"Amazon manual order matched: {amazon_order_id}\n"
                f"Amazon order link: {amazon_order_url or order_line_amazon_url(amazon_order_id)}\n"
                f"Amazon account: {amazon_account_name or 'Chrome Manual Matcher'}"
            )
            for order_id in sorted({int(row["odoo_order_id"]) for row in store_rows}):
                odoo.post_order_note(order_id, note)
        except Exception:
            continue


def backfill_cxml_order_references() -> int:
    updated_count = 0
    with db() as conn:
        rows = conn.execute(
            """
            SELECT order_lines.id, order_lines.amazon_order_id, amazon_attempts.response_json
            FROM order_lines
            JOIN amazon_attempts ON amazon_attempts.order_line_id = order_lines.id
            WHERE order_lines.order_engine = 'cxml'
              AND COALESCE(order_lines.amazon_order_id, '') LIKE 'cxml-%'
              AND amazon_attempts.status = 'ok'
              AND COALESCE(amazon_attempts.response_json, '') != ''
            ORDER BY amazon_attempts.id DESC
            """
        ).fetchall()
        seen: set[int] = set()
        for row in rows:
            line_id = int(row["id"])
            if line_id in seen:
                continue
            seen.add(line_id)
            try:
                response = json.loads(row["response_json"])
            except Exception:
                continue
            text = str(response.get("text") or "")
            replacement = str(response.get("amazon_order_id") or extract_cxml_order_reference(text, row["amazon_order_id"]))
            if replacement and replacement != row["amazon_order_id"] and not replacement.startswith("cxml-"):
                conn.execute(
                    "UPDATE order_lines SET amazon_order_id=?, state='ordered', amazon_status='ordered', ordered_at=COALESCE(ordered_at, ?), updated_at=? WHERE id=?",
                    (replacement, utc_now(), utc_now(), line_id),
                )
                updated_count += 1
            else:
                conn.execute(
                    "UPDATE order_lines SET state='submitted', amazon_status='submitted', ordered_at=COALESCE(ordered_at, ?), updated_at=? WHERE id=?",
                    (utc_now(), utc_now(), line_id),
                )
    return updated_count


def aggregate_items_by_asin(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for line in lines:
        asin = str(line["asin"] or "").strip().upper()
        if not asin:
            continue
        quantity = float(line["quantity"] or 1)
        store_total = order_line_store_total(line)
        entry = grouped.setdefault(
            asin,
            {
                "asin": asin,
                "quantity": 0.0,
                "store_total_price": 0.0,
                "store_unit_price": 0.0,
                "product_names": [],
                "order_names": [],
                "line_id": f"asin-{asin}",
                "supplier_part_auxiliary_id": str(line["supplier_part_auxiliary_id"] or "").strip(),
                "source_line_ids": [],
            },
        )
        entry["quantity"] += quantity
        entry["store_total_price"] += store_total
        entry["store_unit_price"] = entry["store_total_price"] / entry["quantity"] if entry["quantity"] else 0.0
        product_name = str(line["product_name"] or "").strip()
        if product_name and product_name not in entry["product_names"]:
            entry["product_names"].append(product_name)
        order_name = str(line["odoo_order_name"] or "").strip()
        if order_name and order_name not in entry["order_names"]:
            entry["order_names"].append(order_name)
        entry["source_line_ids"].append(line["id"])
    return list(grouped.values())


def row_needs_currency_backfill(row: dict[str, Any]) -> bool:
    currency = clean_text(row.get("store_currency")).upper()
    rate = float(row.get("store_currency_rate_to_usd") or 0)
    native = row.get("store_total_native") if "store_total_native" in row.keys() else None
    return (
        int(row.get("store_id") or 0) > 0
        and int(row.get("odoo_order_id") or 0) > 0
        and (not currency or currency != "USD")
        and (rate <= 0 or native is None)
    )


def backfill_missing_currency_for_chrome_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if row_needs_currency_backfill(row)]
    if not candidates:
        return rows
    try:
        store = get_store(int(candidates[0]["store_id"]))
        order_ids = sorted({int(row["odoo_order_id"]) for row in candidates})
        order_fields = OdooClient(store).existing_fields("sale.order", ["id", "currency_id"])
        order_rows = OdooClient(store).read("sale.order", order_ids, order_fields)
        currencies = {int(order["id"]): order_currency_code(order) for order in order_rows}
        settings = get_service_settings()
        updated_rows = [dict(row) for row in rows]
        with db() as conn:
            for row in updated_rows:
                if not row_needs_currency_backfill(row):
                    continue
                currency = clean_text(row.get("store_currency")).upper() or currencies.get(int(row.get("odoo_order_id") or 0))
                if not currency:
                    continue
                rate = conversion_rate_to_usd(currency, settings)
                native_total = float(
                    row.get("store_total_native")
                    or row.get("store_total_price")
                    or (float(row.get("store_unit_price") or 0) * float(row.get("quantity") or 1))
                    or 0
                )
                usd_total = native_total * rate
                quantity = float(row.get("quantity") or 1)
                row["store_currency"] = currency
                row["store_currency_rate_to_usd"] = rate
                row["store_total_native"] = native_total
                row["store_total_price"] = usd_total
                row["store_unit_price"] = (usd_total / quantity) if quantity else 0
                conn.execute(
                    """
                    UPDATE order_lines
                    SET store_currency=?,
                        store_currency_rate_to_usd=?,
                        store_total_native=?,
                        store_unit_price=?,
                        store_total_price=?,
                        updated_at=?
                    WHERE id=?
                    """,
                    (currency, rate, native_total, row["store_unit_price"], usd_total, utc_now(), row["id"]),
                )
        return updated_rows
    except Exception:
        return rows


def line_ids_from_row(row: dict[str, Any]) -> list[int]:
    raw_ids = clean_text(row.get("source_odoo_line_ids") if "source_odoo_line_ids" in row.keys() else "")
    ids: list[int] = []
    for part in raw_ids.split(","):
        try:
            line_id = int(part.strip())
        except Exception:
            continue
        if line_id not in ids:
            ids.append(line_id)
    if not ids:
        try:
            ids.append(int(row["odoo_line_id"]))
        except Exception:
            pass
    return ids


def backfill_missing_order_financials_for_profit_loss(
    store_id: Optional[int],
    start_text: str,
    end_text: str,
) -> None:
    with db() as conn:
        candidates = rows_to_dicts(
            conn.execute(
                """
                SELECT *
                FROM (
                    SELECT order_lines.*,
                           MIN(COALESCE(NULLIF(odoo_order_date, ''), NULLIF(raw_json::jsonb -> 'order' ->> 'date_order', ''), NULLIF(ordered_at, ''), NULLIF(pulled_at, ''), created_at))
                           OVER (PARTITION BY store_id, odoo_order_id) AS profit_order_date
                    FROM order_lines
                    WHERE (? IS NULL OR store_id=?)
                ) AS candidate_lines
                WHERE profit_order_date BETWEEN ? AND ?
                  AND amazon_total_price IS NOT NULL
                  AND (
                    COALESCE(store_currency, '') = ''
                    OR COALESCE(store_currency_rate_to_usd, 0) <= 0
                    OR store_total_native IS NULL
                  )
                ORDER BY id DESC
                LIMIT 25
                """,
                (store_id, store_id, start_text, end_text),
            ).fetchall()
        )
    if not candidates:
        return
    settings = get_service_settings()
    by_store: dict[int, list[dict[str, Any]]] = {}
    for row in candidates:
        by_store.setdefault(int(row["store_id"]), []).append(row)
    for candidate_store_id, rows in by_store.items():
        try:
            store = get_store(candidate_store_id)
            odoo = OdooClient(store)
            order_ids = sorted({int(row["odoo_order_id"]) for row in rows})
            order_fields = odoo.existing_fields("sale.order", ["id", "currency_id", "order_line"])
            order_rows = odoo.read("sale.order", order_ids, order_fields)
            orders_by_id = {int(order["id"]): order for order in order_rows}
            all_line_ids = sorted({int(line_id) for order in order_rows for line_id in (order.get("order_line") or [])})
            if not all_line_ids:
                continue
            line_fields = odoo.existing_fields(
                "sale.order.line",
                ["id", "name", "display_type", "product_id", "product_uom_qty", "price_unit", "price_subtotal", "price_total", "discount"],
            )
            line_rows = odoo.read("sale.order.line", all_line_ids, line_fields)
            lines_by_id = {int(line["id"]): line for line in line_rows}
            product_ids = sorted({int(line["product_id"][0]) for line in line_rows if line.get("product_id")})
            product_fields = odoo.existing_fields("product.product", ["id", "default_code"])
            products = {int(product["id"]): product for product in odoo.read("product.product", product_ids, product_fields)}
            rows_by_order: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                rows_by_order.setdefault(int(row["odoo_order_id"]), []).append(row)
            updates: list[tuple[Any, ...]] = []
            for order_id, order_rows_for_order in rows_by_order.items():
                order = orders_by_id.get(order_id)
                if not order:
                    continue
                currency = order_currency_code(order)
                rate = conversion_rate_to_usd(currency, settings)
                order_line_ids = [int(line_id) for line_id in (order.get("order_line") or []) if int(line_id) in lines_by_id]
                source_line_ids = {line_id for row in order_rows_for_order for line_id in line_ids_from_row(row)}
                line_totals_by_row: dict[int, float] = {}
                subtotal_native = 0.0
                for row in order_rows_for_order:
                    row_total = 0.0
                    for line_id in line_ids_from_row(row):
                        line = lines_by_id.get(line_id)
                        if line:
                            row_total += line_native_total(line)
                    if not row_total:
                        row_total = float(row.get("store_total_native") or row.get("store_total_price") or 0)
                    line_totals_by_row[int(row["id"])] = row_total
                    subtotal_native += row_total
                delivery_native = 0.0
                discount_native = 0.0
                other_adjustment_native = 0.0
                for line_id in order_line_ids:
                    if line_id in source_line_ids:
                        continue
                    line = lines_by_id[line_id]
                    product_id = int(line["product_id"][0]) if line.get("product_id") else 0
                    default_code = str((products.get(product_id) or {}).get("default_code") or "")
                    if line.get("display_type"):
                        continue
                    if is_delivery_line(line, default_code):
                        delivery_native += line_native_total(line)
                    elif is_discount_line(line, default_code) or line_native_total(line) < 0:
                        discount_native += line_native_total(line)
                    elif is_order_adjustment_line(line, products.get(product_id) or {}, {}):
                        other_adjustment_native += line_native_total(line)
                adjustment_native = delivery_native + discount_native + other_adjustment_native
                for row in order_rows_for_order:
                    row_native = line_totals_by_row.get(int(row["id"]), 0.0)
                    allocation_ratio = (row_native / subtotal_native) if subtotal_native else 0.0
                    allocated_delivery = delivery_native * allocation_ratio
                    allocated_discount = discount_native * allocation_ratio
                    allocated_adjustment = adjustment_native * allocation_ratio
                    total_native = row_native + allocated_adjustment
                    usd_total = total_native * rate
                    quantity = float(row.get("quantity") or 1)
                    updates.append(
                        (
                            currency,
                            rate,
                            row_native,
                            allocated_delivery,
                            allocated_discount,
                            allocated_adjustment,
                            total_native,
                            (usd_total / quantity) if quantity else 0,
                            usd_total,
                            (usd_total - float(row.get("amazon_total_price") or 0)) if row.get("amazon_total_price") is not None else None,
                            utc_now(),
                            row["id"],
                        )
                    )
            if updates:
                with db() as conn:
                    for update in updates:
                        conn.execute(
                            """
                            UPDATE order_lines
                            SET store_currency=?,
                                store_currency_rate_to_usd=?,
                                store_subtotal_native=?,
                                store_delivery_native=?,
                                store_discount_native=?,
                                store_adjustment_native=?,
                                store_total_native=?,
                                store_unit_price=?,
                                store_total_price=?,
                                chrome_profit_total=COALESCE(?, chrome_profit_total),
                                updated_at=?
                            WHERE id=?
                            """,
                            update,
                        )
        except Exception:
            continue


def normalize_converted_profit_columns(store_id: Optional[int], start_text: str, end_text: str) -> None:
    with db() as conn:
        conn.execute(
            """
            UPDATE order_lines
            SET store_total_price=store_total_native * store_currency_rate_to_usd,
                store_unit_price=CASE
                    WHEN COALESCE(quantity, 0) != 0 THEN (store_total_native * store_currency_rate_to_usd) / quantity
                    ELSE store_total_price
                END,
                chrome_profit_total=CASE
                    WHEN amazon_total_price IS NOT NULL THEN (store_total_native * store_currency_rate_to_usd) - amazon_total_price
                    ELSE chrome_profit_total
                END,
                updated_at=?
            WHERE (? IS NULL OR store_id=?)
              AND COALESCE(NULLIF(odoo_order_date, ''), NULLIF(raw_json::jsonb -> 'order' ->> 'date_order', ''), NULLIF(ordered_at, ''), NULLIF(pulled_at, ''), created_at) BETWEEN ? AND ?
              AND store_total_native IS NOT NULL
              AND COALESCE(store_currency_rate_to_usd, 0) > 0
            """,
            (utc_now(), store_id, store_id, start_text, end_text),
        )


def backfill_missing_profit_loss_order_dates(store_id: Optional[int]) -> None:
    updates: list[tuple[str, str, int]] = []
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, raw_json
            FROM order_lines
            WHERE (? IS NULL OR store_id=?)
              AND COALESCE(odoo_order_date, '') = ''
              AND COALESCE(raw_json, '') != ''
            ORDER BY id DESC
            LIMIT 5000
            """,
            (store_id, store_id),
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["raw_json"] or "{}")
                order_date = clean_text((payload.get("order") or {}).get("date_order"))
            except Exception:
                order_date = ""
            if order_date:
                updates.append((order_date, utc_now(), row["id"]))
        for update in updates:
            conn.execute("UPDATE order_lines SET odoo_order_date=?, updated_at=? WHERE id=?", update)


def group_lines_for_amazon_order(lines: list[dict[str, Any]], club: bool = False) -> list[list[dict[str, Any]]]:
    if not lines:
        return []
    if club:
        return [lines]
    grouped: dict[int, list[dict[str, Any]]] = {}
    for line in lines:
        grouped.setdefault(int(line["odoo_order_id"]), []).append(line)
    return list(grouped.values())


def order_line_store_total(line: dict[str, Any]) -> float:
    raw = safe_json(line.get("raw_json"))
    raw_currency = order_currency_code(raw.get("order") or {}) if raw else ""
    stored_currency = clean_text(line.get("store_currency") if "store_currency" in line.keys() else "").upper()
    currency = stored_currency or raw_currency or "USD"
    stored_rate = float(line.get("store_currency_rate_to_usd") or 0) if "store_currency_rate_to_usd" in line.keys() else 0
    if "store_total_native" in line.keys() and line["store_total_native"] is not None:
        native_total = float(line["store_total_native"] or 0)
        return native_total * (stored_rate or conversion_rate_to_usd(currency))
    if raw_currency and raw_currency != "USD":
        try:
            total = 0.0
            for item in raw.get("combined_source_lines") or []:
                source_line = item.get("line") or {}
                total += line_native_total(source_line)
            for adjustment_item in raw.get("order_adjustment_lines") or []:
                adjustment_line = adjustment_item.get("line") if isinstance(adjustment_item, dict) else adjustment_item
                total += line_native_total(adjustment_line)
            if total:
                return convert_to_usd(total, raw_currency)
        except Exception:
            pass
    if "store_total_price" in line.keys() and line["store_total_price"] is not None:
        total = float(line["store_total_price"] or 0)
        if currency != "USD" and stored_rate and not raw_currency:
            return total * stored_rate
        return total
    if "store_unit_price" in line.keys() and line["store_unit_price"] is not None:
        total = float(line["store_unit_price"] or 0) * float(line["quantity"] or 1)
        if currency != "USD" and stored_rate and not raw_currency:
            return total * stored_rate
        return total
    try:
        total = 0.0
        for item in raw.get("combined_source_lines") or []:
            source_line = item.get("line") or {}
            total += line_native_total(source_line)
        for adjustment_item in raw.get("order_adjustment_lines") or []:
            adjustment_line = adjustment_item.get("line") if isinstance(adjustment_item, dict) else adjustment_item
            total += line_native_total(adjustment_line)
        if total:
            currency = order_currency_code(raw.get("order") or {})
            return convert_to_usd(total, currency)
    except Exception:
        pass
    return 0.0


def typesense_headers(settings: dict[str, str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.get("typesense_api_key"):
        headers["X-TYPESENSE-API-KEY"] = settings["typesense_api_key"]
    return headers


def typesense_base(settings: Optional[dict[str, str]] = None) -> str:
    settings = settings or get_service_settings()
    return str(settings.get("typesense_url") or "").rstrip("/")


def order_line_search_document(row: Union[dict[str, Any], dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "row_id": int(row["id"]),
        "store_id": int(row["store_id"]),
        "odoo_order_name": str(row["odoo_order_name"] or ""),
        "product_name": str(row["product_name"] or ""),
        "asin": str(row["asin"] or ""),
        "state": str(row["state"] or ""),
        "odoo_status_label": str(row["odoo_status_label"] or ""),
        "amazon_order_id": str(row["amazon_order_id"] or ""),
        "amazon_account_name": str(row["amazon_account_name"] or ""),
        "fulfilment_note": str(row["fulfilment_note"] or "") if "fulfilment_note" in row.keys() else "",
        "updated_at": str(row["updated_at"] or ""),
    }


def ensure_typesense_collection(settings: Optional[dict[str, str]] = None) -> None:
    settings = settings or get_service_settings()
    base = typesense_base(settings)
    if not base:
        raise RuntimeError("Typesense URL is missing.")
    schema = {
        "name": "order_lines",
        "fields": [
            {"name": "row_id", "type": "int32"},
            {"name": "store_id", "type": "int32", "facet": True},
            {"name": "odoo_order_name", "type": "string"},
            {"name": "product_name", "type": "string"},
            {"name": "asin", "type": "string"},
            {"name": "state", "type": "string", "facet": True},
            {"name": "odoo_status_label", "type": "string", "facet": True},
            {"name": "amazon_order_id", "type": "string", "optional": True},
            {"name": "amazon_account_name", "type": "string", "optional": True},
            {"name": "fulfilment_note", "type": "string", "optional": True},
            {"name": "updated_at", "type": "string", "optional": True},
        ],
    }
    response = requests.get(f"{base}/collections/order_lines", headers=typesense_headers(settings), timeout=8)
    if response.status_code == 404:
        create = requests.post(f"{base}/collections", headers=typesense_headers(settings), json=schema, timeout=15)
        if create.status_code >= 400:
            raise RuntimeError(f"Typesense collection create failed: HTTP {create.status_code}: {create.text[:500]}")
    elif response.status_code >= 400:
        raise RuntimeError(f"Typesense collection check failed: HTTP {response.status_code}: {response.text[:500]}")


def index_order_line(row: Union[dict[str, Any], dict[str, Any]]) -> None:
    settings = get_service_settings()
    if str(settings.get("typesense_enabled", "")).lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        ensure_typesense_collection(settings)
        doc = order_line_search_document(row)
        requests.post(
            f"{typesense_base(settings)}/collections/order_lines/documents?action=upsert",
            headers=typesense_headers(settings),
            json=doc,
            timeout=8,
        )
    except Exception:
        pass


_TYPESENSE_REINDEX_LOCK = threading.Lock()


def typesense_reindex_progress() -> dict[str, Any]:
    raw = get_setting("typesense_reindex_progress", "")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {
        "status": "idle",
        "processed": 0,
        "total": 0,
        "percent": 0,
        "message": "No reindex has run yet.",
        "started_at": "",
        "updated_at": "",
        "completed_at": "",
        "error": "",
    }


def set_typesense_reindex_progress(**updates: Any) -> dict[str, Any]:
    data = typesense_reindex_progress()
    data.update(updates)
    processed = int(data.get("processed") or 0)
    total = int(data.get("total") or 0)
    data["percent"] = round((processed / total) * 100, 1) if total else 0
    data["updated_at"] = utc_now()
    set_setting("typesense_reindex_progress", json.dumps(data, default=str))
    return data


def reindex_order_lines(chunk_size: int = 250) -> int:
    settings = get_service_settings()
    ensure_typesense_collection(settings)
    with db() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS count FROM order_lines").fetchone()["count"] or 0)
    processed = 0
    base = typesense_base(settings)
    headers = typesense_headers(settings)
    while processed < total:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM order_lines
                ORDER BY id
                LIMIT ? OFFSET ?
                """,
                (chunk_size, processed),
            ).fetchall()
        if not rows:
            break
        documents = [json.dumps(order_line_search_document(row), default=str) for row in rows]
        response = requests.post(
            f"{base}/collections/order_lines/documents/import?action=upsert",
            headers={**headers, "Content-Type": "text/plain"},
            data="\n".join(documents),
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Typesense index failed: HTTP {response.status_code}: {response.text[:500]}")
        processed += len(rows)
        set_typesense_reindex_progress(
            status="running",
            processed=processed,
            total=total,
            message=f"Indexed {processed} of {total} order line(s).",
        )
    return processed


def run_typesense_reindex_job() -> None:
    if not _TYPESENSE_REINDEX_LOCK.acquire(blocking=False):
        return
    try:
        with db() as conn:
            total = int(conn.execute("SELECT COUNT(*) AS count FROM order_lines").fetchone()["count"] or 0)
        set_typesense_reindex_progress(
            status="running",
            processed=0,
            total=total,
            message="Starting Typesense reindex.",
            started_at=utc_now(),
            completed_at="",
            error="",
        )
        count = reindex_order_lines()
        set_typesense_reindex_progress(
            status="completed",
            processed=count,
            total=count,
            message=f"Reindexed {count} order line(s) into Typesense.",
            completed_at=utc_now(),
            error="",
        )
    except Exception as exc:
        set_typesense_reindex_progress(
            status="failed",
            message=f"Typesense reindex failed: {exc}",
            completed_at=utc_now(),
            error=str(exc),
        )
    finally:
        _TYPESENSE_REINDEX_LOCK.release()


def start_typesense_reindex_job() -> dict[str, Any]:
    current = typesense_reindex_progress()
    if current.get("status") == "running":
        return {"ok": True, "message": "Typesense reindex is already running.", "progress": current}
    progress = set_typesense_reindex_progress(
        status="queued",
        processed=0,
        total=0,
        message="Typesense reindex queued.",
        started_at=utc_now(),
        completed_at="",
        error="",
    )
    threading.Thread(target=run_typesense_reindex_job, daemon=True).start()
    return {"ok": True, "message": "Typesense reindex started.", "progress": progress}


def typesense_search_ids(query: str, store_id: Optional[int]) -> list[int]:
    settings = get_service_settings()
    if str(settings.get("typesense_enabled", "")).lower() not in {"1", "true", "yes", "on"}:
        return []
    base = typesense_base(settings)
    if not base or not query.strip():
        return []
    params = {
        "q": query,
        "query_by": "odoo_order_name,product_name,asin,state,odoo_status_label,amazon_order_id,amazon_account_name",
        "per_page": "250",
    }
    if store_id:
        params["filter_by"] = f"store_id:={int(store_id)}"
    response = requests.get(f"{base}/collections/order_lines/documents/search", headers=typesense_headers(settings), params=params, timeout=8)
    if response.status_code >= 400:
        raise RuntimeError(f"Typesense search failed: HTTP {response.status_code}: {response.text[:500]}")
    payload = response.json()
    ids = []
    for hit in payload.get("hits") or []:
        doc = hit.get("document") or {}
        if doc.get("row_id"):
            ids.append(int(doc["row_id"]))
    return ids


def mark_line_error(line_id: int, message: str) -> None:
    with db() as conn:
        conn.execute("UPDATE order_lines SET state='error', last_error=?, updated_at=? WHERE id=?", (message, utc_now(), line_id))


def mark_line_error_with_engine(line_id: int, message: str, order_engine: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE order_lines SET state='error', order_engine=?, last_error=?, updated_at=? WHERE id=?",
            (normalize_ordering_engine(order_engine), message, utc_now(), line_id),
        )


def chrome_recipient_name(order_names: Union[list[str], str]) -> str:
    if isinstance(order_names, str):
        names = [order_names]
    else:
        names = list(dict.fromkeys(str(name or "").strip() for name in order_names if str(name or "").strip()))
    return f"Nutricity {' '.join(names)}".strip()


CHROME_JOB_LEASE_MINUTES = 20


def chrome_job_lease_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=CHROME_JOB_LEASE_MINUTES)).isoformat()


def clear_expired_chrome_claims(conn: Any) -> None:
    conn.execute(
        """
        UPDATE order_lines
        SET chrome_claimed_by=NULL,
            chrome_claimed_at=NULL,
            chrome_claim_expires_at=NULL,
            updated_at=?
        WHERE order_engine='chrome'
          AND state='submitted'
          AND COALESCE(amazon_order_id, '') = ''
          AND COALESCE(chrome_claim_expires_at, '') != ''
          AND chrome_claim_expires_at <= ?
        """,
        (utc_now(), utc_now()),
    )


def chrome_job_from_rows(group_rows: list[dict[str, Any]]) -> dict[str, Any]:
    group_rows = backfill_missing_currency_for_chrome_rows(group_rows)
    order_names = list(dict.fromkeys(str(row["odoo_order_name"]) for row in group_rows))
    account = get_amazon_account(group_rows[0]["amazon_account_id"])
    items = aggregate_items_by_asin(group_rows)
    return {
        "group_key": str(group_rows[0]["amazon_group_key"]),
        "store_id": group_rows[0]["store_id"],
        "amazon_account_id": account["id"],
        "amazon_account_name": account["name"],
        "order_names": order_names,
        "recipient_name": chrome_recipient_name(order_names),
        "line_ids": [row["id"] for row in group_rows],
        "claimed_by": group_rows[0].get("chrome_claimed_by") or "",
        "claim_expires_at": group_rows[0].get("chrome_claim_expires_at") or "",
        "items": [
            {
                "asin": item["asin"],
                "quantity": item["quantity"],
                "store_unit_price": round(float(item.get("store_unit_price") or 0), 2),
                "store_total_price": round(float(item.get("store_total_price") or 0), 2),
                "product_name": (item.get("product_names") or [""])[0],
                "product_names": item.get("product_names") or [],
                "order_names": item.get("order_names") or [],
                "line_ids": item["source_line_ids"],
                "cost_approved": all(
                    row["cost_approved_at"] if "cost_approved_at" in row.keys() else False
                    for row in group_rows
                    if row["id"] in item["source_line_ids"]
                ),
            }
            for item in items
        ],
    }


def claim_next_chrome_job(store_id: Optional[int], worker_id: str) -> Optional[dict[str, Any]]:
    worker_id = clean_text(worker_id)[:120] or f"worker-{uuid.uuid4().hex[:12]}"
    with db() as conn:
        clear_expired_chrome_claims(conn)
        existing = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE order_engine='chrome'
              AND state='submitted'
              AND COALESCE(amazon_order_id, '') = ''
              AND COALESCE(amazon_group_key, '') != ''
              AND chrome_claimed_by=?
              AND (? IS NULL OR store_id=?)
            ORDER BY updated_at ASC, odoo_order_id DESC, id ASC
            """,
            (worker_id, store_id, store_id),
        ).fetchall()
        if existing:
            expiry = chrome_job_lease_expiry()
            group_key = str(existing[0]["amazon_group_key"])
            conn.execute(
                """
                UPDATE order_lines
                SET chrome_claim_expires_at=?, updated_at=?
                WHERE amazon_group_key=? AND chrome_claimed_by=?
                """,
                (expiry, utc_now(), group_key, worker_id),
            )
            rows = rows_to_dicts(conn.execute("SELECT * FROM order_lines WHERE amazon_group_key=? ORDER BY id", (group_key,)).fetchall())
            return chrome_job_from_rows(rows)
        candidates = conn.execute(
            """
            SELECT amazon_group_key, MIN(updated_at) AS first_updated
            FROM order_lines
            WHERE order_engine='chrome'
              AND state='submitted'
              AND COALESCE(amazon_order_id, '') = ''
              AND COALESCE(amazon_group_key, '') != ''
              AND COALESCE(chrome_claimed_by, '') = ''
              AND (? IS NULL OR store_id=?)
            GROUP BY amazon_group_key
            ORDER BY first_updated ASC
            LIMIT 10
            """,
            (store_id, store_id),
        ).fetchall()
        for candidate in candidates:
            group_key = str(candidate["amazon_group_key"])
            expiry = chrome_job_lease_expiry()
            cursor = conn.execute(
                """
                UPDATE order_lines
                SET chrome_claimed_by=?,
                    chrome_claimed_at=?,
                    chrome_claim_expires_at=?,
                    updated_at=?
                WHERE amazon_group_key=?
                  AND order_engine='chrome'
                  AND state='submitted'
                  AND COALESCE(amazon_order_id, '') = ''
                  AND COALESCE(chrome_claimed_by, '') = ''
                """,
                (worker_id, utc_now(), expiry, utc_now(), group_key),
            )
            if cursor.rowcount:
                rows = rows_to_dicts(conn.execute("SELECT * FROM order_lines WHERE amazon_group_key=? ORDER BY id", (group_key,)).fetchall())
                return chrome_job_from_rows(rows)
    return None


def ensure_chrome_job_owner(conn: Any, group_key: str, worker_id: str) -> None:
    rows = conn.execute(
        """
        SELECT DISTINCT chrome_claimed_by
        FROM order_lines
        WHERE amazon_group_key=?
          AND order_engine='chrome'
          AND state='submitted'
          AND COALESCE(amazon_order_id, '') = ''
        """,
        (group_key,),
    ).fetchall()
    claimed_by = clean_text(rows[0]["chrome_claimed_by"]) if rows else ""
    if claimed_by and clean_text(worker_id) != claimed_by:
        raise HTTPException(409, "Chrome job is locked by another extension instance.")


def expand_line_ids_to_full_chrome_orders(conn: Any, store_id: int, line_ids: Optional[list[int]]) -> Optional[list[int]]:
    if not line_ids:
        return line_ids
    selected_ids = sorted({int(line_id) for line_id in line_ids if int(line_id or 0) > 0})
    if not selected_ids:
        return []
    selected_orders = conn.execute(
        f"""
        SELECT DISTINCT odoo_order_id
        FROM order_lines
        WHERE store_id=?
          AND id IN ({','.join('?' for _ in selected_ids)})
        """,
        [store_id, *selected_ids],
    ).fetchall()
    order_ids = sorted({int(row["odoo_order_id"]) for row in selected_orders})
    if not order_ids:
        return selected_ids
    sibling_rows = conn.execute(
        f"""
        SELECT id
        FROM order_lines
        WHERE store_id=?
          AND odoo_order_id IN ({','.join('?' for _ in order_ids)})
          AND asin IS NOT NULL AND asin != ''
          AND COALESCE(amazon_order_id, '') = ''
          AND state NOT IN ('ordered', 'delivered', 'dispatched', 'missing', 'costly', 'inventory')
          AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
        ORDER BY odoo_order_id DESC, id ASC
        """,
        [store_id, *order_ids],
    ).fetchall()
    expanded_ids = sorted({int(row["id"]) for row in sibling_rows})
    return expanded_ids or selected_ids


def queue_chrome_order_groups(
    lines: list[dict[str, Any]],
    amazon_account: dict[str, Any],
    fulfilment_address: dict[str, Any],
    club: bool = False,
) -> int:
    if not lines:
        return 0
    grouped: dict[str, list[dict[str, Any]]] = {}
    if club:
        grouped[f"club-{uuid.uuid4().hex[:10]}"] = lines
    else:
        for line in lines:
            grouped.setdefault(str(line["odoo_order_id"]), []).append(line)
    with db() as conn:
        for group_key_seed, group_lines in grouped.items():
            group_key = f"chrome-{group_lines[0]['store_id']}-{group_key_seed}-{uuid.uuid4().hex[:10]}"
            order_names = list(dict.fromkeys(str(line["odoo_order_name"]) for line in group_lines))
            attempt_payload = {
                "group_key": group_key,
                "order_names": order_names,
                "recipient_name": chrome_recipient_name(order_names),
                "address_id": fulfilment_address["id"],
                "amazon_account_id": amazon_account["id"],
                "items": [
                    {
                        "line_id": line["id"],
                        "asin": line["asin"],
                        "quantity": line["quantity"],
                        "product_name": line["product_name"],
                    }
                    for line in group_lines
                ],
            }
            for line in group_lines:
                conn.execute(
                    """
                    UPDATE order_lines
                    SET state='submitted',
                        amazon_status='chrome_queued',
                        order_engine='chrome',
                        amazon_group_key=?,
                        amazon_account_id=?,
                        amazon_account_name=?,
                        chrome_claimed_by=NULL,
                        chrome_claimed_at=NULL,
                        chrome_claim_expires_at=NULL,
                        last_error=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (group_key, amazon_account["id"], amazon_account["name"], utc_now(), line["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO amazon_attempts
                    (order_line_id, external_id, mode, request_json, response_json, status, error, created_at)
                    VALUES (?, ?, 'chrome', ?, NULL, 'queued', NULL, ?)
                    """,
                    (line["id"], group_key, json.dumps(attempt_payload), utc_now()),
                )
    return len(grouped)


def queue_chrome_order_groups_fast(
    store_id: int,
    amazon_account_id: Optional[int] = None,
    address_id: Optional[int] = None,
    line_ids: Optional[list[int]] = None,
    club: bool = False,
) -> tuple[int, int, dict[str, Any], list[str]]:
    with db() as conn:
        cleared_cursor = conn.execute(
            """
            UPDATE order_lines
            SET amazon_order_id = NULL,
                amazon_order_url = NULL,
                amazon_status = NULL,
                state = 'pulled',
                last_error = NULL,
                ordered_at = NULL,
                updated_at = ?
            WHERE store_id = ?
              AND COALESCE(amazon_order_id, '') LIKE 'DRYRUN-%'
            """,
            (utc_now(), store_id),
        )
        account = None
        if amazon_account_id:
            account = conn.execute("SELECT * FROM amazon_accounts WHERE id = ?", (amazon_account_id,)).fetchone()
        if not account:
            account = conn.execute("SELECT * FROM amazon_accounts WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        if not account:
            account = conn.execute("SELECT * FROM amazon_accounts ORDER BY id LIMIT 1").fetchone()
        if not account:
            raise HTTPException(400, "Add an Amazon Business account before placing orders.")

        address = None
        if address_id:
            address = conn.execute("SELECT * FROM fulfilment_addresses WHERE id = ?", (address_id,)).fetchone()
        if not address:
            address = conn.execute("SELECT * FROM fulfilment_addresses WHERE is_default = 1 ORDER BY id LIMIT 1").fetchone()
        if not address:
            address = conn.execute("SELECT * FROM fulfilment_addresses ORDER BY id LIMIT 1").fetchone()
        if not address:
            raise HTTPException(400, "Add a fulfilment address before placing Amazon orders.")

        line_ids = expand_line_ids_to_full_chrome_orders(conn, store_id, line_ids)
        where_line_ids = ""
        params: list[Any] = [store_id]
        if line_ids:
            where_line_ids = f" AND id IN ({','.join('?' for _ in line_ids)})"
            params.extend(line_ids)
        lines = conn.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE store_id = ?
              AND asin IS NOT NULL AND asin != ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state NOT IN ('ordered', 'delivered', 'dispatched', 'missing', 'costly', 'inventory')
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
              {where_line_ids}
            ORDER BY odoo_order_id DESC, id ASC
            """,
            params,
        ).fetchall()
        if not lines:
            return 0, int(cleared_cursor.rowcount or 0), account, []

        grouped: dict[str, list[dict[str, Any]]] = {}
        if club:
            grouped[f"club-{uuid.uuid4().hex[:10]}"] = lines
        else:
            for line in lines:
                grouped.setdefault(str(line["odoo_order_id"]), []).append(line)

        details: list[str] = []
        for group_key_seed, group_lines in grouped.items():
            group_key = f"chrome-{group_lines[0]['store_id']}-{group_key_seed}-{uuid.uuid4().hex[:10]}"
            order_names = list(dict.fromkeys(str(line["odoo_order_name"]) for line in group_lines))
            attempt_payload = {
                "group_key": group_key,
                "order_names": order_names,
                "recipient_name": chrome_recipient_name(order_names),
                "address_id": address["id"],
                "amazon_account_id": account["id"],
                "items": [
                    {
                        "line_id": line["id"],
                        "asin": line["asin"],
                        "quantity": line["quantity"],
                        "product_name": line["product_name"],
                    }
                    for line in group_lines
                ],
            }
            for line in group_lines:
                conn.execute(
                    """
                    UPDATE order_lines
                    SET state='submitted',
                        amazon_status='chrome_queued',
                        order_engine='chrome',
                        amazon_group_key=?,
                        amazon_account_id=?,
                        amazon_account_name=?,
                        chrome_claimed_by=NULL,
                        chrome_claimed_at=NULL,
                        chrome_claim_expires_at=NULL,
                        last_error=NULL,
                        updated_at=?
                    WHERE id=?
                    """,
                    (group_key, account["id"], account["name"], utc_now(), line["id"]),
                )
                conn.execute(
                    """
                    INSERT INTO amazon_attempts
                    (order_line_id, external_id, mode, request_json, response_json, status, error, created_at)
                    VALUES (?, ?, 'chrome', ?, NULL, 'queued', NULL, ?)
                    """,
                    (line["id"], group_key, json.dumps(attempt_payload), utc_now()),
                )
                details.append(f"{line['odoo_order_name']} {line['asin']}: queued for Chrome extension ordering")
        return len(grouped), int(cleared_cursor.rowcount or 0), account, details[:8]


def queue_chrome_order_groups_fast_task(
    store_id: int,
    amazon_account_id: Optional[int] = None,
    address_id: Optional[int] = None,
    line_ids: Optional[list[int]] = None,
    club: bool = False,
) -> None:
    try:
        queue_chrome_order_groups_fast(store_id, amazon_account_id, address_id, line_ids, club)
    except Exception as exc:
        print(f"Chrome queue background task failed: {exc}", flush=True)


def cxml_value(value: Any) -> str:
    return xml_escape(str(value or ""), {'"': "&quot;"})


def local_xml_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1]


def extract_cxml_order_reference(text: str, fallback: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return fallback
    try:
        root = ET.fromstring(raw.encode("utf-8"))
    except ET.ParseError:
        for pattern in (
            r"\b(?:amazonOrderId|AmazonOrderId|orderId|orderID|orderNumber|confirmationID|supplierOrderID)\s*=\s*['\"]([^'\"]+)['\"]",
            r"\b(?:Amazon\s*)?Order(?:\s*ID|\s*Number)?\s*[:#]\s*([A-Z0-9-]{6,})",
        ):
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return fallback

    preferred_names = {
        "amazonorderid",
        "amazon_order_id",
        "orderid",
        "order_id",
        "orderreference",
        "ordernumber",
        "confirmationid",
        "supplierorderid",
        "supplierordernumber",
        "requestid",
    }
    for element in root.iter():
        for key, value in element.attrib.items():
            normalized = key.replace("-", "").replace("_", "").lower()
            if normalized in {name.replace("_", "") for name in preferred_names} and str(value).strip():
                return str(value).strip()
        name = str(element.attrib.get("name") or "").replace(" ", "").replace("-", "").replace("_", "").lower()
        if local_xml_name(element.tag).lower() == "extrinsic" and name in {n.replace("_", "") for n in preferred_names}:
            value = "".join(element.itertext()).strip()
            if value:
                return value
        if local_xml_name(element.tag).replace("-", "").replace("_", "").lower() in {n.replace("_", "") for n in preferred_names}:
            value = "".join(element.itertext()).strip()
            if value:
                return value
    return fallback


def find_child_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if local_xml_name(child.tag).lower() == name.lower():
            return "".join(child.itertext()).strip()
    return ""


def extract_spaid_from_item(item: ET.Element) -> str:
    spaid = find_child_text(item, "SupplierPartAuxiliaryID")
    if spaid:
        return spaid
    description = find_child_text(item, "Description")
    match = re.search(r"\basid-[A-Za-z0-9_-]+", description)
    return match.group(0) if match else ""


def parse_punchout_order_message(text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(text.encode("utf-8"))
    items: list[dict[str, Any]] = []
    for item in root.iter():
        if local_xml_name(item.tag) not in {"ItemIn", "ItemOut"}:
            continue
        asin = normalize_asin(find_child_text(item, "SupplierPartID"))
        spaid = extract_spaid_from_item(item)
        if not asin or not spaid:
            continue
        quantity_text = str(item.attrib.get("quantity") or "1").strip()
        try:
            quantity = float(quantity_text)
        except ValueError:
            quantity = 1.0
        items.append({"asin": asin, "quantity": quantity, "supplier_part_auxiliary_id": spaid})
    return items


def save_punchout_spaids(store_id: int, items: list[dict[str, Any]]) -> list[int]:
    matched_line_ids: list[int] = []
    with db() as conn:
        for item in items:
            rows = conn.execute(
                """
                SELECT *
                FROM order_lines
                WHERE store_id=?
                  AND asin=?
                  AND COALESCE(amazon_order_id, '') = ''
                  AND state NOT IN ('ordered', 'submitted', 'delivered', 'dispatched')
                  AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
                ORDER BY
                  CASE WHEN ABS(COALESCE(quantity, 1) - ?) < 0.0001 THEN 0 ELSE 1 END,
                  updated_at DESC,
                  id DESC
                LIMIT 1
                """,
                (store_id, item["asin"], float(item["quantity"] or 1)),
            ).fetchall()
            if not rows:
                continue
            line = rows[0]
            conn.execute(
                """
                UPDATE order_lines
                SET supplier_part_auxiliary_id=?, last_error=NULL, updated_at=?
                WHERE id=?
                """,
                (item["supplier_part_auxiliary_id"], utc_now(), line["id"]),
            )
            matched_line_ids.append(int(line["id"]))
    return matched_line_ids


def first_missing_spaid_line(store_id: int, line_ids: Optional[list[int]] = None) -> Optional[dict[str, Any]]:
    params: list[Any] = [store_id]
    line_filter = ""
    if line_ids:
        line_filter = f" AND id IN ({','.join('?' for _ in line_ids)})"
        params.extend(line_ids)
    with db() as conn:
        return conn.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE store_id=?
              AND COALESCE(asin, '') != ''
              AND COALESCE(supplier_part_auxiliary_id, '') = ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state NOT IN ('ordered', 'submitted', 'delivered', 'dispatched')
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
              {line_filter}
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()


def cxml_status_response(code: int = 200, text: str = "OK") -> Response:
    payload_id = f"response-{uuid.uuid4().hex}@nutricity"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.014/cXML.dtd">
<cXML payloadID="{cxml_value(payload_id)}" timestamp="{cxml_value(timestamp)}" version="1.2.014">
  <Response>
    <Status code="{code}" text="{cxml_value(text)}"/>
  </Response>
</cXML>"""
    return Response(content=body, media_type="text/xml")


def build_punchout_setup_request(account: dict[str, Any], return_url: str, buyer_email: str, line: Optional[dict[str, Any]] = None) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    payload_id = f"punchout-{uuid.uuid4().hex}@nutricity"
    from_identity = clean_text(account["cxml_from_identity"] if "cxml_from_identity" in account.keys() else "")
    shared_secret = clean_text(account["cxml_shared_secret"] if "cxml_shared_secret" in account.keys() else "")
    credential_domain = clean_text(account["cxml_credential_domain"] if "cxml_credential_domain" in account.keys() else "NetworkId", "NetworkId") or "NetworkId"
    to_identity = clean_text(account["cxml_to_identity"] if "cxml_to_identity" in account.keys() else "Amazon", "Amazon") or "Amazon"
    extrinsics = [
        f'<Extrinsic name="UserEmail">{cxml_value(buyer_email)}</Extrinsic>',
        f'<Extrinsic name="Email">{cxml_value(buyer_email)}</Extrinsic>',
    ]
    if line is not None:
        extrinsics.append(f'<Extrinsic name="OdooOrder">{cxml_value(line["odoo_order_name"])}</Extrinsic>')
        extrinsics.append(f'<Extrinsic name="ASIN">{cxml_value(line["asin"])}</Extrinsic>')
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.014/cXML.dtd">
<cXML payloadID="{cxml_value(payload_id)}" timestamp="{cxml_value(timestamp)}" version="1.2.014">
  <Header>
    <From><Credential domain="{cxml_value(credential_domain)}"><Identity>{cxml_value(from_identity)}</Identity></Credential></From>
    <To><Credential domain="{cxml_value(credential_domain)}"><Identity>{cxml_value(to_identity)}</Identity></Credential></To>
    <Sender>
      <Credential domain="{cxml_value(credential_domain)}">
        <Identity>{cxml_value(from_identity)}</Identity>
        <SharedSecret>{cxml_value(shared_secret)}</SharedSecret>
      </Credential>
      <UserAgent>Nutricity Fulfilment App</UserAgent>
    </Sender>
  </Header>
  <Request>
    <PunchOutSetupRequest operation="create">
      <BuyerCookie>{cxml_value(payload_id)}</BuyerCookie>
      <BrowserFormPost><URL>{cxml_value(return_url)}</URL></BrowserFormPost>
      <Contact role="endUser">
        <Name xml:lang="en">{cxml_value(buyer_email)}</Name>
        <Email>{cxml_value(buyer_email)}</Email>
      </Contact>
      {''.join(extrinsics)}
    </PunchOutSetupRequest>
  </Request>
</cXML>"""


def extract_punchout_start_page(text: str) -> str:
    root = ET.fromstring(str(text or "").encode("utf-8"))
    status = next((element for element in root.iter() if local_xml_name(element.tag) == "Status"), None)
    if status is not None and str(status.attrib.get("code") or "").strip() not in {"", "200"}:
        raise RuntimeError(f"Amazon Punchout failed: HTTP-style cXML status {status.attrib.get('code')} {status.attrib.get('text') or ''}")
    for element in root.iter():
        if local_xml_name(element.tag) == "URL":
            value = "".join(element.itertext()).strip()
            if value:
                return value
    raise RuntimeError(f"Amazon Punchout response did not include a StartPage URL: {str(text or '')[:1000]}")


def create_punchout_session(account: dict[str, Any], punchout_url: str, return_url: str, buyer_email: str, line: Optional[dict[str, Any]] = None) -> str:
    payload = build_punchout_setup_request(account, return_url, buyer_email, line)
    response = requests.post(
        punchout_url,
        data=payload.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": "Nutricity cXML Punchout/1.0"},
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Amazon Punchout setup failed: HTTP {response.status_code}: {response.text[:1000]}")
    return extract_punchout_start_page(response.text)


class CxmlOrderingClient:
    def __init__(self, account: dict[str, Any]) -> None:
        self.account = account
        self.from_identity = clean_text(account["cxml_from_identity"] if "cxml_from_identity" in account.keys() else "")
        self.shared_secret = clean_text(account["cxml_shared_secret"] if "cxml_shared_secret" in account.keys() else "")
        self.po_url = clean_text(account["cxml_po_url"] if "cxml_po_url" in account.keys() else "")
        self.buyer_email = clean_text(account["buyer_email"] if "buyer_email" in account.keys() else "")
        self.auth_mode = normalize_cxml_auth_mode(account["cxml_auth_mode"] if "cxml_auth_mode" in account.keys() else "header")
        self.cart_session_id = clean_text(account["cxml_cart_session_id"] if "cxml_cart_session_id" in account.keys() else "")
        self.credential_domain = clean_text(account["cxml_credential_domain"] if "cxml_credential_domain" in account.keys() else "NetworkId", "NetworkId") or "NetworkId"
        self.to_identity = clean_text(account["cxml_to_identity"] if "cxml_to_identity" in account.keys() else "Amazon", "Amazon") or "Amazon"
        if not self.from_identity or not self.shared_secret or not self.po_url:
            raise RuntimeError("cXML credentials are missing. Edit the Amazon account and set From Identity, Shared Secret, and Purchase Order Request URL.")
        if not self.buyer_email:
            raise RuntimeError("Buyer email is missing. Edit the Amazon account and set Buyer Email for cXML ordering.")

    def _request_kwargs(self) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {}
        if self.auth_mode in {"basic", "both"}:
            request_kwargs["auth"] = (self.from_identity, self.shared_secret)
        return request_kwargs

    def preflight(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        payload_id = f"preflight-{uuid.uuid4().hex}@nutricity"
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.014/cXML.dtd">
<cXML payloadID="{cxml_value(payload_id)}" timestamp="{cxml_value(timestamp)}" version="1.2.014">
    <Header>
    <From>
      <Credential domain="{cxml_value(self.credential_domain)}"><Identity>{cxml_value(self.from_identity)}</Identity></Credential>
    </From>
    <To>
      <Credential domain="{cxml_value(self.credential_domain)}"><Identity>{cxml_value(self.to_identity)}</Identity></Credential>
    </To>
    <Sender>
      <Credential domain="{cxml_value(self.credential_domain)}">
        <Identity>{cxml_value(self.from_identity)}</Identity>
        <SharedSecret>{cxml_value(self.shared_secret)}</SharedSecret>
      </Credential>
      <UserAgent>Nutricity Fulfilment App</UserAgent>
    </Sender>
  </Header>
  <Request deploymentMode="test">
    <ProfileRequest />
  </Request>
</cXML>"""
        response = requests.post(
            self.po_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": "Nutricity cXML Ordering/1.0"},
            timeout=30,
            **self._request_kwargs(),
        )
        return {
            "status_code": response.status_code,
            "text": response.text[:1000],
            "auth_mode": self.auth_mode,
            "cart_session_set": bool(self.cart_session_id),
        }

    def create_order_lines(self, external_id: str, items: list[dict[str, Any]], order_names: list[str], address: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        payload = self._order_request(external_id, items, order_names, address)
        response = requests.post(
            self.po_url,
            data=payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8", "User-Agent": "Nutricity cXML Ordering/1.0"},
            timeout=60,
            **self._request_kwargs(),
        )
        if response.status_code >= 400:
            hint = f" Auth mode: {self.auth_mode}."
            if response.status_code == 401 and self.auth_mode != "header":
                hint += " Try cXML Header Only if this Amazon group expects credentials only in the cXML Header."
            if response.status_code == 401 and not self.cart_session_id:
                hint += " If Amazon requires punchout-context ordering, add the Punchout Cart/Session ID from the punchout cart."
            raise RuntimeError(f"Amazon cXML failed: HTTP {response.status_code}:{hint} {response.text[:1000]}")
        amazon_order_id = extract_cxml_order_reference(response.text, external_id)
        return payload, {
            "status_code": response.status_code,
            "text": response.text[:4000],
            "cxml_order_id": external_id,
            "amazon_order_id": amazon_order_id,
            "confirmed": amazon_order_id != external_id,
        }

    def _address_xml(self, address: dict[str, Any]) -> str:
        lines = [
            f"<Street>{cxml_value(address['address_line1'])}</Street>",
        ]
        if address["address_line2"]:
            lines.append(f"<Street>{cxml_value(address['address_line2'])}</Street>")
        if address["address_line3"]:
            lines.append(f"<Street>{cxml_value(address['address_line3'])}</Street>")
        return "\n".join(
            [
                f'<Address isoCountryCode="{cxml_value(address["country_code"])}" addressID="{cxml_value(address["label"])}">',
                f"<Name xml:lang=\"en\">{cxml_value(address['company_name'] or 'Nutricity')}</Name>",
                "<PostalAddress>",
                *lines,
                f"<City>{cxml_value(address['city'])}</City>",
                f"<State>{cxml_value(address['state_or_region'])}</State>",
                f"<PostalCode>{cxml_value(address['postal_code'])}</PostalCode>",
                f"<Country isoCountryCode=\"{cxml_value(address['country_code'])}\">{cxml_value(address['country_code'])}</Country>",
                "</PostalAddress>",
                f"<Email>{cxml_value(self.buyer_email)}</Email>",
                "</Address>",
            ]
        )

    def _order_request(self, external_id: str, items: list[dict[str, Any]], order_names: list[str], address: dict[str, Any]) -> str:
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        payload_id = f"{external_id}-{uuid.uuid4().hex}@nutricity"
        po_number = " ".join(dict.fromkeys(str(name or "").strip() for name in order_names if str(name or "").strip())) or external_id
        item_xml = []
        for idx, item in enumerate(items, start=1):
            asin = str(item.get("asin") or "").strip().upper()
            quantity = int(float(item.get("quantity") or 1))
            spaid = clean_text(item.get("supplier_part_auxiliary_id") or item.get("spaid") or self.cart_session_id)
            auxiliary_id = (
                f"\n        <SupplierPartAuxiliaryID>{cxml_value(spaid)}</SupplierPartAuxiliaryID>"
                if spaid
                else ""
            )
            item_xml.append(
                f"""
    <ItemOut quantity="{quantity}" lineNumber="{idx}">
      <ItemID>
        <SupplierPartID>{cxml_value(asin)}</SupplierPartID>{auxiliary_id}
      </ItemID>
      <ItemDetail>
        <UnitPrice><Money currency="USD">0.00</Money></UnitPrice>
        <Description xml:lang="en">{cxml_value(asin)}</Description>
        <UnitOfMeasure>EA</UnitOfMeasure>
        <Classification domain="UNSPSC">00000000</Classification>
      </ItemDetail>
    </ItemOut>"""
            )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE cXML SYSTEM "http://xml.cxml.org/schemas/cXML/1.2.014/cXML.dtd">
<cXML payloadID="{cxml_value(payload_id)}" timestamp="{cxml_value(timestamp)}" version="1.2.014">
  <Header>
    <From>
      <Credential domain="{cxml_value(self.credential_domain)}"><Identity>{cxml_value(self.from_identity)}</Identity></Credential>
    </From>
    <To>
      <Credential domain="{cxml_value(self.credential_domain)}"><Identity>{cxml_value(self.to_identity)}</Identity></Credential>
    </To>
    <Sender>
      <Credential domain="{cxml_value(self.credential_domain)}">
        <Identity>{cxml_value(self.from_identity)}</Identity>
        <SharedSecret>{cxml_value(self.shared_secret)}</SharedSecret>
      </Credential>
      <UserAgent>Nutricity Fulfilment App</UserAgent>
    </Sender>
  </Header>
  <Request deploymentMode="production">
    <OrderRequest>
      <OrderRequestHeader orderID="{cxml_value(po_number)}" orderDate="{cxml_value(timestamp)}" type="new">
        <Total><Money currency="USD">0.00</Money></Total>
        <ShipTo>{self._address_xml(address)}</ShipTo>
        <BillTo>{self._address_xml(address)}</BillTo>
        <Contact role="endUser">
          <Name xml:lang="en">{cxml_value(self.buyer_email)}</Name>
          <Email>{cxml_value(self.buyer_email)}</Email>
        </Contact>
        <Extrinsic name="UserEmail">{cxml_value(self.buyer_email)}</Extrinsic>
        <Extrinsic name="Email">{cxml_value(self.buyer_email)}</Extrinsic>
        {f'<Extrinsic name="PunchoutCartSessionId">{cxml_value(self.cart_session_id)}</Extrinsic>' if self.cart_session_id else ''}
      </OrderRequestHeader>
      {''.join(item_xml)}
    </OrderRequest>
  </Request>
</cXML>"""


def place_orders(
    store_id: int,
    address_id: Optional[int] = None,
    amazon_account_id: Optional[int] = None,
    line_ids: Optional[list[int]] = None,
    club: bool = False,
    ordering_engine: str = "rest",
    allow_missing_spaid: bool = False,
) -> tuple[int, int]:
    store = get_store(store_id)
    amazon_account = get_amazon_account(amazon_account_id)
    ordering_engine = normalize_ordering_engine(ordering_engine)
    amazon = AmazonBusinessClient(amazon_account)
    fulfilment_address = get_address(address_id)
    placed = 0
    failed = 0
    with db() as conn:
        if ordering_engine == "chrome":
            line_ids = expand_line_ids_to_full_chrome_orders(conn, store_id, line_ids)
        where_line_ids = ""
        params: list[Any] = [store_id]
        if line_ids:
            placeholders = ",".join("?" for _ in line_ids)
            where_line_ids = f" AND id IN ({placeholders})"
            params.extend(line_ids)
        lines = conn.execute(
            f"""
            SELECT * FROM order_lines
            WHERE store_id = ?
              AND asin IS NOT NULL AND asin != ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state NOT IN ('ordered', 'delivered', 'dispatched', 'missing', 'costly', 'inventory')
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
              {where_line_ids}
            ORDER BY odoo_order_id DESC, id ASC
            """,
            params,
        ).fetchall()
    inventory_lines: list[dict[str, Any]] = []
    purchase_lines: list[dict[str, Any]] = []
    for line in lines:
      if available_inventory_quantity(int(line["store_id"]), str(line["asin"] or "")) >= float(line["quantity"] or 1):
          if reserve_inventory_for_line(line):
              inventory_lines.append(line)
              continue
      purchase_lines.append(line)
    lines = purchase_lines
    if ordering_engine == "chrome" and lines:
        placed = queue_chrome_order_groups(lines, amazon_account, fulfilment_address, club=club)
        return placed, 0
    if ordering_engine == "rest" and amazon.is_sandbox and lines:
        message = (
            "Amazon Business static sandbox does not support placing arbitrary Ordering API orders. "
            "It only returns mocked responses for documented sandbox patterns. Select a production "
            "Amazon Business account with AmazonBusinessOrderPlacement enabled to place real orders."
        )
        for line in lines:
            mark_line_error(line["id"], message)
        write_report(store_id)
        return 0, len(lines)
    if ordering_engine == "cxml" and lines:
        try:
            cxml = CxmlOrderingClient(amazon_account)
        except Exception as exc:
            for line in lines:
                mark_line_error_with_engine(line["id"], str(exc), "cxml")
            write_report(store_id)
            return 0, len(lines)
        missing_spaid = [line for line in lines if not clean_text(line["supplier_part_auxiliary_id"] if "supplier_part_auxiliary_id" in line.keys() else "")]
        if missing_spaid and not allow_missing_spaid:
            message = (
                "Missing SupplierPartAuxiliaryID from Amazon Punchout cart. "
                "Punch out to Amazon, submit the cart back, save the returned SPAID on this line, then place the cXML order."
            )
            for line in missing_spaid:
                mark_line_error_with_engine(line["id"], message, "cxml")
            write_report(store_id)
            return 0, len(lines)
        for group_lines in group_lines_for_amazon_order(lines, club=club):
            group_order_id = int(group_lines[0]["odoo_order_id"])
            old_amazon_order = "" if club else existing_amazon_order_in_odoo(store, group_order_id)
            if old_amazon_order:
                with db() as conn:
                    for line in group_lines:
                        conn.execute(
                            "UPDATE order_lines SET amazon_order_id=?, order_engine='cxml', state='ordered', last_error=NULL, ordered_at=COALESCE(ordered_at, ?), updated_at=? WHERE id=?",
                            (old_amazon_order, utc_now(), utc_now(), line["id"]),
                        )
                placed += len(group_lines)
                continue
            external_id = f"cxml-{'club' if club else 'odoo'}-{store_id}-{group_order_id if not club else uuid.uuid4().hex[:10]}-{uuid.uuid4().hex[:8]}"
            order_names = list(dict.fromkeys(str(line["odoo_order_name"]) for line in group_lines))
            items = aggregate_items_by_asin(group_lines)
            try:
                payload, response = cxml.create_order_lines(external_id, items, order_names, fulfilment_address)
                amazon_order_id = response["amazon_order_id"]
                with db() as conn:
                    for line in group_lines:
                        conn.execute(
                            """
                            INSERT INTO amazon_attempts
                            (order_line_id, external_id, mode, request_json, response_json, status, error, created_at)
                            VALUES (?, ?, 'cxml', ?, ?, 'ok', NULL, ?)
                            """,
                            (line["id"], external_id, payload, json.dumps(response), utc_now()),
                        )
                cxml_state = "ordered" if response.get("confirmed") else "submitted"
                update_lines_after_order(group_lines, amazon_order_id, None, amazon_account, cxml_state, external_id, "cxml")
                note = (
                    f"Amazon cXML {'clubbed ' if club else ''}order {'confirmed' if response.get('confirmed') else 'submitted, confirmation pending'}: {amazon_order_id}<br/>"
                    f"ASINs: {', '.join(dict.fromkeys(str(line['asin']) for line in group_lines))}<br/>"
                    f"Amazon account: {amazon_account['name']}<br/>"
                    f"Engine: cXML Punchout / Purchase Order Request<br/>"
                    f"Fulfilment address: {fulfilment_address['label']}"
                )
                for order_id in dict.fromkeys(int(line["odoo_order_id"]) for line in group_lines):
                    OdooClient(store).post_order_note(order_id, note)
                placed += len(group_lines)
            except Exception as exc:
                failed += len(group_lines)
                for line in group_lines:
                    mark_line_error_with_engine(line["id"], str(exc), "cxml")
        write_report(store_id)
        return placed, failed
    for group_lines in group_lines_for_amazon_order(lines, club=club):
        group_order_id = int(group_lines[0]["odoo_order_id"])
        old_amazon_order = "" if club else existing_amazon_order_in_odoo(store, group_order_id)
        if old_amazon_order:
            with db() as conn:
                for line in group_lines:
                    conn.execute(
                        "UPDATE order_lines SET amazon_order_id=?, state='ordered', last_error=NULL, ordered_at=COALESCE(ordered_at, ?), updated_at=? WHERE id=?",
                        (old_amazon_order, utc_now(), utc_now(), line["id"]),
                    )
            placed += len(group_lines)
            continue
        external_id = f"odoo-{'club' if club else 'order'}-{store_id}-{group_order_id if not club else uuid.uuid4().hex[:10]}-{uuid.uuid4().hex[:8]}"
        order_names = list(dict.fromkeys(str(line["odoo_order_name"]) for line in group_lines))
        items = aggregate_items_by_asin(group_lines)
        try:
            search_responses = {line["asin"]: amazon.verify_asin_for_order(line["asin"]) for line in group_lines}
            mode = "create"
            payload, response = amazon.create_order_lines(external_id, items, order_names, fulfilment_address)
            amazon_order_id = extract_amazon_order_id(response)
            amazon_url = os.getenv(
                "AMAZON_ORDER_URL_TEMPLATE",
                "https://www.amazon.com/gp/css/order-details?orderID={amazon_order_id}",
            ).format(amazon_order_id=amazon_order_id)
            with db() as conn:
                for line in group_lines:
                    conn.execute(
                        """
                        INSERT INTO amazon_attempts
                        (order_line_id, external_id, mode, request_json, response_json, status, error, created_at)
                        VALUES (?, ?, ?, ?, ?, 'ok', NULL, ?)
                        """,
                        (
                            line["id"],
                            external_id,
                            mode,
                            json.dumps({"search": search_responses, "order": payload}),
                            json.dumps(response),
                            utc_now(),
                        ),
                    )
            update_lines_after_order(
                group_lines,
                amazon_order_id,
                amazon_url,
                amazon_account,
                "ordered",
                external_id,
                "rest",
            )
            note = (
                f"Amazon {'clubbed ' if club else ''}order: <a href=\"{amazon_url}\" target=\"_blank\">{amazon_order_id}</a><br/>"
                f"ASINs: {', '.join(dict.fromkeys(str(line['asin']) for line in group_lines))}<br/>"
                f"Amazon account: {amazon_account['name']}<br/>"
                f"Amazon ship-to name: {amazon._recipient_name(order_names)}<br/>"
                f"Fulfilment address: {fulfilment_address['label']}<br/>"
                f"{'Clubbed app' if club else 'Grouped Odoo'} order id: {external_id}"
            )
            for order_id in dict.fromkeys(int(line["odoo_order_id"]) for line in group_lines):
                OdooClient(store).post_order_note(order_id, note)
            placed += len(group_lines)
        except Exception as exc:
            failed += len(group_lines)
            for line in group_lines:
                mark_line_error(line["id"], str(exc))
    write_report(store_id)
    return placed, failed


def extract_amazon_order_id(response: dict[str, Any]) -> str:
    for key in ("orderId", "amazonOrderId", "orderIdentifier", "id"):
        value = response.get(key)
        if value:
            return str(value)
    order = response.get("order") if isinstance(response.get("order"), dict) else {}
    for key in ("orderId", "amazonOrderId", "orderIdentifier", "id"):
        if order.get(key):
            return str(order[key])
    return f"UNKNOWN-{uuid.uuid4().hex[:10].upper()}"


def find_tracking_targets(payload: Any) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []

    def walk(node: Any, order_id: str = "", shipment_id: str = "", package_id: str = "") -> None:
        if isinstance(node, dict):
            next_order = str(node.get("orderId") or node.get("amazonOrderId") or node.get("orderIdentifier") or order_id or "")
            next_shipment = str(node.get("shipmentId") or node.get("shipmentIdentifier") or shipment_id or "")
            next_package = str(node.get("packageId") or node.get("packageIdentifier") or node.get("trackingId") or package_id or "")
            if next_order and next_shipment and next_package:
                target = (next_order, next_shipment, next_package)
                if target not in targets:
                    targets.append(target)
            for value in node.values():
                walk(value, next_order, next_shipment, next_package)
        elif isinstance(node, list):
            for value in node:
                walk(value, order_id, shipment_id, package_id)

    walk(payload)
    return targets


def summarize_tracking(payloads: list[dict[str, Any]], fallback_details: dict[str, Any]) -> str:
    combined = json.dumps(payloads or fallback_details, default=str)
    if re.search(r"\bdelivered\b", combined, re.IGNORECASE):
        return "Delivered"
    if re.search(r"\b(out for delivery|in transit|shipped)\b", combined, re.IGNORECASE):
        return "In transit"
    if re.search(r"\b(ordered|not shipped|pending)\b", combined, re.IGNORECASE):
        return "Ordered"
    return "Unknown"


def tracking_status_from_packages(packages: list[dict[str, Any]]) -> str:
    if not packages:
        return "Unknown"
    package_texts = [json.dumps(package, default=str).lower() for package in packages]
    if all("delivered" in text for text in package_texts):
        return "Delivered"
    text = " ".join(package_texts)
    if "out for delivery" in text:
        return "Out for delivery"
    if "shipped" in text or "carrier" in text or "transit" in text:
        return "Shipped"
    if "arriving" in text:
        return "Ordered"
    return "Unknown"


def package_matches_line(package: dict[str, Any], line: dict[str, Any]) -> bool:
    asins = {normalize_asin(str(value)) for value in package.get("asins") or []}
    asin = normalize_asin(str(line["asin"] or ""))
    return bool(asin and asin in asins)


def tracking_rows(store_id: Optional[int] = None) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE COALESCE(amazon_order_id, '') != ''
              AND state IN ('ordered', 'dispatched')
              AND (? IS NULL OR store_id=?)
            ORDER BY tracking_checked_at IS NULL DESC, tracking_checked_at ASC, ordered_at DESC, updated_at DESC
            """,
            (store_id, store_id),
        ).fetchall()
    data = rows_to_dicts(rows)
    stores_by_id = {store["id"]: store for store in list_stores()}
    for row in data:
        store = stores_by_id.get(row.get("store_id"))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        row["amazon_order_url"] = row.get("amazon_order_url") or order_line_amazon_url(row.get("amazon_order_id") or "")
        row["asin_url"] = asin_product_url(row.get("asin") or "")
    return data


def delivered_unfulfilled_rows(store_id: Optional[int] = None) -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE COALESCE(amazon_order_id, '') != ''
              AND (
                state = 'delivered'
                OR tracking_status = 'Delivered'
                OR LOWER(COALESCE(tracking_status, '')) = 'delivered'
              )
              AND (? IS NULL OR store_id=?)
            ORDER BY tracking_checked_at DESC, ordered_at DESC, updated_at DESC
            """,
            (store_id, store_id),
        ).fetchall()
    stores = {int(store["id"]): store for store in list_stores()}
    clients: dict[int, OdooClient] = {}
    results: list[dict[str, Any]] = []
    for row in rows:
        data = rows_to_dicts([row])[0]
        store = stores.get(int(row["store_id"]))
        data["store_name"] = store["name"] if store else ""
        data["odoo_order_url"] = odoo_order_admin_url(store, row["odoo_order_id"]) if store else ""
        data["amazon_order_url"] = data.get("amazon_order_url") or order_line_amazon_url(row["amazon_order_id"])
        try:
            if not store:
                raise RuntimeError("Store not found")
            client = clients.setdefault(int(row["store_id"]), OdooClient(store))
            status = client.picking_fulfilment_status_for_order(int(row["odoo_order_id"]))
            data.update(status)
            if status.get("fulfilment_status") == "fulfilment pending":
                results.append(data)
        except Exception as exc:
            data.update(
                {
                    "fulfilment_status": "fulfilment pending",
                    "message": f"Odoo fulfilment check failed: {exc}",
                    "picking_ids": [],
                    "picking_names": [],
                    "picking_states": [],
                    "open_picking_ids": [],
                    "open_picking_names": [],
                }
            )
            results.append(data)
    return results


def sync_epost_tracking_from_odoo(store_id: Optional[int] = None, days: int = 2) -> int:
    days = max(1, min(30, int(days or 2)))
    stores = {int(store["id"]): store for store in list_stores()}
    with db() as conn:
        local_rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE COALESCE(odoo_order_id, '') != ''
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
              AND (? IS NULL OR store_id=?)
            ORDER BY updated_at DESC
            LIMIT 5000
            """,
            (store_id, store_id),
        ).fetchall()
    local_by_order: dict[tuple[int, int], dict[str, Any]] = {}
    for row in local_rows:
        try:
            local_by_order[(int(row["store_id"]), int(row["odoo_order_id"]))] = row
        except Exception:
            continue
    synced = 0
    with db() as conn:
        for store_row in stores.values():
            if store_id is not None and int(store_row["id"]) != int(store_id):
                continue
            try:
                client = OdooClient(get_store(int(store_row["id"])))
                tracks = client.epost_tracking_since(days)
            except Exception:
                continue
            for track in tracks:
                code = clean_text(track.get("tracking_code")).upper()
                if not code:
                    continue
                odoo_order_id = track.get("odoo_order_id")
                local_row = None
                if odoo_order_id:
                    try:
                        local_row = local_by_order.get((int(store_row["id"]), int(odoo_order_id)))
                    except Exception:
                        local_row = None
                now = utc_now()
                conn.execute(
                    """
                    INSERT INTO epost_global_tracking
                    (store_id, order_line_id, odoo_order_id, odoo_order_name, amazon_order_id, amazon_order_url,
                     picking_id, picking_name, tracking_code, tracking_url, epost_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    ON CONFLICT(store_id, tracking_code) DO UPDATE SET
                        order_line_id=COALESCE(epost_global_tracking.order_line_id, excluded.order_line_id),
                        odoo_order_id=excluded.odoo_order_id,
                        odoo_order_name=excluded.odoo_order_name,
                        amazon_order_id=COALESCE(NULLIF(epost_global_tracking.amazon_order_id, ''), excluded.amazon_order_id),
                        amazon_order_url=COALESCE(NULLIF(epost_global_tracking.amazon_order_url, ''), excluded.amazon_order_url),
                        picking_id=excluded.picking_id,
                        picking_name=excluded.picking_name,
                        tracking_url=excluded.tracking_url,
                        updated_at=excluded.updated_at
                    """,
                    (
                        int(store_row["id"]),
                        local_row["id"] if local_row else None,
                        odoo_order_id,
                        track.get("odoo_order_name") or "",
                        (local_row["amazon_order_id"] or "") if local_row else "",
                        (local_row["amazon_order_url"] or "") if local_row else "",
                        track.get("picking_id"),
                        track.get("picking_name") or "",
                        code,
                        f"https://epgtrack.com/{code}",
                        now,
                        now,
                    ),
                )
                synced += 1
    return synced


def parse_epost_datetime(value: Any) -> Optional[datetime]:
    text = clean_text(str(value or ""))
    if not text:
        return None
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text.replace("Z", "+0000"), fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def epost_status_from_update(status: str, last_update_at: Any, fallback_at: Any = None) -> str:
    if re.search(r"\bdelivered\b", status or "", re.IGNORECASE):
        return "delivered"
    update_dt = parse_epost_datetime(last_update_at) or parse_epost_datetime(fallback_at)
    if update_dt and datetime.now(timezone.utc) - update_dt >= timedelta(days=12):
        return "lost"
    return "pending"


def refresh_epost_lost_statuses(store_id: Optional[int] = None) -> None:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, status, last_update_at, created_at, epost_status
            FROM epost_global_tracking
            WHERE (? IS NULL OR store_id=?)
              AND epost_status != 'delivered'
            """,
            (store_id, store_id),
        ).fetchall()
        for row in rows:
            next_status = epost_status_from_update(row["status"] or "", row["last_update_at"], row["created_at"])
            if next_status != row["epost_status"]:
                conn.execute(
                    "UPDATE epost_global_tracking SET epost_status=?, updated_at=? WHERE id=?",
                    (next_status, utc_now(), row["id"]),
                )


def epost_tracking_rows(store_id: Optional[int] = None) -> list[dict[str, Any]]:
    refresh_epost_lost_statuses(store_id)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT epost_global_tracking.*, stores.name AS store_name,
                   COALESCE(
                       (SELECT SUM(shipping_fee) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(shipping_fee) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_fee,
                   COALESCE(
                       (SELECT SUM(fulfilment_fee) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(fulfilment_fee) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS fulfilment_fee,
                   COALESCE(
                       (SELECT SUM(total_cost) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(total_cost) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_total,
                   COALESCE(
                       (SELECT COUNT(*) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       0
                   ) AS shipping_tracking_matches,
                   COALESCE(
                       (SELECT COUNT(*) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_order_matches
            FROM epost_global_tracking
            JOIN stores ON stores.id = epost_global_tracking.store_id
            WHERE (? IS NULL OR epost_global_tracking.store_id=?)
            ORDER BY
              CASE WHEN epost_status='lost' THEN 0 ELSE 1 END,
              CASE WHEN epost_status='delivered' THEN 1 ELSE 0 END,
              last_checked_at IS NULL DESC,
              last_checked_at ASC,
              updated_at DESC
            """,
            (store_id, store_id),
        ).fetchall()
    data = rows_to_dicts(rows)
    stores = {int(store["id"]): store for store in list_stores()}
    for row in data:
        store = stores.get(int(row["store_id"]))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        row["amazon_order_url"] = row.get("amazon_order_url") or order_line_amazon_url(row.get("amazon_order_id") or "")
        row["tracking_url"] = row.get("tracking_url") or f"https://epgtrack.com/{row.get('tracking_code')}"
        row["shipping_match_type"] = "tracking" if int(row.get("shipping_tracking_matches") or 0) else "order" if int(row.get("shipping_order_matches") or 0) else ""
    return data


def paged_epost_tracking_rows(
    store_id: Optional[int] = None,
    page: int = 1,
    per_page: int = 100,
    status: str = "all",
) -> tuple[list[dict[str, Any]], int, int, int]:
    refresh_epost_lost_statuses(store_id)
    page, per_page, offset = pagination_bounds(page, per_page)
    status = clean_text(status or "all").lower()
    status_clause = ""
    params: list[Any] = [store_id, store_id]
    if status == "pending":
        status_clause = " AND epost_status NOT IN ('delivered', 'lost')"
    elif status in {"delivered", "lost"}:
        status_clause = " AND epost_status = ?"
        params.append(status)
    with db() as conn:
        total = int(conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM epost_global_tracking
            WHERE (? IS NULL OR store_id=?)
            {status_clause}
            """,
            params,
        ).fetchone()["count"] or 0)
        rows = conn.execute(
            f"""
            SELECT epost_global_tracking.*, stores.name AS store_name,
                   COALESCE(
                       (SELECT SUM(shipping_fee) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(shipping_fee) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_fee,
                   COALESCE(
                       (SELECT SUM(fulfilment_fee) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(fulfilment_fee) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS fulfilment_fee,
                   COALESCE(
                       (SELECT SUM(total_cost) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       (SELECT SUM(total_cost) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_total,
                   COALESCE(
                       (SELECT COUNT(*) FROM shipping_charges WHERE UPPER(tracking_number)=UPPER(epost_global_tracking.tracking_code)),
                       0
                   ) AS shipping_tracking_matches,
                   COALESCE(
                       (SELECT COUNT(*) FROM shipping_charges WHERE odoo_order_name=epost_global_tracking.odoo_order_name),
                       0
                   ) AS shipping_order_matches
            FROM epost_global_tracking
            JOIN stores ON stores.id = epost_global_tracking.store_id
            WHERE (? IS NULL OR epost_global_tracking.store_id=?)
            {status_clause}
            ORDER BY
              CASE WHEN epost_status='lost' THEN 0 ELSE 1 END,
              CASE WHEN epost_status='delivered' THEN 1 ELSE 0 END,
              last_checked_at IS NULL DESC,
              last_checked_at ASC,
              updated_at DESC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()
    data = rows_to_dicts(rows)
    stores = {int(store["id"]): store for store in list_stores()}
    for row in data:
        store = stores.get(int(row["store_id"]))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        row["amazon_order_url"] = row.get("amazon_order_url") or order_line_amazon_url(row.get("amazon_order_id") or "")
        row["tracking_url"] = row.get("tracking_url") or f"https://epgtrack.com/{row.get('tracking_code')}"
        row["shipping_match_type"] = "tracking" if int(row.get("shipping_tracking_matches") or 0) else "order" if int(row.get("shipping_order_matches") or 0) else ""
    return data, total, page, per_page


def due_epost_tracking_rows(days: int = 1, store_id: Optional[int] = None) -> list[dict[str, Any]]:
    days = max(0, min(30, int(days or 1)))
    cutoff = time.time() - days * 86400
    rows = epost_tracking_rows(store_id)
    due = []
    for row in rows:
        if str(row.get("epost_status") or "").lower() == "delivered":
            continue
        checked = str(row.get("last_checked_at") or "")
        if not checked:
            due.append(row)
            continue
        try:
            checked_ts = datetime.fromisoformat(checked.replace("Z", "+00:00")).timestamp()
        except Exception:
            checked_ts = 0
        if checked_ts <= cutoff:
            due.append(row)
    return due


def duplicate_tracking_rows(store_id: Optional[int] = None, q: str = "") -> list[dict[str, Any]]:
    refresh_epost_lost_statuses(store_id)
    term = clean_text(q).lower()
    with db() as conn:
        epost_candidates = conn.execute(
            """
            SELECT UPPER(tracking_code) AS tracking_code
            FROM epost_global_tracking
            WHERE COALESCE(tracking_code, '') != ''
              AND (? IS NULL OR store_id=?)
            GROUP BY UPPER(tracking_code)
            HAVING COUNT(*) > 1 OR COUNT(DISTINCT COALESCE(odoo_order_name, '')) > 1
            """,
            (store_id, store_id),
        ).fetchall()
        shipping_candidates = conn.execute(
            """
            SELECT UPPER(tracking_number) AS tracking_code
            FROM shipping_charges
            WHERE COALESCE(tracking_number, '') != ''
            GROUP BY UPPER(tracking_number)
            HAVING COUNT(*) > 1 OR COUNT(DISTINCT COALESCE(odoo_order_name, '')) > 1
            """
        ).fetchall()
        candidate_codes = sorted({
            clean_text(row["tracking_code"]).upper()
            for row in [*epost_candidates, *shipping_candidates]
            if clean_text(row["tracking_code"])
        })
        stores = {int(store["id"]): store for store in list_stores()}
        results: list[dict[str, Any]] = []
        for code in candidate_codes:
            epost_rows = rows_to_dicts(
                conn.execute(
                    """
                    SELECT epost_global_tracking.*, stores.name AS store_name
                    FROM epost_global_tracking
                    JOIN stores ON stores.id = epost_global_tracking.store_id
                    WHERE UPPER(epost_global_tracking.tracking_code)=?
                      AND (? IS NULL OR epost_global_tracking.store_id=?)
                    ORDER BY epost_global_tracking.updated_at DESC, epost_global_tracking.id DESC
                    """,
                    (code, store_id, store_id),
                ).fetchall()
            )
            shipping_rows = rows_to_dicts(
                conn.execute(
                    """
                    SELECT shipping_charges.*, shipping_imports.filename AS import_filename, shipping_imports.month AS import_month
                    FROM shipping_charges
                    LEFT JOIN shipping_imports ON shipping_imports.id = shipping_charges.import_id
                    WHERE UPPER(shipping_charges.tracking_number)=?
                    ORDER BY shipping_charges.created_at DESC, shipping_charges.id DESC
                    """,
                    (code,),
                ).fetchall()
            )
            if store_id and not epost_rows:
                store_order_names = {
                    row["odoo_order_name"]
                    for row in conn.execute(
                        "SELECT DISTINCT odoo_order_name FROM order_lines WHERE store_id=?",
                        (store_id,),
                    ).fetchall()
                }
                shipping_rows = [row for row in shipping_rows if row.get("odoo_order_name") in store_order_names]
                if not shipping_rows:
                    continue
            order_names = sorted({
                clean_text(row.get("odoo_order_name"))
                for row in [*epost_rows, *shipping_rows]
                if clean_text(row.get("odoo_order_name"))
            })
            amazon_order_ids = sorted({
                clean_text(row.get("amazon_order_id"))
                for row in epost_rows
                if clean_text(row.get("amazon_order_id"))
            })
            if order_names:
                placeholders = ",".join("?" for _ in order_names)
                docs = rows_to_dicts(
                    conn.execute(
                        f"""
                        SELECT id, document_type, odoo_order_name, tax_region, country_code,
                               stored_filename, storage_url, invoice_date, created_at
                        FROM accounting_documents
                        WHERE odoo_order_name IN ({placeholders})
                        ORDER BY created_at DESC
                        """,
                        order_names,
                    ).fetchall()
                )
            else:
                docs = []
            statuses = sorted({
                clean_text(row.get("epost_status") or row.get("status"))
                for row in epost_rows
                if clean_text(row.get("epost_status") or row.get("status"))
            })
            destinations = sorted({
                clean_text(row.get("destination"))
                for row in epost_rows
                if clean_text(row.get("destination"))
            })
            shipping_fee = round(sum(float(row.get("shipping_fee") or 0) for row in shipping_rows), 2)
            fulfilment_fee = round(sum(float(row.get("fulfilment_fee") or 0) for row in shipping_rows), 2)
            shipping_total = round(sum(float(row.get("total_cost") or 0) for row in shipping_rows), 2)
            last_seen_values = [
                clean_text(row.get("updated_at") or row.get("last_checked_at") or row.get("last_update_at"))
                for row in epost_rows
            ] + [clean_text(row.get("created_at") or row.get("shipment_date")) for row in shipping_rows]
            last_seen = max([value for value in last_seen_values if value], default="")
            reasons = []
            if len(epost_rows) > 1:
                reasons.append("ePost duplicate")
            if len(shipping_rows) > 1:
                reasons.append("invoice duplicate")
            if len(order_names) > 1:
                reasons.append("multiple orders")
            if epost_rows and shipping_rows:
                epost_orders = {clean_text(row.get("odoo_order_name")) for row in epost_rows if clean_text(row.get("odoo_order_name"))}
                shipping_orders = {clean_text(row.get("odoo_order_name")) for row in shipping_rows if clean_text(row.get("odoo_order_name"))}
                if epost_orders and shipping_orders and epost_orders != shipping_orders:
                    reasons.append("order mismatch")
            if not reasons:
                continue
            row = {
                "id": code,
                "tracking_code": code,
                "tracking_url": f"https://epgtrack.com/{code}",
                "duplicate_reason": ", ".join(reasons),
                "epost_row_count": len(epost_rows),
                "shipping_row_count": len(shipping_rows),
                "order_count": len(order_names),
                "odoo_order_names": order_names,
                "amazon_order_ids": amazon_order_ids,
                "statuses": statuses,
                "destinations": destinations,
                "shipping_fee": shipping_fee,
                "fulfilment_fee": fulfilment_fee,
                "shipping_total": shipping_total or round(shipping_fee + fulfilment_fee, 2),
                "invoice_count": len(docs),
                "documents": docs,
                "epost_rows": epost_rows,
                "shipping_rows": shipping_rows,
                "last_seen_at": last_seen,
            }
            for epost_row in row["epost_rows"]:
                store = stores.get(int(epost_row.get("store_id") or 0))
                epost_row["odoo_order_url"] = odoo_order_admin_url(store, epost_row.get("odoo_order_id")) if store else ""
                epost_row["amazon_order_url"] = epost_row.get("amazon_order_url") or order_line_amazon_url(epost_row.get("amazon_order_id") or "")
                epost_row["tracking_url"] = epost_row.get("tracking_url") or row["tracking_url"]
            haystack = " ".join(
                [
                    row["tracking_code"],
                    row["duplicate_reason"],
                    " ".join(order_names),
                    " ".join(amazon_order_ids),
                    " ".join(statuses),
                    " ".join(str(doc.get("stored_filename") or "") for doc in docs),
                ]
            ).lower()
            if term and term not in haystack:
                continue
            results.append(row)
    return sorted(results, key=lambda item: (int(item["order_count"]), int(item["shipping_row_count"]), item["last_seen_at"]), reverse=True)


def write_report(store_id: int) -> tuple[str, bytes, str]:
    filename = f"amazon_orders_store_{store_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with db() as conn:
        rows = conn.execute("SELECT * FROM order_lines WHERE store_id=? ORDER BY updated_at DESC", (store_id,)).fetchall()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["store_id", "odoo_order", "odoo_line_id", "product", "asin", "quantity", "engine", "state", "amazon_account", "tracking_status", "amazon_order_id", "amazon_order_url", "error"])
    for row in rows:
        writer.writerow([store_id, row["odoo_order_name"], row["odoo_line_id"], row["product_name"], row["asin"], row["quantity"], row["order_engine"], row["state"], row["amazon_account_name"], row["tracking_status"], row["amazon_order_id"], row["amazon_order_url"], row["last_error"]])
    data = buffer.getvalue().encode("utf-8")
    _, storage_url = upload_storage_bytes(data, f"reports/{filename}", "text/csv")
    return filename, data, storage_url


EXPORT_PART_SIZE = 5000
EXPORT_VIEWS = {"orders", "tracking", "epost", "duplicate_tracking", "fulfilment_pending", "missing", "bulk", "costly"}
_export_lock = threading.Lock()
_active_export_jobs: set[str] = set()


DEFAULT_EXPORT_COLUMNS: dict[str, list[dict[str, str]]] = {
    "orders": [
        {"key": "odoo_order_name", "label": "Odoo Order"},
        {"key": "product_name", "label": "Product"},
        {"key": "default_code", "label": "Reference"},
        {"key": "asin", "label": "ASIN"},
        {"key": "quantity", "label": "Qty"},
        {"key": "odoo_status_label", "label": "Odoo Status"},
        {"key": "state", "label": "State"},
        {"key": "amazon_account_name", "label": "Amazon Account"},
        {"key": "tracking_status", "label": "Tracking"},
        {"key": "amazon_order_id", "label": "Amazon Order"},
        {"key": "fulfilment_note", "label": "Comments"},
        {"key": "last_error", "label": "Error"},
    ],
    "tracking": [
        {"key": "odoo_order_names", "label": "Odoo Orders"},
        {"key": "amazon_order_id", "label": "Amazon Order"},
        {"key": "tracking_status", "label": "Status"},
        {"key": "carrier_tracking", "label": "Carrier / Tracking"},
        {"key": "latest_update", "label": "Latest Update"},
        {"key": "tracking_checked_at", "label": "Checked"},
    ],
    "epost": [
        {"key": "store_name", "label": "Store"},
        {"key": "odoo_order_name", "label": "Odoo Order"},
        {"key": "amazon_order_id", "label": "Amazon Order"},
        {"key": "tracking_code", "label": "ePost Tracking"},
        {"key": "status", "label": "Status"},
        {"key": "last_update_at", "label": "Last Update"},
        {"key": "destination", "label": "Destination"},
        {"key": "awb", "label": "AWB"},
        {"key": "last_checked_at", "label": "Checked"},
    ],
    "duplicate_tracking": [
        {"key": "tracking_code", "label": "Tracking Code"},
        {"key": "duplicate_reason", "label": "Reason"},
        {"key": "epost_row_count", "label": "ePost Rows"},
        {"key": "shipping_row_count", "label": "Invoice Rows"},
        {"key": "order_count", "label": "Linked Orders"},
        {"key": "odoo_order_names", "label": "Odoo Orders"},
        {"key": "amazon_order_ids", "label": "Amazon Orders"},
        {"key": "statuses", "label": "Statuses"},
        {"key": "shipping_fee", "label": "Shipping Fee"},
        {"key": "fulfilment_fee", "label": "Fulfilment Fee"},
        {"key": "shipping_total", "label": "Total Charges"},
        {"key": "invoice_count", "label": "Stored Invoices"},
        {"key": "last_seen_at", "label": "Last Seen"},
    ],
    "fulfilment_pending": [
        {"key": "store_name", "label": "Store"},
        {"key": "odoo_order_name", "label": "Odoo Order"},
        {"key": "amazon_order_id", "label": "Amazon Order"},
        {"key": "carrier_tracking", "label": "Carrier / Tracking"},
        {"key": "tracking_status", "label": "Tracking"},
        {"key": "picking_summary", "label": "Odoo Pickings"},
        {"key": "fulfilment_status", "label": "Status"},
        {"key": "message", "label": "Message"},
    ],
    "missing": [
        {"key": "odoo_order_name", "label": "Odoo Order"},
        {"key": "missing_asin", "label": "Missing ASIN"},
        {"key": "product_name", "label": "Product"},
        {"key": "quantity", "label": "Qty"},
        {"key": "last_error", "label": "Error"},
        {"key": "replacement_asin", "label": "Replacement"},
    ],
    "bulk": [
        {"key": "asin", "label": "ASIN"},
        {"key": "quantity", "label": "Qty"},
        {"key": "order_names", "label": "Orders"},
        {"key": "product_names", "label": "Products"},
        {"key": "status", "label": "Status"},
    ],
    "costly": [
        {"key": "odoo_order_name", "label": "Order"},
        {"key": "asin", "label": "ASIN"},
        {"key": "product_name", "label": "Product"},
        {"key": "cost_review_loss", "label": "Loss"},
        {"key": "last_error", "label": "Error"},
    ],
}


def normalize_export_columns(view: str, columns: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed = {column["key"] for column in DEFAULT_EXPORT_COLUMNS.get(view, [])}
    normalized = [
        {"key": clean_text(column.get("key")), "label": clean_text(column.get("label")) or clean_text(column.get("key"))}
        for column in columns or []
        if clean_text(column.get("key")) in allowed
    ]
    return normalized or DEFAULT_EXPORT_COLUMNS[view]


def export_queryset(view: str, store_id: Optional[int], selected_ids: list[Union[int, str]], select_all: bool, filters: dict[str, Any]) -> list[dict[str, Any]]:
    if view == "orders":
        with db() as conn:
            params: list[Any] = [store_id, store_id]
            id_clause = ""
            search_clause = ""
            term = clean_text(str(filters.get("q") or "")).lower()
            if term:
                search_clause = """
                 AND LOWER(
                   COALESCE(odoo_order_name, '') || ' ' ||
                   COALESCE(product_name, '') || ' ' ||
                   COALESCE(asin, '') || ' ' ||
                   COALESCE(state, '') || ' ' ||
                   COALESCE(odoo_status_label, '') || ' ' ||
                   COALESCE(amazon_order_id, '') || ' ' ||
                   COALESCE(amazon_account_name, '') || ' ' ||
                   COALESCE(fulfilment_note, '')
                 ) LIKE ?
                """
                params.append(f"%{term}%")
            if not select_all and selected_ids:
                ids = [int(value) for value in selected_ids]
                id_clause = f" AND id IN ({','.join('?' for _ in ids)})"
                params.extend(ids)
            rows = conn.execute(
                f"""
                SELECT * FROM order_lines
                WHERE (? IS NULL OR store_id=?)
                {search_clause}
                {id_clause}
                ORDER BY updated_at DESC, id DESC
                """,
                params,
            ).fetchall()
        return rows_to_dicts(rows)
    if view == "missing":
        with db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM order_lines
                WHERE state='missing'
                  AND (? IS NULL OR store_id=?)
                ORDER BY updated_at DESC, odoo_order_id DESC, id ASC
                """,
                (store_id, store_id),
            ).fetchall()
        data = rows_to_dicts(rows)
    elif view == "costly":
        with db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM order_lines
                WHERE state='costly'
                  AND (? IS NULL OR store_id=?)
                ORDER BY updated_at DESC, odoo_order_id DESC, id ASC
                """,
                (store_id, store_id),
            ).fetchall()
        data = rows_to_dicts(rows)
    elif view == "epost":
        status = clean_text(str(filters.get("status") or "all"))
        data = epost_tracking_rows(store_id)
        if status == "pending":
            data = [row for row in data if row.get("epost_status") not in {"delivered", "lost"}]
        elif status in {"delivered", "lost"}:
            data = [row for row in data if row.get("epost_status") == status]
    elif view == "duplicate_tracking":
        data = duplicate_tracking_rows(store_id, clean_text(str(filters.get("q") or "")))
    elif view == "tracking":
        grouped: dict[str, dict[str, Any]] = {}
        for row in tracking_rows(store_id):
            order_id = str(row.get("amazon_order_id") or "")
            if not order_id:
                continue
            entry = grouped.setdefault(
                order_id,
                {
                    "amazon_order_id": order_id,
                    "amazon_order_url": row.get("amazon_order_url") or order_line_amazon_url(order_id),
                    "odoo_order_names": [],
                    "lines": [],
                    "tracking_checked_at": row.get("tracking_checked_at") or "",
                    "tracking_status": row.get("tracking_status") or "",
                },
            )
            if row.get("odoo_order_name") not in entry["odoo_order_names"]:
                entry["odoo_order_names"].append(row.get("odoo_order_name"))
            entry["lines"].append(row)
        data = list(grouped.values())
    elif view == "fulfilment_pending":
        data = delivered_unfulfilled_rows(store_id)
    elif view == "bulk":
        days = int(filters.get("days") or 2)
        data = bulk_opportunity_groups(store_id, days)
        for row in data:
            row["status"] = "missing review" if row.get("has_missing_order") else "ready"
    else:
        data = []
    if not select_all and selected_ids:
        selected_text = {str(value) for value in selected_ids}
        id_key = "amazon_order_id" if view == "tracking" else "asin" if view == "bulk" else "tracking_code" if view == "duplicate_tracking" else "id"
        data = [row for row in data if str(row.get(id_key) or "") in selected_text]
    return data


def export_cell(row: dict[str, Any], key: str) -> Any:
    if key in {"order_names", "product_names", "odoo_order_names", "amazon_order_ids", "statuses", "destinations"}:
        value = row.get(key) or []
        return ", ".join(str(item) for item in value) if isinstance(value, list) else value
    if key == "carrier_tracking":
        try:
            packages = json.loads(row.get("tracking_payload") or "[]") if isinstance(row.get("tracking_payload"), str) else []
        except Exception:
            packages = []
        if not packages and row.get("lines"):
            packages = []
            for line in row.get("lines") or []:
                try:
                    packages.extend(json.loads(line.get("tracking_payload") or "[]"))
                except Exception:
                    pass
        return " | ".join(f"{pkg.get('carrier') or 'Carrier'} {pkg.get('tracking_id') or ''}".strip() for pkg in packages) or row.get("amazon_order_url", "")
    if key == "latest_update":
        try:
            packages = json.loads(row.get("tracking_payload") or "[]") if isinstance(row.get("tracking_payload"), str) else []
        except Exception:
            packages = []
        if not packages and row.get("lines"):
            for line in row.get("lines") or []:
                try:
                    packages.extend(json.loads(line.get("tracking_payload") or "[]"))
                except Exception:
                    pass
        latest = (packages[0] or {}).get("latest_event") if packages else {}
        return " ".join(str(latest.get(part) or "") for part in ("date", "time", "message")).strip() if isinstance(latest, dict) else ""
    if key == "picking_summary":
        names = row.get("picking_names") or []
        states = row.get("picking_states") or []
        return ", ".join(f"{name}: {states[index] if index < len(states) else 'unknown'}" for index, name in enumerate(names))
    value = row.get(key)
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def s3_storage_client() -> Optional[Any]:
    settings = get_service_settings()
    if not settings.get("storage_s3_endpoint") or not settings.get("storage_s3_bucket"):
        return None
    try:
        import boto3  # type: ignore
    except Exception:
        return None
    return boto3.client(
        "s3",
        endpoint_url=s3_endpoint_base(settings["storage_s3_endpoint"]),
        region_name=settings["storage_s3_region"] or "auto",
        aws_access_key_id=settings["storage_s3_access_key_id"],
        aws_secret_access_key=settings["storage_s3_secret_access_key"],
    )


def require_storage_client() -> Any:
    client = s3_storage_client()
    if not client:
        raise RuntimeError("Cloudflare R2/S3 storage is required. Configure storage_s3_endpoint, bucket, access key, and secret key.")
    return client


def presigned_storage_url(key: str, expires_in: int = 7 * 86400) -> str:
    if not key:
        return ""
    settings = get_service_settings()
    client = require_storage_client()
    return client.generate_presigned_url("get_object", Params={"Bucket": settings["storage_s3_bucket"], "Key": key}, ExpiresIn=expires_in)


def upload_export_file(path: Path, key: str) -> tuple[str, str]:
    settings = get_service_settings()
    client = require_storage_client()
    bucket = settings["storage_s3_bucket"]
    client.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": "text/csv"})
    return key, presigned_storage_url(key)


def upload_storage_bytes(data: bytes, key: str, content_type: str = "application/octet-stream") -> tuple[str, str]:
    settings = get_service_settings()
    client = require_storage_client()
    bucket = settings["storage_s3_bucket"]
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type or "application/octet-stream")
    return key, presigned_storage_url(key)


def normalize_order_reference(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text.strip()


def parse_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except Exception:
        return default


def safe_filename(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return text.strip("._") or "file"


def tax_region_for_country(country_code: str) -> str:
    return "india" if str(country_code or "").strip().upper() == "IN" else "international"


def parse_iso_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text[:len(fmt)], fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def month_bounds(month: str) -> tuple[datetime, datetime, str]:
    parsed = parse_iso_date(f"{month[:7]}-01" if re.fullmatch(r"\d{4}-\d{2}", str(month or "")[:7]) else str(month or ""))
    if not parsed:
        now = datetime.now(timezone.utc)
        parsed = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    last_day = monthrange(parsed.year, parsed.month)[1]
    start = datetime(parsed.year, parsed.month, 1, tzinfo=timezone.utc)
    end = datetime(parsed.year, parsed.month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end, f"{parsed.year:04d}-{parsed.month:02d}"


def xml_text(node: Optional[ET.Element]) -> str:
    return "".join(node.itertext()) if node is not None else ""


def excel_serial_to_date(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return str(value or "")
    if number < 20000 or number > 80000:
        return str(value or "")
    return (datetime(1899, 12, 30) + timedelta(days=number)).date().isoformat()


def record_date_text(record: dict[str, Any]) -> str:
    value = record.get("Order Date") or record.get("Date - Order Date") or record.get("Ship Date") or record.get("Date - Shipped Date") or ""
    return excel_serial_to_date(value) if isinstance(value, (int, float)) else str(value or "")


def load_xlsx_rows(data: bytes, preferred_month: str = "") -> list[dict[str, Any]]:
    best_records: list[dict[str, Any]] = []
    best_score = -1
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:  # type: ignore[name-defined]
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [xml_text(item) for item in root.findall(".//a:si", ns)]
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}
        sheet_paths: list[str] = []
        for sheet in workbook.findall(".//a:sheet", ns):
            rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            target = rel_map.get(rel_id or "")
            if target:
                sheet_paths.append("xl/" + target.lstrip("/").replace("xl/", ""))
        for sheet_path in sheet_paths:
            if sheet_path not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(sheet_path))
            sheet_rows: list[list[Any]] = []
            for row in root.findall(".//a:sheetData/a:row", ns):
                values: list[Any] = []
                current_col = 0
                for cell in row.findall("a:c", ns):
                    ref = cell.attrib.get("r", "")
                    match = re.match(r"([A-Z]+)", ref)
                    if match:
                        col = 0
                        for ch in match.group(1):
                            col = col * 26 + ord(ch) - 64
                        while current_col < col - 1:
                            values.append(None)
                            current_col += 1
                    raw = cell.find("a:v", ns)
                    value: Any = raw.text if raw is not None else ""
                    if cell.attrib.get("t") == "s":
                        try:
                            value = shared[int(value)]
                        except Exception:
                            pass
                    elif cell.attrib.get("t") == "inlineStr":
                        value = xml_text(cell.find("a:is", ns))
                    else:
                        try:
                            value = float(value) if value not in {None, ""} else ""
                        except Exception:
                            pass
                    values.append(value)
                    current_col += 1
                if any(v not in {None, ""} for v in values):
                    sheet_rows.append(values)
            records = rows_to_records(sheet_rows)
            useful = [
                record for record in records
                if normalize_order_reference(record.get("Order#") or record.get("Order - Number"))
                and (record.get("Shipping Fee") not in {None, ""} or record.get("Carrier - Fee") not in {None, ""})
            ]
            month_hits = 0
            if preferred_month:
                month_hits = sum(1 for record in useful if record_date_text(record).startswith(preferred_month))
            score = (month_hits * 100000) + len(useful)
            if score > best_score:
                best_score = score
                best_records = records
    return best_records


def rows_to_records(rows: list[list[Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    best_index = 0
    best_score = -1
    for index, row in enumerate(rows[:25]):
        headers = [str(value or "").strip().lower() for value in row]
        score = sum(1 for name in headers if name in {"order#", "order - number", "shipping fee", "carrier - fee", "fulfillment fee", "total cost"})
        if score > best_score:
            best_index = index
            best_score = score
    headers = [str(value or "").strip() for value in rows[best_index]]
    records: list[dict[str, Any]] = []
    for row in rows[best_index + 1:]:
        record = {headers[index]: row[index] if index < len(row) else None for index in range(len(headers)) if headers[index]}
        if any(value not in {None, ""} for value in record.values()):
            records.append(record)
    return records


def load_tabular_upload(filename: str, data: bytes, preferred_month: str = "") -> list[dict[str, Any]]:
    if filename.lower().endswith(".csv"):
        text = data.decode("utf-8-sig")
        return list(csv.DictReader(text.splitlines()))
    if filename.lower().endswith(".xlsx"):
        return load_xlsx_rows(data, preferred_month)
    raise HTTPException(status_code=400, detail="Upload a CSV or XLSX file.")


def delete_export_storage_key(storage_key: str) -> None:
    if not storage_key:
        return
    client = s3_storage_client()
    if not client:
        return
    try:
        client.delete_object(Bucket=get_service_settings()["storage_s3_bucket"], Key=storage_key)
    except Exception:
        pass


def run_export_job(job_id: str) -> None:
    try:
        with db() as conn:
            job = conn.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            return
        columns = json.loads(job["columns_json"] or "[]")
        filters = json.loads(job["filters_json"] or "{}")
        selected_ids = json.loads(job["selected_ids_json"] or "[]")
        rows = export_queryset(job["view"], job["store_id"], selected_ids, bool(job["select_all"]), filters)
        with db() as conn:
            conn.execute(
                "UPDATE export_jobs SET status='running', total_records=?, updated_at=? WHERE id=?",
                (len(rows), utc_now(), job_id),
            )
        for part_index in range(0, len(rows) or 1, EXPORT_PART_SIZE):
            with db() as conn:
                stop_requested = conn.execute("SELECT stop_requested FROM export_jobs WHERE id=?", (job_id,)).fetchone()["stop_requested"]
            if stop_requested:
                with db() as conn:
                    conn.execute("UPDATE export_jobs SET status='cancelled', updated_at=?, completed_at=? WHERE id=?", (utc_now(), utc_now(), job_id))
                return
            part_rows = rows[part_index:part_index + EXPORT_PART_SIZE]
            part_number = part_index // EXPORT_PART_SIZE + 1
            filename = f"{job['view']}_{job_id}_part_{part_number}.csv"
            with tempfile.NamedTemporaryFile("w", newline="", encoding="utf-8", suffix=".csv", delete=True) as fh:
                writer = csv.writer(fh)
                writer.writerow([column["label"] for column in columns])
                for row in part_rows:
                    writer.writerow([export_cell(row, column["key"]) for column in columns])
                fh.flush()
                storage_key, storage_url = upload_export_file(Path(fh.name), f"exports/{job_id}/{filename}")
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO export_files
                    (job_id, part_number, row_count, filename, local_path, storage_key, storage_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (job_id, part_number, len(part_rows), filename, "", storage_key, storage_url, utc_now()),
                )
                conn.execute(
                    """
                    UPDATE export_jobs
                    SET processed_records=?, part_count=?, updated_at=?
                    WHERE id=?
                    """,
                    (min(part_index + len(part_rows), len(rows)), part_number, utc_now(), job_id),
                )
            if not rows:
                break
        with db() as conn:
            conn.execute("UPDATE export_jobs SET status='completed', updated_at=?, completed_at=? WHERE id=?", (utc_now(), utc_now(), job_id))
    except Exception as exc:
        with db() as conn:
            conn.execute("UPDATE export_jobs SET status='failed', error=?, updated_at=?, completed_at=? WHERE id=?", (str(exc), utc_now(), utc_now(), job_id))
    finally:
        with _export_lock:
            _active_export_jobs.discard(job_id)


def start_export_job(job_id: str) -> None:
    with _export_lock:
        if job_id in _active_export_jobs:
            return
        _active_export_jobs.add(job_id)
    threading.Thread(target=run_export_job, args=(job_id,), daemon=True).start()


def date_range_from_params(period: str = "monthly", month: str = "", start: str = "", end: str = "") -> tuple[datetime, datetime, str]:
    if period == "monthly" or month:
        return month_bounds(month or datetime.now(timezone.utc).strftime("%Y-%m"))
    parsed_start = parse_iso_date(start) or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    parsed_end = parse_iso_date(end) or parsed_start
    if period == "weekly" and not end:
        parsed_end = parsed_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    if period == "daily" and not end:
        parsed_end = parsed_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed_start, parsed_end, parsed_start.strftime("%Y-%m")


def profit_loss_data(
    store_id: Optional[int] = None,
    period: str = "monthly",
    month: str = "",
    start: str = "",
    end: str = "",
    q: str = "",
) -> dict[str, Any]:
    start_dt, end_dt, resolved_month = date_range_from_params(period, month, start, end)
    start_text = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_text = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    search = f"%{q.strip()}%" if q.strip() else None
    normalize_converted_profit_columns(store_id, start_text, end_text)
    with db() as conn:
        rows = conn.execute(
            """
            WITH line_orders AS (
                SELECT
                    store_id,
                    odoo_order_id,
                    odoo_order_name,
                    MIN(COALESCE(NULLIF(odoo_order_date, ''), NULLIF(raw_json::jsonb -> 'order' ->> 'date_order', ''), NULLIF(ordered_at, ''), NULLIF(pulled_at, ''), created_at)) AS order_date,
                    SUM(
                        CASE
                            WHEN store_total_native IS NOT NULL AND COALESCE(store_currency_rate_to_usd, 0) > 0
                                THEN COALESCE(store_total_native, 0) * COALESCE(store_currency_rate_to_usd, 1)
                            ELSE COALESCE(store_total_price, store_unit_price * quantity, 0)
                        END
                    ) AS odoo_order_value,
                    SUM(COALESCE(store_delivery_native, 0) * COALESCE(store_currency_rate_to_usd, 1)) AS collected_delivery,
                    SUM(COALESCE(store_discount_native, 0) * COALESCE(store_currency_rate_to_usd, 1)) AS order_discounts,
                    SUM(COALESCE(amazon_total_price, amazon_unit_price * quantity, 0)) AS amazon_order_value,
                    STRING_AGG(DISTINCT amazon_order_id, ',') AS amazon_order_ids,
                    STRING_AGG(DISTINCT amazon_account_name, ',') AS amazon_accounts,
                    COUNT(*) AS line_count
                FROM order_lines
                WHERE (? IS NULL OR store_id=?)
                GROUP BY store_id, odoo_order_id, odoo_order_name
            ),
            shipping AS (
                SELECT
                    odoo_order_name,
                    SUM(shipping_fee) AS shipping_fee,
                    SUM(fulfilment_fee) AS fulfilment_fee,
                    SUM(total_cost) AS shipping_total,
                    COUNT(*) AS package_count
                FROM shipping_charges
                GROUP BY odoo_order_name
            )
            SELECT
                line_orders.*,
                COALESCE(shipping.shipping_fee, 0) AS shipping_fee,
                COALESCE(shipping.fulfilment_fee, 0) AS fulfilment_fee,
                COALESCE(shipping.shipping_total, 0) AS shipping_total,
                COALESCE(shipping.package_count, 0) AS package_count
            FROM line_orders
            LEFT JOIN shipping ON shipping.odoo_order_name = line_orders.odoo_order_name
            WHERE line_orders.order_date BETWEEN ? AND ?
              AND (? IS NULL OR line_orders.odoo_order_name LIKE ? OR line_orders.amazon_order_ids LIKE ?)
            ORDER BY line_orders.order_date DESC, line_orders.odoo_order_id DESC
            """,
            (store_id, store_id, start_text, end_text, search, search, search),
        ).fetchall()
        imports = conn.execute("SELECT * FROM shipping_imports ORDER BY created_at DESC LIMIT 12").fetchall()
    order_rows = rows_to_dicts(rows)
    for row in order_rows:
        row["gross_profit"] = round(float(row["odoo_order_value"] or 0) - float(row["amazon_order_value"] or 0), 2)
        row["net_profit"] = round(row["gross_profit"] - float(row["shipping_fee"] or 0) - float(row["fulfilment_fee"] or 0), 2)
        row["margin_percent"] = round((row["net_profit"] / float(row["odoo_order_value"] or 1)) * 100, 2) if float(row["odoo_order_value"] or 0) else 0
    summary = {
        "orders": len(order_rows),
        "odoo_order_value": round(sum(float(row["odoo_order_value"] or 0) for row in order_rows), 2),
        "collected_delivery": round(sum(float(row.get("collected_delivery") or 0) for row in order_rows), 2),
        "order_discounts": round(sum(float(row.get("order_discounts") or 0) for row in order_rows), 2),
        "amazon_order_value": round(sum(float(row["amazon_order_value"] or 0) for row in order_rows), 2),
        "gross_profit": round(sum(float(row["gross_profit"] or 0) for row in order_rows), 2),
        "shipping_fee": round(sum(float(row["shipping_fee"] or 0) for row in order_rows), 2),
        "fulfilment_fee": round(sum(float(row["fulfilment_fee"] or 0) for row in order_rows), 2),
        "net_profit": round(sum(float(row["net_profit"] or 0) for row in order_rows), 2),
    }
    summary["margin_percent"] = round((summary["net_profit"] / summary["odoo_order_value"]) * 100, 2) if summary["odoo_order_value"] else 0
    grouped: dict[str, dict[str, Any]] = {}
    for row in order_rows:
        order_date = parse_iso_date(str(row.get("order_date") or "")) or start_dt
        if period == "daily":
            key = order_date.strftime("%Y-%m-%d")
        elif period == "weekly":
            iso = order_date.isocalendar()
            key = f"{iso.year}-W{iso.week:02d}"
        else:
            key = order_date.strftime("%Y-%m")
        entry = grouped.setdefault(key, {"period": key, "orders": 0, "odoo_order_value": 0, "amazon_order_value": 0, "shipping_fee": 0, "fulfilment_fee": 0, "net_profit": 0})
        entry["orders"] += 1
        for metric in ("odoo_order_value", "amazon_order_value", "shipping_fee", "fulfilment_fee", "net_profit"):
            entry[metric] = round(float(entry[metric] or 0) + float(row.get(metric) or 0), 2)
    return {
        "summary": summary,
        "period_rows": sorted(grouped.values(), key=lambda item: item["period"], reverse=True),
        "orders": order_rows,
        "imports": rows_to_dicts(imports),
        "start": start_dt.date().isoformat(),
        "end": end_dt.date().isoformat(),
        "month": resolved_month,
    }


def upload_shipping_records(filename: str, data: bytes, month: str, default_fulfilment_fee: float) -> dict[str, Any]:
    _, _, resolved_month = month_bounds(month)
    records = load_tabular_upload(filename, data, resolved_month)
    import_id = str(uuid.uuid4())
    now = utc_now()
    matched = 0
    inserted = 0
    with db() as conn:
        conn.execute(
            "INSERT INTO shipping_imports (id, filename, month, default_fulfilment_fee, created_at) VALUES (?, ?, ?, ?, ?)",
            (import_id, filename, resolved_month, float(default_fulfilment_fee or 4), now),
        )
        for record in records:
            order_value = record.get("Order#") or record.get("Order - Number") or record.get("order#") or record.get("order number")
            order_name = normalize_order_reference(order_value)
            if not order_name:
                continue
            shipping_fee = parse_float(record.get("Shipping Fee") or record.get("Carrier - Fee"))
            fulfilment_fee = parse_float(record.get("Fulfillment Fee"), float(default_fulfilment_fee or 4))
            total_cost = parse_float(record.get("Total Cost"), shipping_fee + fulfilment_fee)
            matched_count = int(conn.execute("SELECT COUNT(*) AS count FROM order_lines WHERE odoo_order_name=?", (order_name,)).fetchone()["count"] or 0)
            if matched_count:
                matched += 1
            conn.execute(
                """
                INSERT INTO shipping_charges
                (import_id, odoo_order_name, shipment_date, tracking_number, carrier, service, quantity,
                 shipping_fee, fulfilment_fee, total_cost, matched_line_count, raw_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    order_name,
                    str(record.get("Ship Date") or record.get("Date - Shipped Date") or ""),
                    str(record.get("Tracking#") or record.get("Shipment - Tracking Number") or ""),
                    str(record.get("Carrier") or record.get("Carrier - Name") or ""),
                    str(record.get("Service") or record.get("Shipment - Service") or ""),
                    parse_float(record.get("Qx") or record.get("Sum - Total of Line Item Quantity"), 1),
                    shipping_fee,
                    fulfilment_fee,
                    total_cost,
                    matched_count,
                    json.dumps(record, default=str),
                    now,
                ),
            )
            inserted += 1
        conn.execute(
            "UPDATE shipping_imports SET row_count=?, matched_count=?, unmatched_count=? WHERE id=?",
            (inserted, matched, max(0, inserted - matched), import_id),
        )
    return {"ok": True, "message": f"Imported {inserted} shipping rows. Matched {matched}, unmatched {max(0, inserted - matched)}.", "import_id": import_id}


def accounting_summary(q: str = "", document_type: str = "all", tax_region: str = "all") -> dict[str, Any]:
    clauses: list[str] = []
    params: list[Any] = []
    if q.strip():
        clauses.append("(odoo_order_name LIKE ? OR original_filename LIKE ?)")
        params.extend([f"%{q.strip()}%", f"%{q.strip()}%"])
    if document_type != "all":
        clauses.append("document_type=?")
        params.append(document_type)
    if tax_region != "all":
        clauses.append("tax_region=?")
        params.append(tax_region)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    with db() as conn:
        docs = conn.execute(
            f"SELECT * FROM accounting_documents {where} ORDER BY created_at DESC, id DESC LIMIT 500",
            params,
        ).fetchall()
        region_rows = conn.execute(
            """
            SELECT tax_region, document_type, COUNT(*) AS document_count, SUM(file_size) AS total_bytes
            FROM accounting_documents
            GROUP BY tax_region, document_type
            ORDER BY tax_region, document_type
            """
        ).fetchall()
    return {"documents": rows_to_dicts(docs), "summary": rows_to_dicts(region_rows)}


def render_odoo_invoice_pdf(odoo: OdooClient, invoice_id: int) -> bytes:
    attempts = [
        ("ir.actions.report", "_render_qweb_pdf", ["account.report_invoice", [invoice_id]]),
        ("ir.actions.report", "render_qweb_pdf", [[invoice_id]]),
    ]
    for model, method, args in attempts:
        try:
            result = odoo.execute(model, method, args)
            payload = result[0] if isinstance(result, (list, tuple)) else result
            if isinstance(payload, bytes):
                return payload
            if isinstance(payload, str):
                import base64

                return base64.b64decode(payload)
        except Exception:
            continue
    raise RuntimeError(f"Could not render Odoo invoice {invoice_id}.")


def delete_accounting_documents(order_name: str, document_type: str = "odoo") -> int:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, storage_key FROM accounting_documents WHERE odoo_order_name=? AND document_type=?",
            (order_name, document_type),
        ).fetchall()
        for row in rows:
            delete_export_storage_key(row["storage_key"] or "")
        conn.execute(
            "DELETE FROM accounting_documents WHERE odoo_order_name=? AND document_type=?",
            (order_name, document_type),
        )
    return len(rows)


def sync_odoo_accounting_documents(store_id: int, days: int = 30, limit: int = 200) -> dict[str, Any]:
    store = get_store(store_id)
    odoo = OdooClient(store)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(days or 30)))
    order_fields = odoo.existing_fields("sale.order", ["id", "name", "state", "invoice_status", "invoice_ids", "partner_id", "date_order"])
    orders = odoo.search_read(
        "sale.order",
        [("date_order", ">=", cutoff.strftime("%Y-%m-%d %H:%M:%S"))],
        order_fields,
        limit=max(1, min(1000, int(limit or 200))),
        order="date_order desc",
    )
    synced = 0
    deleted = 0
    errors: list[str] = []
    for order in orders:
        order_name = normalize_order_reference(order.get("name"))
        if not order_name:
            continue
        if order.get("state") == "cancel" or str(order.get("invoice_status") or "").lower() in {"no", "cancelled", "refunded"}:
            deleted += delete_accounting_documents(order_name, "odoo")
            continue
        invoice_ids = [int(value) for value in (order.get("invoice_ids") or [])]
        if not invoice_ids:
            continue
        country = ""
        partner = order.get("partner_id")
        if partner:
            partner_rows = odoo.read("res.partner", [int(partner[0])], odoo.existing_fields("res.partner", ["country_id"]))
            country_ref = partner_rows[0].get("country_id") if partner_rows else None
            if country_ref:
                country_rows = odoo.read("res.country", [int(country_ref[0])], odoo.existing_fields("res.country", ["code"]))
                country = str(country_rows[0].get("code") or "") if country_rows else ""
        invoice_rows = odoo.read("account.move", invoice_ids, odoo.existing_fields("account.move", ["id", "name", "state", "move_type", "invoice_date", "date"]))
        delete_accounting_documents(order_name, "odoo")
        for invoice in invoice_rows:
            if invoice.get("state") == "cancel":
                continue
            try:
                invoice_id = int(invoice["id"])
                pdf = render_odoo_invoice_pdf(odoo, invoice_id)
                region = tax_region_for_country(country)
                invoice_name = safe_filename(str(invoice.get("name") or invoice_id))
                stored_name = f"{safe_filename(order_name)}_odoo_{invoice_name}.pdf"
                key = f"accounting/{region}/{safe_filename(order_name)}/{stored_name}"
                storage_key, storage_url = upload_storage_bytes(pdf, key, "application/pdf")
                now = utc_now()
                with db() as conn:
                    conn.execute(
                        """
                        INSERT INTO accounting_documents
                        (document_type, odoo_order_name, country_code, tax_region, invoice_date, original_filename,
                         stored_filename, content_type, local_path, storage_key, storage_url, file_size, notes, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            "odoo",
                            order_name,
                            country,
                            region,
                            str(invoice.get("invoice_date") or invoice.get("date") or ""),
                            stored_name,
                            stored_name,
                            "application/pdf",
                            "",
                            storage_key,
                            storage_url,
                            len(pdf),
                            f"Odoo invoice id {invoice_id}",
                            now,
                            now,
                        ),
                    )
                synced += 1
            except Exception as exc:
                errors.append(f"{order_name}: {exc}")
    return {"ok": True, "synced": synced, "deleted": deleted, "errors": errors[:20], "message": f"Synced {synced} Odoo invoice(s), deleted {deleted} stale file(s)."}



def cleanup_expired_exports() -> None:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM export_files
            WHERE deleted_at IS NULL
              AND expires_at IS NOT NULL
              AND datetime(expires_at) <= datetime('now')
            """
        ).fetchall()
    for row in rows:
        delete_export_storage_key(row["storage_key"] or "")
        with db() as conn:
            conn.execute("UPDATE export_files SET deleted_at=? WHERE id=?", (utc_now(), row["id"]))


def sync_inventory_for_store(store_id: int) -> None:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM order_lines
            WHERE store_id=? AND COALESCE(amazon_order_id, '') != ''
              AND COALESCE(odoo_status_label, '') IN ('cancelled', 'refunded')
            """,
            (store_id,),
        ).fetchall()
    for row in rows:
        ensure_inventory_for_line(row)


def check_deliveries(store_id: int) -> tuple[int, int]:
    store = get_store(store_id)
    delivered = 0
    errors = 0
    with db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM order_lines
            WHERE store_id=? AND COALESCE(amazon_order_id, '') != '' AND state = 'ordered'
            """,
            (store_id,),
        ).fetchall()
    for row in rows:
        try:
            amazon = AmazonBusinessClient(get_amazon_account(row["amazon_account_id"]))
            details = amazon.order_details(row["amazon_order_id"])
            tracking_payloads: list[dict[str, Any]] = []
            for order_id, shipment_id, package_id in find_tracking_targets(details):
                try:
                    tracking_payloads.append(amazon.package_tracking(order_id, shipment_id, package_id))
                except Exception as exc:
                    tracking_payloads.append({"error": str(exc), "orderId": order_id, "shipmentId": shipment_id, "packageId": package_id})
            status = summarize_tracking(tracking_payloads, details)
            delivered_flag = status == "Delivered" or re.search(r"\b(delivered|complete|completed)\b", json.dumps(details), re.IGNORECASE) is not None
            if delivered_flag and env_bool("ODOO_VALIDATE_PICKINGS", True):
                dispatch_result = OdooClient(store).validate_pickings_for_order(int(row["odoo_order_id"]))
                OdooClient(store).post_order_note(int(row["odoo_order_id"]), f"Amazon delivered. {dispatch_result}")
            updated_inventory_row = None
            with db() as conn:
                conn.execute(
                    """
                    UPDATE order_lines
                    SET amazon_status=?, tracking_status=?, tracking_payload=?, tracking_checked_at=?, state=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        json.dumps(details, default=str)[:1000],
                        status,
                        json.dumps(tracking_payloads, default=str)[:4000],
                        utc_now(),
                        "dispatched" if delivered_flag else "ordered",
                        utc_now(),
                        row["id"],
                    ),
                )
                if delivered_flag:
                    updated_inventory_row = conn.execute("SELECT * FROM order_lines WHERE id=?", (row["id"],)).fetchone()
            if updated_inventory_row:
                ensure_inventory_for_line(updated_inventory_row)
            delivered += 1 if delivered_flag else 0
        except Exception as exc:
            errors += 1
            with db() as conn:
                conn.execute("UPDATE order_lines SET last_error=?, updated_at=? WHERE id=?", (str(exc), utc_now(), row["id"]))
    return delivered, errors


def parse_optional_int(value: Optional[Union[str, int]]) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if text else None


def clean_text(value: Optional[str], default: str = "") -> str:
    return str(value if value is not None else default).strip()


def normalize_ordering_engine(value: Optional[str]) -> str:
    engine = str(value or "rest").strip().lower()
    return engine if engine in ORDERING_ENGINES else "rest"


def normalize_cxml_auth_mode(value: Optional[str]) -> str:
    mode = str(value or "header").strip().lower()
    return mode if mode in CXML_AUTH_MODES else "header"


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(key: str, value: str) -> None:
    global _ADMIN_ACCESS_TOKEN_CACHE
    with db() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, utc_now()),
        )
    if key == "admin_access_token":
        with _ADMIN_ACCESS_TOKEN_CACHE_LOCK:
            _ADMIN_ACCESS_TOKEN_CACHE = ("", 0.0)


def get_service_settings() -> dict[str, str]:
    settings = dict(DEFAULT_SERVICE_SETTINGS)
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    for row in rows:
        if row["key"] in settings:
            settings[row["key"]] = str(row["value"] or "")
    return settings


def set_service_settings(values: dict[str, str]) -> None:
    global _ADMIN_ACCESS_TOKEN_CACHE
    now = utc_now()
    allowed = {key: str(value or "") for key, value in values.items() if key in DEFAULT_SERVICE_SETTINGS}
    if not allowed:
        return
    with db() as conn:
        for key, value in allowed.items():
            conn.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, value, now),
            )


def upsert_amazon_otp_record(parsed: Any, folder: str = "", uid: str = "") -> bool:
    order_id = clean_text(parsed.amazon_order_id)
    if not order_id or parsed.email_type not in {"otp", "dispatch"}:
        return False
    now = utc_now()
    with db() as conn:
        if uid:
            existing = conn.execute(
                "SELECT id FROM amazon_otp_email_uids WHERE folder=? AND uid=?",
                (folder, uid),
            ).fetchone()
            if existing:
                return False
        conn.execute(
            """
            INSERT INTO amazon_otp_records
              (amazon_order_id, otp, otp_required, tracking_url, shipment_id, package_index,
               recipient, product_summary, otp_email_subject, otp_email_date,
               dispatch_email_subject, dispatch_email_date, status, raw_json,
               last_email_uid, last_synced_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(amazon_order_id) DO UPDATE SET
              otp=COALESCE(NULLIF(excluded.otp, ''), amazon_otp_records.otp),
              otp_required=CASE WHEN excluded.otp_required=1 THEN 1 ELSE amazon_otp_records.otp_required END,
              tracking_url=COALESCE(NULLIF(excluded.tracking_url, ''), amazon_otp_records.tracking_url),
              shipment_id=COALESCE(NULLIF(excluded.shipment_id, ''), amazon_otp_records.shipment_id),
              package_index=COALESCE(NULLIF(excluded.package_index, ''), amazon_otp_records.package_index),
              recipient=COALESCE(NULLIF(excluded.recipient, ''), amazon_otp_records.recipient),
              product_summary=COALESCE(NULLIF(excluded.product_summary, ''), amazon_otp_records.product_summary),
              otp_email_subject=COALESCE(NULLIF(excluded.otp_email_subject, ''), amazon_otp_records.otp_email_subject),
              otp_email_date=COALESCE(NULLIF(excluded.otp_email_date, ''), amazon_otp_records.otp_email_date),
              dispatch_email_subject=COALESCE(NULLIF(excluded.dispatch_email_subject, ''), amazon_otp_records.dispatch_email_subject),
              dispatch_email_date=COALESCE(NULLIF(excluded.dispatch_email_date, ''), amazon_otp_records.dispatch_email_date),
              status=excluded.status,
              raw_json=excluded.raw_json,
              last_email_uid=COALESCE(NULLIF(excluded.last_email_uid, ''), amazon_otp_records.last_email_uid),
              last_synced_at=excluded.last_synced_at,
              updated_at=excluded.updated_at
            """,
            (
                order_id,
                parsed.otp if parsed.email_type == "otp" else "",
                1 if parsed.email_type == "otp" else 0,
                parsed.tracking_url,
                parsed.shipment_id,
                parsed.package_index,
                parsed.recipient,
                parsed.product_summary,
                parsed.subject if parsed.email_type == "otp" else "",
                parsed.email_date if parsed.email_type == "otp" else "",
                parsed.subject if parsed.email_type == "dispatch" else "",
                parsed.email_date if parsed.email_type == "dispatch" else "",
                parsed.email_type,
                parsed.raw_json,
                uid,
                now,
                now,
                now,
            ),
        )
        if uid:
            conn.execute(
                """
                INSERT INTO amazon_otp_email_uids
                  (folder, uid, message_id, amazon_order_id, email_type, subject, email_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(folder, uid) DO NOTHING
                """,
                (folder, uid, parsed.message_id, order_id, parsed.email_type, parsed.subject, parsed.email_date, now),
            )
    return True


def sync_amazon_otp_emails() -> dict[str, Any]:
    settings = get_service_settings()
    if not settings.get("amazon_otp_imap_host") or not settings.get("amazon_otp_imap_username") or not settings.get("amazon_otp_imap_password"):
        return {"ok": False, "message": "Amazon OTP IMAP settings are incomplete.", "processed": 0}
    folder = clean_text(settings.get("amazon_otp_imap_folder")) or "INBOX"
    since_days = int(float(settings.get("amazon_otp_imap_since_days") or 14))
    processed = 0
    matched = 0
    client = imap_connect(settings)
    try:
        status, _ = client.select(folder)
        if status != "OK":
            raise RuntimeError(f"Unable to select IMAP folder {folder}.")
        criteria = f'(SINCE "{imap_search_since(since_days)}")'
        status, data = client.uid("SEARCH", None, criteria)
        if status != "OK":
            status, data = client.uid("SEARCH", None, f'(SINCE "{imap_search_since(since_days)}")')
        uids = (data[0] or b"").split() if data else []
        for uid_bytes in uids[-500:]:
            uid = uid_bytes.decode("ascii", errors="ignore")
            status, payload = client.uid("FETCH", uid, "(RFC822)")
            if status != "OK" or not payload:
                continue
            raw = b""
            for item in payload:
                if isinstance(item, tuple) and item[1]:
                    raw = item[1]
                    break
            if not raw:
                continue
            processed += 1
            parsed = parse_amazon_email(raw)
            if upsert_amazon_otp_record(parsed, folder, uid):
                matched += 1
        set_setting("amazon_otp_last_sync_at", utc_now())
        set_setting("amazon_otp_last_sync_message", f"Processed {processed}, matched {matched}.")
        return {"ok": True, "processed": processed, "matched": matched, "message": f"Processed {processed} email(s), matched {matched} Amazon OTP/dispatch email(s)."}
    finally:
        try:
            client.logout()
        except Exception:
            pass


def amazon_otp_loop() -> None:
    last_run = 0.0
    while True:
        try:
            settings = get_service_settings()
            interval = int(float(settings.get("amazon_otp_imap_interval_minutes") or 5))
            if interval > 0 and time.time() - last_run >= interval * 60:
                sync_amazon_otp_emails()
                last_run = time.time()
        except Exception as exc:
            try:
                set_setting("amazon_otp_last_sync_message", str(exc)[:500])
                set_setting("amazon_otp_last_sync_at", utc_now())
            except Exception:
                pass
        time.sleep(60)


def backfill_missing_order_dates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = [row for row in rows if not clean_text(row.get("odoo_order_date")) and int(row.get("store_id") or 0) and int(row.get("odoo_order_id") or 0)]
    if not candidates:
        return rows
    updated_rows = [dict(row) for row in rows]
    parsed_dates: dict[int, str] = {}
    for row in candidates:
        try:
            payload = json.loads(row.get("raw_json") or "{}")
            order_date = clean_text((payload.get("order") or {}).get("date_order"))
            if order_date:
                parsed_dates[int(row["id"])] = order_date
        except Exception:
            continue
    if parsed_dates:
        with db() as conn:
            for row in updated_rows:
                order_date = parsed_dates.get(int(row.get("id") or 0))
                if not order_date:
                    continue
                row["odoo_order_date"] = order_date
                conn.execute(
                    "UPDATE order_lines SET odoo_order_date=?, updated_at=? WHERE id=?",
                    (order_date, utc_now(), row["id"]),
                )
        candidates = [row for row in updated_rows if not clean_text(row.get("odoo_order_date")) and int(row.get("store_id") or 0) and int(row.get("odoo_order_id") or 0)]
        if not candidates:
            return updated_rows
    by_store: dict[int, set[int]] = {}
    for row in candidates:
        by_store.setdefault(int(row["store_id"]), set()).add(int(row["odoo_order_id"]))
    dates: dict[tuple[int, int], str] = {}
    for store_id, order_ids in by_store.items():
        try:
            store = get_store(store_id)
            odoo = OdooClient(store)
            fields = odoo.existing_fields("sale.order", ["id", "date_order"])
            if "date_order" not in fields:
                continue
            for order in odoo.read("sale.order", sorted(order_ids), fields):
                order_date = clean_text(order.get("date_order"))
                if order_date:
                    dates[(store_id, int(order["id"]))] = order_date
        except Exception:
            continue
    if not dates:
        return rows
    with db() as conn:
        for row in updated_rows:
            order_date = dates.get((int(row.get("store_id") or 0), int(row.get("odoo_order_id") or 0)))
            if not order_date:
                continue
            row["odoo_order_date"] = order_date
            conn.execute(
                "UPDATE order_lines SET odoo_order_date=?, updated_at=? WHERE id=?",
                (order_date, utc_now(), row["id"]),
            )
    return updated_rows


def amazon_otp_rows(q: str = "") -> list[dict[str, Any]]:
    term = clean_text(q).lower()
    term_digits = re.sub(r"\D+", "", term)
    with db() as conn:
        records = conn.execute(
            """
            SELECT *
            FROM amazon_otp_records
            WHERE otp_required=1 OR COALESCE(otp, '') != '' OR COALESCE(tracking_url, '') != ''
            ORDER BY COALESCE(otp_email_date, dispatch_email_date, updated_at) DESC
            LIMIT 500
            """
        ).fetchall()
        lines = conn.execute(
            """
            SELECT amazon_order_id, odoo_order_name, store_id, tracking_status, tracking_payload, tracking_checked_at, state
            FROM order_lines
            WHERE COALESCE(amazon_order_id, '') != ''
            """
        ).fetchall()
    lines_by_order: dict[str, list[dict[str, Any]]] = {}
    for line in rows_to_dicts(lines):
        lines_by_order.setdefault(clean_text(line.get("amazon_order_id")), []).append(line)
    stores = {int(store["id"]): store for store in list_stores()}
    results: list[dict[str, Any]] = []
    for record in rows_to_dicts(records):
        order_id = clean_text(record.get("amazon_order_id"))
        order_lines = lines_by_order.get(order_id, [])
        packages: list[dict[str, Any]] = []
        odoo_order_names: list[str] = []
        statuses: list[str] = []
        checked_at = ""
        store_names: list[str] = []
        for line in order_lines:
            odoo_name = clean_text(line.get("odoo_order_name"))
            if odoo_name and odoo_name not in odoo_order_names:
                odoo_order_names.append(odoo_name)
            status = clean_text(line.get("tracking_status") or line.get("state"))
            if status and status not in statuses:
                statuses.append(status)
            if line.get("tracking_checked_at") and not checked_at:
                checked_at = line.get("tracking_checked_at")
            store = stores.get(int(line.get("store_id") or 0))
            if store and store["name"] not in store_names:
                store_names.append(store["name"])
            try:
                parsed_packages = json.loads(line.get("tracking_payload") or "[]")
                if isinstance(parsed_packages, list):
                    for package in parsed_packages:
                        if isinstance(package, dict) and package not in packages:
                            packages.append(package)
            except Exception:
                pass
        tracking_numbers = []
        carriers = []
        for package in packages:
            tracking_id = clean_text(package.get("tracking_id") or package.get("trackingId") or package.get("tracking_number"))
            carrier = clean_text(package.get("carrier") or package.get("carrier_name"))
            if tracking_id and tracking_id not in tracking_numbers:
                tracking_numbers.append(tracking_id)
            if carrier and carrier not in carriers:
                carriers.append(carrier)
        status_text = " ".join(statuses).lower()
        package_text = json.dumps(packages, default=str).lower()
        if order_lines and ("delivered" in status_text or "delivered" in package_text):
            continue
        row = {
            **record,
            "store_names": ", ".join(store_names),
            "odoo_order_names": ", ".join(odoo_order_names),
            "tracking_status": ", ".join(statuses) or clean_text(record.get("status")),
            "tracking_checked_at": checked_at,
            "tracking_numbers": ", ".join(tracking_numbers),
            "carriers": ", ".join(carriers),
            "package_count": len(packages),
            "amazon_order_url": order_line_amazon_url(order_id),
            "match_status": "matched" if record.get("otp") and (tracking_numbers or record.get("tracking_url")) else "needs tracking" if record.get("otp") else "waiting for OTP",
        }
        searchable = " ".join(str(row.get(key) or "") for key in ("amazon_order_id", "otp", "tracking_numbers", "carriers", "odoo_order_names", "product_summary", "recipient")).lower()
        searchable_digits = re.sub(r"\D+", "", searchable)
        if not term or term in searchable or (term_digits and term_digits in searchable_digits):
            results.append(row)
    return results


def get_default_ordering_engine() -> str:
    return normalize_ordering_engine(get_setting("default_ordering_engine", "rest"))


def dashboard_data(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        payload = conn.execute(
            """
            WITH stores_cte AS (
                SELECT * FROM stores ORDER BY name
            ),
            chosen AS (
                SELECT COALESCE(?, (SELECT id FROM stores_cte LIMIT 1)) AS store_id
            ),
            line_rows AS (
                SELECT order_lines.*,
                       COALESCE((
                           SELECT COUNT(*)
                           FROM order_lines AS duplicate_lines
                           WHERE duplicate_lines.store_id = order_lines.store_id
                             AND duplicate_lines.asin = order_lines.asin
                             AND COALESCE(duplicate_lines.amazon_order_id, '') = ''
                             AND COALESCE(duplicate_lines.odoo_status_label, '') NOT IN ('cancelled', 'refunded')
                       ), 0) AS duplicate_asin_count,
                       COALESCE((
                           SELECT SUM(quantity)
                           FROM inventory_items
                           WHERE inventory_items.store_id = order_lines.store_id
                             AND inventory_items.asin = order_lines.asin
                             AND inventory_items.status = 'available'
                       ), 0) AS inventory_quantity,
                       COALESCE((
                           SELECT COUNT(DISTINCT same_order_lines.asin)
                           FROM order_lines AS same_order_lines
                           WHERE same_order_lines.store_id = order_lines.store_id
                             AND same_order_lines.odoo_order_id = order_lines.odoo_order_id
                             AND COALESCE(same_order_lines.asin, '') != ''
                             AND COALESCE(same_order_lines.amazon_order_id, '') = ''
                             AND same_order_lines.state != 'missing'
                             AND COALESCE(same_order_lines.odoo_status_label, '') NOT IN ('cancelled', 'refunded')
                       ), 0) AS odoo_order_distinct_asin_count
                FROM order_lines
                WHERE ((SELECT store_id FROM chosen) IS NULL OR store_id=(SELECT store_id FROM chosen))
                ORDER BY odoo_order_id DESC, asin, id
                LIMIT ? OFFSET ?
            ),
            duplicate_all AS (
                SELECT asin,
                       COUNT(*) AS line_count,
                       COUNT(DISTINCT odoo_order_name) AS order_count,
                       SUM(quantity) AS total_quantity,
                       STRING_AGG(DISTINCT odoo_order_name, ',') AS orders
                FROM order_lines
                WHERE ((SELECT store_id FROM chosen) IS NULL OR store_id=(SELECT store_id FROM chosen))
                  AND COALESCE(asin, '') != ''
                  AND COALESCE(amazon_order_id, '') = ''
                  AND state = 'pulled'
                  AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
                GROUP BY asin
                HAVING COUNT(DISTINCT odoo_order_name) > 1
                ORDER BY order_count DESC, total_quantity DESC, asin
            ),
            duplicate_rows AS (
                SELECT *
                FROM duplicate_all
                LIMIT 12
            )
            SELECT
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(stores_cte)), '[]'::json) FROM stores_cte) AS stores,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(a)), '[]'::json) FROM (SELECT * FROM fulfilment_addresses ORDER BY is_default DESC, label) a) AS addresses,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(ac)), '[]'::json) FROM (SELECT * FROM amazon_accounts ORDER BY is_default DESC, name) ac) AS amazon_accounts,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(p)), '[]'::json) FROM (SELECT * FROM punchout_return_urls ORDER BY is_default DESC, label) p) AS punchout_return_urls,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(line_rows)), '[]'::json) FROM line_rows) AS rows,
                (SELECT COUNT(*) FROM order_lines WHERE ((SELECT store_id FROM chosen) IS NULL OR store_id=(SELECT store_id FROM chosen))) AS total,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(c)), '[]'::json) FROM (
                    SELECT state, COUNT(*) AS count
                    FROM order_lines
                    WHERE ((SELECT store_id FROM chosen) IS NULL OR store_id=(SELECT store_id FROM chosen))
                    GROUP BY state
                ) c) AS counts,
                (SELECT COALESCE(JSON_AGG(ROW_TO_JSON(duplicate_rows)), '[]'::json) FROM duplicate_rows) AS duplicate_asins,
                (SELECT COUNT(*) FROM duplicate_all) AS duplicate_asins_total,
                (SELECT store_id FROM chosen) AS current_store_id,
                (SELECT value FROM app_settings WHERE key='default_ordering_engine') AS default_ordering_engine
            """
            ,
            (store_id, per_page, offset),
        ).fetchone()
    stores = payload.get("stores") or []
    addresses = payload.get("addresses") or []
    amazon_accounts = payload.get("amazon_accounts") or []
    punchout_return_urls = payload.get("punchout_return_urls") or []
    current_store_id = payload.get("current_store_id")
    row_dicts = payload.get("rows") or []
    stores_by_id = {store["id"]: store for store in stores}
    row_dicts = backfill_missing_order_dates(row_dicts)
    for row in row_dicts:
        store = stores_by_id.get(row.get("store_id"))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        if row.get("amazon_order_id") and not row.get("amazon_order_url"):
            row["amazon_order_url"] = order_line_amazon_url(row["amazon_order_id"])
    return {
        "stores": rows_to_dicts(stores),
        "current_store_id": current_store_id,
        "rows": row_dicts,
        "page": page,
        "per_page": per_page,
        "total": int(payload.get("total") or 0),
        "counts": payload.get("counts") or [],
        "addresses": addresses,
        "amazon_accounts": amazon_accounts,
        "punchout_return_urls": punchout_return_urls,
        "duplicate_asins": payload.get("duplicate_asins") or [],
        "duplicate_asins_page": 1,
        "duplicate_asins_per_page": 12,
        "duplicate_asins_total": int(payload.get("duplicate_asins_total") or 0),
        "default_ordering_engine": normalize_ordering_engine(str(payload.get("default_ordering_engine") or "rest")),
        "pull_orders_days": int(get_setting("pull_orders_days", "7") or 7),
        "pull_orders_limit": int(get_setting("pull_orders_limit", "50") or 50),
    }


@app.on_event("startup")
def startup() -> None:
    global _sync_thread_started
    init_db()
    backfill_cxml_order_references()
    if not _sync_thread_started:
        threading.Thread(target=autosync_loop, daemon=True).start()
        threading.Thread(target=backup_loop, daemon=True).start()
        threading.Thread(target=amazon_otp_loop, daemon=True).start()
        _sync_thread_started = True


@app.get("/api/dashboard")
def api_dashboard(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    return dashboard_data(store_id, page, per_page)


@app.post("/api/settings/ordering-engine")
def api_save_ordering_engine(payload: EnginePayload) -> dict[str, Any]:
    engine = normalize_ordering_engine(payload.ordering_engine)
    set_setting("default_ordering_engine", engine)
    return {"ok": True, "message": f"Default ordering engine saved: {engine}.", "default_ordering_engine": engine}


@app.get("/api/settings/services")
def api_service_settings() -> dict[str, Any]:
    settings = get_service_settings()
    masked = dict(settings)
    for key in ("typesense_api_key", "storage_s3_secret_access_key", "amazon_otp_imap_password", "openexchange_api_key"):
        if masked.get(key):
            masked[key] = "********"
    masked["amazon_otp_last_sync_at"] = get_setting("amazon_otp_last_sync_at", "")
    masked["amazon_otp_last_sync_message"] = get_setting("amazon_otp_last_sync_message", "")
    masked["openexchange_last_sync_at"] = get_setting("openexchange_last_sync_at", "")
    masked["openexchange_last_sync_message"] = get_setting("openexchange_last_sync_message", "")
    return {"ok": True, "settings": masked}


@app.post("/api/settings/services")
def api_save_service_settings(payload: ServiceSettingsPayload) -> dict[str, Any]:
    current = get_service_settings()
    values = {}
    for key, value in payload.settings.items():
        if value == "********":
            values[key] = current.get(key, "")
        else:
            values[key] = value
    set_service_settings(values)
    return {"ok": True, "message": "Service settings saved.", "settings": get_service_settings()}


@app.get("/api/settings/admin-access")
def api_admin_access_settings() -> dict[str, Any]:
    return {"ok": True, "has_admin_access_token": bool(effective_admin_access_token()), "master_recovery_enabled": bool(MASTER_ADMIN_ACCESS_TOKEN)}


@app.post("/api/settings/admin-access")
def api_save_admin_access_settings(payload: AdminSettingsPayload) -> dict[str, Any]:
    token = clean_text(payload.admin_access_token)
    if len(token) < 4:
        raise HTTPException(400, "Admin code must be at least 4 characters.")
    if token == MASTER_ADMIN_ACCESS_TOKEN:
        raise HTTPException(400, "Choose a code different from the master recovery code.")
    set_setting("admin_access_token", token)
    return {"ok": True, "message": "Admin code saved.", "has_admin_access_token": True}


@app.post("/api/settings/test/{service}")
def api_test_service(service: str) -> dict[str, Any]:
    settings = get_service_settings()
    try:
        if service == "typesense":
            response = requests.get(f"{typesense_base(settings)}/health", headers=typesense_headers(settings), timeout=8)
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
            return {"ok": True, "message": f"Typesense reachable: {response.text[:200]}"}
        if service == "postgres":
            try:
                import psycopg2  # type: ignore
            except Exception as exc:
                return {"ok": False, "message": f"Install psycopg2-binary to test Postgres: {exc}"}
            conn = psycopg2.connect(settings["postgres_url"], connect_timeout=8)
            conn.close()
            return {"ok": True, "message": "Postgres connection successful."}
        if service == "s3":
            try:
                import boto3  # type: ignore
            except Exception as exc:
                return {"ok": False, "message": f"Install boto3 to test S3/R2: {exc}"}
            client = boto3.client(
                "s3",
                endpoint_url=settings["storage_s3_endpoint"].rsplit("/", 1)[0],
                region_name=settings["storage_s3_region"] or "auto",
                aws_access_key_id=settings["storage_s3_access_key_id"],
                aws_secret_access_key=settings["storage_s3_secret_access_key"],
            )
            client.head_bucket(Bucket=settings["storage_s3_bucket"])
            return {"ok": True, "message": "S3/R2 bucket connection successful."}
        if service == "amazon-otp-imap":
            client = imap_connect(settings)
            try:
                folder = clean_text(settings.get("amazon_otp_imap_folder")) or "INBOX"
                status, data = client.select(folder, readonly=True)
                if status != "OK":
                    raise RuntimeError(f"Unable to open folder {folder}.")
                count = data[0].decode("ascii", errors="ignore") if data else "0"
                return {"ok": True, "message": f"IMAP connection successful. Folder {folder} has {count} message(s)."}
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
        if service == "openexchange":
            rates = sync_openexchange_rates_if_due(settings, force=True)
            return {"ok": True, "message": f"OpenExchange synced {len(rates)} currency rate(s)."}
        raise HTTPException(404, "Unknown service")
    except Exception as exc:
        return {"ok": False, "message": str(exc)}


@app.post("/api/settings/amazon-otp/sync")
def api_amazon_otp_sync() -> dict[str, Any]:
    return sync_amazon_otp_emails()


@app.post("/api/settings/typesense/reindex")
def api_typesense_reindex() -> dict[str, Any]:
    return start_typesense_reindex_job()


@app.get("/api/settings/typesense/reindex")
def api_typesense_reindex_status() -> dict[str, Any]:
    return {"ok": True, "progress": typesense_reindex_progress()}


@app.post("/api/settings/backup/run")
def api_run_backup() -> dict[str, Any]:
    key = verify_product_storage()
    return {"ok": True, "message": "Postgres and Cloudflare R2 are reachable.", "key": key}


@app.get("/api/search")
def api_search(q: str = "", store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    data = dashboard_data(store_id, page, per_page)
    if not q.strip():
        return data
    rows = data["rows"]
    try:
        ids = typesense_search_ids(q, store_id)
        if ids:
            order = {row_id: idx for idx, row_id in enumerate(ids)}
            data["rows"] = sorted([row for row in rows if int(row["id"]) in order], key=lambda row: order[int(row["id"])])
            data["search_engine"] = "typesense"
            return data
    except Exception as exc:
        data["search_warning"] = str(exc)
    term = q.strip().lower()
    data["rows"] = [
        row for row in rows
        if term in " ".join(str(row.get(key) or "") for key in ("odoo_order_name", "product_name", "asin", "state", "odoo_status_label", "amazon_order_id", "amazon_account_name", "fulfilment_note")).lower()
    ]
    data["search_engine"] = "local"
    return data


def autosync_loop() -> None:
    last_run = 0.0
    last_chrome_run = 0.0
    while True:
        try:
            settings = get_service_settings()
            interval = int(float(settings.get("autosync_interval_minutes") or 0))
            if interval > 0 and time.time() - last_run >= interval * 60:
                for store in list_stores():
                    fetch_odoo_lines(get_store(int(store["id"])), days=2, limit=100)
                last_run = time.time()
            chrome_interval = int(float(settings.get("auto_chrome_fulfil_interval_minutes") or 0))
            if chrome_interval > 0 and time.time() - last_chrome_run >= chrome_interval * 60:
                days = max(1, min(30, int(float(settings.get("auto_chrome_fulfil_days") or 2))))
                limit = max(1, min(500, int(float(settings.get("auto_chrome_fulfil_limit") or 100))))
                for store in list_stores():
                    store_id = int(store["id"])
                    fetch_odoo_lines(get_store(store_id), days=days, limit=limit)
                    place_orders(store_id, ordering_engine="chrome")
                last_chrome_run = time.time()
        except Exception:
            pass
        time.sleep(60)


def s3_endpoint_base(endpoint: str) -> str:
    return str(endpoint or "").rstrip("/").rsplit("/", 1)[0]


def verify_product_storage() -> str:
    with db() as conn:
        conn.execute("SELECT 1")
    client = require_storage_client()
    settings = get_service_settings()
    client.head_bucket(Bucket=settings["storage_s3_bucket"])
    return "postgres+r2:ready"


def backup_loop() -> None:
    last_run = 0.0
    while True:
        try:
            cleanup_expired_exports()
            settings = get_service_settings()
            interval = int(float(settings.get("backup_interval_minutes") or 0))
            if interval > 0 and time.time() - last_run >= interval * 60:
                verify_product_storage()
                last_run = time.time()
        except Exception:
            pass
        time.sleep(60)


@app.get("/api/inventory")
def api_inventory(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    items, total = list_inventory_items(store_id, page, per_page)
    return {
        "stores": rows_to_dicts(list_stores()),
        "current_store_id": store_id,
        "items": rows_to_dicts(items),
        "page": max(1, int(page or 1)),
        "per_page": max(1, min(100, int(per_page or 100))),
        "total": total,
    }


@app.post("/api/inventory")
def api_create_inventory(payload: InventoryCreatePayload) -> dict[str, Any]:
    asin = normalize_asin(payload.asin)
    if not asin:
        raise HTTPException(400, "ASIN is required.")
    quantity = float(payload.quantity or 0)
    if quantity <= 0:
        raise HTTPException(400, "Quantity must be greater than zero.")
    with db() as conn:
        conn.execute(
            """
            INSERT INTO inventory_items
            (store_id, order_line_id, asin, quantity, product_name, source_odoo_order_id, source_odoo_order_name,
             amazon_order_id, amazon_order_url, amazon_account_name, status, source_type, notes, manual_reference, created_at, updated_at)
            VALUES (?, NULL, ?, ?, ?, NULL, '', '', '', '', 'available', 'manual', ?, ?, ?, ?)
            """,
            (
                payload.store_id,
                asin,
                quantity,
                clean_text(payload.product_name),
                clean_text(payload.notes),
                f"manual-{uuid.uuid4().hex[:10]}",
                utc_now(),
                utc_now(),
            ),
        )
    items, total = list_inventory_items(payload.store_id, 1, 100)
    return {"ok": True, "message": f"Added {quantity:g} inventory unit(s) for {asin}.", "items": rows_to_dicts(items), "page": 1, "per_page": 100, "total": total}


@app.get("/api/missing")
def api_missing(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    page, per_page, _ = pagination_bounds(page, per_page)
    raw_rows, total = list_missing_order_lines(store_id, page, per_page)
    rows = rows_to_dicts(raw_rows)
    stores = rows_to_dicts(list_stores())
    stores_by_id = {store["id"]: store for store in stores}
    for row in rows:
        store = stores_by_id.get(row.get("store_id"))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        row["missing_asin_url"] = asin_product_url(row.get("missing_asin") or row.get("asin") or "")
        row["asin_url"] = asin_product_url(row.get("asin") or "")
        row["replacement_asin_url"] = asin_product_url(row.get("replacement_asin") or "")
    return {"stores": stores, "current_store_id": store_id, "rows": rows, "page": page, "per_page": per_page, "total": total}


@app.get("/api/duplicate-asins")
def api_duplicate_asins(store_id: Optional[int] = None, page: int = 1, per_page: int = 12) -> dict[str, Any]:
    rows, total, page, per_page = duplicate_asin_groups(store_id, page, per_page)
    return {
        "stores": rows_to_dicts(list_stores()),
        "current_store_id": store_id,
        "groups": rows,
        "page": page,
        "per_page": per_page,
        "total": total,
    }


@app.post("/api/missing/lines/{line_id}/replacement")
def api_assign_replacement(line_id: int, payload: ReplacementPayload) -> dict[str, Any]:
    replacement_asin = normalize_asin(payload.asin)
    if not replacement_asin:
        raise HTTPException(400, "Replacement ASIN must be a valid 10-character ASIN.")
    title = fetch_amazon_product_title(replacement_asin)
    with db() as conn:
        row = conn.execute("SELECT * FROM order_lines WHERE id=? AND store_id=?", (line_id, payload.store_id)).fetchone()
        if not row:
            raise HTTPException(404, "Missing order line not found.")
        original_asin = row["original_asin"] if "original_asin" in row.keys() and row["original_asin"] else row["asin"]
        replacement_name = title or f"Replacement ASIN {replacement_asin}"
        conn.execute(
            """
            UPDATE order_lines
            SET original_asin=COALESCE(NULLIF(original_asin, ''), asin),
                replacement_asin=?,
                replacement_product_name=?,
                replacement_note=?,
                replacement_assigned_at=?,
                asin=?,
                product_name=?,
                state='pulled',
                amazon_status=NULL,
                amazon_group_key=NULL,
                missing_asin=NULL,
                last_error=NULL,
                updated_at=?
            WHERE id=? AND store_id=?
            """,
            (
                replacement_asin,
                replacement_name,
                clean_text(payload.note),
                utc_now(),
                replacement_asin,
                replacement_name,
                utc_now(),
                line_id,
                payload.store_id,
            ),
        )
        updated = conn.execute("SELECT * FROM order_lines WHERE id=?", (line_id,)).fetchone()
    try:
        store = get_store(payload.store_id)
        note = (
            "Replacement item assigned in fulfilment app.<br/>"
            f"Original ASIN: <a href=\"{asin_product_url(original_asin)}\" target=\"_blank\">{original_asin}</a><br/>"
            f"Replacement ASIN: <a href=\"{asin_product_url(replacement_asin)}\" target=\"_blank\">{replacement_asin}</a><br/>"
            f"Replacement title: {html.escape(replacement_name)}"
        )
        if payload.note:
            note += f"<br/>Note: {html.escape(clean_text(payload.note))}"
        OdooClient(store).post_order_note(int(updated["odoo_order_id"]), note)
    except Exception:
        pass
    if updated:
        index_order_line(updated)
    return {"ok": True, "message": f"Replacement {replacement_asin} assigned and marked ready to queue.", "row": row_to_dict(updated)}


@app.get("/api/bulk")
def api_bulk(store_id: Optional[int] = None, days: int = 2, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    groups, total, page, per_page = paginate_values(bulk_opportunity_groups(store_id, days), page, per_page)
    return {"stores": rows_to_dicts(list_stores()), "current_store_id": store_id, "groups": groups, "page": page, "per_page": per_page, "total": total}


@app.post("/api/bulk/place")
def api_bulk_place(payload: BulkPlacePayload) -> dict[str, Any]:
    if not payload.line_ids:
        raise HTTPException(400, "Select at least one bulk line.")
    with db() as conn:
        placeholders = ",".join("?" for _ in payload.line_ids)
        rows = conn.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE store_id=?
              AND id IN ({placeholders})
              AND COALESCE(asin, '') != ''
              AND COALESCE(amazon_order_id, '') = ''
              AND state = 'pulled'
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
            """,
            [payload.store_id, *payload.line_ids],
        ).fetchall()
        if not rows:
            return {"ok": False, "message": "Those bulk rows are no longer available to order. Refreshing opportunities."}
        missing_orders = conn.execute(
            f"""
            SELECT DISTINCT odoo_order_id
            FROM order_lines
            WHERE store_id=?
              AND state='missing'
              AND odoo_order_id IN ({','.join('?' for _ in rows) or 'NULL'})
            """,
            [payload.store_id, *[row["odoo_order_id"] for row in rows]],
        ).fetchall() if rows else []
        if missing_orders:
            missing_ids = {int(row["odoo_order_id"]) for row in missing_orders}
            for row in rows:
                if int(row["odoo_order_id"]) in missing_ids:
                    conn.execute(
                        """
                        UPDATE order_lines
                        SET state='missing',
                            last_error='Bulk buying opportunity found, but another item in this Odoo order is missing. Review together before fulfilment.',
                            updated_at=?
                        WHERE id=?
                        """,
                        (utc_now(), row["id"]),
                    )
            return {"ok": False, "message": "Moved affected bulk rows to Missing because one of their Odoo orders also has a missing item."}
    eligible_line_ids = [int(row["id"]) for row in rows]
    ordered, skipped = place_orders(
        payload.store_id,
        address_id=payload.address_id,
        amazon_account_id=payload.amazon_account_id,
        line_ids=eligible_line_ids,
        club=True,
        ordering_engine=payload.ordering_engine,
    )
    groups, total, page, per_page = paginate_values(bulk_opportunity_groups(payload.store_id), 1, 100)
    return {"ok": True, "message": f"Bulk order placed/queued for {ordered} line(s); skipped {skipped}.", "groups": groups, "page": page, "per_page": per_page, "total": total}


@app.get("/api/costly")
def api_costly(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    page, per_page, _ = pagination_bounds(page, per_page)
    raw_rows, total = list_costly_order_lines(store_id, page, per_page)
    rows = rows_to_dicts(raw_rows)
    stores = rows_to_dicts(list_stores())
    stores_by_id = {store["id"]: store for store in stores}
    for row in rows:
        store = stores_by_id.get(row.get("store_id"))
        row["odoo_order_url"] = odoo_order_admin_url(store, row.get("odoo_order_id")) if store else ""
        row["asin_url"] = asin_product_url(row.get("asin") or "")
    return {"stores": stores, "current_store_id": store_id, "rows": rows, "page": page, "per_page": per_page, "total": total}


@app.post("/api/costly/approve")
def api_approve_costly(payload: CostlyApprovalPayload) -> dict[str, Any]:
    with db() as conn:
        where = ""
        params: list[Any] = [utc_now(), utc_now(), payload.store_id]
        if payload.line_ids:
            where = f" AND id IN ({','.join('?' for _ in payload.line_ids)})"
            params.extend(payload.line_ids)
        cursor = conn.execute(
            f"""
            UPDATE order_lines
            SET state='pulled',
                amazon_status=NULL,
                amazon_group_key=NULL,
                last_error=NULL,
                cost_approved_at=?,
                updated_at=?
            WHERE store_id=?
              AND state='costly'
              {where}
            """,
            params,
        )
    rows, total = list_costly_order_lines(payload.store_id, 1, 100)
    return {"ok": True, "message": f"Approved {cursor.rowcount} costly line(s) for fulfilment.", "rows": rows_to_dicts(rows), "page": 1, "per_page": 100, "total": total}


@app.get("/api/profit-loss")
def api_profit_loss(
    store_id: Optional[int] = None,
    period: str = "monthly",
    month: str = "",
    start: str = "",
    end: str = "",
    q: str = "",
) -> dict[str, Any]:
    return profit_loss_data(store_id, period, month, start, end, q)


@app.post("/api/profit-loss/shipping-upload")
async def api_profit_loss_shipping_upload(
    file: UploadFile = File(...),
    month: str = Form(""),
    default_fulfilment_fee: float = Form(4.0),
) -> dict[str, Any]:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    return upload_shipping_records(file.filename or "shipping-upload", data, month, default_fulfilment_fee)


@app.get("/api/accounting")
def api_accounting(q: str = "", document_type: str = "all", tax_region: str = "all") -> dict[str, Any]:
    return accounting_summary(q, document_type, tax_region)


@app.post("/api/accounting/odoo-sync")
def api_accounting_odoo_sync(store_id: int, days: int = 30, limit: int = 200) -> dict[str, Any]:
    return sync_odoo_accounting_documents(store_id, days, limit)


@app.get("/api/accounting/amazon-invoice-orders")
def api_amazon_invoice_orders(store_id: Optional[int] = None, limit: int = 100) -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT order_lines.odoo_order_name,
                   order_lines.amazon_order_id,
                   MAX(order_lines.amazon_order_url) AS amazon_order_url,
                   MAX(COALESCE(order_lines.ordered_at, order_lines.updated_at)) AS last_ordered_at
            FROM order_lines
            LEFT JOIN accounting_documents
              ON accounting_documents.odoo_order_name=order_lines.odoo_order_name
             AND accounting_documents.document_type='amazon'
            WHERE (? IS NULL OR order_lines.store_id=?)
              AND COALESCE(order_lines.amazon_order_id, '') != ''
              AND accounting_documents.id IS NULL
            GROUP BY order_lines.odoo_order_name, order_lines.amazon_order_id
            ORDER BY last_ordered_at DESC
            LIMIT ?
            """,
            (store_id, store_id, max(1, min(500, int(limit or 100)))),
        ).fetchall()
    return {"ok": True, "orders": rows_to_dicts(rows)}


@app.post("/api/accounting/documents")
async def api_upload_accounting_document(
    file: UploadFile = File(...),
    document_type: str = Form("odoo"),
    odoo_order_name: str = Form(""),
    country_code: str = Form(""),
    invoice_date: str = Form(""),
    notes: str = Form(""),
) -> dict[str, Any]:
    document_type = document_type.strip().lower()
    if document_type not in {"odoo", "amazon"}:
        raise HTTPException(status_code=400, detail="document_type must be odoo or amazon.")
    order_name = normalize_order_reference(odoo_order_name)
    if not order_name:
        raise HTTPException(status_code=400, detail="Odoo order number is required.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    country = country_code.strip().upper()
    region = tax_region_for_country(country)
    original = file.filename or "invoice"
    suffix = Path(original).suffix or ".pdf"
    stored_name = f"{safe_filename(order_name)}_{document_type}_invoice{suffix}"
    key = f"accounting/{region}/{safe_filename(order_name)}/{stored_name}"
    storage_key, storage_url = upload_storage_bytes(data, key, file.content_type or "application/octet-stream")
    now = utc_now()
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO accounting_documents
            (document_type, odoo_order_name, country_code, tax_region, invoice_date, original_filename,
             stored_filename, content_type, local_path, storage_key, storage_url, file_size, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                document_type,
                order_name,
                country,
                region,
                invoice_date,
                original,
                stored_name,
                file.content_type or "",
                "",
                storage_key,
                storage_url,
                len(data),
                notes,
                now,
                now,
            ),
        )
        inserted = cursor.fetchone()
    return {"ok": True, "message": f"Stored {document_type} invoice for {order_name} in {region}.", "id": inserted["id"] if inserted else None, "storage_key": storage_key, "storage_url": storage_url}


@app.post("/api/accounting/amazon-document")
async def api_upload_amazon_document(
    file: UploadFile = File(...),
    odoo_order_name: str = Form(""),
    amazon_order_id: str = Form(""),
    country_code: str = Form(""),
) -> dict[str, Any]:
    return await api_upload_accounting_document(file, "amazon", odoo_order_name or amazon_order_id, country_code, "", f"Amazon order {amazon_order_id}")


def run_pull_job(job_id: str) -> None:
    try:
        with db() as conn:
            job = conn.execute("SELECT * FROM pull_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return
            conn.execute("UPDATE pull_jobs SET status='running', updated_at=? WHERE id=?", (utc_now(), job_id))
        inserted = fetch_odoo_lines(get_store(int(job["store_id"])), int(job["days"] or 7), int(job["limit_value"] or 50))
        with db() as conn:
            conn.execute(
                """
                UPDATE pull_jobs
                SET status='completed', inserted_records=?, updated_at=?, completed_at=?
                WHERE id=?
                """,
                (inserted, utc_now(), utc_now(), job_id),
            )
    except Exception as exc:
        with db() as conn:
            conn.execute(
                "UPDATE pull_jobs SET status='failed', error=?, updated_at=?, completed_at=? WHERE id=?",
                (str(exc)[:1000], utc_now(), utc_now(), job_id),
            )


def start_pull_job(store_id: int, days: int, limit: int) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    now = utc_now()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO pull_jobs
            (id, store_id, status, days, limit_value, inserted_records, error, created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?, 0, '', ?, ?)
            """,
            (job_id, store_id, days, limit, now, now),
        )
    threading.Thread(target=run_pull_job, args=(job_id,), daemon=True).start()
    return {"id": job_id, "store_id": store_id, "status": "queued", "days": days, "limit_value": limit, "inserted_records": 0, "error": "", "created_at": now, "updated_at": now, "completed_at": ""}


@app.post("/api/pull")
def api_pull_orders(payload: PullPayload) -> dict[str, Any]:
    store_ids = [int(store_id) for store_id in (payload.store_ids or ([] if payload.store_id is None else [payload.store_id]))]
    store_ids = list(dict.fromkeys(store_id for store_id in store_ids if store_id > 0))
    if not store_ids:
        raise HTTPException(400, "Select at least one store to pull orders.")
    days = max(1, min(365, int(payload.days or 7)))
    limit = max(1, min(50000, int(payload.limit or 50)))
    set_setting("pull_orders_days", str(days))
    set_setting("pull_orders_limit", str(limit))
    jobs = [start_pull_job(store_id, days, limit) for store_id in store_ids]
    message = f"Started {len(jobs)} background pull job{'s' if len(jobs) != 1 else ''}."
    return {"ok": True, "message": message, "jobs": jobs, "defer_refresh": True}


@app.get("/api/pull/jobs")
def api_pull_jobs(page: int = 1, per_page: int = 100) -> dict[str, Any]:
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS count FROM pull_jobs").fetchone()["count"] or 0)
        rows = conn.execute(
            """
            SELECT pull_jobs.*, stores.name AS store_name
            FROM pull_jobs
            LEFT JOIN stores ON stores.id = pull_jobs.store_id
            ORDER BY pull_jobs.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall()
    return {"ok": True, "jobs": rows_to_dicts(rows), "page": page, "per_page": per_page, "total": total}


@app.post("/api/settings/pull-defaults")
def api_save_pull_defaults(payload: PullPayload) -> dict[str, Any]:
    days = max(1, min(365, int(payload.days or 7)))
    limit = max(1, min(50000, int(payload.limit or 50)))
    set_setting("pull_orders_days", str(days))
    set_setting("pull_orders_limit", str(limit))
    return {"ok": True, "message": f"Pull defaults saved: {days} day(s), limit {limit}.", "days": days, "limit": limit}


@app.get("/api/settings/pull-defaults")
def api_pull_defaults() -> dict[str, Any]:
    return {
        "ok": True,
        "days": int(get_setting("pull_orders_days", "7") or 7),
        "limit": int(get_setting("pull_orders_limit", "50") or 50),
    }


@app.post("/api/place")
def api_place(payload: PlacePayload) -> dict[str, Any]:
    ordering_engine = normalize_ordering_engine(payload.ordering_engine or get_default_ordering_engine())
    if ordering_engine == "chrome":
        selected_count = len(payload.line_ids or [])
        threading.Thread(
            target=queue_chrome_order_groups_fast_task,
            args=(payload.store_id, payload.amazon_account_id, payload.address_id, payload.line_ids or None, payload.club),
            daemon=True,
        ).start()
        message = (
            f"Using engine: chrome. Queueing {selected_count or 'matching'} selected line{'s' if selected_count != 1 else ''} for the Chrome extension. "
            "You can open the Chrome extension and click Start next queued order in a moment."
        )
        return {
            "ok": True,
            "message": message,
            "defer_refresh": True,
        }

    cleared = clear_dry_run_order_markers(payload.store_id)
    account = get_amazon_account(payload.amazon_account_id)
    ordered, skipped = place_orders(
        payload.store_id,
        address_id=payload.address_id,
        amazon_account_id=payload.amazon_account_id,
        line_ids=payload.line_ids or None,
        club=payload.club,
        ordering_engine=ordering_engine,
        allow_missing_spaid=payload.allow_missing_spaid,
    )
    details = selected_line_reasons(payload.store_id, payload.line_ids or None)
    account_type = "sandbox" if "sandbox." in str(account["api_base_url"]) else "production"
    data = dashboard_data(payload.store_id)
    message = (
        f"Using engine: {ordering_engine}. Using Amazon account: {account['name']} ({account_type}, {account['api_base_url']}). "
        f"Placed {ordered} Amazon order{'s' if ordered != 1 else ''}; failed/skipped {skipped}."
    )
    if cleared:
        message += f" Cleared {cleared} old dry-run marker{'s' if cleared != 1 else ''} before placing."
    if details:
        message += " Reason: " + " | ".join(details)
    data["message"] = message
    data["ok"] = skipped == 0 and ordered > 0
    if ordering_engine == "cxml" and skipped and not payload.allow_missing_spaid:
        line = first_missing_spaid_line(payload.store_id, payload.line_ids or None)
        if line:
            query = urlencode(
                {
                    "store_id": payload.store_id,
                    "amazon_account_id": payload.amazon_account_id or "",
                    "line_id": line["id"],
                }
            )
            data["punchout_launch_url"] = f"/punchout/launch?{query}"
            data["message"] += " Opening Amazon Punchout to create the required cart/session."
    return data


@app.get("/api/chrome/jobs")
def api_chrome_jobs(store_id: Optional[int] = None, worker_id: str = "", claim: bool = True) -> dict[str, Any]:
    if claim:
        job = claim_next_chrome_job(store_id, worker_id)
        return {"ok": True, "jobs": [job] if job else []}
    with db() as conn:
        clear_expired_chrome_claims(conn)
        rows = conn.execute(
            """
            SELECT *
            FROM order_lines
            WHERE order_engine='chrome'
              AND state='submitted'
              AND COALESCE(amazon_order_id, '') = ''
              AND COALESCE(amazon_group_key, '') != ''
              AND (? IS NULL OR store_id=?)
            ORDER BY COALESCE(chrome_claimed_by, '') != '', updated_at ASC, odoo_order_id DESC, id ASC
            """,
            (store_id, store_id),
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["amazon_group_key"]), []).append(row)
    jobs: list[dict[str, Any]] = []
    for group_key, group_rows in grouped.items():
        jobs.append(chrome_job_from_rows(rows_to_dicts(group_rows)))
    return {"ok": True, "jobs": jobs}


@app.post("/api/chrome/jobs/{group_key}/heartbeat")
def api_chrome_job_heartbeat(group_key: str, payload: ChromeJobHeartbeatPayload) -> dict[str, Any]:
    worker_id = clean_text(payload.worker_id)
    if not worker_id:
        raise HTTPException(400, "worker_id is required")
    expiry = chrome_job_lease_expiry()
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE order_lines
            SET chrome_claim_expires_at=?, updated_at=?
            WHERE amazon_group_key=?
              AND order_engine='chrome'
              AND state='submitted'
              AND COALESCE(amazon_order_id, '') = ''
              AND chrome_claimed_by=?
            """,
            (expiry, utc_now(), group_key, worker_id),
        )
    if not cursor.rowcount:
        raise HTTPException(409, "Chrome job lock is no longer owned by this worker.")
    return {"ok": True, "claim_expires_at": expiry}


@app.post("/api/chrome/jobs/{group_key}/release")
def api_chrome_job_release(group_key: str, payload: ChromeJobHeartbeatPayload) -> dict[str, Any]:
    worker_id = clean_text(payload.worker_id)
    if not worker_id:
        raise HTTPException(400, "worker_id is required")
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE order_lines
            SET chrome_claimed_by=NULL,
                chrome_claimed_at=NULL,
                chrome_claim_expires_at=NULL,
                updated_at=?
            WHERE amazon_group_key=?
              AND order_engine='chrome'
              AND state='submitted'
              AND COALESCE(amazon_order_id, '') = ''
              AND chrome_claimed_by=?
            """,
            (utc_now(), group_key, worker_id),
        )
    return {"ok": True, "released": cursor.rowcount}


@app.post("/api/chrome/failed-jobs/clear")
def api_chrome_clear_failed_jobs(store_id: Optional[int] = None) -> dict[str, Any]:
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE order_lines
            SET state='pulled',
                amazon_status=NULL,
                amazon_group_key=NULL,
                chrome_claimed_by=NULL,
                chrome_claimed_at=NULL,
                chrome_claim_expires_at=NULL,
                last_error=NULL,
                updated_at=?
            WHERE order_engine='chrome'
              AND state='error'
              AND COALESCE(amazon_order_id, '') = ''
              AND (? IS NULL OR store_id=?)
            """,
            (utc_now(), store_id, store_id),
        )
        conn.execute(
            """
            UPDATE amazon_attempts
            SET status='cleared'
            WHERE mode='chrome'
              AND status='error'
            """
        )
    return {"ok": True, "cleared": cursor.rowcount, "message": f"Cleared {cursor.rowcount} failed Chrome job line(s)."}


@app.post("/api/chrome/jobs/{group_key}/complete")
def api_chrome_job_complete(group_key: str, payload: ChromeJobCompletePayload) -> dict[str, Any]:
    amazon_order_id = clean_text(payload.amazon_order_id) or f"CHROME-{uuid.uuid4().hex[:10]}"
    amazon_order_url = clean_text(payload.amazon_order_url)
    chrome_account_name = clean_text(payload.amazon_account_name) or "Chrome Extension"
    pricing_by_asin: dict[str, dict[str, Any]] = {}
    for item in payload.pricing_summary or []:
        asin = normalize_asin(str(item.get("asin") or ""))
        if asin:
            pricing_by_asin[asin] = item
    with db() as conn:
        ensure_chrome_job_owner(conn, group_key, payload.worker_id)
        params: list[Any] = [group_key]
        line_filter = ""
        if payload.line_ids:
            line_filter = f" AND id IN ({','.join('?' for _ in payload.line_ids)})"
            params.extend(payload.line_ids)
        rows = conn.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE amazon_group_key=?
              AND order_engine='chrome'
              {line_filter}
            """,
            params,
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Chrome job not found")
        for row in rows:
            pricing = pricing_by_asin.get(str(row["asin"] or "").upper(), {})
            quantity = float(row["quantity"] or 1)
            store_total = order_line_store_total(row)
            amazon_unit = float(pricing.get("amazon_unit_price") or 0)
            amazon_total = float(pricing.get("amazon_total_price") or (amazon_unit * quantity if amazon_unit else 0))
            profit_total = store_total - amazon_total if amazon_total else None
            fulfilment_note = clean_text(str(pricing.get("fulfilment_note") or ""))
            conn.execute(
                """
                UPDATE order_lines
                SET amazon_order_id=?,
                    amazon_order_url=?,
                    store_unit_price=?,
                    store_total_price=?,
                    amazon_unit_price=?,
                    amazon_total_price=?,
                    chrome_profit_total=?,
                    fulfilment_note=?,
                    amazon_account_name=?,
                    amazon_status='ordered',
                    state='ordered',
                    last_error=NULL,
                    ordered_at=COALESCE(ordered_at, ?),
                    updated_at=?
                WHERE id=?
                """,
                (
                    amazon_order_id,
                    amazon_order_url,
                    (store_total / quantity) if quantity else 0,
                    store_total,
                    amazon_unit or None,
                    amazon_total or None,
                    profit_total,
                    fulfilment_note or None,
                    chrome_account_name,
                    utc_now(),
                    utc_now(),
                    row["id"],
                ),
            )
            conn.execute(
                """
                UPDATE amazon_attempts
                SET response_json=?, status='ok', error=NULL
                WHERE order_line_id=? AND external_id=? AND mode='chrome'
                """,
                (
                    json.dumps(
                        {
                            "amazon_order_id": amazon_order_id,
                            "amazon_order_url": amazon_order_url,
                            "pricing": pricing,
                            "store_total_price": store_total,
                            "amazon_total_price": amazon_total,
                            "chrome_profit_total": profit_total,
                            "fulfilment_note": fulfilment_note,
                            "amazon_account_name": chrome_account_name,
                        }
                    ),
                    row["id"],
                    group_key,
                ),
            )
            updated = conn.execute("SELECT * FROM order_lines WHERE id = ?", (row["id"],)).fetchone()
            if updated:
                ensure_inventory_for_line(updated)
                index_order_line(updated)
    try:
        variant_notes_by_order: dict[int, list[str]] = {}
        for row in rows:
            pricing = pricing_by_asin.get(str(row["asin"] or "").upper(), {})
            fulfilment_note = clean_text(str(pricing.get("fulfilment_note") or ""))
            if fulfilment_note:
                variant_notes_by_order.setdefault(int(row["odoo_order_id"]), []).append(fulfilment_note)
        note = (
            f"Amazon Chrome order placed: {amazon_order_id}\n"
            f"Amazon order link: {amazon_order_url or order_line_amazon_url(amazon_order_id)}\n"
            f"Amazon account: {chrome_account_name}"
        )
        store = get_store(int(rows[0]["store_id"]))
        odoo = OdooClient(store)
        for order_id in sorted({int(row["odoo_order_id"]) for row in rows}):
            order_note = note
            for fulfilment_note in variant_notes_by_order.get(order_id, []):
                order_note += f"\n{fulfilment_note}"
            odoo.post_order_note(order_id, order_note)
    except Exception:
        pass
    for store_id in sorted({int(row["store_id"]) for row in rows}):
        write_report(store_id)
    return {"ok": True, "message": f"Chrome job {group_key} marked ordered.", "amazon_order_id": amazon_order_id}


@app.post("/api/chrome/jobs/{group_key}/fail")
def api_chrome_job_fail(group_key: str, payload: ChromeJobFailPayload) -> dict[str, Any]:
    message = clean_error_message(payload.message)
    missing_asin = normalize_asin(payload.missing_asin)
    with db() as conn:
        ensure_chrome_job_owner(conn, group_key, payload.worker_id)
    if missing_asin:
        linked_message = f"ASIN {missing_asin} is missing or unavailable on Amazon. Skipped fulfilment for this Odoo order."
        count = mark_chrome_group_missing(group_key, linked_message, missing_asin, payload.missing_line_id)
        if count:
            return {"ok": True, "message": f"Moved {count} Chrome line(s) to Missing because {missing_asin} is unavailable."}
    with db() as conn:
        params: list[Any] = [message, utc_now(), group_key]
        line_filter = ""
        if payload.line_ids:
            line_filter = f" AND id IN ({','.join('?' for _ in payload.line_ids)})"
            params.extend(payload.line_ids)
        cursor = conn.execute(
            f"""
            UPDATE order_lines
            SET state='error', amazon_status='chrome_error', last_error=?, updated_at=?
            WHERE amazon_group_key=?
              AND order_engine='chrome'
              {line_filter}
            """,
            params,
        )
        conn.execute(
            """
            UPDATE amazon_attempts
            SET status='error', error=?
            WHERE external_id=? AND mode='chrome'
            """,
            (message, group_key),
        )
    return {"ok": True, "message": f"Marked {cursor.rowcount} Chrome line(s) as error."}


@app.post("/api/chrome/jobs/{group_key}/costly")
def api_chrome_job_costly(group_key: str, payload: ChromeJobCostlyPayload) -> dict[str, Any]:
    with db() as conn:
        ensure_chrome_job_owner(conn, group_key, payload.worker_id)
    costly_asin = normalize_asin(payload.costly_asin)
    message = clean_error_message(
        payload.message
        or f"ASIN {costly_asin} costs more on Amazon than the store sale value. Approval required before fulfilment."
    )
    count = mark_chrome_group_costly(
        group_key,
        message,
        costly_asin,
        payload.costly_line_id,
        payload.store_total_price,
        payload.amazon_total_price,
    )
    if not count:
        raise HTTPException(404, "Chrome job not found")
    return {"ok": True, "message": f"Moved {count} Chrome line(s) to Costly review."}


@app.get("/api/tracking/orders")
def api_tracking_orders(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    rows = tracking_rows(store_id)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        order_id = str(row.get("amazon_order_id") or "")
        if not order_id:
            continue
        entry = grouped.setdefault(
            order_id,
            {
                "amazon_order_id": order_id,
                "amazon_order_url": row.get("amazon_order_url") or order_line_amazon_url(order_id),
                "odoo_order_names": [],
                "lines": [],
                "tracking_checked_at": row.get("tracking_checked_at") or "",
                "tracking_status": row.get("tracking_status") or "",
            },
        )
        if row.get("odoo_order_name") not in entry["odoo_order_names"]:
            entry["odoo_order_names"].append(row.get("odoo_order_name"))
        entry["lines"].append(row)
    orders, total, page, per_page = paginate_values(list(grouped.values()), page, per_page)
    return {"ok": True, "orders": orders, "rows": rows[:per_page], "page": page, "per_page": per_page, "total": total}


@app.get("/api/tracking/fulfilment-pending")
def api_tracking_fulfilment_pending(store_id: Optional[int] = None, page: int = 1, per_page: int = 100) -> dict[str, Any]:
    rows = delivered_unfulfilled_rows(store_id)
    paged_rows, total, page, per_page = paginate_values(rows, page, per_page)
    return {"ok": True, "rows": paged_rows, "count": total, "page": page, "per_page": per_page, "total": total}


@app.post("/api/tracking/update")
def api_tracking_update(payload: ChromeTrackingUpdatePayload) -> dict[str, Any]:
    amazon_order_id = clean_text(payload.amazon_order_id)
    if not amazon_order_id:
        raise HTTPException(400, "amazon_order_id is required")
    packages = payload.packages or []
    status = tracking_status_from_packages(packages)
    delivered_flag = status == "Delivered" and packages and all(
        "delivered" in json.dumps(package, default=str).lower() for package in packages
    )
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_lines WHERE amazon_order_id=? AND state IN ('ordered', 'dispatched')",
            (amazon_order_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(404, "Tracked Amazon order not found")
        updated = 0
        for row in rows:
            line_packages = [package for package in packages if package_matches_line(package, row)]
            if not line_packages:
                line_packages = packages
            line_status = tracking_status_from_packages(line_packages)
            line_delivered = line_status == "Delivered" and line_packages and all(
                "delivered" in json.dumps(package, default=str).lower() for package in line_packages
            )
            conn.execute(
                """
                UPDATE order_lines
                SET amazon_order_url=COALESCE(NULLIF(?, ''), amazon_order_url),
                    tracking_status=?,
                    tracking_payload=?,
                    tracking_checked_at=?,
                    state=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    clean_text(payload.amazon_order_url),
                    line_status,
                    json.dumps(line_packages, default=str)[:4000],
                    utc_now(),
                    "delivered" if line_delivered else "ordered",
                    utc_now(),
                    row["id"],
                ),
            )
            updated += 1
            if line_delivered:
                updated_row = conn.execute("SELECT * FROM order_lines WHERE id=?", (row["id"],)).fetchone()
                if updated_row:
                    ensure_inventory_for_line(updated_row)
    if delivered_flag:
        try:
            store = get_store(int(rows[0]["store_id"]))
            note = f"Amazon tracking delivered for order {html.escape(amazon_order_id)}."
            for order_id in sorted({int(row["odoo_order_id"]) for row in rows}):
                OdooClient(store).post_order_note(order_id, note)
        except Exception:
            pass
    return {"ok": True, "updated": updated, "tracking_status": status}


@app.post("/api/manual-amazon/match")
def api_manual_amazon_match(payload: ManualAmazonOrderMatchPayload) -> dict[str, Any]:
    amazon_order_id = clean_text(payload.amazon_order_id)
    if not amazon_order_id:
        raise HTTPException(400, "amazon_order_id is required")
    refs = manual_order_refs_from_payload(payload)
    if not refs:
        return {"ok": True, "matched": 0, "skipped": 0, "order_names": [], "message": f"No Nutricity order references found for {amazon_order_id}."}
    amazon_order_url = clean_text(payload.amazon_order_url) or order_line_amazon_url(amazon_order_id)
    amazon_account_name = clean_text(payload.amazon_account_name) or "Chrome Manual Matcher"
    with db() as conn:
        params: list[Any] = refs[:]
        store_filter = ""
        if payload.store_id:
            store_filter = " AND store_id=?"
            params.append(int(payload.store_id))
        rows = rows_to_dicts(conn.execute(
            f"""
            SELECT *
            FROM order_lines
            WHERE UPPER(odoo_order_name) IN ({','.join('?' for _ in refs)})
              AND COALESCE(amazon_order_id, '') = ''
              AND state NOT IN ('cancelled', 'refunded')
              AND COALESCE(odoo_status_label, '') NOT IN ('cancelled', 'refunded')
              {store_filter}
            ORDER BY store_id, odoo_order_id, id
            """,
            params,
        ).fetchall())
        if not rows:
            return {"ok": True, "matched": 0, "skipped": len(refs), "order_names": refs, "message": f"No unmatched pulled rows found for {', '.join(refs)}."}
        now = utc_now()
        for row in rows:
            conn.execute(
                """
                UPDATE order_lines
                SET amazon_order_id=?,
                    amazon_order_url=?,
                    amazon_account_name=?,
                    order_engine='chrome',
                    amazon_status='ordered',
                    state='ordered',
                    last_error=NULL,
                    ordered_at=COALESCE(ordered_at, ?),
                    updated_at=?
                WHERE id=?
                """,
                (amazon_order_id, amazon_order_url, amazon_account_name, now, now, row["id"]),
            )
            updated = conn.execute("SELECT * FROM order_lines WHERE id=?", (row["id"],)).fetchone()
            if updated:
                ensure_inventory_for_line(updated)
                index_order_line(updated)
    note_manual_amazon_match(rows, amazon_order_id, amazon_order_url, amazon_account_name)
    write_report(rows[0]["store_id"])
    matched_refs = sorted({str(row["odoo_order_name"]).upper() for row in rows})
    return {
        "ok": True,
        "matched": len(rows),
        "order_names": matched_refs,
        "amazon_order_id": amazon_order_id,
        "message": f"Matched Amazon order {amazon_order_id} to {len(rows)} line(s): {', '.join(matched_refs)}.",
    }


@app.get("/api/epost/tracking")
def api_epost_tracking(store_id: Optional[int] = None, page: int = 1, per_page: int = 100, status: str = "all") -> dict[str, Any]:
    rows, total, page, per_page = paged_epost_tracking_rows(store_id, page, per_page, status)
    return {"ok": True, "rows": rows, "page": page, "per_page": per_page, "total": total}


@app.get("/api/public/epost")
def api_public_epost(store_id: Optional[int] = None, status: str = "pending") -> dict[str, Any]:
    rows, total, page, per_page = paged_epost_tracking_rows(store_id, 1, 100, status)
    public_rows = [
        {
            "store_name": row.get("store_name"),
            "odoo_order_name": row.get("odoo_order_name"),
            "tracking_code": row.get("tracking_code"),
            "tracking_url": row.get("tracking_url"),
            "status": row.get("status") or row.get("epost_status"),
            "last_update_at": row.get("last_update_at"),
            "destination": row.get("destination"),
        }
        for row in rows
    ]
    return {"ok": True, "rows": public_rows, "page": page, "per_page": per_page, "total": total}


@app.get("/api/public/pending")
def api_public_pending(store_id: Optional[int] = None) -> dict[str, Any]:
    rows = delivered_unfulfilled_rows(store_id)
    return {
        "ok": True,
        "rows": [
            {
                "store_name": row.get("store_name"),
                "odoo_order_name": row.get("odoo_order_name"),
                "amazon_order_id": row.get("amazon_order_id"),
                "tracking_status": row.get("tracking_status"),
                "picking_names": row.get("picking_names"),
                "message": row.get("message"),
            }
            for row in rows
        ],
        "total": len(rows),
    }


@app.get("/api/public/tracking")
def api_public_tracking(store_id: Optional[int] = None) -> dict[str, Any]:
    rows = tracking_rows(store_id)
    stores = {int(store["id"]): store for store in list_stores()}
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        amazon_order_id = clean_text(row.get("amazon_order_id"))
        if not amazon_order_id:
            continue
        store = stores.get(int(row.get("store_id") or 0))
        entry = grouped.setdefault(
            amazon_order_id,
            {
                "store_name": store["name"] if store else "",
                "odoo_order_names": [],
                "amazon_order_id": amazon_order_id,
                "tracking_status": row.get("tracking_status") or "",
                "tracking_checked_at": row.get("tracking_checked_at") or "",
                "packages": [],
            },
        )
        odoo_name = clean_text(row.get("odoo_order_name"))
        if odoo_name and odoo_name not in entry["odoo_order_names"]:
            entry["odoo_order_names"].append(odoo_name)
        if row.get("tracking_status") and not entry.get("tracking_status"):
            entry["tracking_status"] = row.get("tracking_status")
        if row.get("tracking_checked_at") and not entry.get("tracking_checked_at"):
            entry["tracking_checked_at"] = row.get("tracking_checked_at")
        try:
            packages = json.loads(row.get("tracking_payload") or "[]")
            if not isinstance(packages, list):
                packages = []
        except Exception:
            packages = []
        for package in packages:
            if not isinstance(package, dict):
                continue
            package_summary = {
                "carrier": package.get("carrier") or package.get("carrier_name") or "",
                "tracking_id": package.get("tracking_id") or package.get("trackingId") or package.get("tracking_number") or "",
                "status": package.get("status") or package.get("delivery_status") or entry["tracking_status"],
            }
            if package_summary not in entry["packages"]:
                entry["packages"].append(package_summary)
    public_rows = []
    for entry in grouped.values():
        packages = entry.pop("packages")
        entry["odoo_order_names"] = ", ".join(entry["odoo_order_names"])
        entry["package_count"] = len(packages)
        entry["tracking_numbers"] = ", ".join(clean_text(package.get("tracking_id")) for package in packages if clean_text(package.get("tracking_id")))
        entry["carriers"] = ", ".join(dict.fromkeys(clean_text(package.get("carrier")) for package in packages if clean_text(package.get("carrier"))))
        public_rows.append(entry)
    return {"ok": True, "rows": public_rows, "total": len(public_rows)}


@app.get("/api/amazon-otp")
def api_amazon_otp(q: str = "") -> dict[str, Any]:
    rows = amazon_otp_rows(q)
    return {"ok": True, "rows": rows, "total": len(rows)}


@app.get("/api/public/amazon-otp")
def api_public_amazon_otp(q: str = "") -> dict[str, Any]:
    rows = amazon_otp_rows(q)
    public_rows = [
        {
            "match_status": row.get("match_status"),
            "otp": row.get("otp"),
            "tracking_numbers": row.get("tracking_numbers"),
            "carriers": row.get("carriers"),
            "amazon_order_id": row.get("amazon_order_id"),
            "tracking_status": row.get("tracking_status"),
            "product_summary": row.get("product_summary"),
            "recipient": row.get("recipient"),
            "last_updated": row.get("updated_at"),
        }
        for row in rows
    ]
    return {"ok": True, "rows": public_rows, "total": len(public_rows)}


def public_table_page(title: str, rows: list[dict[str, Any]]) -> HTMLResponse:
    columns = list(rows[0].keys()) if rows else ["status"]
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(row.get(column) or ''))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    head = "".join(f"<th>{html.escape(column.replace('_', ' ').title())}</th>" for column in columns)
    return HTMLResponse(
        f"""
        <!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <link rel='stylesheet' href='/static/vendor/tabler/css/tabler.min.css'><title>{html.escape(title)}</title></head>
        <body><div class='page'><main class='page-wrapper'><div class='page-header'><div class='container-xl'>
        <div class='page-pretitle'>Public view</div><h1 class='page-title'>{html.escape(title)}</h1></div></div>
        <div class='page-body'><div class='container-xl'><div class='card'><div class='table-responsive'>
        <table class='table table-vcenter'><thead><tr>{head}</tr></thead><tbody>{body or f"<tr><td colspan='{len(columns)}' class='text-secondary'>No rows.</td></tr>"}</tbody></table>
        </div></div></div></div></main></div></body></html>
        """
    )


@app.get("/public/epost", response_class=HTMLResponse)
def public_epost_page(store_id: Optional[int] = None, status: str = "pending") -> HTMLResponse:
    return public_table_page("ePost Tracking", api_public_epost(store_id, status)["rows"])


@app.get("/public/pending", response_class=HTMLResponse)
def public_pending_page(store_id: Optional[int] = None) -> HTMLResponse:
    return public_table_page("Pending Odoo Fulfilment", api_public_pending(store_id)["rows"])


@app.get("/public/tracking", response_class=HTMLResponse)
def public_tracking_page(store_id: Optional[int] = None) -> HTMLResponse:
    return public_table_page("Amazon Tracking", api_public_tracking(store_id)["rows"])


@app.get("/public/amazon-otp", response_class=HTMLResponse)
def public_amazon_otp_page(q: str = "") -> HTMLResponse:
    initial_q = html.escape(q)
    return HTMLResponse(
        f"""
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width, initial-scale=1">
          <link rel="stylesheet" href="/static/vendor/tabler/css/tabler.min.css">
          <title>Amazon OTP Lookup</title>
        </head>
        <body>
          <div class="page">
            <main class="page-wrapper">
              <div class="page-header">
                <div class="container-xl">
                  <div class="page-pretitle">Public view</div>
                  <h1 class="page-title">Amazon OTP Lookup</h1>
                </div>
              </div>
              <div class="page-body">
                <div class="container-xl">
                  <form id="search-form" class="row g-2 mb-3">
                    <div class="col">
                      <input id="search-input" class="form-control form-control-lg" value="{initial_q}" placeholder="Search tracking number, Amazon order, OTP, product">
                    </div>
                    <div class="col-auto">
                      <button class="btn btn-primary btn-lg" type="submit">Search</button>
                    </div>
                  </form>
                  <div class="card">
                    <div class="table-responsive">
                      <table class="table table-vcenter table-mobile-md">
                        <thead>
                          <tr>
                            <th>OTP</th>
                            <th>Tracking</th>
                            <th>Amazon Order</th>
                            <th>Status</th>
                            <th>Product</th>
                            <th>Recipient</th>
                            <th>Updated</th>
                          </tr>
                        </thead>
                        <tbody id="rows">
                          <tr><td colspan="7" class="text-secondary">Loading...</td></tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </main>
          </div>
          <script>
            const rowsEl = document.getElementById("rows");
            const inputEl = document.getElementById("search-input");
            const formEl = document.getElementById("search-form");
            const esc = (value) => String(value || "").replace(/[&<>"']/g, (char) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[char]));
            const fmt = (value) => {{
              if (!value) return "";
              const date = new Date(value);
              return Number.isNaN(date.getTime()) ? esc(value) : esc(date.toLocaleString());
            }};
            async function loadRows() {{
              const query = inputEl.value.trim();
              const response = await fetch(`/api/public/amazon-otp${{query ? `?q=${{encodeURIComponent(query)}}` : ""}}`);
              const payload = await response.json();
              const rows = payload.rows || [];
              if (!rows.length) {{
                rowsEl.innerHTML = '<tr><td colspan="7" class="text-secondary">No matching OTP records.</td></tr>';
                return;
              }}
              rowsEl.innerHTML = rows.map((row) => `
                <tr>
                  <td data-label="OTP"><span class="h2 font-monospace">${{esc(row.otp || "Pending")}}</span><div class="text-secondary small">${{esc(row.match_status)}}</div></td>
                  <td data-label="Tracking"><div class="font-monospace">${{esc(row.tracking_numbers || "Pending")}}</div><div class="text-secondary small">${{esc(row.carriers)}}</div></td>
                  <td data-label="Amazon Order"><span class="font-monospace">${{esc(row.amazon_order_id)}}</span></td>
                  <td data-label="Status">${{esc(row.tracking_status || "")}}</td>
                  <td data-label="Product">${{esc(row.product_summary || "")}}</td>
                  <td data-label="Recipient">${{esc(row.recipient || "")}}</td>
                  <td data-label="Updated">${{fmt(row.last_updated)}}</td>
                </tr>
              `).join("");
            }}
            formEl.addEventListener("submit", (event) => {{
              event.preventDefault();
              loadRows();
            }});
            loadRows();
            setInterval(loadRows, 300000);
          </script>
        </body>
        </html>
        """
    )


@app.get("/api/duplicate-tracking")
def api_duplicate_tracking(store_id: Optional[int] = None, q: str = "") -> dict[str, Any]:
    rows = duplicate_tracking_rows(store_id, q)
    return {"ok": True, "rows": rows, "total": len(rows)}


@app.post("/api/epost/sync")
def api_epost_sync(payload: EpostSyncPayload) -> dict[str, Any]:
    days = max(1, min(30, int(payload.days or 2)))
    synced = sync_epost_tracking_from_odoo(payload.store_id, days)
    rows, total, page, per_page = paged_epost_tracking_rows(payload.store_id, 1, 100, "all")
    return {
        "ok": True,
        "synced": synced,
        "rows": rows,
        "page": page,
        "per_page": per_page,
        "total": total,
        "message": f"Synced {synced} ePost tracking code(s) from Odoo pickings fulfilled in the last {days} day(s). Existing codes were updated, not duplicated.",
    }


@app.get("/api/epost/due")
def api_epost_due(days: int = 1, store_id: Optional[int] = None) -> dict[str, Any]:
    return {"ok": True, "rows": due_epost_tracking_rows(days, store_id)}


@app.post("/api/epost/update")
def api_epost_update(payload: EpostTrackingUpdatePayload) -> dict[str, Any]:
    updated = 0
    with db() as conn:
        for item in payload.results or []:
            code = clean_text(str(item.get("tracking_code") or item.get("tracking") or "")).upper()
            if not code:
                continue
            status = clean_text(str(item.get("status") or ""))
            last_update_at = clean_text(str(item.get("date") or item.get("last_update_at") or ""))
            epost_status = epost_status_from_update(status, last_update_at)
            cursor = conn.execute(
                """
                UPDATE epost_global_tracking
                SET status=?,
                    last_update_at=?,
                    location=?,
                    destination=?,
                    awb=?,
                    tracking_url=COALESCE(NULLIF(?, ''), tracking_url),
                    events_json=?,
                    last_checked_at=?,
                    epost_status=?,
                    updated_at=?
                WHERE tracking_code=?
                """,
                (
                    status,
                    last_update_at,
                    clean_text(str(item.get("location") or "")),
                    clean_text(str(item.get("destination") or "")),
                    clean_text(str(item.get("awb") or "")),
                    clean_text(str(item.get("tracking_url") or "")),
                    json.dumps(item.get("events") or [], default=str)[:4000],
                    utc_now(),
                    epost_status,
                    utc_now(),
                    code,
                ),
            )
            updated += cursor.rowcount
    return {"ok": True, "updated": updated}


@app.post("/api/exports")
def api_create_export(payload: ExportCreatePayload) -> dict[str, Any]:
    view = clean_text(payload.view).lower().replace("-", "_")
    if view not in EXPORT_VIEWS:
        raise HTTPException(400, "Unknown export view.")
    job_id = uuid.uuid4().hex
    columns = normalize_export_columns(view, payload.columns)
    with db() as conn:
        conn.execute(
            """
            INSERT INTO export_jobs
            (id, view, store_id, status, columns_json, filters_json, selected_ids_json, select_all,
             created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                view,
                payload.store_id,
                json.dumps(columns),
                json.dumps(payload.filters or {}),
                json.dumps(payload.selected_ids or []),
                1 if payload.select_all else 0,
                utc_now(),
                utc_now(),
            ),
        )
    start_export_job(job_id)
    return {"ok": True, "job_id": job_id, "message": f"Export job started for {view.replace('_', ' ')}."}


@app.get("/api/exports")
def api_exports(page: int = 1, per_page: int = 100) -> dict[str, Any]:
    cleanup_expired_exports()
    page, per_page, offset = pagination_bounds(page, per_page)
    with db() as conn:
        total = int(conn.execute("SELECT COUNT(*) AS count FROM export_jobs").fetchone()["count"] or 0)
        jobs = rows_to_dicts(conn.execute(
            """
            SELECT * FROM export_jobs
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (per_page, offset),
        ).fetchall())
        job_ids = [str(job["id"]) for job in jobs]
        files: list[dict[str, Any]] = []
        if job_ids:
            files = rows_to_dicts(conn.execute(
                f"""
                SELECT * FROM export_files
                WHERE job_id IN ({','.join('?' for _ in job_ids)})
                ORDER BY job_id, part_number
                """,
                job_ids,
            ).fetchall())
    by_job: dict[str, list[dict[str, Any]]] = {}
    for file_row in files:
        file_row["storage_url"] = ""
        by_job.setdefault(str(file_row["job_id"]), []).append(file_row)
    for job in jobs:
        job["files"] = by_job.get(str(job["id"]), [])
    return {"ok": True, "jobs": jobs, "page": page, "per_page": per_page, "total": total}


@app.post("/api/exports/{job_id}/cancel")
def api_cancel_export(job_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM export_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Export job not found.")
        if row["status"] in {"completed", "failed", "cancelled"}:
            return {"ok": True, "message": f"Export is already {row['status']}."}
        conn.execute("UPDATE export_jobs SET stop_requested=1, status='cancelling', updated_at=? WHERE id=?", (utc_now(), job_id))
    return {"ok": True, "message": "Export cancellation requested."}


@app.get("/api/exports/files/{file_id}/download")
def api_download_export_file(file_id: int) -> Response:
    with db() as conn:
        row = conn.execute("SELECT * FROM export_files WHERE id=? AND deleted_at IS NULL", (file_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Export file not found.")
        expires_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        conn.execute("UPDATE export_files SET downloaded_at=COALESCE(downloaded_at, ?), expires_at=? WHERE id=?", (utc_now(), expires_at, file_id))
    if not row["storage_key"]:
        raise HTTPException(404, "Export file is missing its R2 storage key.")
    return RedirectResponse(presigned_storage_url(row["storage_key"]))


@app.post("/punchout/cart-return")
async def punchout_cart_return(request: Request, store_id: Optional[int] = None) -> Response:
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        stores = list_stores()
        target_store_id = store_id or (stores[0]["id"] if stores else None)
        if not target_store_id:
            return cxml_status_response(400, "No store configured")
        items = parse_punchout_order_message(raw)
        if not items:
            return cxml_status_response(400, "No SupplierPartAuxiliaryID items found")
        line_ids = save_punchout_spaids(int(target_store_id), items)
        if line_ids:
            try:
                place_orders(
                    int(target_store_id),
                    address_id=None,
                    amazon_account_id=None,
                    line_ids=line_ids,
                    club=False,
                    ordering_engine="cxml",
                    allow_missing_spaid=False,
                )
            except Exception:
                pass
        return cxml_status_response(200, f"OK matched {len(line_ids)} item(s)")
    except ET.ParseError:
        return cxml_status_response(400, "Invalid cXML")
    except Exception as exc:
        return cxml_status_response(500, str(exc)[:200])


@app.get("/punchout/launch", response_class=HTMLResponse)
def punchout_launch(store_id: int, amazon_account_id: Optional[int] = None, line_id: Optional[int] = None, test: bool = False) -> HTMLResponse:
    account = get_amazon_account(amazon_account_id)
    punchout_url = clean_text(account["cxml_punchout_test_url"] if test and "cxml_punchout_test_url" in account.keys() else "")
    if not punchout_url:
        punchout_url = clean_text(account["cxml_punchout_url"] if "cxml_punchout_url" in account.keys() else "")
    if not punchout_url:
        raise HTTPException(400, "Amazon Punchout URL is missing on the selected Amazon account.")
    return_url = get_default_punchout_return_url()
    if not return_url:
        raise HTTPException(400, "Default Punchout return URL is missing in Settings.")
    buyer_email = clean_text(account["buyer_email"] if "buyer_email" in account.keys() else "")
    line = None
    if line_id:
        with db() as conn:
            line = conn.execute("SELECT * FROM order_lines WHERE id=? AND store_id=?", (line_id, store_id)).fetchone()
    try:
        start_page_url = create_punchout_session(account, punchout_url, return_url, buyer_email, line)
        return RedirectResponse(start_page_url, status_code=303)
    except Exception as exc:
        html_body = f"""<!doctype html>
<html>
  <head><meta charset="utf-8"><title>Amazon Punchout Failed</title></head>
  <body>
    <h1>Amazon Punchout Failed</h1>
    <p>{html.escape(str(exc))}</p>
    <p>Punchout URL: <code>{html.escape(punchout_url)}</code></p>
    <p>Return URL: <code>{html.escape(return_url)}</code></p>
  </body>
</html>"""
        return HTMLResponse(html_body, status_code=502)


@app.post("/api/delivery-check")
def api_delivery_check(payload: StoreActionPayload) -> dict[str, Any]:
    delivered, errors = check_deliveries(payload.store_id)
    data = dashboard_data(payload.store_id)
    data["message"] = f"Delivery check complete. Delivered: {delivered}; errors: {errors}."
    return data


@app.post("/api/lines/delete")
def api_delete_lines(payload: DeleteLinesPayload) -> dict[str, Any]:
    if payload.line_ids:
        placeholders = ",".join("?" for _ in payload.line_ids)
        with db() as conn:
            conn.execute(
                f"DELETE FROM order_lines WHERE store_id = ? AND id IN ({placeholders})",
                [payload.store_id, *payload.line_ids],
            )
    data = dashboard_data(payload.store_id)
    data["message"] = f"Deleted {len(payload.line_ids)} selected line{'s' if len(payload.line_ids) != 1 else ''}."
    return data


@app.post("/api/lines/reset-fulfilment")
def api_reset_line_fulfilment(payload: DeleteLinesPayload) -> dict[str, Any]:
    if not payload.line_ids:
        raise HTTPException(400, "Select at least one order line to reset.")
    placeholders = ",".join("?" for _ in payload.line_ids)
    now = utc_now()
    with db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE order_lines
            SET state='pulled',
                amazon_order_id=NULL,
                amazon_order_url=NULL,
                amazon_account_id=NULL,
                amazon_account_name=NULL,
                amazon_group_key=NULL,
                amazon_status=NULL,
                tracking_status=NULL,
                tracking_payload=NULL,
                tracking_checked_at=NULL,
                chrome_claimed_by=NULL,
                chrome_claimed_at=NULL,
                chrome_claim_expires_at=NULL,
                amazon_unit_price=NULL,
                amazon_total_price=NULL,
                chrome_profit_total=NULL,
                fulfilment_note=NULL,
                last_error=NULL,
                missing_asin=NULL,
                cost_approved_at=NULL,
                cost_review_loss=NULL,
                ordered_at=NULL,
                updated_at=?
            WHERE store_id=?
              AND id IN ({placeholders})
            """,
            [now, payload.store_id, *payload.line_ids],
        )
        conn.execute(
            f"""
            UPDATE amazon_attempts
            SET status='reset', error=NULL
            WHERE order_line_id IN ({placeholders})
              AND mode IN ('chrome', 'rest', 'cxml')
              AND status IN ('queued', 'submitted', 'ok', 'error', 'costly')
            """,
            payload.line_ids,
        )
    data = dashboard_data(payload.store_id)
    data["message"] = f"Reset {cursor.rowcount} selected line{'s' if cursor.rowcount != 1 else ''} to fresh pulled status."
    return data


@app.put("/api/lines/{line_id}/spaid")
def api_update_line_spaid(line_id: int, payload: LineSpaidPayload) -> dict[str, Any]:
    value = clean_text(payload.supplier_part_auxiliary_id)
    with db() as conn:
        row = conn.execute("SELECT id FROM order_lines WHERE id=? AND store_id=?", (line_id, payload.store_id)).fetchone()
        if not row:
            raise HTTPException(404, "Order line not found.")
        conn.execute(
            """
            UPDATE order_lines
            SET supplier_part_auxiliary_id=?, last_error=NULL, updated_at=?
            WHERE id=? AND store_id=?
            """,
            (value, utc_now(), line_id, payload.store_id),
        )
    data = dashboard_data(payload.store_id)
    data["message"] = f"SupplierPartAuxiliaryID {'saved' if value else 'cleared'}."
    data["ok"] = True
    return data


@app.get("/api/punchout-return-urls")
def api_list_punchout_return_urls() -> list[dict[str, Any]]:
    return rows_to_dicts(list_punchout_return_urls())


@app.get("/api/punchout-launch-settings")
def api_punchout_launch_settings(amazon_account_id: Optional[int] = None) -> dict[str, Any]:
    account = get_amazon_account(amazon_account_id)
    return {
        "punchout_url": clean_text(account["cxml_punchout_url"] if "cxml_punchout_url" in account.keys() else ""),
        "punchout_test_url": clean_text(account["cxml_punchout_test_url"] if "cxml_punchout_test_url" in account.keys() else ""),
        "return_url": get_default_punchout_return_url(),
    }


@app.post("/api/punchout-return-urls")
def api_create_punchout_return_url(payload: PunchoutReturnUrlPayload) -> dict[str, Any]:
    label = clean_text(payload.label)
    url = clean_text(payload.url)
    if not label or not url:
        raise HTTPException(400, "Label and URL are required.")
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE punchout_return_urls SET is_default=0")
        conn.execute(
            """
            INSERT INTO punchout_return_urls (label, url, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (label, url, 1 if payload.is_default else 0, utc_now(), utc_now()),
        )
    return {"ok": True, "punchout_return_urls": rows_to_dicts(list_punchout_return_urls())}


@app.put("/api/punchout-return-urls/{url_id}")
def api_update_punchout_return_url(url_id: int, payload: PunchoutReturnUrlPayload) -> dict[str, Any]:
    label = clean_text(payload.label)
    url = clean_text(payload.url)
    if not label or not url:
        raise HTTPException(400, "Label and URL are required.")
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE punchout_return_urls SET is_default=0")
        conn.execute(
            """
            UPDATE punchout_return_urls
            SET label=?, url=?, is_default=?, updated_at=?
            WHERE id=?
            """,
            (label, url, 1 if payload.is_default else 0, utc_now(), url_id),
        )
    return {"ok": True, "punchout_return_urls": rows_to_dicts(list_punchout_return_urls())}


@app.delete("/api/punchout-return-urls/{url_id}")
def api_delete_punchout_return_url(url_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT is_default FROM punchout_return_urls WHERE id=?", (url_id,)).fetchone()
        conn.execute("DELETE FROM punchout_return_urls WHERE id=?", (url_id,))
        if row and int(row["is_default"]):
            replacement = conn.execute("SELECT id FROM punchout_return_urls ORDER BY id LIMIT 1").fetchone()
            if replacement:
                conn.execute("UPDATE punchout_return_urls SET is_default=1 WHERE id=?", (replacement["id"],))
    return {"ok": True, "punchout_return_urls": rows_to_dicts(list_punchout_return_urls())}


@app.get("/api/stores")
def api_list_stores() -> list[dict[str, Any]]:
    return rows_to_dicts(list_stores())


@app.post("/api/stores")
def api_create_store(payload: StorePayload) -> dict[str, Any]:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO stores (name, odoo_url, odoo_db, odoo_user, odoo_password, website_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                payload.name,
                payload.odoo_url.rstrip("/"),
                payload.odoo_db,
                payload.odoo_user,
                payload.odoo_password,
                parse_optional_int(payload.website_id),
                utc_now(),
                utc_now(),
            ),
        )
    with _STORE_CACHE_LOCK:
        _STORE_CACHE.clear()
    return {"ok": True, "stores": rows_to_dicts(list_stores())}


@app.put("/api/stores/{store_id}")
def api_update_store(store_id: int, payload: StorePayload) -> dict[str, Any]:
    with db() as conn:
        conn.execute(
            """
            UPDATE stores
            SET name=?, odoo_url=?, odoo_db=?, odoo_user=?, odoo_password=?, website_id=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.name,
                payload.odoo_url.rstrip("/"),
                payload.odoo_db,
                payload.odoo_user,
                payload.odoo_password,
                parse_optional_int(payload.website_id),
                utc_now(),
                store_id,
            ),
        )
    with _STORE_CACHE_LOCK:
        _STORE_CACHE.pop(int(store_id), None)
    return {"ok": True, "stores": rows_to_dicts(list_stores())}


@app.delete("/api/stores/{store_id}")
def api_delete_store(store_id: int) -> dict[str, Any]:
    with db() as conn:
        conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    with _STORE_CACHE_LOCK:
        _STORE_CACHE.pop(int(store_id), None)
    return {"ok": True, "stores": rows_to_dicts(list_stores())}


@app.post("/api/stores/{store_id}/test")
def api_test_store(store_id: int) -> dict[str, Any]:
    try:
        store = get_store(store_id)
        version = OdooClient(store).common.version()
        return {"ok": True, "message": f"OK: {store.name} connected to Odoo {version.get('server_version', 'unknown')}."}
    except Exception as exc:
        return {"ok": False, "message": f"Failed: {exc}"}


@app.get("/api/addresses")
def api_list_addresses() -> list[dict[str, Any]]:
    return rows_to_dicts(list_addresses())


@app.post("/api/addresses")
def api_create_address(payload: AddressPayload) -> dict[str, Any]:
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE fulfilment_addresses SET is_default = 0")
        conn.execute(
            """
            INSERT INTO fulfilment_addresses
            (label, company_name, phone_number, address_line1, address_line2, address_line3,
             city, state_or_region, postal_code, country_code, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.label,
                payload.company_name,
                payload.phone_number,
                payload.address_line1,
                payload.address_line2,
                payload.address_line3,
                payload.city,
                payload.state_or_region,
                payload.postal_code,
                payload.country_code.strip().upper(),
                1 if payload.is_default else 0,
                utc_now(),
                utc_now(),
            ),
        )
    return {"ok": True, "addresses": rows_to_dicts(list_addresses())}


@app.put("/api/addresses/{address_id}")
def api_update_address(address_id: int, payload: AddressPayload) -> dict[str, Any]:
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE fulfilment_addresses SET is_default = 0")
        conn.execute(
            """
            UPDATE fulfilment_addresses
            SET label=?, company_name=?, phone_number=?, address_line1=?, address_line2=?, address_line3=?,
                city=?, state_or_region=?, postal_code=?, country_code=?, is_default=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.label,
                payload.company_name,
                payload.phone_number,
                payload.address_line1,
                payload.address_line2,
                payload.address_line3,
                payload.city,
                payload.state_or_region,
                payload.postal_code,
                payload.country_code.strip().upper(),
                1 if payload.is_default else 0,
                utc_now(),
                address_id,
            ),
        )
    return {"ok": True, "addresses": rows_to_dicts(list_addresses())}


@app.delete("/api/addresses/{address_id}")
def api_delete_address(address_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT is_default FROM fulfilment_addresses WHERE id = ?", (address_id,)).fetchone()
        conn.execute("DELETE FROM fulfilment_addresses WHERE id = ?", (address_id,))
        if row and int(row["is_default"]):
            replacement = conn.execute("SELECT id FROM fulfilment_addresses ORDER BY id LIMIT 1").fetchone()
            if replacement:
                conn.execute("UPDATE fulfilment_addresses SET is_default = 1 WHERE id = ?", (replacement["id"],))
    return {"ok": True, "addresses": rows_to_dicts(list_addresses())}


@app.get("/api/amazon-accounts")
def api_list_amazon_accounts() -> list[dict[str, Any]]:
    return rows_to_dicts(list_amazon_accounts())


@app.post("/api/amazon-accounts")
def api_create_amazon_account(payload: AmazonAccountPayload) -> dict[str, Any]:
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE amazon_accounts SET is_default = 0")
        conn.execute(
            """
            INSERT INTO amazon_accounts
            (name, api_base_url, tracking_api_base_url, lwa_token_url, lwa_client_id, lwa_client_secret,
             lwa_refresh_token, api_access_token, buyer_email, buying_group_id, product_region, locale,
             cxml_from_identity, cxml_shared_secret, cxml_po_url, cxml_punchout_url, cxml_punchout_test_url,
             cxml_auth_mode, cxml_cart_session_id, cxml_credential_domain, cxml_to_identity, dry_run, is_default,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                payload.name,
                normalize_amazon_endpoint(payload.api_base_url),
                normalize_amazon_endpoint(payload.tracking_api_base_url),
                payload.lwa_token_url,
                payload.lwa_client_id,
                payload.lwa_client_secret,
                payload.lwa_refresh_token,
                payload.api_access_token,
                clean_text(payload.buyer_email),
                clean_text(payload.buying_group_id),
                clean_text(payload.product_region, "US").upper() or "US",
                clean_text(payload.locale, "en_US") or "en_US",
                clean_text(payload.cxml_from_identity),
                clean_text(payload.cxml_shared_secret),
                clean_text(payload.cxml_po_url),
                clean_text(payload.cxml_punchout_url),
                clean_text(payload.cxml_punchout_test_url),
                normalize_cxml_auth_mode(payload.cxml_auth_mode),
                clean_text(payload.cxml_cart_session_id),
                clean_text(payload.cxml_credential_domain, "NetworkId") or "NetworkId",
                clean_text(payload.cxml_to_identity, "Amazon") or "Amazon",
                1 if payload.is_default else 0,
                utc_now(),
                utc_now(),
            ),
        )
    return {"ok": True, "accounts": rows_to_dicts(list_amazon_accounts())}


@app.put("/api/amazon-accounts/{account_id}")
def api_update_amazon_account(account_id: int, payload: AmazonAccountPayload) -> dict[str, Any]:
    with db() as conn:
        if payload.is_default:
            conn.execute("UPDATE amazon_accounts SET is_default = 0")
        conn.execute(
            """
            UPDATE amazon_accounts
            SET name=?, api_base_url=?, tracking_api_base_url=?, lwa_token_url=?, lwa_client_id=?,
                lwa_client_secret=?, lwa_refresh_token=?, api_access_token=?, buyer_email=?, buying_group_id=?,
                product_region=?, locale=?, cxml_from_identity=?, cxml_shared_secret=?, cxml_po_url=?,
                cxml_punchout_url=?, cxml_punchout_test_url=?, cxml_auth_mode=?, cxml_cart_session_id=?,
                cxml_credential_domain=?, cxml_to_identity=?, dry_run=0, is_default=?, updated_at=?
            WHERE id=?
            """,
            (
                payload.name,
                normalize_amazon_endpoint(payload.api_base_url),
                normalize_amazon_endpoint(payload.tracking_api_base_url),
                payload.lwa_token_url,
                payload.lwa_client_id,
                payload.lwa_client_secret,
                payload.lwa_refresh_token,
                payload.api_access_token,
                clean_text(payload.buyer_email),
                clean_text(payload.buying_group_id),
                clean_text(payload.product_region, "US").upper() or "US",
                clean_text(payload.locale, "en_US") or "en_US",
                clean_text(payload.cxml_from_identity),
                clean_text(payload.cxml_shared_secret),
                clean_text(payload.cxml_po_url),
                clean_text(payload.cxml_punchout_url),
                clean_text(payload.cxml_punchout_test_url),
                normalize_cxml_auth_mode(payload.cxml_auth_mode),
                clean_text(payload.cxml_cart_session_id),
                clean_text(payload.cxml_credential_domain, "NetworkId") or "NetworkId",
                clean_text(payload.cxml_to_identity, "Amazon") or "Amazon",
                1 if payload.is_default else 0,
                utc_now(),
                account_id,
            ),
        )
    return {"ok": True, "accounts": rows_to_dicts(list_amazon_accounts())}


@app.delete("/api/amazon-accounts/{account_id}")
def api_delete_amazon_account(account_id: int) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT is_default FROM amazon_accounts WHERE id = ?", (account_id,)).fetchone()
        conn.execute("DELETE FROM amazon_accounts WHERE id = ?", (account_id,))
        if row and int(row["is_default"]):
            replacement = conn.execute("SELECT id FROM amazon_accounts ORDER BY id LIMIT 1").fetchone()
            if replacement:
                conn.execute("UPDATE amazon_accounts SET is_default = 1 WHERE id = ?", (replacement["id"],))
    return {"ok": True, "accounts": rows_to_dicts(list_amazon_accounts())}


@app.post("/api/amazon-accounts/{account_id}/test")
def api_test_amazon_account(account_id: int) -> dict[str, Any]:
    try:
        account = get_amazon_account(account_id)
        token = AmazonBusinessClient(account)._token()
        cxml_auth_mode = normalize_cxml_auth_mode(account["cxml_auth_mode"] if "cxml_auth_mode" in account.keys() else "header")
        cxml_po_url = clean_text(account["cxml_po_url"] if "cxml_po_url" in account.keys() else "")
        cxml_cart_session_id = clean_text(account["cxml_cart_session_id"] if "cxml_cart_session_id" in account.keys() else "")
        cxml_credential_domain = clean_text(account["cxml_credential_domain"] if "cxml_credential_domain" in account.keys() else "NetworkId", "NetworkId") or "NetworkId"
        cxml_to_identity = clean_text(account["cxml_to_identity"] if "cxml_to_identity" in account.keys() else "Amazon", "Amazon") or "Amazon"
        cxml_status = (
            f" cXML PO URL is {'set' if cxml_po_url else 'missing'}; auth mode is {cxml_auth_mode}; "
            f"Credential domain is {cxml_credential_domain}; To identity is {cxml_to_identity}; "
            f"Punchout Cart/Session ID is {'set' if cxml_cart_session_id else 'not set'}."
        )
        if cxml_po_url:
            preflight = CxmlOrderingClient(account).preflight()
            cxml_status += (
                f" cXML preflight returned HTTP {preflight['status_code']} using auth mode {preflight['auth_mode']}."
            )
            if preflight["status_code"] == 401:
                cxml_status += " Amazon rejected the cXML URL/identity/secret before order payload validation."
            elif preflight["status_code"] < 500:
                cxml_status += " cXML endpoint responded, so credentials likely passed the first auth gate."
        return {"ok": True, "message": f"OK: {account['name']} generated an LWA access token ({len(token)} chars).{cxml_status}"}
    except Exception as exc:
        return {"ok": False, "message": f"Failed: {exc}"}


@app.get("/")
def app_home():
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return HTMLResponse(
        "<h1>Frontend not built</h1><p>Run <code>cd frontend && npm run build</code>, then restart FastAPI.</p>",
        status_code=503,
    )


@app.get("/legacy", response_class=HTMLResponse)
def dashboard(request: Request, store_id: Optional[int] = None) -> HTMLResponse:
    data = dashboard_data(store_id)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            **data,
        },
    )


@app.get("/inventory", response_class=HTMLResponse)
def inventory_page(request: Request, store_id: Optional[int] = None) -> HTMLResponse:
    items, _total = list_inventory_items(store_id, 1, 100)
    return templates.TemplateResponse(
        "inventory.html",
        {
            "request": request,
            "stores": list_stores(),
            "current_store_id": store_id,
            "items": items,
        },
    )


@app.get("/stores", response_class=HTMLResponse)
def stores_page(request: Request, test_result: str = "", test_status: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "stores.html",
        {
            "request": request,
            "stores": list_stores(),
            "test_result": test_result,
            "test_status": test_status,
        },
    )


@app.get("/addresses", response_class=HTMLResponse)
def addresses_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("addresses.html", {"request": request, "addresses": list_addresses()})


@app.get("/amazon-accounts", response_class=HTMLResponse)
def amazon_accounts_page(request: Request, test_result: str = "", test_status: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "amazon_accounts.html",
        {
            "request": request,
            "accounts": list_amazon_accounts(),
            "test_result": test_result,
            "test_status": test_status,
        },
    )


@app.post("/amazon-accounts")
def save_amazon_account(
    name: str = Form(...),
    api_base_url: str = Form("https://na.business-api.amazon.com"),
    tracking_api_base_url: str = Form("https://na.business-api.amazon.com"),
    lwa_token_url: str = Form("https://api.amazon.com/auth/o2/token"),
    lwa_client_id: str = Form(""),
    lwa_client_secret: str = Form(""),
    lwa_refresh_token: str = Form(""),
    api_access_token: str = Form(""),
    cxml_auth_mode: str = Form("header"),
    cxml_cart_session_id: str = Form(""),
    cxml_credential_domain: str = Form("NetworkId"),
    cxml_to_identity: str = Form("Amazon"),
    is_default: str = Form("false"),
) -> RedirectResponse:
    make_default = is_default == "true"
    with db() as conn:
        if make_default:
            conn.execute("UPDATE amazon_accounts SET is_default = 0")
        conn.execute(
            """
            INSERT INTO amazon_accounts
            (name, api_base_url, tracking_api_base_url, lwa_token_url, lwa_client_id, lwa_client_secret,
             lwa_refresh_token, api_access_token, cxml_auth_mode, cxml_cart_session_id, dry_run, is_default,
             cxml_credential_domain, cxml_to_identity, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                normalize_amazon_endpoint(api_base_url),
                normalize_amazon_endpoint(tracking_api_base_url),
                lwa_token_url,
                lwa_client_id,
                lwa_client_secret,
                lwa_refresh_token,
                api_access_token,
                normalize_cxml_auth_mode(cxml_auth_mode),
                clean_text(cxml_cart_session_id),
                0,
                1 if make_default else 0,
                clean_text(cxml_credential_domain, "NetworkId") or "NetworkId",
                clean_text(cxml_to_identity, "Amazon") or "Amazon",
                utc_now(),
                utc_now(),
            ),
        )
    return RedirectResponse("/amazon-accounts", status_code=303)


@app.post("/amazon-accounts/{account_id}/update")
def update_amazon_account(
    account_id: int,
    name: str = Form(...),
    api_base_url: str = Form("https://na.business-api.amazon.com"),
    tracking_api_base_url: str = Form("https://na.business-api.amazon.com"),
    lwa_token_url: str = Form("https://api.amazon.com/auth/o2/token"),
    lwa_client_id: str = Form(""),
    lwa_client_secret: str = Form(""),
    lwa_refresh_token: str = Form(""),
    api_access_token: str = Form(""),
    cxml_auth_mode: str = Form("header"),
    cxml_cart_session_id: str = Form(""),
    cxml_credential_domain: str = Form("NetworkId"),
    cxml_to_identity: str = Form("Amazon"),
    is_default: str = Form("false"),
) -> RedirectResponse:
    make_default = is_default == "true"
    with db() as conn:
        if make_default:
            conn.execute("UPDATE amazon_accounts SET is_default = 0")
        conn.execute(
            """
            UPDATE amazon_accounts
            SET name=?, api_base_url=?, tracking_api_base_url=?, lwa_token_url=?, lwa_client_id=?,
                lwa_client_secret=?, lwa_refresh_token=?, api_access_token=?, cxml_auth_mode=?,
                cxml_cart_session_id=?, cxml_credential_domain=?, cxml_to_identity=?, dry_run=?, is_default=?, updated_at=?
            WHERE id=?
            """,
            (
                name,
                normalize_amazon_endpoint(api_base_url),
                normalize_amazon_endpoint(tracking_api_base_url),
                lwa_token_url,
                lwa_client_id,
                lwa_client_secret,
                lwa_refresh_token,
                api_access_token,
                normalize_cxml_auth_mode(cxml_auth_mode),
                clean_text(cxml_cart_session_id),
                clean_text(cxml_credential_domain, "NetworkId") or "NetworkId",
                clean_text(cxml_to_identity, "Amazon") or "Amazon",
                0,
                1 if make_default else 0,
                utc_now(),
                account_id,
            ),
        )
    return RedirectResponse("/amazon-accounts", status_code=303)


@app.post("/amazon-accounts/{account_id}/test")
def test_amazon_account(account_id: int) -> RedirectResponse:
    try:
        account = get_amazon_account(account_id)
        token = AmazonBusinessClient(account)._token()
        message = f"OK: {account['name']} generated an LWA access token ({len(token)} chars)."
        status = "ok"
    except Exception as exc:
        message = f"Failed: {exc}"
        status = "error"
    from urllib.parse import quote_plus

    return RedirectResponse(
        f"/amazon-accounts?test_status={quote_plus(status)}&test_result={quote_plus(message)}",
        status_code=303,
    )


@app.post("/amazon-accounts/{account_id}/default")
def make_default_amazon_account(account_id: int) -> RedirectResponse:
    with db() as conn:
        conn.execute("UPDATE amazon_accounts SET is_default = 0")
        conn.execute("UPDATE amazon_accounts SET is_default = 1, updated_at = ? WHERE id = ?", (utc_now(), account_id))
    return RedirectResponse("/amazon-accounts", status_code=303)


@app.post("/amazon-accounts/{account_id}/delete")
def delete_amazon_account(account_id: int) -> RedirectResponse:
    with db() as conn:
        row = conn.execute("SELECT is_default FROM amazon_accounts WHERE id = ?", (account_id,)).fetchone()
        conn.execute("DELETE FROM amazon_accounts WHERE id = ?", (account_id,))
        if row and int(row["is_default"]):
            replacement = conn.execute("SELECT id FROM amazon_accounts ORDER BY id LIMIT 1").fetchone()
            if replacement:
                conn.execute("UPDATE amazon_accounts SET is_default = 1 WHERE id = ?", (replacement["id"],))
    return RedirectResponse("/amazon-accounts", status_code=303)


@app.post("/addresses")
def save_address(
    label: str = Form(...),
    company_name: str = Form("Nutricity"),
    phone_number: str = Form(""),
    address_line1: str = Form(...),
    address_line2: str = Form(""),
    address_line3: str = Form(""),
    city: str = Form(...),
    state_or_region: str = Form(""),
    postal_code: str = Form(...),
    country_code: str = Form("US"),
    is_default: str = Form("false"),
) -> RedirectResponse:
    make_default = is_default == "true"
    with db() as conn:
        if make_default:
            conn.execute("UPDATE fulfilment_addresses SET is_default = 0")
        conn.execute(
            """
            INSERT INTO fulfilment_addresses
            (label, company_name, phone_number, address_line1, address_line2, address_line3,
             city, state_or_region, postal_code, country_code, is_default, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label,
                company_name,
                phone_number,
                address_line1,
                address_line2,
                address_line3,
                city,
                state_or_region,
                postal_code,
                country_code.strip().upper(),
                1 if make_default else 0,
                utc_now(),
                utc_now(),
            ),
        )
    return RedirectResponse("/addresses", status_code=303)


@app.post("/addresses/{address_id}/default")
def make_default_address(address_id: int) -> RedirectResponse:
    with db() as conn:
        conn.execute("UPDATE fulfilment_addresses SET is_default = 0")
        conn.execute("UPDATE fulfilment_addresses SET is_default = 1, updated_at = ? WHERE id = ?", (utc_now(), address_id))
    return RedirectResponse("/addresses", status_code=303)


@app.post("/addresses/{address_id}/delete")
def delete_address(address_id: int) -> RedirectResponse:
    with db() as conn:
        row = conn.execute("SELECT is_default FROM fulfilment_addresses WHERE id = ?", (address_id,)).fetchone()
        conn.execute("DELETE FROM fulfilment_addresses WHERE id = ?", (address_id,))
        if row and int(row["is_default"]):
            replacement = conn.execute("SELECT id FROM fulfilment_addresses ORDER BY id LIMIT 1").fetchone()
            if replacement:
                conn.execute("UPDATE fulfilment_addresses SET is_default = 1 WHERE id = ?", (replacement["id"],))
    return RedirectResponse("/addresses", status_code=303)


@app.post("/addresses/{address_id}/update")
def update_address(
    address_id: int,
    label: str = Form(...),
    company_name: str = Form("Nutricity"),
    phone_number: str = Form(""),
    address_line1: str = Form(...),
    address_line2: str = Form(""),
    address_line3: str = Form(""),
    city: str = Form(...),
    state_or_region: str = Form(""),
    postal_code: str = Form(...),
    country_code: str = Form("US"),
    is_default: str = Form("false"),
) -> RedirectResponse:
    make_default = is_default == "true"
    with db() as conn:
        if make_default:
            conn.execute("UPDATE fulfilment_addresses SET is_default = 0")
        conn.execute(
            """
            UPDATE fulfilment_addresses
            SET label=?, company_name=?, phone_number=?, address_line1=?, address_line2=?, address_line3=?,
                city=?, state_or_region=?, postal_code=?, country_code=?, is_default=?, updated_at=?
            WHERE id=?
            """,
            (
                label,
                company_name,
                phone_number,
                address_line1,
                address_line2,
                address_line3,
                city,
                state_or_region,
                postal_code,
                country_code.strip().upper(),
                1 if make_default else 0,
                utc_now(),
                address_id,
            ),
        )
    return RedirectResponse("/addresses", status_code=303)


@app.post("/stores")
def save_store(
    name: str = Form(...),
    odoo_url: str = Form(...),
    odoo_db: str = Form(...),
    odoo_user: str = Form(...),
    odoo_password: str = Form(...),
    website_id: str = Form(""),
) -> RedirectResponse:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO stores (name, odoo_url, odoo_db, odoo_user, odoo_password, website_id, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (name, odoo_url.rstrip("/"), odoo_db, odoo_user, odoo_password, int(website_id) if website_id.strip() else None, utc_now(), utc_now()),
        )
    return RedirectResponse("/stores", status_code=303)


@app.post("/stores/{store_id}/delete")
def delete_store(store_id: int) -> RedirectResponse:
    with db() as conn:
        conn.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    return RedirectResponse("/stores", status_code=303)


@app.post("/stores/{store_id}/update")
def update_store(
    store_id: int,
    name: str = Form(...),
    odoo_url: str = Form(...),
    odoo_db: str = Form(...),
    odoo_user: str = Form(...),
    odoo_password: str = Form(...),
    website_id: str = Form(""),
) -> RedirectResponse:
    with db() as conn:
        conn.execute(
            """
            UPDATE stores
            SET name=?, odoo_url=?, odoo_db=?, odoo_user=?, odoo_password=?, website_id=?, updated_at=?
            WHERE id=?
            """,
            (
                name,
                odoo_url.rstrip("/"),
                odoo_db,
                odoo_user,
                odoo_password,
                int(website_id) if website_id.strip() else None,
                utc_now(),
                store_id,
            ),
        )
    return RedirectResponse("/stores", status_code=303)


@app.post("/stores/{store_id}/test")
def test_store(store_id: int) -> RedirectResponse:
    try:
        store = get_store(store_id)
        odoo = OdooClient(store)
        version = odoo.common.version()
        message = f"OK: {store.name} connected to Odoo {version.get('server_version', 'unknown')}."
        status = "ok"
    except Exception as exc:
        message = f"Failed: {exc}"
        status = "error"
    from urllib.parse import quote_plus

    return RedirectResponse(
        f"/stores?test_status={quote_plus(status)}&test_result={quote_plus(message)}",
        status_code=303,
    )


@app.post("/pull")
def pull_orders(store_id: int = Form(...), days: int = Form(7), limit: int = Form(50)) -> RedirectResponse:
    fetch_odoo_lines(get_store(store_id), days, limit)
    return RedirectResponse(f"/?store_id={store_id}", status_code=303)


@app.post("/place")
def place(
    store_id: int = Form(...),
    address_id: Optional[int] = Form(None),
    amazon_account_id: Optional[int] = Form(None),
) -> RedirectResponse:
    place_orders(
        store_id,
        address_id=address_id,
        amazon_account_id=amazon_account_id,
    )
    return RedirectResponse(f"/?store_id={store_id}", status_code=303)


@app.post("/lines/place-selected")
def place_selected_lines(
    store_id: int = Form(...),
    action: str = Form("place"),
    address_id: Optional[int] = Form(None),
    amazon_account_id: Optional[int] = Form(None),
    line_ids: list[int] = Form(default=[]),
) -> RedirectResponse:
    if line_ids:
        club = action == "club_place"
        place_orders(
            store_id,
            address_id=address_id,
            amazon_account_id=amazon_account_id,
            line_ids=line_ids,
            club=club,
        )
    return RedirectResponse(f"/?store_id={store_id}", status_code=303)


@app.post("/delivery-check")
def delivery_check(store_id: int = Form(...)) -> RedirectResponse:
    check_deliveries(store_id)
    return RedirectResponse(f"/?store_id={store_id}", status_code=303)


@app.post("/lines/delete")
@app.post("/lines/delete/")
def delete_lines(store_id: int = Form(...), line_ids: list[int] = Form(default=[])) -> RedirectResponse:
    if line_ids:
        placeholders = ",".join("?" for _ in line_ids)
        with db() as conn:
            conn.execute(
                f"DELETE FROM order_lines WHERE store_id = ? AND id IN ({placeholders})",
                [store_id, *line_ids],
            )
    return RedirectResponse(f"/?store_id={store_id}", status_code=303)


@app.get("/reports/latest")
def latest_report(store_id: int) -> StreamingResponse:
    filename, data, _storage_url = write_report(store_id)
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/health")
def health() -> dict[str, Any]:
    with db() as conn:
        conn.execute("SELECT 1")
    return {"ok": True, "db": "postgres", "storage": "cloudflare-r2"}
