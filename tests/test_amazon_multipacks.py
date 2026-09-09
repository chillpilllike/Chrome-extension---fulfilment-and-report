"""NC26815: a three-pack purchase must never turn into three individual units."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from app.services import amazon_bundles as bundles
from app import main

PARENT = 'B0BV67RXQH'
CHILD = 'B076F324JN'

class AmazonMultipackTests(unittest.TestCase):
    def test_bundle_save_endpoint_obeys_real_settings_constraints(self, decoded_json=False):
        import sqlite3
        from contextlib import contextmanager
        conn = sqlite3.connect(':memory:')
        def row_factory(cursor, values):
            row = dict(zip([col[0] for col in cursor.description], values))
            if decoded_json and 'value' in row:
                row['value'] = json.loads(row['value'])
            return row
        conn.row_factory = row_factory
        conn.execute('CREATE TABLE app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)')
        conn.execute('CREATE TABLE order_lines(asin TEXT, replacement_asin TEXT, amazon_group_key TEXT)')
        conn.execute('INSERT INTO order_lines VALUES (?, NULL, ?)', (PARENT, 'test-pack'))
        @contextmanager
        def database():
            yield conn
            conn.commit()
        with patch.object(main, 'db', database), patch.object(main, 'ensure_chrome_job_owner'), patch.dict(bundles.CATALOG, dict(bundles.CATALOG), clear=True):
            payload = {'worker_id': 'test-worker', 'evidence': bundles.multipack_evidence(PARENT)}
            result = main.api_chrome_bundle_components('test-pack', payload)
            self.assertTrue(result['ok'])
            self.assertEqual(result['components'], {CHILD: 2})
            saved = conn.execute('SELECT * FROM app_settings').fetchone()
            self.assertTrue(saved['updated_at'])
            with patch.object(main, '_SERVICE_SETTINGS_CACHE', ({}, 0)):
                self.assertIsInstance(main.get_service_settings(), dict)
            self.assertEqual(main.api_chrome_bundle_components('test-pack', payload)['components'], {CHILD: 2})
            self.assertEqual(main.api_chrome_bundle_components('test-pack', {'read_only': True})['items'][PARENT]['components'], {CHILD: 2})
        conn.close()

    def test_bundle_save_and_reload_accept_postgres_decoded_json(self):
        self.test_bundle_save_endpoint_obeys_real_settings_constraints(decoded_json=True)

    def test_verified_pack_evidence_rejects_variant_and_count_conflicts(self):
        evidence = bundles.multipack_evidence(PARENT)
        self.assertEqual(bundles.validate_evidence(evidence), (PARENT, {CHILD: 2}))
        for change in [dict(pack_count=1), dict(single_size='60 Count (Pack of 1)'),
                       dict(components={CHILD: 3}), dict(source='title'),
                       dict(observed_asin=CHILD), dict(single_asin=PARENT)]:
            with self.assertRaises(ValueError):
                bundles.validate_evidence({**evidence, **change})

    def test_history_sync_requires_six_components_for_three_replacement_packs(self):
        row = dict(id=1, asin='B07CK6WG4L', replacement_asin=PARENT, quantity=3, state='submitted', odoo_order_name='NC26815')
        self.assertEqual(bundles.expand_quantities({PARENT: 3}), {CHILD: 6})
        for count in [1, 3, 5, 6, 7]:
            allowed, _ = main.safe_history_match_rows([row], '111-1111111-1111111', {CHILD}, {CHILD: count})
            self.assertEqual(bool(allowed), count == 6)

    def test_tracking_deduplicates_shipments_and_requires_all_six_units(self):
        row = dict(asin=PARENT, quantity=3)
        def package(key, count):
            return dict(tracking_id=key, asins=[CHILD], products=[dict(asin=CHILD, quantity=count, quantity_verified=True)])
        a, b = package('A', 3), package('B', 3)
        self.assertFalse(bundles.delivered_complete(row, [a]))
        self.assertFalse(bundles.delivered_complete(row, [a, a]))
        self.assertTrue(bundles.delivered_complete(row, [a, b]))
        self.assertFalse(bundles.delivered_complete(row, [a, {**b, 'status_only': True}]))
        self.assertFalse(bundles.delivered_complete(row, [a, {**b, 'asin_evidence_source': 'order_inferred'}]))

    def test_checkout_separate_pack_path_checks_quantity_price_title_and_freshness(self):
        source = (Path(__file__).resolve().parents[1] / 'chrome-extension/content.js').read_text()
        def function(name, next_name):
            return source[source.index('function ' + name + '('):source.index('function ' + next_name + '(')]
        helper = function('multipackCheckoutQuantities', 'productBundleEvidence')
        helper += function('exactAsinQuantitiesMatch', 'checkoutVisibleQuantityValues')
        helper += function('cartVerificationMatches', 'productIdentityVerificationsMatch')
        script = '''
const assert = require('node:assert/strict');
const expectedCartQuantities = active => Object.fromEntries(active.job.items.map(i => [i.asin, i.quantity]));
''' + helper + '\nconst pack = ' + json.dumps(bundles.multipack_evidence(PARENT)) + ''';
const make = () => ({job:{group_key:'NC26815',items:[{asin:pack.parent_asin,quantity:3,multipack_evidence:pack}]},
cartVerification:{group_key:'NC26815',verified_at:Date.now(),quantities:{[pack.parent_asin]:3},
multipacks:{[pack.parent_asin]:{title:'ZzzQuil (Pack of 2)',price:23.38,quantity:3}}}});
const evidence = () => ({readable:true,rows:[{asin:pack.single_asin,title:'ZzzQuil (Pack of 2)',price:23.38,quantity:3}]});
for(const experience of ['consumer','business']) {
  assert.deepEqual(multipackCheckoutQuantities(make(),evidence(),experience),{[pack.parent_asin]:3});
  for(const mutate of [
    e=>e.rows[0].quantity=6, e=>e.rows[0].quantity=2,
    e=>e.rows[0].price=9.74, e=>e.rows[0].title='ZzzQuil (Pack of 1)',
    e=>e.rows[0].asin='B000000099', e=>e.readable=false,
    e=>e.rows.push({...e.rows[0]}), e=>e.rows[0].price=null,
  ]) {const e=evidence();mutate(e);assert.equal(multipackCheckoutQuantities(make(),e,experience),null);}
  for(const mutate of [
    a=>a.cartVerification.verified_at=Date.now()-31*60*1000,
    a=>a.cartVerification.group_key='other', a=>a.cartVerification.multipacks={},
    a=>a.job.items[0].multipack_evidence=null,
    a=>a.job.items.push({asin:pack.single_asin,quantity:1}),
  ]) {const a=make();mutate(a);assert.equal(multipackCheckoutQuantities(a,evidence(),experience),null);}
  // An ordinary ASIN still uses the original exact path, not a pack alias.
  const ordinary=make(); ordinary.job.items[0].multipack_evidence=null;
  const e=evidence();e.rows[0].asin=pack.parent_asin;
  assert.equal(multipackCheckoutQuantities(ordinary,e,experience),null);
}
assert.equal(multipackCheckoutQuantities(make(),evidence(),'unknown'),null);
'''
        subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

    def test_paused_verification_saves_and_reloads_without_resuming(self):
        source = (Path(__file__).resolve().parents[1] / 'chrome-extension/content.js').read_text()
        helper = source[source.index('async function verifyPausedBundleMapping('):source.index('function visible(')]
        script = helper + """
const assert=require('node:assert/strict');
let active={paused:true,stage:'product',workerId:'worker',job:{group_key:'group',items:[{asin:'B0BV67RXQH'}]}};
const getActiveJob=async()=>active;
const currentProductAsinEvidence=()=>({asin:'B0BV67RXQH'});
const productMultipackEvidence=()=>({components:{B076F324JN:2}});
const exactAsinQuantitiesMatch=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const calls=[]; let failed=false; let title='';
const send=async message=>{calls.push(message.type);if(failed)throw Error('server failure');return {ok:true,items:{B0BV67RXQH:{components:{B076F324JN:2}}}}};
const showPanel=t=>{title=t};
(async()=>{
 await verifyPausedBundleMapping();
 assert.deepEqual(calls,['SAVE_BUNDLE_COMPONENTS','LOAD_MULTIPACK_EVIDENCE']);
 assert.equal(active.paused,true);assert.equal(active.stage,'product');assert.equal(title,'Bundle mapping verified');
 calls.length=0;active.paused=false;await verifyPausedBundleMapping();assert.equal(calls.length,0);
 active.paused=true;failed=true;await verifyPausedBundleMapping();assert.equal(title,'Bundle verification failed');assert.equal(active.paused,true);
})().catch(e=>{console.error(e);process.exit(1)});
"""
        subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
