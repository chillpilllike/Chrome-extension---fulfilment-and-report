import contextlib
import unittest
from unittest.mock import Mock, patch
from app import main
from app.services.tracking_options import boolean_option

class TrackingValidationDefaultsTests(unittest.TestCase):
    def test_blank_inherits_default_and_explicit_false_is_preserved(self):
        for empty in (None, '', '  '):
            self.assertTrue(boolean_option(empty, default=True))
            self.assertFalse(boolean_option(empty, default=False))
        for value in (False, 0, 'false', '0', 'off', 'no'):
            self.assertFalse(boolean_option(value, default=True))
        for value in (True, 1, 'true', '1', 'on', 'yes'):
            self.assertTrue(boolean_option(value, default=False))
        with self.assertRaises(ValueError):
            boolean_option('unexpected', default=True)

    def test_queued_jobs_restore_validation_but_backfill_can_opt_out(self):
        for saved, payload, expected in [
            ('', {}, 1), (None, {}, 1), ('true', {}, 1), ('false', {}, 0),
            ('', {'validate_deliveries':False}, 0),
            ('', {'validate_deliveries':'false'}, 0),
            ('false', {'validate_deliveries':True}, 1),
            ('', {'validate_deliveries':''}, 1),
        ]:
            with self.subTest(saved=saved,payload=payload):
                conn=Mock()
                @contextlib.contextmanager
                def database(): yield conn
                with patch.object(main,'db',database), \
                     patch.object(main,'get_service_settings',return_value={'shopify_tracking_validate_deliveries':saved}), \
                     patch.object(main,'get_setting',return_value=''), \
                     patch.object(main,'start_shopify_tracking_job') as start:
                    result=main.create_shopify_tracking_job(payload)
                self.assertTrue(result['ok'])
                params=conn.execute.call_args.args[1]
                self.assertEqual(params[5],expected)
                start.assert_called_once_with(result['job_id'])
