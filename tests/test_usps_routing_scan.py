import unittest
from app.main import normalize_dispatch_scan_code, dispatch_scan_code_is_physical


class USPSRoutingScanTests(unittest.TestCase):
    def test_zip5_and_zip9_match_full_tracking_number(self):
        tracking = '9361289691068393847592'
        for prefix in ('42011234', '420112346021'):
            self.assertEqual(normalize_dispatch_scan_code(prefix + tracking), tracking)
            self.assertTrue(dispatch_scan_code_is_physical(prefix + tracking))
        self.assertEqual(normalize_dispatch_scan_code('420 11234 ' + tracking), tracking)
        self.assertEqual(normalize_dispatch_scan_code(tracking), tracking)

    def test_other_lengths_and_nonpostal_numbers_are_not_trimmed(self):
        for value in ('42011234', '42011234936128969106839384759',
                      '421112349361289691068393847592', '420112348361289691068393847592',
                      'TBA334608917114', '1ZR1B5860338051528', '113-5491407-3133014'):
            self.assertEqual(normalize_dispatch_scan_code(value), value)
