from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

def load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_local_env()

DEFAULT_SERVICE_SETTINGS = {
    "typesense_url": os.getenv("TYPESENSE_URL", ""),
    "typesense_api_key": os.getenv("TYPESENSE_API_KEY", ""),
    "typesense_enabled": os.getenv("TYPESENSE_ENABLED", "false"),
    "postgres_url": os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL", ""),
    "backup_interval_minutes": os.getenv("BACKUP_INTERVAL_MINUTES", "0"),
    "autosync_interval_minutes": os.getenv("AUTOSYNC_INTERVAL_MINUTES", "0"),
    "auto_chrome_fulfil_interval_minutes": os.getenv("AUTO_CHROME_FULFIL_INTERVAL_MINUTES", "0"),
    "auto_chrome_fulfil_days": os.getenv("AUTO_CHROME_FULFIL_DAYS", "2"),
    "auto_chrome_fulfil_limit": os.getenv("AUTO_CHROME_FULFIL_LIMIT", "100"),
    "cancelled_orders_sync_interval_minutes": os.getenv("CANCELLED_ORDERS_SYNC_INTERVAL_MINUTES", "0"),
    "cancelled_orders_sync_days": os.getenv("CANCELLED_ORDERS_SYNC_DAYS", "30"),
    "openexchange_api_key": os.getenv("OPENEXCHANGE_API_KEY", ""),
    "openexchange_sync_interval_minutes": os.getenv("OPENEXCHANGE_SYNC_INTERVAL_MINUTES", "2880"),
    "storage_s3_endpoint": os.getenv("STORAGE_S3_ENDPOINT", ""),
    "storage_s3_bucket": os.getenv("STORAGE_S3_BUCKET", ""),
    "storage_s3_region": os.getenv("STORAGE_S3_REGION", "auto"),
    "storage_s3_access_key_id": os.getenv("STORAGE_S3_ACCESS_KEY_ID", ""),
    "storage_s3_secret_access_key": os.getenv("STORAGE_S3_SECRET_ACCESS_KEY", ""),
    "backup_s3_endpoint": os.getenv("BACKUP_S3_ENDPOINT", os.getenv("STORAGE_S3_ENDPOINT", "")),
    "backup_s3_bucket": os.getenv("BACKUP_S3_BUCKET", ""),
    "amazon_otp_imap_host": os.getenv("AMAZON_OTP_IMAP_HOST", ""),
    "amazon_otp_imap_port": os.getenv("AMAZON_OTP_IMAP_PORT", "993"),
    "amazon_otp_imap_ssl": os.getenv("AMAZON_OTP_IMAP_SSL", "true"),
    "amazon_otp_imap_username": os.getenv("AMAZON_OTP_IMAP_USERNAME", ""),
    "amazon_otp_imap_password": os.getenv("AMAZON_OTP_IMAP_PASSWORD", ""),
    "amazon_otp_imap_folder": os.getenv("AMAZON_OTP_IMAP_FOLDER", "INBOX"),
    "amazon_otp_imap_interval_minutes": os.getenv("AMAZON_OTP_IMAP_INTERVAL_MINUTES", "5"),
    "amazon_otp_imap_since_days": os.getenv("AMAZON_OTP_IMAP_SINCE_DAYS", "14"),
    "amazon_otp_delete_processed_emails": "true",
    "shopify_dtc_script_path": os.getenv("SHOPIFY_DTC_SCRIPT_PATH", str(BASE_DIR / "app" / "services" / "shopify_scripts" / "dtc_orders_export.py")),
    "shopify_dtb_script_path": os.getenv("SHOPIFY_DTB_SCRIPT_PATH", str(BASE_DIR / "app" / "services" / "shopify_scripts" / "dtb_orders_export.py")),
    "shopify_tracking_script_path": os.getenv("SHOPIFY_TRACKING_SCRIPT_PATH", str(BASE_DIR / "app" / "services" / "shopify_scripts" / "tracking_sync.py")),
    "shopify_dtc_dest_name": os.getenv("SHOPIFY_DTC_DEST_NAME", "gofinch1-usa"),
    "shopify_dtc_shop": os.getenv("SHOPIFY_DTC_SHOP", "gofinch1-usa.myshopify.com"),
    "shopify_dtc_client_id": os.getenv("SHOPIFY_DTC_CLIENT_ID", "195db1cdb832e35000d5dd2080b8fee3"),
    "shopify_dtc_client_secret": "",
    "shopify_dtc_scopes": os.getenv("SHOPIFY_DTC_SCOPES", "read_orders,write_orders,read_customers,write_customers,read_products,write_products"),
    "shopify_dtc_redirect_uri": os.getenv("SHOPIFY_DTC_REDIRECT_URI", "http://localhost:8080/callback"),
    "shopify_dtc_api_version": os.getenv("SHOPIFY_DTC_API_VERSION", "2025-10"),
    "shopify_dtc_force_reauth": os.getenv("SHOPIFY_DTC_FORCE_REAUTH", "false"),
    "shopify_dtb_dest_name": os.getenv("SHOPIFY_DTB_DEST_NAME", "gofinch-newaud"),
    "shopify_dtb_shop": os.getenv("SHOPIFY_DTB_SHOP", "gofinch-newaud.myshopify.com"),
    "shopify_dtb_client_id": os.getenv("SHOPIFY_DTB_CLIENT_ID", "3b80d3adb0700f351ca60e6fe88d8a12"),
    "shopify_dtb_client_secret": "",
    "shopify_dtb_scopes": os.getenv("SHOPIFY_DTB_SCOPES", "read_orders,write_orders,read_customers,write_customers,read_products,write_products"),
    "shopify_dtb_redirect_uri": os.getenv("SHOPIFY_DTB_REDIRECT_URI", "http://localhost:8080/callback"),
    "shopify_dtb_api_version": os.getenv("SHOPIFY_DTB_API_VERSION", "2025-10"),
    "shopify_dtb_force_reauth": os.getenv("SHOPIFY_DTB_FORCE_REAUTH", "false"),
    "shopify_tracking_client_secret": "",
    "odoo_script_password": "",
    "shopify_oauth_public_base_url": os.getenv("SHOPIFY_OAUTH_PUBLIC_BASE_URL", ""),
    "shopify_auto_enqueue_enabled": os.getenv("SHOPIFY_AUTO_ENQUEUE_ENABLED", "true"),
    "shopify_product_rename_enabled": os.getenv("SHOPIFY_PRODUCT_RENAME_ENABLED", "true"),
    "shopify_generic_product_name": os.getenv("SHOPIFY_GENERIC_PRODUCT_NAME", "Generic Product"),
    "shopify_job_max_attempts": os.getenv("SHOPIFY_JOB_MAX_ATTEMPTS", "5"),
    "shopify_tracking_from_days": os.getenv("SHOPIFY_TRACKING_FROM_DAYS", "7"),
    "shopify_tracking_validate_deliveries": os.getenv("SHOPIFY_TRACKING_VALIDATE_DELIVERIES", "true"),
    "shopify_tracking_skip_done_pickings": os.getenv("SHOPIFY_TRACKING_SKIP_DONE_PICKINGS", "false"),
    "pull_orders_batch_size": os.getenv("PULL_ORDERS_BATCH_SIZE", "50"),
    "email_alerts_enabled": os.getenv("EMAIL_ALERTS_ENABLED", "false"),
    "email_alert_to": os.getenv("EMAIL_ALERT_TO", ""),
    "email_driver": os.getenv("EMAIL_DRIVER", "smtp"),
    "email_from_address": os.getenv("EMAIL_FROM_ADDRESS", "projects@gofinch.com"),
    "email_from_name": os.getenv("EMAIL_FROM_NAME", "GofinchCRM"),
    "email_smtp_host": os.getenv("EMAIL_SMTP_HOST", "smtp.resend.com"),
    "email_smtp_password": os.getenv("EMAIL_SMTP_PASSWORD", ""),
    "email_smtp_port": os.getenv("EMAIL_SMTP_PORT", "465"),
    "email_smtp_secure": os.getenv("EMAIL_SMTP_SECURE", "true"),
    "email_smtp_user": os.getenv("EMAIL_SMTP_USER", "resend"),
    "email_system_address": os.getenv("EMAIL_SYSTEM_ADDRESS", "projects@gofinch.com"),
}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
