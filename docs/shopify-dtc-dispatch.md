# Shopify DTC dispatch notifications

Version: `dtc-dispatch-v1`.

The independent one-minute monitor polls the configured DTC Shopify destination,
not the separate DTB/ePost tracking source. It paginates updated orders with a
five-minute overlap and handles successful fulfilments of partially shipped
orders. The `dtc` export ledger and original-order tags must agree before an
Odoo order is selected. Generic Shopify customer details are never recipients.

On first startup `after_order_shopify_dispatch_started_at` establishes an activation
boundary. Older fulfilments do not generate a customer backlog. Both the original
order cutoff and existing site guards still apply. Do not backdate this setting
without a separate, reviewed backfill plan.

Each successful fulfilment gets one `shopify_dispatch` case and one logical email
reservation. Multiple tracking entries appear together. Separate fulfilments get
separate notices. No item-to-parcel association is invented. No carrier movement,
delivery or lost-package state is inferred from a Shopify fulfilment.

Before a live send the app rechecks Shopify SUCCESS status, cancellation, export
mapping, original-order tags, tracking numbers and HTTPS tracking URLs. Changed
evidence blocks the saved message. Invalid/private/admin tracking links are
rejected. The email also links to the original website's customer order page.

Email uses the shared branded, translated layout and existing translated dispatch
and tracking copy. SMS aliases the existing `package_movement` template mapping:
the approved fixed text is unchanged, and its tracking-details (`url`) variable
contains the tracking number and carrier URL. No duplicate MSG91 templates need
registration. Active website language selection, approved English fallback,
provider approval checks and fixed sender names are preserved. Other providers
use the shared translated tracking SMS copy.

Only this new notification type gains the automatic-send exception. Existing
action, refund and payment approval rules are unchanged. Test mode routes email
to `sonianuj1284@gmail.com` and SMS to `+19296526393`. FinchKart remains disabled.
Tracking opt-outs, suppression, website SMS enablement and phone validation apply.

Recovery resumes unattempted reservations and missing companion SMS, not ambiguous
or failed provider attempts. Those remain in the existing logs for safe retry.
Email and SMS histories remain separate, linked to the new case timeline. Read-only
health: `GET /api/after-order/shopify-dispatch/status` (normal app authentication).

Validation: unit tests cover evidence, URLs, split tracking, date boundary,
fingerprints, cancelled orders, wrong mappings, polling pagination and recovery,
automatic permission boundaries, existing SMS localization reuse/English fallback,
test-number enforcement and duplicate prevention. Real Shopify read-only queries
were verified against the configured DTC store before deployment.
