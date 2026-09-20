"""Staff-only, order-capped Airwallex payouts. No payout access through the Odoo proxy."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ConfigDict

from app.services.airwallex_api import server_request
from app.services.airwallex_hub import verify_webhook_signature

ZERO = Decimal('0')
# Failed transfers can fail after being paid. Keep funds reserved until finance reconciles.
RELEASED = {'CANCELLED', 'CANCELED'}
UUID_RE = re.compile(r'^[a-zA-Z0-9-]{1,100}$')
SCHEMA_KEYS = {'bank_country_code', 'account_currency', 'entity_type', 'transfer_method',
               'local_clearing_system', 'country_code', 'beneficiary_type'}


def money(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Invalid monetary amount.')
    if not result.is_finite() or abs(result) > Decimal('1000000000'):
        raise ValueError('Invalid monetary amount.')
    return result


def amount_guard(value, remaining, rounding):
    value, remaining, step = money(value), money(remaining), money(rounding)
    if step <= 0 or value <= 0 or value % step:
        raise ValueError('Enter a positive amount using the currency’s supported precision.')
    if value > remaining:
        raise ValueError('Refund exceeds the remaining refundable order value.')
    return value


def whole_reference(reference, name):
    return bool(re.search(r'(?<![A-Z0-9])' + re.escape(name.upper()) + r'(?![A-Z0-9])',
                          str(reference or '').upper()))


def refund_totals(name, currency, transfers, local):
    """Deduplicate API/local entries; never sum a different currency without evidence."""
    total = ZERO
    rows, ids, request_ids = [], set(), set()
    for t in transfers:
        if not whole_reference(' '.join(str(t.get(k) or '') for k in ('reference', 'remarks')), name):
            continue
        if t['id'] in ids or (t.get('request_id') and t['request_id'] in request_ids):
            continue
        ids.add(t['id'])
        request_ids.add(t.get('request_id'))
        amount = money(t['transfer_amount'])
        status = str(t.get('status') or 'UNKNOWN').upper()
        rows.append({'id': t['id'], 'amount': str(amount), 'currency': t['transfer_currency'],
                     'status': status, 'reference': t.get('reference'), 'created_at': t.get('created_at'),
                     'source': 'Airwallex'})
        if status in RELEASED:
            continue
        if t['transfer_currency'] != currency or amount < 0:
            raise ValueError('An existing transfer uses a different currency. Finance reconciliation is required.')
        total += amount
    for t in local:
        if t.get('transfer_id') in ids or t['request_id'] in request_ids:
            continue
        if t['status'] not in RELEASED:
            if t['currency'] != currency:
                raise ValueError('An existing refund uses a different currency. Finance reconciliation is required.')
            total += money(t['amount'])
        rows.append({'id': t['request_id'], 'amount': str(t['amount']), 'currency': t['currency'],
                     'status': t['status'], 'reference': t['order_name'], 'created_at': t['created_at'],
                     'source': 'App'})
    return total, rows


def daily_refund_usage(local, transfers, now=None):
    """Count submitted attempts, deduplicating app reservations and tagged external refunds."""
    now = now or datetime.now(timezone.utc)
    zone = ZoneInfo('Asia/Kolkata')
    day = now.astimezone(zone).date()
    start = datetime.combine(day, datetime.min.time(), zone)
    end = start + timedelta(days=1)
    ids, requests, count = set(), set(), 0
    def today(value):
        try:
            stamp = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', str(value).replace('Z', '+00:00'))
            timestamp = datetime.fromisoformat(stamp)
            if timestamp.tzinfo is None:
                raise ValueError()
            return start <= timestamp < end
        except (ValueError, TypeError):
            raise ValueError('A refund date cannot be verified. Finance reconciliation is required.') from None
    for row in local:
        ids.add(row.get('transfer_id'))
        requests.add(row['request_id'])
        if today(row['created_at']):
            count += 1
    for row in transfers:
        if (row.get('id') and row['id'] in ids) or (row.get('request_id') and row['request_id'] in requests):
            continue
        reference = ' '.join(str(row.get(k) or '') for k in ('reference', 'remarks'))
        if re.search(r'\brefund\b', reference, re.I) and today(row.get('created_at')):
            count += 1
            ids.add(row.get('id'))
            if row.get('request_id'):
                requests.add(row['request_id'])
    return {'limit': 5, 'used': count, 'remaining': max(0, 5-count),
            'timezone': 'Asia/Kolkata', 'date': str(day), 'resets_at': end.isoformat()}


def set_path(target, path, value):
    parts = path.split('.')
    if any(not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', p) or p.startswith('__') for p in parts):
        raise ValueError('Invalid recipient field.')
    node = target
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    store_id: int = Field(gt=0)
    order_id: int = Field(gt=0)
    amount: str = Field(max_length=30)
    fields: dict[str, str] = Field(default_factory=dict)
    edit_acknowledged: bool = False
    edit_reason: str = Field(default='', max_length=500)
    recipient_confirmed: bool = False
    other_refunds_checked: bool = False


class SubmitInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    review_token: str = Field(max_length=30000)
    confirmed: bool = False


class AirwallexRefunds:
    def __init__(self, *, db, get_store, client_factory, configuration, staff_check, list_stores=None):
        self.db, self.get_store, self.client_factory = db, get_store, client_factory
        self.configuration, self.staff_check = configuration, staff_check
        self.list_stores = list_stores or (lambda: [])
        self.last_history_sync = 0

    def init_db(self):
        with self.db() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS airwallex_refund_payouts (
                request_id TEXT PRIMARY KEY, order_key TEXT NOT NULL, store_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL, order_name TEXT NOT NULL, account_key TEXT NOT NULL,
                amount NUMERIC(20,6) NOT NULL CHECK(amount > 0), currency TEXT NOT NULL,
                status TEXT NOT NULL, transfer_id TEXT UNIQUE, recipient TEXT NOT NULL,
                edit_reason TEXT NOT NULL DEFAULT '', payload_hash TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT '',
                fee_amount TEXT, fee_currency TEXT, actor TEXT NOT NULL DEFAULT 'staff')''')
            c.execute('''CREATE TABLE IF NOT EXISTS airwallex_refund_external_history (
                account_key TEXT NOT NULL, transfer_id TEXT NOT NULL, payload TEXT NOT NULL,
                synced_at TEXT NOT NULL, PRIMARY KEY(account_key,transfer_id))''')
            c.execute('CREATE INDEX IF NOT EXISTS airwallex_refund_order_idx ON airwallex_refund_payouts(order_key)')
            c.execute('''CREATE TABLE IF NOT EXISTS airwallex_refund_webhook (
                account_key TEXT PRIMARY KEY, webhook_id TEXT NOT NULL, secret TEXT NOT NULL)''')

    def auth(self, request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        if not self.staff_check(request):
            raise HTTPException(403, 'Administrator access required.')
        if request.method != 'GET':
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                raise HTTPException(403, 'Cross-origin refund requests are not allowed.')

    def config(self):
        cfg = self.configuration()
        if not cfg or not cfg.get('client_id') or not cfg.get('api_key'):
            raise ValueError('The central Airwallex connection is not configured.')
        if cfg.get('state') != 'enabled':
            raise ValueError('The shared Airwallex account is not enabled for live payouts.')
        return cfg

    @staticmethod
    def account_key(cfg):
        return hashlib.sha256((cfg['client_id'] + ':' + (cfg.get('account_id') or '')).encode()).hexdigest()

    @staticmethod
    def cipher(cfg):
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('refund-review-v1:' + cfg['api_key']).encode()).digest()))

    def call(self, method, path, *, data=None, params=None):
        # Only this staff service can use these endpoints, never the public Odoo operation proxy.
        allowed = (method == 'GET' and (path in {'/api/v1/transfers', '/api/v1/balances/current'}
                   or re.fullmatch(r'/api/v1/(transfers|deposits)/[A-Za-z0-9-]+', path))) or (
            method == 'POST' and path in {'/api/v1/beneficiary_form_schemas/generate',
                                         '/api/v1/beneficiaries/validate', '/api/v1/transfers/validate',
                                         '/api/v1/transfers/create'})
        if not allowed:
            raise ValueError('Unsupported refund API operation.')
        try:
            return server_request(method, path, self.config(), params=params, data=data)
        except requests.HTTPError as exc:
            response = exc.response
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            # Never return raw payloads: upstream errors can echo bank or credential data.
            code = re.sub(r'[^A-Za-z0-9_]', '', str(payload.get('code', 'upstream_error')))[:80]
            fields = [re.sub(r'[^a-zA-Z0-9_.]', '', str(e.get('source', '')))[:100]
                      for e in payload.get('details', {}).get('errors', [])[:20]]
            suffix = ': ' + ', '.join(fields) if any(fields) else ''
            raise ValueError(f'Airwallex {response.status_code} ({code}){suffix}') from None
        except requests.RequestException:
            raise ValueError('Airwallex could not be reached. Refresh status before retrying.') from None

    def transfers(self, request_id=None):
        page, seen, rows = '0', set(), []
        for _ in range(100):
            params = {'page_size': 500, 'page': page}
            if request_id:
                params['request_id'] = request_id
            response = self.call('GET', '/api/v1/transfers', params=params)
            if not isinstance(response.get('items'), list):
                raise ValueError('Airwallex transfer history is unavailable.')
            rows.extend(response['items'])
            page = response.get('page_after')
            if not page:
                return rows
            if page in seen:
                break
            seen.add(page)
        raise ValueError('Complete transfer history could not be checked. Refunds are blocked.')

    def daily_usage(self, transfers=None, now=None):
        with self.db() as c:
            local = c.execute('SELECT request_id,transfer_id,created_at FROM airwallex_refund_payouts').fetchall()
        return daily_refund_usage(local, self.transfers() if transfers is None else transfers, now)

    @staticmethod
    def daily_guard(usage):
        if usage['remaining'] <= 0:
            raise ValueError('The daily limit of 5 refunds has been reached across all stores. Resets at midnight Asia/Kolkata.')

    def order_client(self, store_id):
        store = self.get_store(store_id)
        return store, self.client_factory(store)

    @staticmethod
    def order_key(store, order_id):
        # Same Odoo database registered more than once still shares one refund cap and lock.
        return hashlib.sha256(f'{store.odoo_url.rstrip("/")}:{store.odoo_db}:{order_id}'.encode()).hexdigest()

    def search(self, store_id, query):
        store, client = self.order_client(store_id)
        if len(query.strip()) < 2:
            return []
        domain = [('name', 'ilike', query.strip()[:80])]
        if store.website_id:
            domain.append(('website_id', '=', store.website_id))
        return client.search_read('sale.order', domain,
            ['name', 'partner_id', 'amount_total', 'currency_id', 'state'], limit=25, order='id desc')

    @staticmethod
    def customer_matches(client, order, partner):
        if not partner:
            return False
        if partner[0] == order['partner_id'][0]:
            return True
        # Odoo checkout transactions may belong to the customer's invoice contact.
        if not order.get('partner_invoice_id') or partner[0] != order['partner_invoice_id'][0]:
            return False
        contacts = client.search_read('res.partner', [('id', 'in', [partner[0], order['partner_id'][0]])],
                                      ['commercial_partner_id'])
        roots = {p['commercial_partner_id'][0] for p in contacts if p.get('commercial_partner_id')}
        return len(contacts) == 2 and len(roots) == 1 and all(p.get('commercial_partner_id') for p in contacts)

    def snapshot(self, store_id, order_id):
        store, client = self.order_client(store_id)
        domain = [('id', '=', order_id)]
        if store.website_id:
            domain.append(('website_id', '=', store.website_id))
        orders = client.search_read('sale.order', domain,
            ['name', 'amount_total', 'currency_id', 'partner_id', 'partner_invoice_id', 'invoice_ids', 'state', 'date_order'])
        if len(orders) != 1:
            raise ValueError('Order does not belong to the selected store.')
        order = orders[0]
        order_currency = order['currency_id'][1]
        total = money(order['amount_total'])
        if total <= 0:
            raise ValueError('The order has no positive refundable value.')
        fields = client.execute('payment.transaction', 'fields_get', [], {'attributes': ['type']})
        tx_fields = ['amount', 'currency_id', 'state', 'operation', 'sale_order_ids', 'provider_code', 'provider_id', 'partner_id', 'reference']
        tx_fields += [k for k in ('airwallex_deposit_id', 'airwallex_payment_amount',
                                  'airwallex_payment_currency_id', 'payment_method_id') if k in fields]
        txs = client.search_read('payment.transaction', [('sale_order_ids', 'in', [order_id])], tx_fields)
        # Include refund child transactions even when Odoo did not copy sale_order_ids.
        if txs and 'source_transaction_id' in fields:
            children = client.search_read('payment.transaction', [('source_transaction_id', 'in', [t['id'] for t in txs])], tx_fields)
            txs = list({t['id']: t for t in [*txs, *children]}.values())
        invoices = client.search_read('account.move', [('id', 'in', order['invoice_ids'])],
            ['move_type', 'state', 'payment_state', 'amount_total', 'amount_residual', 'currency_id', 'partner_id']) if order['invoice_ids'] else []
        if order['invoice_ids']:
            reversals = client.search_read('account.move', [('reversed_entry_id', 'in', order['invoice_ids']),
                ('move_type', '=', 'out_refund'), ('state', '=', 'posted')],
                ['move_type', 'state', 'payment_state', 'amount_total', 'amount_residual', 'currency_id', 'partner_id'])
            invoices = list({i['id']: i for i in [*invoices, *reversals]}.values())
        posted = [i for i in invoices if i['state'] == 'posted']
        if any(i['currency_id'][1] != order_currency for i in posted):
            raise ValueError('Mixed invoice currencies require finance reconciliation.')
        credits = sum((money(i['amount_total']) for i in posted if i['move_type'] == 'out_refund'), ZERO)
        payments, provider_refunds, currencies = [], ZERO, set()
        seen_deposits = set()
        payment_matches = []
        for t in txs:
            is_refund = t.get('operation') == 'refund' or money(t['amount']) < 0
            if is_refund and t['state'] not in {'cancel', 'error', 'draft'}:
                if t['currency_id'][1] != order_currency:
                    raise ValueError('A provider refund uses another currency. Finance review is required.')
                provider_refunds += abs(money(t['amount']))
            elif t['state'] == 'done' and not is_refund:
                if t.get('sale_order_ids') != [order_id]:
                    raise ValueError('A payment covers multiple orders. Finance allocation is required.')
                if not self.customer_matches(client, order, t.get('partner_id')):
                    raise ValueError('The payment customer does not match the order customer. Finance review is required.')
                amount, currency = money(t['amount']), t['currency_id'][1]
                if currency != order_currency:
                    raise ValueError('The payment currency does not match its Odoo order.')
                if amount <= 0:
                    raise ValueError('A completed payment has an invalid amount.')
                collected = amount
                match = {'transaction': t.get('reference') or str(t['id']),
                         'provider': t['provider_id'][1] if t.get('provider_id') else t['provider_code'],
                         'method': t['payment_method_id'][1] if t.get('payment_method_id') else '',
                         'customer': t['partner_id'][1], 'customer_matched': True, 'order_matched': True}
                if t.get('provider_code') == 'airwallex_transfer':
                    deposit_id = t.get('airwallex_deposit_id')
                    if not deposit_id or not UUID_RE.fullmatch(deposit_id):
                        raise ValueError('The settled Airwallex deposit has not been linked to this payment.')
                    if deposit_id in seen_deposits:
                        raise ValueError('A deposit is linked to more than one transaction. Finance review is required.')
                    seen_deposits.add(deposit_id)
                    deposit = self.call('GET', '/api/v1/deposits/' + deposit_id)
                    if deposit.get('status') != 'SETTLED':
                        raise ValueError('The original Airwallex deposit is not settled.')
                    if not whole_reference(deposit.get('reference'), order['name']):
                        raise ValueError('The Airwallex deposit reference does not match this order. Finance review is required.')
                    reused = client.search_read('payment.transaction', [('airwallex_deposit_id', '=', deposit_id), ('id', '!=', t['id'])], ['id'])
                    if reused:
                        raise ValueError('The Airwallex deposit is linked to another payment. Finance review is required.')
                    match.update(deposit_id=deposit_id, deposit_reference=deposit.get('reference'),
                                 payer=(deposit.get('payer') or {}).get('name') or deposit.get('payer_name') or '',
                                 deposit_status='SETTLED')
                    payment_currency = t.get('airwallex_payment_currency_id')
                    currency = payment_currency[1] if payment_currency else currency
                    collected = money(t.get('airwallex_payment_amount') if payment_currency else amount)
                    if deposit.get('currency') != currency or money(deposit['amount']) != collected:
                        raise ValueError('The settled deposit does not match the locked payment amount.')
                match.update(amount=str(collected), currency=currency)
                payment_matches.append(match)
                payments.append((amount, collected))
                currencies.add(currency)
        if len(currencies) > 1:
            raise ValueError('Payments in multiple currencies need finance allocation.')
        currency = next(iter(currencies), order_currency)
        currency_record = client.search_read('res.currency', [('name', '=', currency)], ['rounding'])
        if len(currency_record) != 1:
            raise ValueError('Currency precision is unavailable.')
        rounding = money(currency_record[0]['rounding'])
        if payments:
            original_paid = sum((p[0] for p in payments), ZERO)
            collected_paid = sum((p[1] for p in payments), ZERO)
            rates = {p[1] / p[0] for p in payments if p[0] > 0}
            if len(rates) != 1:
                raise ValueError('Payments with different conversion rates need finance allocation.')
            rate = next(iter(rates))
            cap = min(total, original_paid) * rate
            evidence = 'Completed Odoo payment' + (' + settled Airwallex deposit' if any(t.get('provider_code') == 'airwallex_transfer' for t in txs) else '')
        else:
            # A posted invoice is not itself evidence of payment; only fully paid invoices qualify.
            paid_invoices = [i for i in posted if i['move_type'] == 'out_invoice' and i['payment_state'] == 'paid' and money(i['amount_residual']) == 0]
            if not paid_invoices:
                raise ValueError('No completed payment or fully paid invoice could be verified in Odoo.')
            if any(not self.customer_matches(client, order, i.get('partner_id')) for i in paid_invoices):
                raise ValueError('The paid invoice customer does not match the order customer.')
            # Avoid allocating a multi-order invoice to just this order.
            invoice_ids = [i['id'] for i in paid_invoices]
            related = client.search_read('sale.order', [('invoice_ids', 'in', invoice_ids)], ['id'])
            if any(o['id'] != order_id for o in related):
                raise ValueError('A paid invoice covers multiple orders. Finance allocation is required.')
            invoice_fields = client.execute('account.move', 'fields_get', [], {'attributes': ['type']})
            methods = set()
            if 'invoice_payments_widget' in invoice_fields:
                for inv in client.search_read('account.move', [('id', 'in', invoice_ids)], ['invoice_payments_widget']):
                    widget = inv.get('invoice_payments_widget') or {}
                    if isinstance(widget, str):
                        widget = json.loads(widget) or {}
                    for payment in widget.get('content', []):
                        label = payment.get('payment_method_name') or payment.get('journal_name')
                        if label:
                            methods.add(label)
            payment_matches.append({'provider': ' / '.join(sorted(methods)) or 'Original payment method unavailable in Odoo',
                                    'method': 'Paid invoice', 'customer': order['partner_id'][1],
                                    'customer_matched': True, 'order_matched': True, 'transaction': 'Invoice payment'})
            collected_paid = sum((money(i['amount_total']) for i in paid_invoices), ZERO)
            cap, rate, evidence = min(total, collected_paid), Decimal(1), 'Fully paid Odoo invoice'
        key = self.order_key(store, order_id)
        with self.db() as c:
            local = c.execute('SELECT * FROM airwallex_refund_payouts WHERE order_key=?', (key,)).fetchall()
        transfers = self.transfers()
        used, history = refund_totals(order['name'], currency, transfers, local)
        # Conservative: credit notes and external provider refunds may overlap; do not guess.
        accounting_deduction = (credits + provider_refunds) * rate
        remaining = max(ZERO, cap - used - accounting_deduction)
        remaining = (remaining / rounding).to_integral_value(rounding=ROUND_DOWN) * rounding
        partner = client.search_read('res.partner', [('id', '=', order['partner_id'][0])],
            ['name', 'email', 'street', 'street2', 'city', 'zip', 'country_id', 'state_id'])[0]
        country = client.search_read('res.country', [('id', '=', partner['country_id'][0])], ['code'])[0]['code'] if partner['country_id'] else ''
        state = client.search_read('res.country.state', [('id', '=', partner['state_id'][0])], ['code'])[0]['code'] if partner['state_id'] else ''
        return {'store_id': store_id, 'order_id': order_id, 'order_key': key, 'order_name': order['name'],
                'customer': partner['name'], 'order_value': str(total), 'order_currency': order_currency,
                'order_equivalent': str(total * rate), 'conversion_rate': str(rate),
                'payment_matches': payment_matches,
                'currency': currency, 'paid': str(collected_paid), 'cap': str(cap), 'refunded_reserved': str(used),
                'accounting_deduction': str(accounting_deduction), 'remaining': str(remaining),
                'rounding': str(rounding), 'evidence': evidence, 'history': history,
                'defaults': {'beneficiary.entity_type': 'PERSONAL', 'beneficiary.type': 'BANK_ACCOUNT',
                    'beneficiary.bank_details.bank_country_code': country, 'beneficiary.bank_details.account_currency': currency,
                    'beneficiary.bank_details.account_name': partner['name'], 'beneficiary.address.country_code': country,
                    'beneficiary.address.street_address': ' '.join(filter(None, [partner['street'], partner['street2']])),
                    'beneficiary.address.city': partner['city'] or '', 'beneficiary.address.postcode': partner['zip'] or '',
                    'beneficiary.address.state': f'{country}-{state}' if country == 'CA' and state else state,
                    'beneficiary.additional_info.personal_email': partner['email'] or ''}}

    def schema(self, params):
        params = {k: str(v)[:80] for k, v in params.items() if k in SCHEMA_KEYS and v}
        return self.call('POST', '/api/v1/beneficiary_form_schemas/generate', data=params)

    def prepare(self, form):
        if not form.recipient_confirmed or not form.other_refunds_checked:
            raise ValueError('Confirm recipient details and check for refunds through other providers first.')
        self.daily_guard(self.daily_usage())
        snapshot = self.snapshot(form.store_id, form.order_id)
        amount = amount_guard(form.amount, snapshot['remaining'], snapshot['rounding'])
        if amount != money(snapshot['remaining']) and (not form.edit_acknowledged or not form.edit_reason.strip()):
            raise ValueError('Acknowledge the Edit cost warning and enter a reason for a partial refund.')
        values = dict(form.fields)
        values['beneficiary.bank_details.account_currency'] = snapshot['currency']
        schema_params = {k: values.get(path) for k, path in {
            'bank_country_code': 'beneficiary.bank_details.bank_country_code', 'account_currency': 'beneficiary.bank_details.account_currency',
            'entity_type': 'beneficiary.entity_type', 'transfer_method': 'transfer_method',
            'local_clearing_system': 'beneficiary.bank_details.local_clearing_system',
            'country_code': 'beneficiary.address.country_code', 'beneficiary_type': 'beneficiary.type'}.items()}
        if not schema_params['transfer_method'] or not schema_params['bank_country_code']:
            raise ValueError('Select the recipient country and supported transfer method.')
        schema = self.schema(schema_params)
        recipient = {}
        for item in schema.get('fields', []):
            path, field = item.get('path', ''), item.get('field', {})
            if not path.startswith('beneficiary.'):
                continue
            value = (values.get(path, str(field.get('default') or '')) if item.get('enabled', True)
                     else str(field.get('default') or ''))[:500]
            if item.get('required') and not value.strip():
                raise ValueError(f"Required recipient field: {field.get('label') or path}")
            if value:
                set_path(recipient, path, value.strip())
        if recipient.get('beneficiary', {}).get('type') != 'BANK_ACCOUNT':
            raise ValueError('This refund page supports bank accounts and their local clearing methods.')
        beneficiary = recipient.get('beneficiary', {})
        self.call('POST', '/api/v1/beneficiaries/validate', data={'beneficiary': beneficiary, 'transfer_methods': [schema_params['transfer_method']]})
        request_id = str(uuid.uuid4())
        transfer = {'request_id': request_id, 'reference': 'Refund ' + snapshot['order_name'],
                    'reason': 'other_services', 'remarks': 'Customer order refund', 'beneficiary': beneficiary,
                    'source_currency': snapshot['currency'], 'transfer_currency': snapshot['currency'],
                    'transfer_amount': float(amount), 'transfer_method': schema_params['transfer_method'], 'fee_paid_by': 'PAYER'}
        if transfer['transfer_method'] == 'SWIFT':
            transfer['swift_charge_option'] = 'OUR'
        self.call('POST', '/api/v1/transfers/validate', data=transfer)
        balances = self.call('GET', '/api/v1/balances/current')
        available = sum((money(b['available_amount']) for b in balances if b.get('currency') == snapshot['currency']), ZERO)
        if available < amount:
            raise ValueError('Insufficient available balance in the refund currency. Fund that currency before refunding.')
        cfg = self.config()
        payload = {'store_id': form.store_id, 'order_id': form.order_id, 'order_key': snapshot['order_key'],
                   'order_name': snapshot['order_name'], 'transfer': transfer, 'edit_reason': form.edit_reason,
                   'account_key': self.account_key(cfg), 'amount': str(amount)}
        token = self.cipher(cfg).encrypt(json.dumps(payload, sort_keys=True).encode()).decode()
        bank = beneficiary.get('bank_details', {})
        destination = bank.get('iban') or bank.get('account_number') or bank.get('account_routing_value1') or ''
        return {'review_token': token, 'order_name': snapshot['order_name'], 'amount': str(amount),
                'currency': snapshot['currency'], 'recipient': bank.get('account_name', ''),
                'destination': (bank.get('account_routing_value1', '') if bank.get('local_clearing_system') == 'INTERAC'
                                else '••••' + destination[-4:]), 'method': schema_params.get('local_clearing_system') or transfer['transfer_method'],
                'available_balance': str(available), 'fees': 'Airwallex fees are additional and paid by the business. Exact fees are recorded after submission.',
                'expires_in_seconds': 600}

    def submit(self, token, actor='staff'):
        cfg = self.config()
        try:
            payload = json.loads(self.cipher(cfg).decrypt(token.encode(), ttl=600))
        except (InvalidToken, ValueError):
            raise ValueError('Review has expired or changed. Review the refund again.') from None
        transfer = payload['transfer']
        rid, key = transfer['request_id'], payload['order_key']
        fingerprint = hashlib.sha256(json.dumps(transfer, sort_keys=True).encode()).hexdigest()
        if payload['account_key'] != self.account_key(cfg):
            raise ValueError('Airwallex account changed. Review again.')
        # Lock across processes and duplicate store registrations. Commit reservation BEFORE API call.
        with self.db() as c:
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(?, 0))', ('airwallex-refund-global-daily-limit',))
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(?, 0))', (key,))
            existing = c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (rid,)).fetchone()
            if existing:
                return self.public_row(existing)
            fresh = self.snapshot(payload['store_id'], payload['order_id'])
            if fresh['order_key'] != key or fresh['currency'] != transfer['transfer_currency']:
                raise ValueError('Order currency or identity changed. Review again.')
            amount_guard(payload['amount'], fresh['remaining'], fresh['rounding'])
            self.call('POST', '/api/v1/transfers/validate', data=transfer)
            balances = self.call('GET', '/api/v1/balances/current')
            available = sum((money(b['available_amount']) for b in balances if b.get('currency') == fresh['currency']), ZERO)
            if available < money(payload['amount']):
                raise ValueError('Insufficient balance in the refund currency. No transfer was submitted.')
            submitted_at = datetime.now(timezone.utc)
            self.daily_guard(self.daily_usage(now=submitted_at))
            now = submitted_at.isoformat()
            recipient = transfer['beneficiary']['bank_details'].get('account_name', '')
            c.execute('''INSERT INTO airwallex_refund_payouts
                (request_id,order_key,store_id,order_id,order_name,account_key,amount,currency,status,
                 recipient,edit_reason,payload_hash,created_at,updated_at,actor)
                VALUES (?,?,?,?,?,?,?,?,'SUBMITTING',?,?,?,?,?,?)''',
                (rid,key,payload['store_id'],payload['order_id'],fresh['order_name'],payload['account_key'],
                 payload['amount'],fresh['currency'],recipient,payload['edit_reason'],fingerprint,now,now,actor))
        try:
            result = self.call('POST', '/api/v1/transfers/create', data=transfer)
            self.record_result(rid, result)
        except Exception:
            # Do not automatically resend. Even HTTP errors can have an ambiguous result.
            with self.db() as c:
                c.execute("UPDATE airwallex_refund_payouts SET status='UNKNOWN',last_error=?,updated_at=? WHERE request_id=?",
                          ('Submission outcome requires reconciliation. Refresh status; do not create a replacement refund.',
                           datetime.now(timezone.utc).isoformat(), rid))
        with self.db() as c:
            row = c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (rid,)).fetchone()
        return self.public_row(row)

    def record_result(self, rid, result):
        with self.db() as c:
            row = c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (rid,)).fetchone()
            if not row:
                return
            if (result.get('request_id') != rid or money(result.get('transfer_amount')) != money(row['amount'])
                or result.get('transfer_currency') != row['currency'] or not result.get('id')):
                raise ValueError('Transfer reconciliation mismatch.')
            c.execute('''UPDATE airwallex_refund_payouts SET status=?,transfer_id=?,updated_at=?,last_error='',
                         fee_amount=?,fee_currency=? WHERE request_id=?''',
                      (result.get('status') or 'UNKNOWN',result['id'],datetime.now(timezone.utc).isoformat(),
                       str(result.get('fee_amount', '')),result.get('fee_currency'),rid))

    @staticmethod
    def public_row(row):
        return {k: str(v) if isinstance(v, Decimal) else v for k,v in dict(row).items()
                if k not in {'payload_hash','account_key','order_key'}}

    def refresh(self, rid):
        with self.db() as c:
            row = c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (rid,)).fetchone()
        if not row:
            raise ValueError('Refund request was not found.')
        if row['account_key'] != self.account_key(self.config()):
            raise ValueError('Refund belongs to another Airwallex account.')
        matches = self.transfers(request_id=rid)
        matches = [t for t in matches if t.get('request_id') == rid]
        if len(matches) == 1:
            self.record_result(rid, matches[0])
        with self.db() as c:
            return self.public_row(c.execute('SELECT * FROM airwallex_refund_payouts WHERE request_id=?', (rid,)).fetchone())

    def sync_history(self):
        # Import sanitized history without changing provider transfers or Odoo.
        transfers = self.transfers()
        refunds = {}
        names = set()
        for t in transfers:
            reference = str(t.get('reference') or '')
            remarks = str(t.get('remarks') or '')
            if not re.search(r'\brefund\b', reference + ' ' + remarks, re.I):
                continue
            match = re.search(r'\brefund\s+([A-Z0-9][A-Z0-9/_-]*)', reference, re.I)
            name = match.group(1).upper() if match else ''
            refunds[t['id']] = (t, name)
            if name:
                names.add(name)
        groups = {}
        for record in self.list_stores():
            store = self.get_store(record['id'])
            groups.setdefault((store.odoo_url.rstrip('/'), store.odoo_db), []).append((record, store))
        matches = {name: {} for name in names}
        failed = False
        for stores in groups.values():
            if not names:
                break
            try:
                client = self.client_factory(stores[0][1])
                orders = client.search_read('sale.order', [('name', 'in', sorted(names))],
                    ['name', 'partner_id', 'website_id', 'amount_total', 'currency_id'])
                for order in orders:
                    name = order['name'].upper()
                    if name not in matches:
                        continue
                    eligible = [(record, store) for record, store in stores
                                if not store.website_id or (order.get('website_id') and store.website_id == order['website_id'][0])]
                    # Orders on an unconfigured website still belong in history.
                    # Keep their database/order identity, but do not bypass store access when opening.
                    # Prefer an exact website registration over a database-wide registration.
                    record, store = sorted(eligible or stores, key=lambda pair: (not bool(pair[1].website_id), pair[0]['id']))[0]
                    key = self.order_key(store, order['id'])
                    matches[name][key] = {'store_id': record['id'] if eligible else None,
                        'store_name': record['name'] if eligible else (order.get('website_id') or [0, 'Unconfigured website'])[1],
                        'mapping_note': '' if eligible else 'Order matched; website is not configured in this app.',
                        'order_id': order['id'], 'customer': order['partner_id'][1],
                        'order_value': str(order['amount_total']), 'order_currency': order['currency_id'][1]}
            except Exception:
                failed = True  # Never guess a unique mapping when another database could not be checked.
        account = self.account_key(self.config())
        now = datetime.now(timezone.utc).isoformat()
        mapped = 0
        with self.db() as c:
            for tid, (t, name) in refunds.items():
                candidates = list(matches.get(name, {}).values())
                state = 'unavailable' if failed else 'matched' if len(candidates) == 1 else 'ambiguous' if candidates else 'not_found'
                bank = (t.get('beneficiary') or {}).get('bank_details') or {}
                row = {'request_id': t.get('request_id') or tid, 'transfer_id': tid,
                       'order_name': name or t.get('reference') or 'Unidentified order',
                       'reference': t.get('reference') or '', 'amount': str(money(t['transfer_amount'])),
                       'currency': t['transfer_currency'], 'status': t.get('status') or 'UNKNOWN',
                       'recipient': bank.get('account_name') or '', 'created_at': t.get('created_at'),
                       'fee_amount': str(t.get('fee_amount', '')), 'fee_currency': t.get('fee_currency'),
                       'source': 'Manual Airwallex', 'mapping_status': state, 'last_error': ''}
                if state == 'matched':
                    row.update(candidates[0]); mapped += 1
                c.execute('''INSERT INTO airwallex_refund_external_history(account_key,transfer_id,payload,synced_at)
                    VALUES (?,?,?,?) ON CONFLICT(account_key,transfer_id) DO UPDATE SET
                    payload=excluded.payload,synced_at=excluded.synced_at''', (account, tid, json.dumps(row), now))
        self.last_history_sync = time.monotonic()
        return {'total': len(refunds), 'mapped': mapped, 'unresolved': len(refunds)-mapped, 'synced_at': now}

    def history(self):
        account = self.account_key(self.config())
        with self.db() as c:
            local = c.execute('SELECT * FROM airwallex_refund_payouts WHERE account_key=? ORDER BY created_at DESC', (account,)).fetchall()
            external = c.execute('SELECT payload,synced_at FROM airwallex_refund_external_history WHERE account_key=?', (account,)).fetchall()
        ids = {row.get('transfer_id') for row in local if row.get('transfer_id')}
        requests = {row['request_id'] for row in local}
        rows = [{**self.public_row(row), 'source': 'App', 'mapping_status': 'matched'} for row in local]
        for item in external:
            row = json.loads(item['payload'])
            if row['transfer_id'] not in ids and row['request_id'] not in requests:
                rows.append(row)
                ids.add(row['transfer_id']); requests.add(row['request_id'])
        rows.sort(key=lambda row: str(row.get('created_at') or ''), reverse=True)
        return {'rows': rows, 'synced_at': max((r['synced_at'] for r in external), default=None)}

    def verify_transfer_webhook(self, timestamp, signature, raw_body):
        try:
            with self.db() as c:
                row = c.execute('SELECT secret FROM airwallex_refund_webhook WHERE account_key=?',
                    (self.account_key(self.config()),)).fetchone()
            return bool(row and verify_webhook_signature(timestamp=timestamp, signature=signature,
                raw_body=raw_body, secrets=[row['secret']]))
        except Exception:
            return False

    def handle_webhook(self, payload):
        data = payload.get('data') or {}
        if not isinstance(data, dict):
            return
        with self.db() as c:
            row = c.execute('SELECT request_id FROM airwallex_refund_payouts WHERE request_id=? OR transfer_id=?',
                (str(data.get('request_id') or ''), str(data.get('id') or ''))).fetchone()
        if row:
            # Always re-read current provider status; out-of-order webhook data cannot regress it.
            self.refresh(row['request_id'])

    def poll(self):
        with self.db() as c:
            rows = c.execute("SELECT request_id FROM airwallex_refund_payouts WHERE status NOT IN ('CANCELLED','CANCELED') ORDER BY updated_at LIMIT 30").fetchall()
        for row in rows:
            try:
                self.refresh(row['request_id'])
            except Exception:
                pass  # Reservations stay in force on failed reconciliation; never retry a payout here.

    def loop(self):
        while True:
            try:
                self.init_db()
                self.poll()
                if time.monotonic() - self.last_history_sync > 300:
                    self.sync_history()
            except Exception:
                pass
            time.sleep(60)

    def router(self):
        r = APIRouter(prefix='/api/airwallex/refunds', dependencies=[Depends(self.auth)])
        def guarded(fn, *args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(503, 'Refund verification is unavailable. Check refund history and transfer status before retrying.') from None
        @r.get('/connection')
        def connection():
            guarded(self.config)
            guarded(self.call, 'GET', '/api/v1/balances/current')
            return {'connected': True, 'account': 'Shared Airwallex account'}
        @r.get('/limits')
        def limits():
            return guarded(self.daily_usage)
        @r.get('/orders')
        def orders(store_id: int, q: str = ''):
            return {'rows': guarded(self.search, store_id, q)}
        @r.get('/orders/{store_id}/{order_id}')
        def order(store_id: int, order_id: int):
            return guarded(self.snapshot, store_id, order_id)
        @r.post('/schema')
        def schema(params: dict):
            return guarded(self.schema, params)
        @r.post('/review')
        def review(form: ReviewInput):
            return guarded(self.prepare, form)
        @r.post('/submit')
        def submit(form: SubmitInput, request: Request):
            if not form.confirmed:
                raise HTTPException(400, 'Confirm the reviewed refund first.')
            actor = hashlib.sha256((request.headers.get('x-admin-token') or request.cookies.get('admin_access_token') or 'staff').encode()).hexdigest()[:16]
            return guarded(self.submit, form.review_token, actor)
        @r.get('/history')
        def history():
            return guarded(self.history)
        @r.post('/history/sync')
        def sync_history():
            return guarded(self.sync_history)
        @r.post('/{request_id}/refresh')
        def refresh(request_id: str):
            return guarded(self.refresh, request_id)
        return r
