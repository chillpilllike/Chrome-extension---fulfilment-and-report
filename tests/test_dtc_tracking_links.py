import contextlib
import copy
import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.shopify_scripts import tracking_sync as sync


class DtcTrackingLinksTests(unittest.TestCase):
    def setUp(self):
        self.client = sync.OdooClient('https://example.invalid', 'test', 'test', 'test')
        self.client._picking_tracking_fields = {'epg_tracking_data': {'type': 'json'}}
        self.picking = {'id': 1, 'state': 'done', 'picking_type_code': 'outgoing',
                        'carrier_id': [7, 'EPG Priority Packet'], 'carrier_tracking_ref': 'EPG123',
                        'note': '<p>Keep instructions</p>', 'epg_tracking_data': False}
        self.info = [{'number': 'EPG123', 'company': 'ePost', 'url': 'https://epgtrack.com/EPG123'}]

    def test_matching_number_still_backfills_url_and_preserves_carrier(self):
        values = self.client.picking_tracking_values(self.picking, 'EPG123', self.info, 'dtc:F1', 99)
        self.assertEqual(values, {'epg_tracking_data': [dict(source='dtc:F1', **self.info[0])]})
        self.picking.update(values)
        self.assertEqual(self.client.picking_tracking_values(self.picking, 'EPG123', self.info, 'dtc:F1', 99), {})

    def test_url_change_and_number_replacement_preserve_other_parcels(self):
        self.picking['epg_tracking_data'] = [dict(source='dtc:F2', number='OTHER', company='UPS', url='https://ups.com/OTHER')]
        values = self.client.picking_tracking_values(self.picking, 'EPG123', self.info, 'dtc:F1')
        self.assertEqual(values['carrier_tracking_ref'], 'EPG123, OTHER')
        self.picking.update(values)
        changed = [dict(self.info[0], number='NEW', url='https://epgtrack.com/NEW')]
        values = self.client.picking_tracking_values(self.picking, 'NEW', changed, 'dtc:F1')
        self.assertEqual(values['carrier_tracking_ref'], 'NEW, OTHER')
        self.assertEqual(len(values['epg_tracking_data']), 2)
        self.assertEqual(values['epg_tracking_data'][1]['source'], 'dtc:F2')

    def test_returns_internal_and_cancelled_pickings_are_untouched(self):
        for values in ({'state': 'cancel'}, {'picking_type_code': 'incoming'}, {'picking_type_code': 'internal'}):
            self.assertEqual(self.client.picking_tracking_values(dict(self.picking, **values), 'EPG123', self.info, 'dtc:F1'), {})

    def test_urls_cannot_execute_script_or_contain_credentials(self):
        for url in ['javascript:alert(1)', '//example.com', 'https://u:p@example.com',
                    'https://example.com\n/x', 'https://example.com:bad', 'https://example.com\\x', 'data:text/html,evil']:
            with self.subTest(url=url):
                self.assertEqual(sync.safe_tracking_url(url), '')
        self.assertEqual(sync.safe_tracking_url('https://epgtrack.com/a?x=1&y=2'), 'https://epgtrack.com/a?x=1&y=2')

    def test_instance_without_new_addon_preserves_notes_and_syncs_numbers(self):
        self.client._picking_tracking_fields = {}
        values = self.client.picking_tracking_values(self.picking, 'EPG123', self.info, 'dtc:F1')
        self.assertTrue(values['note'].startswith(self.picking['note']))
        self.picking.update(values)
        self.assertEqual(self.client.picking_tracking_values(self.picking, 'EPG123', self.info, 'dtc:F1'), {})

    def test_complete_run_backfills_old_fulfillment_and_reruns_without_writes(self):
        source = {'id': 'S1', 'name': '#100', 'tags': ['SRC_ODOO_DB:test', 'SRC_ODOO_ORDER:NC123'],
                  'updatedAt': '2026-10-21T10:00:00Z', 'fulfillments': [
                      {'id': 'F1', 'status': 'SUCCESS', 'createdAt': '2026-01-01T00:00:00Z', 'trackingInfo': self.info},
                      {'id': 'F2', 'status': 'CANCELLED', 'createdAt': '2026-10-21T00:00:00Z',
                       'trackingInfo': [dict(self.info[0], number='CANCELLED')]},
                  ]}
        client = self.client
        client.connect = Mock()
        client.find_sale_order_id_by_name = Mock(return_value=5)
        client.sale_order_is_eligible = Mock(return_value=(True, {'state': 'sale'}, ''))
        client.get_pickings_of_sale_order = Mock(return_value=[1])
        client.read_pickings = Mock(side_effect=lambda _: [copy.deepcopy(self.picking)])
        client.find_carrier_id_by_name = Mock(return_value=99)
        def save(_pid, values):
            self.picking.update(values)
            return True
        client.write_picking_tracking = Mock(side_effect=save)
        state = Mock()
        state.already_synced.return_value = True
        with tempfile.TemporaryDirectory() as tmp:
            report = str(Path(tmp) / 'report.csv')
            argv = ['tracking_sync', '--from-date', '2026-10-21', '--to-date', '2026-10-21',
                    '--no-validate-deliveries', '--report-csv', report]
            dest = {'url': 'https://example.invalid', 'db': 'test', 'username': 'test', 'password': 'test'}
            with patch.object(sync, 'ODOO_DESTS', [dest]), patch.object(sync, 'SHOPIFY_SOURCES', [{'shop': 'dtc'}]), \
                    patch.object(sync, 'OdooClient', return_value=client), patch.object(sync, 'StateDB', return_value=state), \
                    patch.object(sync, 'ensure_shopify_access_token', return_value='test'), \
                    patch.object(sync, 'fetch_source_orders', return_value=[source]), patch('sys.argv', argv), \
                    contextlib.redirect_stdout(io.StringIO()):
                with patch('sys.argv', argv + ['--dry-run']):
                    sync.main()
                self.assertEqual(client.write_picking_tracking.call_count, 0)
                sync.main()
                sync.main()
                self.assertEqual(client.write_picking_tracking.call_count, 1)
                self.info[0]['url'] = 'https://epgtrack.com/corrected/EPG123'
                sync.main()
            self.assertEqual(client.write_picking_tracking.call_count, 2)
            self.assertEqual(self.picking['epg_tracking_data'][0]['url'], self.info[0]['url'])
            with open(report) as f:
                self.assertFalse(any(r['result'] == 'SYNC_FAILED' for r in csv.DictReader(f)))

    def test_multiple_fulfillments_do_not_reorder_on_repeat(self):
        rows = sync.merge_tracking_data([], self.info, 'dtc:F1')
        rows = sync.merge_tracking_data(rows, [dict(self.info[0], number='SECOND')], 'dtc:F2')
        self.assertEqual(sync.merge_tracking_data(rows, self.info, 'dtc:F1'), rows)

    def test_query_includes_partially_fulfilled_orders(self):
        with patch.object(sync, 'gql', return_value={'orders': {'edges': [], 'pageInfo': {'hasNextPage': False}}}) as gql:
            list(sync.fetch_source_orders('dtc', 'test', '2026-01', 'start', 'end'))
        self.assertIn('fulfillment_status:partial', gql.call_args.args[4]['q'])


if __name__ == '__main__':
    unittest.main()
