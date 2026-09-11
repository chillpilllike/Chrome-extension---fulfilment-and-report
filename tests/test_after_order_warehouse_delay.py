import ast
import json
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock

from app.services.warehouse_dispatch_delay import NY, Monitor, delivery_day, due_at, holidays
from app.services.after_order_email import render_after_order_email


class WarehouseDelayTests(unittest.TestCase):
    def test_two_complete_business_days(self):
        for received, due in [('2026-09-07','2026-09-10'), ('2026-09-04','2026-09-10'),
                              ('2026-09-08','2026-09-11'), ('2026-07-02','2026-07-08'),
                              ('2026-11-25','2026-12-01'), ('2021-12-30','2022-01-05')]:
            with self.subTest(received=received):
                self.assertEqual(due_at(datetime.fromisoformat(received).replace(tzinfo=NY)).isoformat()[:10], due)

    def test_observed_holidays_and_dst(self):
        self.assertIn(date(2021,12,31), holidays(2021))
        self.assertIn(date(2026,7,3), holidays(2026))
        self.assertEqual(due_at(datetime(2026,3,6,tzinfo=NY)).isoformat(), '2026-03-11T00:00:00-04:00')
        self.assertEqual(due_at(datetime(2026,10,30,tzinfo=NY)).isoformat(), '2026-11-04T00:00:00-05:00')

    def test_delivery_evidence_never_uses_eta_or_check_time(self):
        for package in ({}, {'status':'Not delivered September 1'}, {'status':'Out for delivery'},
                        {'status':'Delivered'}, {'status':'Delivered','expected_delivery_date':'2026-09-01'},
                        {'status':'Delivered September 12'}):
            self.assertIsNone(delivery_day(package, '2026-09-11T15:00:00Z'))
        self.assertEqual(delivery_day({'status':'Delivered September 1'}, '2026-09-11T15:00:00Z').date(), date(2026,9,1))
        self.assertEqual(delivery_day({'status':'Delivered','events':[{'message':'Delivered','date':'2026-09-02'}]}, '2026-09-11T15:00:00Z').date(), date(2026,9,2))
        self.assertEqual(delivery_day({'status':'Delivered December 29'}, '2026-01-05T15:00:00Z').year, 2025)

    def test_monitor_disabled_without_both_guards(self):
        for test, approval, enabled in [(True,True,'true'),(False,False,'true'),(False,True,'false')]:
            db = Mock()
            monitor = Monitor({'db':db,'after_order_email_test_mode':lambda:test,
                               'after_order_approval_only_live':lambda:approval,
                               'clean_text':str,'get_service_settings':lambda:{'after_order_warehouse_delay_enabled':enabled}})
            self.assertEqual(monitor.run_checks(None), {'checked':0,'queued':0})
            db.assert_not_called()

    def fixture(self, *, line_overrides=None, local_dispatch=False, picking=None, extra_line=False):
        line = dict(id=1, store_id=1, odoo_order_id=2, odoo_line_id=3, odoo_order_name='TEST',
                    odoo_order_date='2026-08-21', asin='B012345678',amazon_order_id='123',state='ordered',
                    order_engine='chrome',tracking_payload=json.dumps([{'status':'Delivered September 1'}]),
                    tracking_checked_at='2026-09-11T15:00:00Z')
        line.update(line_overrides or {})
        connection = Mock()
        def execute(sql, args):
            result = Mock()
            result.fetchall.return_value = [line]
            result.fetchone.return_value = {'id':1} if local_dispatch else None
            return result
        connection.execute.side_effect = execute
        @contextmanager
        def db():
            yield connection
        client = Mock()
        def read(model, ids, fields):
            if model == 'sale.order':
                return [{'state':'sale','order_line':[3]}]
            if model == 'sale.order.line':
                return [{'id':4 if extra_line else 3,'product_id':[5,'Item'],'product_uom_qty':1,'qty_delivered':0}]
            return [{'id':5,'type':'consu'}]
        client.read.side_effect = read
        client.execute.return_value = [picking or {'state':'assigned','carrier_tracking_ref':False}]
        monitor = Monitor({'db':db,'after_order_cutoff_date':lambda:'2026-08-20','parse_tracking_packages':json.loads,
                           'OdooClient':lambda _:client,'get_store':lambda _: {}})
        return monitor, client

    def test_eligible_and_fresh_odoo_check(self):
        monitor, client = self.fixture()
        self.assertIsNotNone(monitor.evidence(1,2,verify_odoo=True))
        client.execute.assert_called_once()

    def test_unsafe_orders_are_excluded(self):
        for overrides in ({'odoo_order_date':'2026-08-19'}, {'state':'fulfilled'}, {'order_engine':'third_party'},
                          {'tracking_payload':'[]'}, {'odoo_order_state':'cancel'}, {'asin':''},
                          {'tracking_payload':json.dumps([{'status':'Delivered September 1'},{'status':'Arriving September 15'}])},
                          {'tracking_payload':json.dumps([{'status':'Delivered September 11, 2099'}])}):
            with self.subTest(overrides=overrides):
                monitor, _ = self.fixture(line_overrides=overrides)
                self.assertIsNone(monitor.evidence(1,2))
        for options in ({'local_dispatch':True}, {'extra_line':True}, {'picking':{'state':'done'}},
                        {'picking':{'state':'assigned','carrier_tracking_ref':'EPG123'}}):
            monitor, _ = self.fixture(**options)
            with self.assertRaises(ValueError):
                monitor.validate({'store_id':1,'odoo_order_id':2})

    def test_odoo_failure_is_fail_closed(self):
        monitor, client = self.fixture()
        client.read.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            monitor.validate({'store_id':1,'odoo_order_id':2})

    def test_template_is_information_only_and_branded(self):
        subject, html, plain = render_after_order_email(
            {'case_type':'warehouse_dispatch_delay','odoo_order_name':'NC123', 'context':{'website_name':'Nutricity Australia'}},
            '', actions=[], labels={})
        self.assertIn('NC123', subject)
        for text in (html, plain):
            self.assertIn('24–48 hours', text)
            self.assertIn('You don’t need to take any action', text)
            self.assertIn('placed on Nutricity Australia', text)
            self.assertNotIn('Amazon', text)
            self.assertNotIn('choice=', text)
            self.assertNotIn('3 days', text)

    def test_preparation_and_approval_both_revalidate_and_have_once_key(self):
        tree = ast.parse(Path('app/main.py').read_text())
        for name in ('send_after_order_email','retry_after_order_email'):
            fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
            self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                                and isinstance(n.func.value,ast.Name) and n.func.value.id=='warehouse_dispatch_delay'
                                and n.func.attr=='validate' for n in ast.walk(fn)))
        self.assertIn('warehouse_dispatch_delay:once', Path('app/main.py').read_text())


if __name__ == '__main__':
    unittest.main()
