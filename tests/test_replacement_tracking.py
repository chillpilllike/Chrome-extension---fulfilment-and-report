import json
import inspect
import sqlite3
from unittest.mock import Mock
from pathlib import Path
import subprocess
import unittest
from app import main
from app.services import replacement_tracking as tracking

A, B, OLD = 'B000000001', 'B000000002', 'B000000099'
ORDER1, ORDER2 = '111-1111111-1111111', '222-2222222-2222222'


def rows():
    return [dict(id=i, asin=asin, replacement_asin=asin, original_asin=OLD,
                 bundle_component_count=2, quantity=2) for i, asin in [(1, A), (2, B)]]


def mapping(line, asin, order):
    return dict(line_ids=[line], asin=asin, amazon_order_id=order, observed_asins=[asin])


class ReplacementTrackingTests(unittest.TestCase):
    def test_separate_and_shared_amazon_order_ids(self):
        for second in [ORDER1, ORDER2]:
            self.assertEqual(tracking.completion_error(rows(), [mapping(1, A, ORDER1), mapping(2, B, second)]), '')

    def test_swapped_missing_conflicting_and_unproven_order_mappings_rejected(self):
        for mappings in [
            [mapping(1, B, ORDER1), mapping(2, A, ORDER2)],
            [mapping(1, A, ORDER1)],
            [mapping(1, A, ORDER1), mapping(1, A, ORDER2), mapping(2, B, ORDER2)],
            [dict(mapping(1, A, ORDER1), observed_asins=[]), mapping(2, B, ORDER2)],
            [dict(mapping(1, A, ORDER1), amazon_order_id='bad'), mapping(2, B, ORDER2)],
        ]:
            self.assertTrue(tracking.completion_error(rows(), mappings))

    def test_separate_packages_match_only_exact_replacement_asin(self):
        packages = [dict(asins=[A], tracking_id='TBA123456789001'), dict(asins=[B], tracking_id='TBA123456789002')]
        mapped, unmatched = main.tracking_packages_by_line_with_one_to_one_fallback(packages, rows())
        self.assertEqual(mapped, {1: [packages[0]], 2: [packages[1]]})
        self.assertFalse(unmatched)
        self.assertFalse(main.package_matches_line(dict(asins=[OLD]), rows()[0]))

    def test_shared_package_is_attached_to_both_only_with_both_asins(self):
        package = dict(asins=[A, B], tracking_id='TBA123456789001')
        mapped, unmatched = main.tracking_packages_by_line_with_one_to_one_fallback([package], rows())
        self.assertEqual(set(mapped), {1, 2})
        self.assertFalse(unmatched)

    def test_partial_update_never_falls_back_to_other_component(self):
        a = dict(asins=[A], tracking_id='TBA123456789001')
        unknown = dict(asins=[OLD], tracking_id='TBA123456789002')
        mapped, unmatched = main.tracking_packages_by_line_with_one_to_one_fallback([a, unknown], rows())
        self.assertEqual(mapped, {1: [a]})
        self.assertEqual([r['id'] for r in unmatched], [2])
        self.assertEqual(main.tracking_unambiguous_replacement_product([unknown], rows()[1]), {})

    def test_missing_or_order_inferred_asins_are_rejected(self):
        inferred = main.merge_order_products_into_tracking_packages([{'tracking_id': 'TBA123456789001'}], [{'asin': A, 'quantity': 2}])
        self.assertTrue(tracking.tracking_error(rows(), inferred))
        self.assertFalse(main.package_matches_line(inferred[0], rows()[0]))
        self.assertTrue(tracking.tracking_error(rows(), [{'asins': [], 'status_only': True}]))
        self.assertEqual(tracking.tracking_error(rows(), [{'asins': [A], 'tracking_id': 'TBA123456789001'}]), '')

    def test_split_quantity_and_retries_preserve_two_tracking_codes(self):
        first = dict(asins=[A], products=[dict(asin=A, quantity=1, quantity_verified=True)], tracking_id='TBA123456789001', status='Delivered')
        second = dict(asins=[A], products=[dict(asin=A, quantity=1, quantity_verified=True)], tracking_id='TBA123456789002', status='Delivered')
        merged = main.merge_replacement_tracking_packages([first, second, dict(first)])
        self.assertEqual(len(merged), 2)
        self.assertFalse(tracking.delivered_quantity_complete(rows()[0], [first]))
        self.assertTrue(tracking.delivered_quantity_complete(rows()[0], merged))

    def test_actual_api_preflight_preserves_ids_and_tracking_on_missing_asin(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        conn.execute('CREATE TABLE order_lines (id INTEGER, asin TEXT, replacement_asin TEXT, bundle_component_count INTEGER, amazon_order_id TEXT, tracking_payload TEXT, last_error TEXT, updated_at TEXT)')
        conn.execute('INSERT INTO order_lines VALUES (1, ?, ?, 2, ?, ?, NULL, NULL)', (A, A, ORDER1, 'saved tracking'))
        conn.commit()
        source = inspect.getsource(main.api_tracking_update_impl)
        source = source[:source.index('    status = tracking_status_from_packages(packages)')] + '    return strict_order\n'
        namespace = dict(main.__dict__, db=lambda: conn, fast_page_cache_clear_matching=Mock(), index_order_line=Mock())
        exec(source, namespace)
        payload = main.ChromeTrackingUpdatePayload(amazon_order_id=ORDER1, packages=[{'tracking_id': 'TBA123456789001', 'status': 'Delivered'}])
        with self.assertRaises(main.HTTPException) as error:
            namespace['api_tracking_update_impl'](payload)
        self.assertEqual(error.exception.status_code, 409)
        row = conn.execute('SELECT * FROM order_lines').fetchone()
        self.assertEqual(row['amazon_order_id'], ORDER1)
        self.assertEqual(row['tracking_payload'], 'saved tracking')
        self.assertIn('exact shipment ASIN', row['last_error'])
        payload.packages[0]['asins'] = [A]
        self.assertTrue(namespace['api_tracking_update_impl'](payload))

    def test_extension_maps_exact_history_asins_and_blocks_ambiguous_orders(self):
        source = (Path(__file__).resolve().parents[1] / 'chrome-extension/content.js').read_text()
        helper = source[source.index('function buildOrderMappings('):source.index('function requiresAsinMappedReporting(')]
        script = '''
const assert = require('node:assert/strict');
const orderDetailsUrl = id => id;
let verifiedBusinessBundle = false;
const businessBundleCompletionEvidence = () => verifiedBusinessBundle;
const active = {amazonAccountExperience:'consumer',job:{items:[{asin:'B000000001',line_ids:[1]},{asin:'B000000002',line_ids:[2]}]}};
const a = {amazon_order_id:'111-1111111-1111111',asins:['B000000001']};
const b = {amazon_order_id:'222-2222222-2222222',asins:['B000000002']};
assert.deepEqual(buildOrderMappings(active,[a,b]).map(m=>[m.line_ids[0],m.amazon_order_id,m.observed_asins[0]]),[[1,a.amazon_order_id,a.asins[0]],[2,b.amazon_order_id,b.asins[0]]]);
assert.equal(buildOrderMappings(active,[a]).length,1);
assert.equal(buildOrderMappings(active,[{...a,asins:[]}]).length,0);
assert.equal(buildOrderMappings(active,[a,{...b,asins:a.asins}]).length,0);
assert.equal(buildOrderMappings(active,[{...a,asins:[...a.asins,...b.asins]}]).length,2);
const business = {amazonAccountExperience:'business',job:{items:[active.job.items[0]]}};
assert.equal(buildOrderMappings(business,[b]).length,0);
verifiedBusinessBundle = true;
assert.equal(buildOrderMappings(business,[b]).length,1);
assert.equal(buildOrderMappings({...business,amazonAccountExperience:'consumer'},[b]).length,0);
'''
        subprocess.run(['node', '-e', helper+'\n'+script], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
