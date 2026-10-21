# DTC tracking links in Odoo

The existing Shopify tracking sync now sends structured source parcel tracking
to delivery_epg 18.0.1.7.0 and newer. Upgrade the addon first. The source remains
Shopify fulfilment trackingInfo, matched only through SRC_ODOO_DB and
SRC_ODOO_ORDER tags. Tracking numbers, carrier names and safe HTTP(S) URLs remain
paired per fulfillment, supporting multiple parcels and URL corrections.

The purchased delivery method and existing picking notes are preserved. Returns,
internal transfers, cancelled pickings and unsuccessful source fulfillments are
not assigned outbound tracking. Existing completed deliveries can receive URLs
without validating a new shipment. On instances without the new field the sync
retains its compatible number/note behavior; customer buttons require the addon.

Fetch windows apply to the order's updated_at, so later tracking changes on old
fulfillments are not discarded. Partially fulfilled orders are also fetched.
The existing scheduled tracking job handles future updates; rerun a suitable
updated-order date range with validate_deliveries=false to backfill links.

Validation: 15 tracking tests passed locally, including full mocked dry-run,
backfill, repeated reconciliation and URL-correction runs; 84 shipping addon
checks passed in an isolated real Odoo database, including customer QWeb output.
