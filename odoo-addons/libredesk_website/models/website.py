import secrets
import re
from urllib.parse import urlsplit
import requests
from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError


def host(value):
    return (urlsplit(value or '').hostname or '').lower().removeprefix('www.')


class Inbox(models.Model):
    _name = 'libredesk.website.inbox'
    _description = 'LibreDesk widget catalog'
    name = fields.Char(required=True)
    remote_id = fields.Integer(required=True)
    uuid = fields.Char(required=True)
    website_url = fields.Char()
    enabled = fields.Boolean()
    signing_secret = fields.Char(groups='base.group_system', copy=False)
    _sql_constraints = [('remote_unique', 'unique(remote_id)', 'Inbox already synchronized.')]


class Website(models.Model):
    _inherit = 'website'
    libredesk_inbox_id = fields.Many2one('libredesk.website.inbox', groups='base.group_system')
    libredesk_enabled = fields.Boolean(groups='base.group_system')

    def _libredesk_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only administrators can configure LibreDesk.'))

    def _libredesk_api(self, method, path, payload=None):
        self._libredesk_admin()
        params = self.env['ir.config_parameter'].sudo()
        base = params.get_param('libredesk_website.base_url', '').rstrip('/')
        token = params.get_param('libredesk_website.api_token', '').strip()
        if not base.startswith('https://') or not host(base) or ':' not in token:
            raise UserError(_('Set the HTTPS LibreDesk URL and API token in key:secret format.'))
        try:
            response = requests.request(method, base + '/api/v1' + path,
                headers={'Authorization': 'token ' + token}, json=payload,
                timeout=25, allow_redirects=False)
            if not response.ok or response.is_redirect:
                raise UserError(_('LibreDesk request failed (HTTP %s).') % response.status_code)
            return response.json()['data']
        except (requests.RequestException, ValueError, KeyError):
            raise UserError(_('LibreDesk could not be reached or returned an invalid response.')) from None

    def action_libredesk_sync(self):
        self._libredesk_admin()
        catalog = self.env['libredesk.website.inbox']
        rows = self._libredesk_api('GET', '/inboxes')
        seen = []
        for row in rows:
            if row['channel'] != 'livechat':
                continue
            record = catalog.search([('remote_id', '=', row['id'])], limit=1)
            vals = dict(name=row['name'], remote_id=row['id'], uuid=row['uuid'],
                        website_url=row.get('config', {}).get('website_url'), enabled=row['enabled'])
            if record:
                record.write(vals)
            else:
                record = catalog.create(vals)
            seen.append(record.id)
        catalog.search([('id', 'not in', seen)]).write({'enabled': False})
        for site in self.env['website'].search([]):
            matches = catalog.search([('enabled', '=', True)]).filtered(
                lambda x: host(x.website_url) and host(x.website_url) == host(site.domain))
            if not site.libredesk_inbox_id and len(matches) == 1:
                site.libredesk_inbox_id = matches
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_libredesk_connect(self):
        self._libredesk_admin()
        for site in self:
            inbox = site.libredesk_inbox_id
            if not inbox or not inbox.enabled or not host(site.domain):
                raise UserError(_('Select an enabled widget and set the website domain first.'))
            other = self.search([('libredesk_inbox_id', '=', inbox.id), ('id', '!=', site.id), ('libredesk_enabled', '=', True)])
            if other:
                raise UserError(_('This widget is already enabled on another website. Select a separate channel.'))
            remote = site._libredesk_api('GET', '/inboxes/%s' % inbox.remote_id)
            if remote['uuid'] != inbox.uuid or remote['channel'] != 'livechat':
                raise UserError(_('Widget identity changed. Refresh the catalog.'))
            secret = inbox.signing_secret or secrets.token_urlsafe(32)
            domain = host(site.domain)
            config = remote['config']
            config.update(brand_name=site.name, website_url=site.domain,
                          logo_url=site.domain.rstrip('/') + '/web/image/website/%s/logo' % site.id,
                          show_powered_by=False, trusted_domains=[domain, 'www.' + domain])
            config.setdefault('launcher', {})['logo_url'] = config['logo_url']
            emails = site._libredesk_api('GET', '/inboxes')
            matching_emails = [x for x in emails if x['channel'] == 'email' and x['enabled'] and
                re.search(r'@' + re.escape(domain) + r'(?:>|$)', (x.get('config', {}).get('from') or x.get('from') or '').strip(), re.I)]
            remote['linked_email_inbox_id'] = matching_emails[0]['id'] if len(matching_emails) == 1 else None
            config.setdefault('continuity', {'offline_threshold': '10m', 'max_messages_per_email': 10, 'min_email_interval': '30m'})
            for kind in ('users', 'visitors'):
                config.setdefault(kind, {})['prevent_reply_to_closed_conversation'] = True
            remote.update(secret=secret, csat_enabled=True)
            site._libredesk_api('PUT', '/inboxes/%s' % inbox.remote_id, remote)
            inbox.signing_secret = secret
            # Remove our earlier static installation, so the addon is the sole loader.
            for field in ('custom_code_head', 'custom_code_footer'):
                original = site[field] or ''
                cleaned = re.sub(r'<!-- (?:libredesk-website|secretgreen-official-support) -->.*?<!-- /(?:libredesk-website|secretgreen-official-support) -->', '', original, flags=re.S)
                if cleaned != original:
                    site[field] = cleaned
            site.libredesk_enabled = True
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class Settings(models.TransientModel):
    _inherit = 'res.config.settings'
    libredesk_base_url = fields.Char(config_parameter='libredesk_website.base_url', default='https://libredesk.185.194.236.161.sslip.io', groups='base.group_system')
    libredesk_api_token = fields.Char(config_parameter='libredesk_website.api_token', groups='base.group_system')
    libredesk_inbox_id = fields.Many2one(related='website_id.libredesk_inbox_id', readonly=False, groups='base.group_system')
    libredesk_enabled = fields.Boolean(related='website_id.libredesk_enabled', readonly=False, groups='base.group_system')

    def action_libredesk_sync(self):
        self.ensure_one()
        self.execute()
        return self.website_id.action_libredesk_sync()

    def action_libredesk_connect(self):
        self.ensure_one()
        self.execute()
        return self.website_id.action_libredesk_connect()
