# After-order Customer Portal

Install this addon once in every Odoo database that hosts customer websites. It automatically uses the active website selected from the request domain.

Set these Odoo system parameters after installation:

- `after_order_portal.api_base_url`: the fulfilment app public base URL, for example `https://fulfilment.gofinch.com`
- `after_order_portal.bridge_key`: the same secret as the fulfilment app's `AFTER_ORDER_BRIDGE_KEY`

Keep `AFTER_ORDER_WEBSITE_PORTAL_ENABLED=false` in the fulfilment app until the addon is installed and both parameters are saved. After a successful test on each website domain, set it to `true`. Until then, newly generated links continue using the existing safe fallback page.

The customer route is `/order-update/<opaque-token>`. The addon calls the fulfilment app server-to-server; the bridge key and hidden alternative-search terms are never sent to the browser.
