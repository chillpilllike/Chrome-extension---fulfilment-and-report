import json
from pathlib import Path
import subprocess
import unittest
import sqlite3
import inspect
from unittest.mock import patch

from app import main
from app.services import amazon_bundles, replacement_tracking

PARENT = 'B0D51GVTKS'
A, B = 'B00ENS39XK', 'B00F7OZJQE'
ORDER = '111-5640817-7945804'


class VirtualBundleTests(unittest.TestCase):
    def setUp(self):
        self.row = dict(id=1, asin=PARENT, quantity=1, state='pulled', odoo_order_name='NC26628')

    def test_new_bundle_evidence_loads_durably_and_cannot_rewrite_old_composition(self):
        parent = 'B000000088'
        evidence = dict(parent_asin=parent, observed_asin=parent, source_url='https://www.amazon.com/dp/' + parent,
                        source='bundleComponentDetails_feature_div', components={A: 2, B: 1})
        with patch.dict(amazon_bundles.CATALOG, dict(amazon_bundles.CATALOG), clear=True):
            self.assertEqual(amazon_bundles.validate_evidence(evidence), (parent, {A: 2, B: 1}))
            with sqlite3.connect(':memory:') as conn:
                conn.row_factory = lambda cursor, values: dict(zip([col[0] for col in cursor.description], values))
                conn.execute('CREATE TABLE app_settings(key TEXT, value TEXT)')
                conn.execute('INSERT INTO app_settings VALUES (?,?)', ('amazon_bundle:' + parent, json.dumps(evidence)))
                amazon_bundles.load_catalog(conn)
                self.assertEqual(amazon_bundles.components(parent), {A: 2, B: 1})
            for invalid in [{**evidence, 'components': {A: 1, B: 1}}, {**evidence, 'observed_asin': A},
                            {**evidence, 'source_url': 'https://example.com/dp/' + parent},
                            {**evidence, 'source': 'recommendations'}, {**evidence, 'components': {A: float('nan'), B: 1}}]:
                with self.assertRaises(ValueError):
                    amazon_bundles.validate_evidence(invalid)

    def test_sync_requires_complete_components_and_quantities(self):
        for observed, quantities, matches in [
            ({A, B}, {A: 1, B: 1}, True),
            ({A}, {A: 1}, False),
            ({A, B}, {A: 2, B: 1}, False),
            ({A, B}, None, False),
            ({A, B, 'B000000099'}, {A: 1, B: 1}, False),
        ]:
            allowed, _ = main.safe_history_match_rows([self.row], ORDER, observed, quantities)
            self.assertEqual(bool(allowed), matches)
        for patch in [dict(amazon_order_id='111-1111111-1111111'), dict(state='cancelled'), dict(replacement_asin=A)]:
            allowed, _ = main.safe_history_match_rows([{**self.row, **patch}], ORDER, {A, B}, {A: 1, B: 1})
            # An explicit replacement has its own ordinary ASIN semantics.
            if 'replacement_asin' not in patch:
                self.assertFalse(allowed)

    def test_direct_odoo_quantity_checks_explain_the_bundle(self):
        candidate = dict(asins=[PARENT], asin_quantities={PARENT: 1})
        records = [dict(amazon_order_id=ORDER, asin_quantities={A: 1, B: 1})]
        main.amazon_history_apply_quantity_checks(records, {ORDER: [candidate]})
        self.assertTrue(candidate['quantity_matches'])
        self.assertEqual(candidate['bundle_components'], {PARENT: {A: 1, B: 1}})
        records[0]['asin_quantities'] = {A: 1}
        main.amazon_history_apply_quantity_checks(records, {ORDER: [candidate]})
        self.assertFalse(candidate['quantity_matches'])

    def test_preshipment_estimate_updates_status_without_claiming_contents_or_delivery(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory = lambda cursor, values: dict(zip([col[0] for col in cursor.description], values))
            conn.execute('CREATE TABLE app_settings(key TEXT, value TEXT)')
            conn.execute('CREATE TABLE order_lines(id INTEGER, asin TEXT, quantity REAL, state TEXT, amazon_order_id TEXT, tracking_payload TEXT, tracking_status TEXT, tracking_checked_at TEXT, last_error TEXT, updated_at TEXT)')
            conn.execute('INSERT INTO order_lines(id, asin, quantity, state, amazon_order_id, last_error) VALUES(1, ?, 1, ?, ?, ?)', (PARENT, 'ordered', ORDER, 'missing shipment ASIN'))
            conn.commit()
            source = inspect.getsource(main.api_tracking_update_impl)
            source = source[:source.index('    status = tracking_status_from_packages(packages)')] + '    return strict_order\n'
            namespace = dict(main.__dict__, db=lambda: conn, fast_page_cache_clear_matching=lambda *a: None, index_order_line=lambda *a: None)
            exec(source, namespace)
            payload = main.ChromeTrackingUpdatePayload(amazon_order_id=ORDER, packages=[dict(status_only=True, status='Arriving tomorrow')])
            result = namespace['api_tracking_update_impl'](payload)
            self.assertTrue(result['ok'])
            saved = conn.execute('SELECT * FROM order_lines').fetchone()
            self.assertEqual(saved['state'], 'ordered')
            self.assertEqual(saved['tracking_status'], 'Arriving tomorrow')
            self.assertIsNone(saved['tracking_payload'])
            self.assertIsNone(saved['last_error'])
            payload.packages[0]['status'] = 'Delivered'
            with self.assertRaises(main.HTTPException):
                namespace['api_tracking_update_impl'](payload)

    def test_split_packages_preserve_parent_and_require_every_component(self):
        first = dict(asins=[A], tracking_id='TBA123456789001', status='Delivered')
        second = dict(asins=[B], tracking_id='TBA123456789002', status='Delivered')
        mapped, missing = main.tracking_packages_by_line_with_one_to_one_fallback([first, second], [self.row])
        self.assertEqual(mapped[1], [first, second])
        self.assertFalse(missing)
        self.assertEqual(self.row['asin'], PARENT)
        self.assertFalse(replacement_tracking.delivered_quantity_complete(self.row, [first]))
        self.assertTrue(replacement_tracking.delivered_quantity_complete(self.row, [first, second]))
        self.assertFalse(replacement_tracking.delivered_quantity_complete(self.row, [first, first]))
        self.assertFalse(main.package_matches_line(dict(asins=[A, B], asin_evidence_source='order_inferred'), self.row))
        self.assertFalse(main.package_matches_line(dict(asins=['B000000099']), self.row))
        self.assertEqual(main.tracking_unambiguous_replacement_product([first], self.row), {})

    def test_multiple_bundle_units_need_verified_quantity(self):
        row = {**self.row, 'quantity': 2}
        packages = [dict(asins=[child], tracking_id=child) for child in [A, B]]
        self.assertFalse(amazon_bundles.delivered_complete(row, packages))
        for package in packages:
            package['products'] = [dict(asin=package['asins'][0], quantity=2, quantity_verified=True)]
        self.assertTrue(amazon_bundles.delivered_complete(row, packages))
        self.assertFalse(amazon_bundles.delivered_complete(row, [{**p, 'status_only': True} for p in packages]))

    def test_completion_accepts_only_catalog_composition_and_quantities(self):
        mapping = dict(line_ids=[1], asin=PARENT, amazon_order_id=ORDER, observed_asins=[A, B], observed_quantities={A: 1, B: 1})
        payload = main.ChromeJobCompletePayload(amazon_order_id=ORDER, amazon_recipient='Nutricity NC26628', amazon_asins=[A, B], order_mappings=[mapping])
        self.assertEqual(main.chrome_completion_history_evidence_error('group', [self.row], payload, {}, ORDER), '')
        payload.order_mappings[0]['observed_quantities'][A] = 2
        self.assertTrue(main.chrome_completion_history_evidence_error('group', [self.row], payload, {}, ORDER))
        payload.amazon_asins.append('B000000099')
        self.assertTrue(main.chrome_completion_history_evidence_error('group', [self.row], payload, {}, ORDER))

    def test_extension_uses_server_composition_in_both_account_experiences(self):
        source = (Path(__file__).resolve().parents[1] / 'chrome-extension/content.js').read_text()
        helpers = source[source.index('function historyExpectedAsins('):source.index('function recentOrderMatchesActiveJob(')]
        helpers += source[source.index('function orderHistoryAsinIdentityCheck('):source.index('async function reportAmazonOrders(')]
        script = '''
const assert = require('node:assert/strict');
const activeJobAsins = active => active.job.items.map(i => i.asin);
for (const experience of ['consumer', 'business']) {
 const active = {amazonAccountExperience:experience,job:{items:[{asin:'B0D51GVTKS',bundle_components:{B00ENS39XK:1,B00F7OZJQE:1}}]}};
 assert.equal(orderHistoryAsinIdentityCheck(active,[{asins:['B00ENS39XK','B00F7OZJQE']}]).ok,true);
 assert.equal(orderHistoryAsinIdentityCheck(active,[{asins:['B00ENS39XK']}]).ok,false);
 assert.equal(orderHistoryAsinIdentityCheck(active,[{asins:['B00ENS39XK','B00F7OZJQE','B000000099']}]).ok,false);
 assert.equal(active.job.items[0].asin,'B0D51GVTKS');
 delete active.job.items[0].bundle_components;
 assert.equal(orderHistoryAsinIdentityCheck(active,[{asins:['B00ENS39XK','B00F7OZJQE']}]).ok,false);
}
'''
        subprocess.run(['node', '-e', helpers + script], check=True, capture_output=True, text=True)
