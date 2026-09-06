{
    'name': 'LibreDesk Website Chat',
    'summary': 'Official branded chat, domain matching and server-signed customer identity',
    'version': '18.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['website', 'portal'],
    'external_dependencies': {'python': ['requests']},
    'data': ['security/ir.model.access.csv', 'views/settings.xml', 'views/widget.xml'],
    'assets': {'web.assets_frontend': ['libredesk_website/static/src/widget.js']},
    'installable': True,
}
