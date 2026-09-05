# After-order Customer Portal

Install this addon once in every Odoo database that hosts customer websites. It automatically uses the active website selected from the request domain.

Set these Odoo system parameters after installation:

- `after_order_portal.api_base_url`: the fulfilment app public base URL, for example `https://fulfilment.gofinch.com`
- `after_order_portal.bridge_key`: the same secret as the fulfilment app's `AFTER_ORDER_BRIDGE_KEY`

Keep `AFTER_ORDER_WEBSITE_PORTAL_ENABLED=false` in the fulfilment app until the addon is installed and both parameters are saved. After a successful test on each website domain, set it to `true`. Until then, newly generated links continue using the existing safe fallback page.

The actions are embedded in Odoo's standard `/my/orders/<order_id>` preview, in an Order updates section below the normal order content. Eligible cases are discovered automatically using the Odoo database, order ID and website host. The standard Odoo order permission/access-token checks still apply.

Existing `/order-update/<opaque-token>` links redirect to that standard preview (through login when necessary). Submitting a choice returns to the same preview. The addon calls the fulfilment app server-to-server; the bridge key and hidden alternative-search terms are never sent to the browser.

When the fulfilment app is in email test mode, only signed-in Odoo Settings administrators (`base.group_system`) can see or submit test actions. Portal customers, anonymous visitors and ordinary internal staff cannot. The bridge verifies this server-side assertion after checking its shared key. Test links stay test-only even after test mode is disabled; live links also become non-mutating while test mode is enabled.

## Upgrade to 18.0.1.1.0

Replace the installed addon with the updated ZIP contents, restart the Odoo workers to load the Python controller, then upgrade **After-order Customer Portal** in Apps so its inherited order-preview view is loaded. Retain both existing system parameters. Uploading files or refreshing the Apps list alone is not enough.

Verify a post-cutoff order with an open after-order case as a Settings administrator. Then open the same preview as a portal customer or anonymously using its standard order access link: order details should remain available as permitted by Odoo, but the test section must be absent. Old test email links must be denied to non-admins. Keep global website email routing disabled until all relevant databases are upgraded and checked.
