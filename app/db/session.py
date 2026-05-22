from __future__ import annotations

import os
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable, Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor, execute_values

from app.core.config import DEFAULT_SERVICE_SETTINGS


POSTGRES_URL = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL") or DEFAULT_SERVICE_SETTINGS["postgres_url"]

_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()


def _translate_placeholders(sql: str) -> str:
    out: list[str] = []
    in_single = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'":
            out.append(char)
            if index + 1 < len(sql) and sql[index + 1] == "'":
                out.append(sql[index + 1])
                index += 2
                continue
            in_single = not in_single
        elif char == "?" and not in_single:
            out.append("%s")
        elif char == "%":
            out.append("%%")
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _translate_sql(sql: str) -> str:
    sql = sql.strip()
    sql = re.sub(r"\bINTEGER PRIMARY KEY AUTOINCREMENT\b", "SERIAL PRIMARY KEY", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bINTEGER PRIMARY KEY\b", "SERIAL PRIMARY KEY", sql, flags=re.IGNORECASE)
    sql = sql.replace("datetime(COALESCE(pulled_at, created_at)) >= datetime('now', ?)", "(COALESCE(pulled_at, created_at))::timestamp >= NOW() + (?)::interval")
    sql = sql.replace("datetime(expires_at) <= datetime('now')", "expires_at::timestamp <= NOW()")
    return _translate_placeholders(sql)


def _table_from_insert(sql: str) -> Optional[str]:
    match = re.search(r"INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.IGNORECASE)
    return match.group(1) if match else None


class PostgresCursor:
    def __init__(self, cursor: RealDictCursor, connection: "PostgresConnection") -> None:
        self._cursor = cursor
        self._connection = connection
        self.lastrowid: Optional[int] = None

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> "PostgresCursor":
        translated = _translate_sql(sql)
        self._cursor.execute(translated, tuple(params or ()))
        self.lastrowid = None
        return self

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> "PostgresCursor":
        translated = _translate_sql(sql)
        self._cursor.executemany(translated, [tuple(params or ()) for params in seq_of_params])
        self.lastrowid = None
        return self

    def execute_values(
        self,
        sql: str,
        argslist: Iterable[Iterable[Any]],
        template: str | None = None,
        page_size: int = 1000,
    ) -> "PostgresCursor":
        translated = _translate_sql(sql)
        translated_template = _translate_placeholders(template) if template else None
        execute_values(self._cursor, translated, [tuple(params or ()) for params in argslist], template=translated_template, page_size=page_size)
        self.lastrowid = None
        return self

    def fetchone(self) -> Optional[dict[str, Any]]:
        row = self._cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._cursor.fetchall()]


class PostgresConnection:
    def __init__(self, raw: Any, pooled: bool = False) -> None:
        self._raw = raw
        self._pooled = pooled
        self._raw.autocommit = False

    @classmethod
    def open(cls) -> "PostgresConnection":
        if not POSTGRES_URL:
            raise RuntimeError("POSTGRES_URL or DATABASE_URL is required. SQLite is intentionally disabled.")
        connect_timeout = int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "8"))
        statement_timeout_ms = int(os.getenv("POSTGRES_STATEMENT_TIMEOUT_MS", "120000"))
        use_pool = os.getenv("POSTGRES_USE_POOL", "false").strip().lower() in {"1", "true", "yes", "on"}
        if not use_pool:
            raw = psycopg2.connect(
                POSTGRES_URL,
                connect_timeout=connect_timeout,
                options=f"-c statement_timeout={statement_timeout_ms} -c idle_in_transaction_session_timeout={statement_timeout_ms}",
            )
            return cls(raw, pooled=False)
        global _pool
        if _pool is None:
            with _pool_lock:
                if _pool is None:
                    max_connections = int(os.getenv("POSTGRES_POOL_MAX", "30"))
                    _pool = pool.ThreadedConnectionPool(
                        1,
                        max_connections,
                        POSTGRES_URL,
                        connect_timeout=connect_timeout,
                        options=f"-c statement_timeout={statement_timeout_ms} -c idle_in_transaction_session_timeout={statement_timeout_ms}",
                    )
        last_error: Exception | None = None
        for attempt in range(int(os.getenv("POSTGRES_POOL_RETRIES", "5"))):
            try:
                return cls(_pool.getconn(), pooled=True)
            except pool.PoolError as error:
                last_error = error
                time.sleep(0.2 * (attempt + 1))
        raise last_error or RuntimeError("Could not get a PostgreSQL connection from the pool.")

    def cursor(self) -> PostgresCursor:
        return PostgresCursor(self._raw.cursor(cursor_factory=RealDictCursor), self)

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> PostgresCursor:
        if sql.strip().upper().startswith("PRAGMA TABLE_INFO"):
            match = re.search(r"PRAGMA\s+table_info\(([^)]+)\)", sql, re.IGNORECASE)
            table = match.group(1).strip("\"' ") if match else ""
            return self.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = ?
                ORDER BY ordinal_position
                """,
                (table,),
            )
        cursor = self.cursor()
        return cursor.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> PostgresCursor:
        cursor = self.cursor()
        return cursor.executemany(sql, seq_of_params)

    def execute_values(
        self,
        sql: str,
        argslist: Iterable[Iterable[Any]],
        template: str | None = None,
        page_size: int = 1000,
    ) -> PostgresCursor:
        cursor = self.cursor()
        return cursor.execute_values(sql, argslist, template=template, page_size=page_size)

    def executescript(self, script: str) -> None:
        statements = [statement.strip() for statement in script.split(";") if statement.strip()]
        for statement in statements:
            self.execute(statement)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        global _pool
        if self._pooled and _pool is not None:
            _pool.putconn(self._raw)
        else:
            self._raw.close()


@contextmanager
def db() -> Iterable[PostgresConnection]:
    conn = PostgresConnection.open()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_pool() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None
