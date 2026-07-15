from __future__ import annotations

import hashlib
import os
import pickle
import time
from typing import Any


_CLIENT: Any = None
_DISABLED_UNTIL = 0.0
_GLOBAL_VERSION_KEY = "nutricity:cache:version:global"
_PREFIX_VERSION_KEY = "nutricity:cache:version:prefix:"
_PAGE_CACHE_PREFIX = "nutricity:page-cache:"
_STALE_CACHE_PREFIX = "nutricity:page-cache-stale:"
_STATE_PREFIX = "nutricity:state:"
_VERSION_CACHE: dict[str, tuple[str, float]] = {}
_VERSION_CACHE_TTL_SECONDS = float(os.getenv("REDIS_VERSION_CACHE_TTL", "5") or 5)
_SETTING_CACHE: dict[str, tuple[str, float]] = {}
_SETTING_CACHE_TTL_SECONDS = float(os.getenv("REDIS_SETTING_CACHE_TTL", "30") or 30)
_STALE_CACHE_TTL_SECONDS = int(float(os.getenv("REDIS_STALE_CACHE_TTL", "3600") or 3600))
_STALE_DISABLED_PREFIXES = {
    "settings-services",
    "dashboard",
    "orders",
    "orders-count",
    "orders-condition-count",
    "search",
    "chrome-jobs",
    "shopify-fulfilment",
    "fulfilment-pending",
    "fulfilment-pending-count",
    "tracking-orders",
}


def postgres_setting(name: str) -> str:
    now = time.monotonic()
    cached = _SETTING_CACHE.get(name)
    if cached and cached[1] > now:
        return cached[0]
    try:
        from app.db.session import db

        with db() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (name,)).fetchone()
        value = str(row["value"] or "") if row else ""
        _SETTING_CACHE[name] = (value, now + _SETTING_CACHE_TTL_SECONDS)
        return value
    except Exception:
        return ""


def redis_url() -> str:
    return os.getenv("REDIS_URL") or os.getenv("DRAMATIQ_REDIS_URL") or postgres_setting("redis_url") or "redis://127.0.0.1:6379/0"


def redis_enabled() -> bool:
    value = os.getenv("REDIS_ENABLED") or postgres_setting("redis_enabled") or "true"
    return value.strip().lower() not in {"0", "false", "no", "off"}


def dramatiq_enabled() -> bool:
    value = os.getenv("DRAMATIQ_ENABLED") or postgres_setting("dramatiq_enabled") or "true"
    return value.strip().lower() not in {"0", "false", "no", "off"}


def reset_client() -> None:
    global _CLIENT, _DISABLED_UNTIL
    _CLIENT = None
    _DISABLED_UNTIL = 0.0
    _VERSION_CACHE.clear()
    _SETTING_CACHE.clear()


def redis_client() -> Any:
    global _CLIENT, _DISABLED_UNTIL
    if not redis_enabled():
        return None
    now = time.monotonic()
    if _DISABLED_UNTIL > now:
        return None
    if _CLIENT is not None:
        return _CLIENT
    try:
        import redis

        connect_timeout = float(os.getenv("REDIS_CONNECT_TIMEOUT", "0.08") or 0.08)
        socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", "0.12") or 0.12)
        client = redis.Redis.from_url(
            redis_url(),
            socket_connect_timeout=connect_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=30,
        )
        client.ping()
        _CLIENT = client
        return client
    except Exception:
        _DISABLED_UNTIL = now + 30
        return None


def note_redis_failure(disable_seconds: float = 15) -> None:
    global _CLIENT, _DISABLED_UNTIL
    _CLIENT = None
    _DISABLED_UNTIL = time.monotonic() + max(1.0, float(disable_seconds or 15))


def _version(client: Any, key: str) -> str:
    now = time.monotonic()
    cached = _VERSION_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        value = client.get(key)
        if value is None:
            client.set(key, "1")
            _VERSION_CACHE[key] = ("1", now + _VERSION_CACHE_TTL_SECONDS)
            return "1"
    except Exception:
        note_redis_failure()
        return "1"
    if isinstance(value, bytes):
        version = value.decode("utf-8", errors="ignore") or "1"
    else:
        version = str(value or "1")
    _VERSION_CACHE[key] = (version, now + _VERSION_CACHE_TTL_SECONDS)
    return version


def page_cache_key(key: tuple[Any, ...]) -> str:
    client = redis_client()
    if client is None:
        return ""
    try:
        prefix = str(key[0] if key else "default")
        global_version = _version(client, _GLOBAL_VERSION_KEY)
        prefix_version = _version(client, f"{_PREFIX_VERSION_KEY}{prefix}")
        digest = hashlib.sha256(pickle.dumps(key, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()
        return f"{_PAGE_CACHE_PREFIX}{global_version}:{prefix}:{prefix_version}:{digest}"
    except Exception:
        note_redis_failure()
        return ""


def stale_page_cache_key(key: tuple[Any, ...]) -> str:
    if not key:
        return ""
    prefix = str(key[0] if key else "default")
    digest = hashlib.sha256(pickle.dumps(key, protocol=pickle.HIGHEST_PROTOCOL)).hexdigest()
    return f"{_STALE_CACHE_PREFIX}{prefix}:{digest}"


def stale_cache_enabled(key: tuple[Any, ...]) -> bool:
    return bool(key) and str(key[0]) not in _STALE_DISABLED_PREFIXES


def page_cache_get(key: tuple[Any, ...]) -> Any:
    client = redis_client()
    if client is None:
        return None
    cache_key = page_cache_key(key)
    if not cache_key:
        return None
    try:
        raw = client.get(cache_key)
        return pickle.loads(raw) if raw else None
    except Exception:
        note_redis_failure()
        return None


def page_cache_get_stale(key: tuple[Any, ...]) -> Any:
    client = redis_client()
    if client is None or not key or str(key[0]) in _STALE_DISABLED_PREFIXES:
        return None
    try:
        raw = client.get(stale_page_cache_key(key))
        return pickle.loads(raw) if raw else None
    except Exception:
        note_redis_failure()
        return None


def page_cache_set(key: tuple[Any, ...], value: Any, ttl_seconds: int) -> bool:
    client = redis_client()
    if client is None:
        return False
    try:
        cache_key = page_cache_key(key)
        if not cache_key:
            return False
        payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        client.setex(cache_key, max(1, int(ttl_seconds or 1)), payload)
        if key and str(key[0]) not in _STALE_DISABLED_PREFIXES:
            client.setex(stale_page_cache_key(key), max(1, _STALE_CACHE_TTL_SECONDS), payload)
        return True
    except Exception:
        note_redis_failure()
        return False


def page_cache_clear() -> None:
    client = redis_client()
    if client is None:
        return
    try:
        version = str(client.incr(_GLOBAL_VERSION_KEY))
        _VERSION_CACHE[_GLOBAL_VERSION_KEY] = (version, time.monotonic() + _VERSION_CACHE_TTL_SECONDS)
        for key in client.scan_iter(f"{_STALE_CACHE_PREFIX}*"):
            client.delete(key)
    except Exception:
        note_redis_failure()
        return


def page_cache_clear_matching(prefixes: set[str]) -> None:
    client = redis_client()
    if client is None:
        return
    try:
        pipe = client.pipeline()
        keys: list[str] = []
        version_result_indexes: list[int] = []
        command_index = 0
        for prefix in prefixes:
            if prefix:
                key = f"{_PREFIX_VERSION_KEY}{prefix}"
                keys.append(key)
                version_result_indexes.append(command_index)
                pipe.incr(key)
                command_index += 1
                for stale_key in client.scan_iter(f"{_STALE_CACHE_PREFIX}{prefix}:*"):
                    pipe.delete(stale_key)
                    command_index += 1
        versions = pipe.execute()
        now = time.monotonic()
        for key, result_index in zip(keys, version_result_indexes):
            version = versions[result_index] if result_index < len(versions) else "1"
            _VERSION_CACHE[key] = (str(version or "1"), now + _VERSION_CACHE_TTL_SECONDS)
    except Exception:
        note_redis_failure()
        return


def state_get(name: str) -> Any:
    client = redis_client()
    if client is None or not name:
        return None
    try:
        raw = client.get(f"{_STATE_PREFIX}{name}")
        return pickle.loads(raw) if raw else None
    except Exception:
        note_redis_failure()
        return None


def state_set(name: str, value: Any, ttl_seconds: int = 24 * 60 * 60) -> bool:
    client = redis_client()
    if client is None or not name:
        return False
    try:
        client.setex(f"{_STATE_PREFIX}{name}", max(1, int(ttl_seconds or 1)), pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL))
        return True
    except Exception:
        note_redis_failure()
        return False
