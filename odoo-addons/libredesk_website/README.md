# LibreDesk Website Chat (Odoo 18)

Install `libredesk_website` in your Odoo addons path, restart Odoo, update the Apps list, and install **LibreDesk Website Chat**. This is a Python server addon, not an Odoo Studio/import-data module.

In Website → Configuration → Settings, choose the website and open **LibreDesk chat**. Enter your HTTPS LibreDesk instance URL and one API token field in `key:secret` format. Click **Save and match widgets**. Enabled native chat channels are fetched from the official API and uniquely matched by website hostname (www normalized). Unmatched or ambiguous sites retain a dropdown for manual selection. Existing manual selections are preserved. A distinct channel is required per website.

Click **Connect selected widget** for each website. This applies its brand, website logo URL, domain allowlist, CSAT, hidden powered-by footer, and closed-conversation reply restrictions. The enabled email inbox matching that exact domain becomes its continuity inbox. Missing/disabled/ambiguous email matches are left unlinked rather than routed across brands. Email credentials are managed in LibreDesk, not copied into this addon.

The API returns masked widget signing keys. On first connection this addon provisions a new server-side key unless a system administrator has supplied the existing key on the catalog record. Existing signed sessions for that channel may need to sign in again; any separate service that signs JWTs for this inbox must use the same key. The prior Secretgreen service has a saved runtime signing key: preserve it when connecting that existing channel. Subsequent reconnects reuse the saved key.

The official widget loads on the current website only. Logged-in Odoo accounts receive a one-hour HS256 JWT generated from the server session; anonymous visitors remain anonymous. No request-supplied identity is accepted. Secrets/API tokens never enter website markup or frontend assets. Native verified order tools remain separately scoped; this addon does not extend Secretgreen's order tools to other brands.

Channel names containing domains distinguish Nutricity sites for staff. LibreDesk's native visited-pages history supplies page URLs. The addon uses supported identity claims only and does not send full customer profiles, purchase data or internal notes to the widget.

Connecting removes this project's previous marked static widget snippet to prevent duplicates. Other independently installed chat scripts must be reviewed by the site administrator. Disabling the checkbox stops addon loading without deleting the channel or conversations.

Validation in this workspace covers module XML/Python/JavaScript syntax, host matching and JWT signatures/expiry. Installation and signed login on a running Odoo 18 server require deployment of the Python addon to that server's addons path; an API token alone cannot install Python server code remotely.
