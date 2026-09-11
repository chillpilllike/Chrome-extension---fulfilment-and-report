"""NC26541: split shipments, duplicate-ASIN lines and orphan physical scans."""
import sqlite3
import unittest
from unittest.mock import patch

from app import main
from app.services import replacement_tracking


class MixedOrderReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(id=i, asin=asin, replacement_asin=asin, quantity=1,
                          bundle_component_count=2)
                     for i, asin in [(1, 'B0D9QL3Y7J'), (2, 'B0DSFZXK1S')]]
        self.packages = [
            dict(asins=['B0CRYNRKKJ'], tracking_id='TBA334482556657'),
            dict(asins=['B0FC6BD77Y'], tracking_id='TBA334469666593'),
            dict(asins=['B0D9QL3Y7J', 'B0DSFZXK1S'], tracking_id='TBA334466365119'),
        ]

    def test_mixed_order_accepts_exact_replacement_package_without_assigning_siblings(self):
        self.assertEqual(replacement_tracking.tracking_error(self.rows, self.packages), '')
        mapped, unmatched = main.tracking_packages_by_line_with_one_to_one_fallback(self.packages, self.rows)
        self.assertEqual(mapped, {1: [self.packages[2]], 2: [self.packages[2]]})
        self.assertEqual(unmatched, [])

    def test_unrelated_only_or_inferred_shipment_keeps_guard(self):
        for packages in [self.packages[:2], [], self.packages + [dict(asins=[])],
                         [dict(self.packages[2], asin_evidence_source='order_inferred')]]:
            self.assertTrue(replacement_tracking.tracking_error(self.rows, packages))

    def test_explicit_wrong_asin_never_becomes_automatic_replacement(self):
        row = dict(id=3, asin='B0DSFYFC9P', quantity=2)
        mapped, unmatched = main.tracking_packages_by_line_with_one_to_one_fallback([self.packages[1]], [row])
        self.assertEqual(mapped, {})
        self.assertEqual(unmatched, [row])

    def test_one_scanned_parcel_and_five_missing_lines_is_not_six_packages(self):
        parts = [dict(id=10, received=True)] + [dict(id=-i, received=False) for i in range(1, 6)]
        result = main.package_pickup_readiness_from_parts(parts)
        self.assertEqual((result['received_packages'], result['total_packages'], result['remaining_packages']), (1, 1, 0))
        self.assertEqual(result['unresolved_line_count'], 5)
        self.assertFalse(result['ready_to_ship'])
        self.assertIn('5 order line(s)', result['message'])
        historical = main.package_pickup_historical_readiness(parts, {10})
        self.assertEqual(historical['total_packages'], 1)
        self.assertFalse(historical['ready_to_ship'])

    def test_received_orphan_never_claims_entire_order_ready(self):
        result = main.package_pickup_readiness_from_parts([dict(id=10, received=True)], order_linked=False)
        self.assertFalse(result['ready_to_ship'])
        self.assertIn('Odoo order not linked', result['message'])

    def test_received_extra_package_with_no_item_match_keeps_hold(self):
        result = main.package_pickup_readiness_from_parts([
            dict(id=10, received=True, item_reconciliation_required=True)])
        self.assertEqual(result['total_packages'], 1)
        self.assertEqual(result['unmapped_package_count'], 1)
        self.assertFalse(result['ready_to_ship'])

    def test_extra_shipment_is_saved_without_claiming_a_line_or_overwriting_ownership(self):
        primary = dict(self.rows[0], store_id=1, odoo_order_id=26547,
                       odoo_order_name='NC26541', amazon_order_id='113-2933273-1909827',
                       tracking_payload='')
        class Result:
            def fetchall(self): return [primary]
            def fetchone(self): return existing
        class Connection:
            def execute(self, sql, params=()): return Result()
        package = dict(self.packages[1], status='Delivered', products=[dict(asin='B0FC6BD77Y', quantity=1)])
        existing = None
        with patch.object(main, 'bulk_upsert_dispatch_package_rows') as save:
            self.assertEqual(main.save_unassigned_tracking_packages(Connection(), primary['amazon_order_id'], [package]), 1)
            value = save.call_args.args[1][0]
            self.assertEqual(value[0], 'TBA334469666593')
            self.assertEqual(value[7], 'NC26541')
            self.assertEqual(value[9], '[]')
            self.assertIn('B0FC6BD77Y', value[15])
            for existing in [dict(amazon_order_id='111-1111111-1111111'),
                             dict(amazon_order_id=primary['amazon_order_id'], order_line_ids_json='[99]')]:
                self.assertEqual(main.save_unassigned_tracking_packages(Connection(), primary['amazon_order_id'], [package]), 0)

    def test_physical_pending_and_unresolved_lines_both_keep_hold(self):
        result = main.package_pickup_readiness_from_parts([
            dict(id=10, received=True), dict(id=11, received=False), dict(id=-3, received=False)])
        self.assertEqual((result['total_packages'], result['remaining_packages'], result['unresolved_line_count']), (2, 1, 1))
        self.assertFalse(result['ready_to_ship'])

    def repeated_rows(self):
        return [dict(id=i, asin='B0CRYNRKKJ', quantity=qty, state='pulled',
                     store_id=1, odoo_order_id=26547, odoo_order_name='NC26541')
                for i, qty in [(1, 5), (2, 2)]]

    def test_repeated_asin_requires_verified_full_quantity_and_same_order(self):
        rows = self.repeated_rows()
        order_id = '113-2933273-1909827'
        evidence = {'B0CRYNRKKJ'}
        for verified in [None, {}, {'B0CRYNRKKJ': 5}, {'B0CRYNRKKJ': 8}]:
            matched, skipped = main.safe_history_match_rows(rows, order_id, evidence, verified_quantities=verified)
            self.assertEqual(matched, [])
            self.assertEqual(len(skipped), 2)
        matched, skipped = main.safe_history_match_rows(rows, order_id, evidence, verified_quantities={'B0CRYNRKKJ': 7})
        self.assertEqual(matched, rows)
        self.assertEqual(skipped, [])
        for change in [dict(amazon_order_id='111-1111111-1111111'), dict(odoo_order_name='NC99999'), dict(state='cancelled')]:
            conflicting = [rows[0], dict(rows[1], **change)]
            matched, _ = main.safe_history_match_rows(conflicting, order_id, evidence, verified_quantities={'B0CRYNRKKJ': 7})
            self.assertEqual(matched, [])

    def test_link_repair_preserves_timestamp_count_and_undo_and_is_idempotent(self):
        raw = sqlite3.connect(':memory:')
        self.addCleanup(raw.close)
        raw.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        class Connection:
            def execute(self, sql, params=()):
                return raw.execute(sql.replace('FOR UPDATE OF e SKIP LOCKED', ''), params)
        conn = Connection()
        raw.execute('CREATE TABLE amazon_dispatch_packages (id INT, store_id INT, odoo_order_name TEXT, amazon_order_id TEXT, recipient_ref TEXT, scan_code TEXT, canonical_scan_code TEXT)')
        raw.execute('CREATE TABLE package_pickup_scan_events (id INT, package_id INT, store_id INT, odoo_order_name TEXT, amazon_order_id TEXT, recipient_ref TEXT, scan_code TEXT, matched INT, undone_at TEXT, original_result_status TEXT, result_status TEXT, original_message TEXT, message TEXT, reconciled_at TEXT, scanned_at TEXT, duplicate INT)')
        raw.execute("INSERT INTO amazon_dispatch_packages VALUES (10,1,'NC26541','113-2933273-1909827','Nutricity NC26541','TBA334482556657','TBA334482556657')")
        for i, store, undone, amazon, code in [
            (1,0,None,'113-2933273-1909827','TBA334482556657'),
            (2,0,'undone','113-2933273-1909827','TBA334482556657'),
            (3,2,None,'113-2933273-1909827','TBA334482556657'),
            (4,0,None,'111-1111111-1111111','TBA334482556657'),
            (5,0,None,'113-2933273-1909827','6557')]:
            raw.execute("INSERT INTO package_pickup_scan_events (id,package_id,store_id,odoo_order_name,amazon_order_id,scan_code,matched,undone_at,result_status,message,scanned_at,duplicate) VALUES (?,10,?,'',?,?,1,?,'matched','original scan','2026-09-11T15:49:50+00:00',0)", (i,store,amazon,code,undone))
        self.assertEqual(main.reconcile_linked_pickup_scan_events(conn), 1)
        self.assertEqual(main.reconcile_linked_pickup_scan_events(conn), 0)
        event = raw.execute('SELECT * FROM package_pickup_scan_events WHERE id=1').fetchone()
        self.assertEqual(event['odoo_order_name'], 'NC26541')
        self.assertEqual(event['store_id'], 1)
        self.assertEqual(event['scanned_at'], '2026-09-11T15:49:50+00:00')
        self.assertEqual(event['duplicate'], 0)
        self.assertEqual(event['original_message'], 'original scan')
        self.assertEqual(raw.execute('SELECT COUNT(*) AS n FROM package_pickup_scan_events').fetchone()['n'], 5)
        self.assertEqual(raw.execute("SELECT COUNT(*) AS n FROM package_pickup_scan_events WHERE odoo_order_name='' ").fetchone()['n'], 4)


if __name__ == '__main__':
    unittest.main()
