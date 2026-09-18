import unittest
from unittest.mock import Mock, patch

from app.services.airwallex_api import WEBHOOK_URL, _tokens, execute_operation, validate_operation


class AirwallexApiTests(unittest.TestCase):
    def setUp(self):
        _tokens.clear()
        self.config = dict(client_id='client', api_key='key', account_id='',
                           state='enabled', webhook_id='hook-1', secret='secret')

    def test_rejects_payment_writes_arbitrary_hosts_and_other_webhooks(self):
        for method, path in [('POST', '/api/v1/transfers/create'),
                             ('GET', 'https://example.com/'),
                             ('GET', '/api/v1/deposits/../authentication/login'),
                             ('POST', '/api/v1/webhooks/other/update')]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_operation({'method': method, 'endpoint': path}, self.config)

    def test_webhook_url_cannot_be_overridden_and_existing_registration_is_reused(self):
        operation = {'method': 'POST', 'endpoint': '/api/v1/webhooks/hook-1/update',
                     'json_data': {'url': 'https://example.com'}}
        self.assertEqual(validate_operation(operation, self.config)[3], {'url': WEBHOOK_URL})
        with patch('app.services.airwallex_api.requests.post') as post:
            result = execute_operation({'method': 'POST', 'endpoint': '/api/v1/webhooks/create',
                                        'json_data': {'request_id': 'stable'}}, self.config)
            self.assertEqual(result['id'], 'hook-1')
            post.assert_not_called()

    def test_expired_token_retries_read_with_fresh_authentication(self):
        login = Mock()
        login.json.return_value = {'token': 'fresh'}
        expired, settled = Mock(status_code=401), Mock(status_code=200)
        settled.json.return_value = {'id': 'deposit-1', 'status': 'SETTLED'}
        with patch('app.services.airwallex_api.requests.post', return_value=login) as post, \
             patch('app.services.airwallex_api.requests.request', side_effect=[expired, settled]) as request:
            result = execute_operation({'endpoint': '/api/v1/deposits/deposit-1'}, self.config)
        self.assertEqual(result['status'], 'SETTLED')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(request.call_count, 2)


if __name__ == '__main__':
    unittest.main()
