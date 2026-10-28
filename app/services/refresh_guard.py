"""Keep overlapping materialization requests from consuming the connection pool."""
from contextlib import contextmanager
import threading


_after_order_lock = threading.Lock()
AFTER_ORDER_REFRESH_LOCK_ID = 771905433


@contextmanager
def after_order_refresh_connection(db):
    # Avoid even checking out a connection when this process already refreshes.
    if not _after_order_lock.acquire(blocking=False):
        yield None
        return
    try:
        with db() as conn:
            # The transaction lock also covers other web/worker processes. A
            # competing reader can use the last committed cases immediately.
            row = conn.execute(
                "SELECT pg_try_advisory_xact_lock(?) AS locked",
                (AFTER_ORDER_REFRESH_LOCK_ID,),
            ).fetchone()
            yield conn if row and row["locked"] else None
    finally:
        _after_order_lock.release()
