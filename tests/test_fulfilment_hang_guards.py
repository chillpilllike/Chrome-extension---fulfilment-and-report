import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock, patch

from app.services.refresh_guard import after_order_refresh_connection

spec = importlib.util.spec_from_file_location('watchdog', Path(__file__).parents[1] / 'scripts/fulfilment-health-watchdog.py')
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class RefreshGuardTests(unittest.TestCase):
    def connection(self, locked=True):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {'locked': locked}
        @contextmanager
        def db():
            yield conn
        return conn, Mock(side_effect=db)

    def test_overlapping_refresh_does_not_checkout_another_connection(self):
        conn, db = self.connection()
        with after_order_refresh_connection(db) as first:
            self.assertIs(first, conn)
            with after_order_refresh_connection(db) as second:
                self.assertIsNone(second)
            self.assertEqual(db.call_count, 1)
        with after_order_refresh_connection(db) as next_refresh:
            self.assertIs(next_refresh, conn)

    def test_other_process_holds_transaction_lock(self):
        _, db = self.connection(locked=False)
        with after_order_refresh_connection(db) as conn:
            self.assertIsNone(conn)

    def test_failed_checkout_or_refresh_releases_local_lock(self):
        for error_in_checkout in (True, False):
            conn, db = self.connection()
            if error_in_checkout:
                db.side_effect = RuntimeError('pool unavailable')
            with self.assertRaises(RuntimeError):
                with after_order_refresh_connection(db):
                    raise RuntimeError('query failed')
            conn, db = self.connection()
            with after_order_refresh_connection(db) as recovered:
                self.assertIs(recovered, conn)


class WatchdogTests(unittest.TestCase):
    def test_public_outage_is_detected_even_when_docker_reports_healthy(self):
        calls = []
        def docker(*args):
            calls.append(args)
            if args[0] == 'ps':
                return 'gntjqbrkd1l1t131309fu82q-live'
            if args[0] == 'inspect':
                return json.dumps([{'Id':'a','State':{'Running':True,'Health':{'Status':'healthy'}}}])
            return ''
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / 'state.json'
            with patch.object(watchdog, 'docker', side_effect=docker), \
                 patch.object(watchdog, 'public_health_ok', return_value=False), \
                 patch.dict(watchdog.os.environ, {'FULFILMENT_WATCHDOG_STATE':str(state_path)}), \
                 patch.object(watchdog.sys, 'argv', ['watchdog']), \
                 patch.object(watchdog.time, 'time', side_effect=[1000,1180]):
                watchdog.main()
                self.assertFalse(any(call[0]=='restart' for call in calls))
                watchdog.main()
                self.assertEqual(sum(call[0]=='restart' for call in calls),1)
                self.assertEqual(json.loads(state_path.read_text())['last_restart'],1180)

    def test_transient_failure_does_not_restart(self):
        state, restart = watchdog.evaluate({}, 'a', 'unhealthy', 1000)
        self.assertFalse(restart)
        state, restart = watchdog.evaluate(state, 'a', 'unhealthy', 1179)
        self.assertFalse(restart)
        _, restart = watchdog.evaluate(state, 'a', 'unhealthy', 1180)
        self.assertTrue(restart)

    def test_recovery_resets_failure_window(self):
        state, _ = watchdog.evaluate({}, 'a', 'unhealthy', 1000)
        state, restart = watchdog.evaluate(state, 'a', 'healthy', 1170)
        self.assertFalse(restart)
        self.assertNotIn('unhealthy_since', state)
        _, restart = watchdog.evaluate(state, 'a', 'unhealthy', 1200)
        self.assertFalse(restart)

    def test_restarts_are_rate_limited_across_container_replacement(self):
        state = {'container_id': 'a', 'last_restart': 1000, 'unhealthy_since': 1000}
        state, restart = watchdog.evaluate(state, 'b', 'unhealthy', 1200)
        self.assertFalse(restart)
        _, restart = watchdog.evaluate(state, 'b', 'unhealthy', 1500)
        self.assertFalse(restart)
        _, restart = watchdog.evaluate(state, 'b', 'unhealthy', 1900)
        self.assertTrue(restart)

    def test_starting_or_missing_health_never_restarts(self):
        for health in ('starting', None, 'healthy'):
            state = {'container_id': 'a', 'unhealthy_since': 1000}
            _, restart = watchdog.evaluate(state, 'a', health, 2000)
            self.assertFalse(restart)


if __name__ == '__main__':
    unittest.main()
