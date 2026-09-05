import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]


class PortalSecurityTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((ROOT / 'odoo-addons/after_order_portal/controllers/main.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        for node in cls.body:
            if isinstance(node, ast.FunctionDef):
                node.decorator_list = []
        self.admin = False
        self.website = SimpleNamespace(id=4)
        self.request = SimpleNamespace(
            env=SimpleNamespace(user=SimpleNamespace(has_group=lambda group: self.admin)),
            website=self.website, params={}, session={},
        )
        base = type('BasePortal', (), {
            '_sale_order_get_page_view_values': lambda self, order, access, values, history, **kw: values,
        })
        scope = {'CustomerPortal': base, 'request': self.request}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), 'portal', 'exec'), scope)
        self.controller = scope['AfterOrderPortal']()
        self.order = SimpleNamespace(id=123, website_id=self.website)
        self.payload = {'order': {'id': 123}, 'test_mode': True}

    def test_test_payload_denied_to_customer_and_ordinary_staff(self):
        with self.assertRaises(RuntimeError):
            self.controller._validate_payload(self.payload, self.order)

    def test_admin_can_preview_but_not_cross_order_or_website(self):
        self.admin = True
        self.controller._validate_payload(self.payload, self.order)
        with self.assertRaises(RuntimeError):
            self.controller._validate_payload({**self.payload, 'order': {'id': 999}}, self.order)
        self.order.website_id = SimpleNamespace(id=5)
        with self.assertRaises(RuntimeError):
            self.controller._validate_payload(self.payload, self.order)

    def test_live_payload_permitted_after_standard_order_access_check(self):
        self.controller._validate_payload({**self.payload, 'test_mode': False}, self.order)

    def test_normal_customer_order_view_has_no_test_panel(self):
        self.controller._bridge = Mock(return_value={'actions': []})
        values = self.controller._sale_order_get_page_view_values(self.order, None, {}, 'history')
        self.assertEqual([], values['after_order_panels'])
        self.assertEqual('', values['after_order_error'])

    def test_old_test_token_does_not_render_for_customer(self):
        self.request.params['after_order_token'] = 'test-token'
        self.controller._bridge = Mock(return_value=self.payload)
        values = self.controller._sale_order_get_page_view_values(self.order, None, {}, 'history')
        self.assertEqual([], values['after_order_panels'])
        self.assertEqual('', values['after_order_error'])

    def test_admin_panel_is_embedded_with_standard_access_token(self):
        self.admin = True
        self.request.params['after_order_token'] = 'test-token'
        self.controller._bridge = Mock(return_value=self.payload)
        self.controller._alternative_products = Mock(return_value=[])
        values = self.controller._sale_order_get_page_view_values(self.order, 'order-access', {}, 'history')
        self.assertEqual(1, len(values['after_order_panels']))
        self.assertEqual('order-access', values['after_order_access_token'])

    def test_template_inherits_standard_preview_and_forms_have_csrf(self):
        tree = ElementTree.parse(ROOT / 'odoo-addons/after_order_portal/views/order_preview.xml')
        self.assertEqual('sale.sale_order_portal_template', tree.find('template').get('inherit_id'))
        for form in tree.findall('.//form'):
            self.assertIsNotNone(form.find("input[@name='csrf_token']"))
            self.assertIsNotNone(form.find("input[@name='access_token']"))


class BridgeAdminGuardTests(unittest.TestCase):
    def test_test_mode_and_test_link_require_trusted_admin_assertion(self):
        tree = ast.parse((ROOT / 'app/main.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'require_after_order_test_admin')
        class Denied(Exception):
            pass
        for mode, test_link in [(True, False), (False, True), (True, True), (False, False)]:
            scope = {'Request': object, 'Any': object, 'HTTPException': Denied, 'after_order_email_test_mode': lambda: mode}
            exec(compile(ast.Module(body=[node], type_ignores=[]), 'guard', 'exec'), scope)
            guard = scope['require_after_order_test_admin']
            if mode or test_link:
                with self.assertRaises(Denied):
                    guard(SimpleNamespace(headers={}), {'test_mode': test_link})
            else:
                guard(SimpleNamespace(headers={}), {'test_mode': test_link})
            guard(SimpleNamespace(headers={'x-after-order-admin': 'true'}), {'test_mode': test_link})


if __name__ == '__main__':
    unittest.main()
