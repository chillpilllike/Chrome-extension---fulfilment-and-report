import base64
import hashlib
import hmac
import json
import time
from odoo import http
from odoo.http import request
from ..models.website import host


def sign(payload, secret):
    def enc(value):
        return base64.urlsafe_b64encode(json.dumps(value, separators=(',', ':')).encode()).rstrip(b'=')
    value = enc({'alg': 'HS256', 'typ': 'JWT'}) + b'.' + enc(payload)
    return (value + b'.' + base64.urlsafe_b64encode(hmac.new(secret.encode(), value, hashlib.sha256).digest()).rstrip(b'=')).decode()


class Chat(http.Controller):
    @http.route('/libredesk/widget-config', type='http', auth='public', website=True, methods=['GET'])
    def config(self):
        site = request.website.sudo()
        inbox = site.libredesk_inbox_id
        result = {}
        # Never use the fallback website's widget on an unrelated Host header.
        if site.libredesk_enabled and inbox.enabled and host('https://' + request.httprequest.host) == host(site.domain):
            base = request.env['ir.config_parameter'].sudo().get_param('libredesk_website.base_url', '').rstrip('/')
            if base.startswith('https://'):
                result = {'baseURL': base, 'inboxID': inbox.uuid}
                if not request.env.user._is_public() and inbox.signing_secret:
                    partner = request.env.user.partner_id
                    now = int(time.time())
                    # Derive identity only from the authenticated Odoo session, never request parameters.
                    result['userJWT'] = sign({'external_user_id': 'odoo:%s:%s:%s' % (request.db, site.id, partner.id),
                        'first_name': partner.name or '', 'email': partner.email or '',
                        'iat': now, 'exp': now + 3600}, inbox.signing_secret)
        return request.make_response(json.dumps(result), headers=[('Content-Type', 'application/json'),
            ('Cache-Control', 'no-store, private'), ('Vary', 'Cookie, Host'), ('X-Content-Type-Options', 'nosniff')])
