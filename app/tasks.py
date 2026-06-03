from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import load_local_env
from app import redis_support


load_local_env()


def configure_broker() -> None:
    broker = RedisBroker(url=redis_support.redis_url())
    dramatiq.set_broker(broker)


configure_broker()


@dramatiq.actor(max_retries=3, min_backoff=5_000, max_backoff=60_000, time_limit=15 * 60 * 1000)
def sync_dispatch_order_task(amazon_order_id: str) -> None:
    from app.db.session import db
    from app.main import api_dispatch_sorting_summary, fast_page_cache_clear_matching, sync_dispatch_packages_for_order

    with db() as conn:
        sync_dispatch_packages_for_order(conn, amazon_order_id)
    fast_page_cache_clear_matching({
        "dispatch-sorting-summary",
        "dispatch-sorting-summary-base",
        "dispatch-status",
        "dispatch-status-summary",
    })
    api_dispatch_sorting_summary(store_id=None, scan_page=1, scan_per_page=10, scan_q="")


@dramatiq.actor(max_retries=1, min_backoff=10_000, max_backoff=60_000, time_limit=60 * 60 * 1000)
def rebuild_dispatch_scan_index_task(store_id: Optional[int] = None) -> None:
    from app.main import api_dispatch_sorting_summary, run_dispatch_rebuild_job

    run_dispatch_rebuild_job(store_id)
    api_dispatch_sorting_summary(store_id=store_id, scan_page=1, scan_per_page=10, scan_q="")


@dramatiq.actor(max_retries=2, min_backoff=10_000, max_backoff=120_000, time_limit=10 * 60 * 1000)
def cleanup_stale_payment_failures_task(amazon_order_id: str = "") -> None:
    from app.db.session import db
    from app.main import cleanup_stale_payment_failures

    with db() as conn:
        cleanup_stale_payment_failures(conn, amazon_order_id)


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=60_000, time_limit=10 * 60 * 1000)
def warm_dispatch_summary_task(store_id: Optional[int] = None) -> None:
    from app.main import api_dispatch_sorting_summary

    api_dispatch_sorting_summary(store_id=store_id, scan_page=1, scan_per_page=10, scan_q="")


@dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=60_000, time_limit=20 * 60 * 1000)
def warm_app_pages_task(store_id: Optional[int] = None) -> None:
    from app.main import (
        api_accounting,
        api_amazon_otp,
        api_back_in_stock,
        api_bulk,
        api_cancelled_orders,
        api_chrome_jobs,
        api_costly,
        api_dashboard,
        api_dispatch_sorting_summary,
        api_dispatch_status,
        api_dispatch_status_summary,
        api_duplicate_asins,
        api_epost_tracking,
        api_exports,
        api_inventory,
        api_list_backups,
        api_missing,
        api_order_countries,
        api_orders,
        api_partial_fulfilments,
        api_profit_loss,
        api_pull_jobs,
        api_service_settings,
        api_ui_copy,
        api_tracking_fulfilment_pending,
        api_tracking_orders,
        api_tracking_payment_failures,
        list_stores,
    )

    try:
        store_ids = [int(store["id"]) for store in list_stores()]
    except Exception:
        store_ids = []
    store_targets: list[Optional[int]] = [store_id]
    if store_id is None:
        store_targets.extend(store_ids)
    store_targets = list(dict.fromkeys(store_targets))
    warm_calls = [
        lambda: api_ui_copy(),
        lambda: api_service_settings(),
        lambda: api_pull_jobs(page=1, per_page=20),
        lambda: api_exports(page=1, per_page=20),
        lambda: api_accounting(page=1, per_page=20),
        lambda: api_profit_loss(store_id=None, page=1, per_page=20),
        lambda: api_costly(store_id=None, page=1, per_page=20),
        lambda: api_amazon_otp(page=1, per_page=20),
        lambda: api_list_backups(),
    ]
    for target_store_id in store_targets:
        warm_calls.extend([
            lambda target_store_id=target_store_id: api_dashboard(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_dashboard(store_id=target_store_id, page=1, per_page=100),
            lambda target_store_id=target_store_id: api_orders(store_id=target_store_id, page=1, per_page=100),
            lambda target_store_id=target_store_id: api_order_countries(store_id=target_store_id),
            lambda target_store_id=target_store_id: api_inventory(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_cancelled_orders(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_missing(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_back_in_stock(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_partial_fulfilments(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_bulk(store_id=target_store_id, page=1, per_page=20, days=2),
            lambda target_store_id=target_store_id: api_duplicate_asins(store_id=target_store_id, page=1, per_page=20, days=2),
            lambda target_store_id=target_store_id: api_tracking_fulfilment_pending(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_tracking_payment_failures(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_dispatch_status(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_dispatch_status_summary(store_id=target_store_id),
            lambda target_store_id=target_store_id: api_epost_tracking(store_id=target_store_id, page=1, per_page=20),
            lambda target_store_id=target_store_id: api_chrome_jobs(store_id=target_store_id, claim=False, job_limit=50),
        ])
        for warm_page in range(1, 11):
            warm_calls.extend([
                lambda target_store_id=target_store_id, warm_page=warm_page: api_orders(store_id=target_store_id, page=warm_page, per_page=20),
                lambda target_store_id=target_store_id, warm_page=warm_page: api_tracking_orders(store_id=target_store_id, page=warm_page, per_page=20),
                lambda target_store_id=target_store_id, warm_page=warm_page: api_dispatch_sorting_summary(store_id=target_store_id, scan_page=warm_page, scan_per_page=10, scan_q=""),
            ])
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(warm_call) for warm_call in warm_calls]
        for future in futures:
            try:
                future.result()
            except Exception:
                continue


def enqueue(actor: dramatiq.Actor, *args: object, **kwargs: object) -> bool:
    if not redis_support.dramatiq_enabled():
        return False
    try:
        actor.send(*args, **kwargs)
        return True
    except Exception:
        return False
