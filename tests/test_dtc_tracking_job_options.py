import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import main


class TrackingJobOptionTests(unittest.TestCase):
    def test_explicit_no_validation_reaches_script_without_changing_default(self):
        for enabled in (0, 1, None):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                job = {'status': 'queued', 'attempts': 0, 'report_csv': '', 'dry_run': 0,
                       'skip_done_pickings': 0, 'validate_deliveries': enabled,
                       'from_date': '2026-10-20', 'to_date': '2026-10-21T16:00:00Z'}
                conn = Mock()
                conn.execute.return_value.rowcount = 1
                conn.execute.return_value.fetchone.side_effect = [job, {'progress_json': '{}'}]
                @contextlib.contextmanager
                def database():
                    yield conn
                argv = []
                module = SimpleNamespace(main=lambda: argv.extend(sys.argv))
                with patch.object(main, 'db', database), \
                        patch.object(main, 'BASE_DIR', Path(tmp)), \
                        patch.object(main, 'get_service_settings', return_value={'shopify_tracking_script_path': 'tracking.py'}), \
                        patch.object(main, 'start_shopify_tracking_job_heartbeat', return_value=Mock()), \
                        patch.object(main, 'shopify_tracking_worker_count', return_value=1), \
                        patch.object(main, 'load_external_script', return_value=module), \
                        patch.object(main, 'apply_shopify_runtime_settings'), \
                        patch.object(main, 'shopify_tracking_job_cancel_requested', return_value=False), \
                        patch.object(main, 'set_setting'), \
                        patch.object(main, 'send_email_alert_async') as alerts:
                    main.run_shopify_tracking_job('test')
                self.assertTrue(argv)
                self.assertEqual('--no-validate-deliveries' in argv, enabled == 0)
                alerts.assert_not_called()
