"""Idempotent Odoo annotations for confirmed app refunds, independent of email delivery."""
from html import escape


def annotate_refund(client, row):
    order_id = int(row['order_id'])
    orders = client.read('sale.order', [order_id], ['name', 'website_id'])
    if len(orders) != 1 or orders[0]['name'] != row['order_name']:
        raise ValueError('Refund order identity does not match Odoo.')
    website = orders[0].get('website_id')
    expected = getattr(client.store, 'website_id', None)
    if expected and (not website or int(website[0]) != int(expected)):
        raise ValueError('Refund order website does not match Odoo.')
    model = client.execute('sale.order', 'fields_get', [['tag_ids']], {'attributes': ['relation']})['tag_ids']['relation']
    tags = client.search_read(model, [('name', '=ilike', 'Refunded')], ['id'], limit=1)
    tag_id = tags[0]['id'] if tags else client.execute(model, 'create', [{'name': 'Refunded'}])
    client.write('sale.order', [order_id], {'tag_ids': [(4, tag_id)]})
    marker = 'Airwallex refund transfer: ' + row['transfer_id']
    notes = client.search_read('mail.message', [('model', '=', 'sale.order'), ('res_id', '=', order_id), ('body', 'ilike', marker)], ['id'], limit=1)
    if not notes:
        body = '<p>Refunded through Airwallex.</p><p>Refund amount: ' + escape(str(row['amount']) + ' ' + row['currency']) + '</p><p>' + escape(marker) + '</p><p>Status: Paid. This note records this refund amount; it does not imply the entire order was refunded.</p>'
        client.execute('sale.order', 'message_post', [[order_id]], {'body': body, 'body_is_html': True, 'message_type': 'comment', 'subtype_xmlid': 'mail.mt_note', 'partner_ids': [], 'context': {'mail_notify_force_send': False, 'mail_post_autofollow': False}})


def sync_paid_refunds(service):
    with service.db() as guard:
        if not guard.execute('SELECT pg_try_advisory_xact_lock(781905438) AS locked').fetchone()['locked']:
            return
        rows = guard.execute("SELECT p.* FROM airwallex_refund_payouts p LEFT JOIN airwallex_refund_odoo o USING(request_id) WHERE p.status='PAID' AND p.notify_customer=1 AND COALESCE(o.state,'')!='done' ORDER BY p.created_at LIMIT 30").fetchall()
        for saved in rows:
            row = dict(saved)
            try:
                annotate_refund(service.client_factory(service.get_store(int(row['store_id']))), row)
                state, error = 'done', ''
            except Exception:
                state, error = 'retry', 'Could not verify or update the Odoo refund tag and internal note. Will retry.'
            guard.execute('INSERT INTO airwallex_refund_odoo(request_id,state,last_error) VALUES(?,?,?) ON CONFLICT(request_id) DO UPDATE SET state=excluded.state,last_error=excluded.last_error', (row['request_id'], state, error))
