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
}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
