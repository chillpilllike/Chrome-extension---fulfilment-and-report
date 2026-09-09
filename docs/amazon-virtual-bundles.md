# Amazon virtual bundles

Purchase identity and shipment identity are separate. Keep the original bundle
ASIN and quantity on the app/Odoo line. Amazon may report its physical components
on order history and on different packages. Do not convert a component into an
unrequested replacement or create another purchase to repair reporting.

Fulfilment 0.1.198 captures only Amazon's explicit
`bundleComponentDetails_feature_div`, after exact parent ASIN verification and
before adding the authorized parent to the cart. The claimed worker saves the
parent, component ASINs, per-bundle quantities, source URL and verification time
through `/api/chrome/jobs/{group_key}/bundle-components`. Evidence is persisted in
`app_settings` under `amazon_bundle:<ASIN>`. Existing compositions are immutable;
a changed component list is held for review. Unreadable/unrecognized bundles
remain subject to exact ASIN reporting guards. Recommendations and recipient
names never establish bundle composition.

The shared catalog is loaded by history, matching, completion and tracking APIs.
It also supplies component metadata to subsequent jobs. It does not change cart,
checkout, account-type routing or the authorized purchase ASIN.

History reconciliation requires the complete component ASIN set and expected
quantities. Existing conflicting IDs and cancelled/completed fulfilments retain
their protections. Fulfilment and tracking clients now send ASIN/item evidence
with manual history sync; older clients can use cached history evidence.

Tracking maps physical packages by component ASIN, merges separate scans, and
requires all components before reporting the parent delivered. Order-inferred
product lists cannot establish shipment contents. Multi-unit quantities require
explicit quantity evidence; ambiguous splits across separate Amazon order IDs
remain blocked for manual reconciliation. Tracking 0.1.76 includes the 0.1.75
account-identity protections already present in the local installed release.

NC26628 was independently verified in Gurdev Chrome on 2026-09-09:
Amazon order `111-5640817-7945804`, parent `B0D51GVTKS`, one each of
`B00ENS39XK` and `B00F7OZJQE`. Amazon's product page explicitly lists those
components and warns they may ship separately. This reviewed composition is
seeded in the shared catalog so an already-placed order can be reconciled without
reopening or re-placing a fulfilment job.
