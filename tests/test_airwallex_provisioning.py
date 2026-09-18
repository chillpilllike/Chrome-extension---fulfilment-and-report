"""Test automatic setup without importing production startup hooks."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


def load_function(name, scope):
    tree = ast.parse((Path(__file__).parents[1] / 'app/main.py').read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<setup>', 'exec'), scope)
    return scope[name]


class ProvisioningTests(unittest.TestCase):
    def test_only_active_registered_stores_receive_authorized_default_configuration(self):
        defaults = dict(client_id='client', api_key='PRIVATE', secret='PRIVATE-WEBHOOK',
                        pay_account_name='Example', pay_account_number='123', state='enabled')
        client = Mock()
        client.execute.side_effect = [[{'provider_id': 7, 'needs_accounts': True}], {}]
        factory = Mock(return_value=client)
        scope = dict(airwallex_default_connection=lambda: defaults,
                     list_stores=lambda: [{'id': 1, 'active': 1}, {'id': 2, 'active': 0}],
                     get_store=lambda id: SimpleNamespace(id=id, odoo_url='https://store.example'), OdooClient=factory)
        load_function('airwallex_provision_registered_stores', scope)()
        self.assertEqual(factory.call_count, 1)
        payload = client.execute.call_args_list[0].args[2][0]
        self.assertEqual(payload['api_key'], 'PRIVATE')
        self.assertEqual(payload['secret'], 'PRIVATE-WEBHOOK')
        self.assertEqual(payload['client_id'], 'client')
        self.assertEqual(client.execute.call_args_list[1].args,
                         ('payment.provider', 'action_airwallex_import_global_accounts', [[7]]))

    def test_no_default_does_not_contact_stores(self):
        stores = Mock()
        scope = dict(airwallex_default_connection=lambda: {}, list_stores=stores)
        load_function('airwallex_provision_registered_stores', scope)()
        stores.assert_not_called()

    def test_existing_configured_accounts_are_not_reimported(self):
        client = Mock()
        client.execute.return_value = [{'provider_id': 7, 'needs_accounts': False}]
        scope = dict(airwallex_default_connection=lambda: {'client_id': 'client'},
                     list_stores=lambda: [{'id': 1, 'active': 1}],
                     get_store=lambda id: SimpleNamespace(id=id, odoo_url='https://store.example'),
                     OdooClient=lambda store: client)
        load_function('airwallex_provision_registered_stores', scope)()
        self.assertEqual(client.execute.call_count, 1)

    def test_credentials_are_not_sent_to_plain_http_stores(self):
        factory = Mock()
        scope = dict(airwallex_default_connection=lambda: {'client_id': 'client'},
                     list_stores=lambda: [{'id': 1, 'active': 1}],
                     get_store=lambda id: SimpleNamespace(odoo_url='http://store.example'),
                     OdooClient=factory)
        load_function('airwallex_provision_registered_stores', scope)()
        factory.assert_not_called()
