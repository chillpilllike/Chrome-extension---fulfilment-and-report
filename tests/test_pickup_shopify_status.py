import sqlite3
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app import main


class AnyTrue:
    def __init__(self): self.value = False
    def step(self, value): self.value = self.value or bool(value)
    def finalize(self): return self.value


class PickupShopifyStatusTests(unittest.TestCase):
    def connection(self):
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.create_aggregate('BOOL_OR', 1, AnyTrue)
        conn.executescript('''
        CREATE TABLE shopify_order_status_cache(store_id, odoo_order_name, fulfillment_status, fulfillment_at, cancelled_at, synced_at);
        CREATE TABLE order_lines(store_id, odoo_order_name, amazon_order_id, odoo_order_date, updated_at);
        CREATE TABLE amazon_dispatch_packages(store_id, odoo_order_name);
        CREATE TABLE package_pickup_manual_amazon(store_id, odoo_order_name);
        CREATE TABLE package_pickup_checks(id, store_id);
        CREATE TABLE package_pickup_non_amazon(check_id, order_number);
        ''')
        return conn

    def test_fulfilled_dtc_wins_over_newer_unfulfilled_or_cancelled_copy(self):
        conn = self.connection()
        conn.executemany('INSERT INTO shopify_order_status_cache VALUES (?,?,?,?,?,?)', [
            (1, 'NC15427', 'FULFILLED', '2026-09-08', '', '2026-09-08'),
            (1, 'NC15427', 'UNFULFILLED', '', '', '2026-09-12'),
            (1, 'NC15427', 'FULFILLED', '', '2026-09-12', '2026-09-13'),
            (2, 'NC15427', 'UNFULFILLED', '', '', '2026-09-12'),
        ])
        statuses = main.package_pickup_shopify_statuses(conn)
        self.assertEqual(statuses[1, 'NC15427']['fulfillment_status'], 'FULFILLED')
        self.assertEqual(statuses[1, 'NC15427']['cancelled_at'], '')
        self.assertEqual(statuses[2, 'NC15427']['fulfillment_status'], 'UNFULFILLED')

    def test_scheduled_reconciliation_includes_old_pickups_and_excludes_unrelated_orders(self):
        conn = self.connection()
        recent = datetime.now(timezone.utc).isoformat()
        names = ['OLD_PICKUP', 'OLD_MANUAL', 'OLD_NON_AMAZON', 'OLD_UNRELATED', 'RECENT', 'DONE', 'CANCELLED_COPY']
        for name in names:
            conn.execute('INSERT INTO order_lines VALUES (1,?,?,?,?)', (name, 'amazon' if name != 'OLD_NON_AMAZON' else '', recent if name == 'RECENT' else '2020-01-01', recent))
        conn.executemany('INSERT INTO amazon_dispatch_packages VALUES (1,?)', [(n,) for n in ['OLD_PICKUP', 'DONE', 'CANCELLED_COPY']])
        conn.execute("INSERT INTO amazon_dispatch_packages VALUES (2,'OLD_UNRELATED')")
        conn.execute("INSERT INTO package_pickup_manual_amazon VALUES (1,'OLD_MANUAL')")
        conn.execute('INSERT INTO package_pickup_checks VALUES (1,1)')
        conn.execute("INSERT INTO package_pickup_non_amazon VALUES (1,'OLD_NON_AMAZON')")
        conn.executemany('INSERT INTO shopify_order_status_cache VALUES (1,?,?,?,?,?)', [
            ('DONE','FULFILLED','2020-01-01','','2020-01-01'),
            ('CANCELLED_COPY','FULFILLED','2020-01-01','2020-01-01','2020-01-01'),
        ])
        @contextmanager
        def database(): yield conn
        with patch.object(main, 'db', database), patch.object(main, 'set_setting'), patch.object(main, 'fast_page_cache_clear_matching'), patch.object(main, 'sync_shopify_status_for_order_names') as sync:
            main.sync_package_tracker_shopify_statuses(force=True)
        actual = {name for call in sync.call_args_list for name in call.args[1]}
        self.assertEqual(actual, {'OLD_PICKUP','OLD_MANUAL','OLD_NON_AMAZON','RECENT','CANCELLED_COPY'})

    def test_pickup_refreshes_five_minute_old_pending_status_only(self):
        now = datetime.now(timezone.utc)
        def row(name, minutes, status='UNFULFILLED'):
            return dict(store_id=1, odoo_order_name=name, shopify_synced_at=(now-timedelta(minutes=minutes)).isoformat(), shopify_fulfillment_status=status, shopify_fulfillment_at='2026-09-08' if status=='FULFILLED' else '')
        with patch.object(main, 'sync_shopify_status_for_order_names') as sync, patch.object(main, 'fast_page_cache_clear_matching'):
            count = main.refresh_missing_shopify_status_for_rows([row('NC15427',6),row('FRESH',1),row('DONE',60,'FULFILLED')], wait=True, max_age_seconds=300)
        self.assertEqual(count,1)
        sync.assert_called_once_with(1,['NC15427'],force=True)
