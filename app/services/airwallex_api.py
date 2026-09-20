"""Restricted Airwallex operations executed only by the fulfilment server."""
import re
import threading
import time

import requests

WEBHOOK_URL = 'https://fulfilment.gofinch.com/api/airwallex/webhook'
_tokens = {}
_lock = threading.Lock()


def validate_operation(operation, config):
    method = str(operation.get('method', 'GET')).upper()
    endpoint = str(operation.get('endpoint', ''))
    data = operation.get('json_data')
    params = operation.get('params')
    if method == 'GET' and re.fullmatch(r'/api/v1/(deposits|global_accounts)(/[A-Za-z0-9-]+)?', endpoint):
        if params is not None and not isinstance(params, dict):
            raise ValueError('Invalid query parameters')
        return method, endpoint, params, None
    if method == 'POST' and endpoint == '/api/v1/webhooks/create':
        if not isinstance(data, dict) or not data.get('request_id'):
            raise ValueError('Webhook request ID required')
        return method, endpoint, None, {
            'request_id': str(data['request_id'])[:100],
            'url': WEBHOOK_URL,
            'version': config.get('api_version') or '2026-02-27',
            'events': ['deposit.pending', 'deposit.rejected', 'deposit.reversed', 'deposit.settled'],
        }
    webhook_id = config.get('webhook_id')
    if method == 'POST' and webhook_id and endpoint == f'/api/v1/webhooks/{webhook_id}/update':
        return method, endpoint, None, {'url': WEBHOOK_URL}
    raise ValueError('This Airwallex operation is not allowed')


def execute_operation(operation, config):
    method, endpoint, params, data = validate_operation(operation, config)
    # A shared registration already exists: reuse it instead of creating a duplicate.
    if endpoint == '/api/v1/webhooks/create' and config.get('webhook_id'):
        return {'id': config['webhook_id'], 'secret': config['secret']}
    return server_request(method, endpoint, config, params=params, data=data)


def server_request(method, endpoint, config, *, params=None, data=None):
    """Private transport. Callers must enforce their own operation and staff guards."""
    base = 'https://api.sandbox.airwallex.com' if config['state'] == 'test' else 'https://api.airwallex.com'
    identity = (base, config['client_id'], config.get('account_id', ''), config['api_key'])
    for attempt in range(2):
        with _lock:
            token, expiry = _tokens.get(identity, ('', 0))
            if not token or expiry <= time.monotonic():
                headers = {'x-client-id': config['client_id'], 'x-api-key': config['api_key'],
                           'Content-Type': 'application/json'}
                if config.get('account_id'):
                    headers['x-login-as'] = config['account_id']
                response = requests.post(base + '/api/v1/authentication/login', headers=headers, timeout=20)
                response.raise_for_status()
                token = response.json()['token']
                _tokens[identity] = (token, time.monotonic() + 240)
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        if config.get('api_version'):
            headers['x-api-version'] = config['api_version']
        response = requests.request(method, base + endpoint, params=params, json=data, headers=headers, timeout=30)
        if response.status_code == 401 and attempt == 0:
            with _lock:
                _tokens.pop(identity, None)
            continue
        response.raise_for_status()
        return response.json() if response.content else {}
    raise RuntimeError('Airwallex authentication failed')
