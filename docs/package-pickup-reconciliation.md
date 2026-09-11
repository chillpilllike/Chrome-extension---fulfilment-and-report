# Mixed Amazon shipments and pickup readiness

An Amazon order can contain both replacement components and other products in
separate shipments. Exact, unrelated shipment ASINs no longer reject a batch
containing a proven replacement shipment. Missing/inferred evidence and wholly
unrelated replacement updates remain rejected. Explicit ASIN mismatches cannot
use the legacy one-line fallback or become implicit replacement products.

Additional shipments under a uniquely linked Amazon order remain visible with
no assigned order-line IDs. They require item reconciliation; they never fulfil
an unrelated item. Existing package ownership and item assignments are retained.

Pickup readiness counts physical packages separately from unresolved order lines.
Unresolved lines and packages without item mappings keep the order on hold.
An unlinked package cannot claim that an entire Odoo order is ready. History
displays current readiness while retaining the stored scan-time evidence.
When an orphan scan's existing package gains an exact order link, its labels are
repaired without changing its timestamp, undo state, duplicate status or counts.

Repeated ASIN lines may share a discovered purchase only when they belong to the
same customer order and verified Amazon quantities equal the complete selected
group's required quantity. Conflicting purchases and unverified quantities remain
blocked. Existing replacement ASIN authorization is unchanged.

Regression case NC26541 covers one captured parcel plus five line placeholders,
mixed shipment rejection, 5+2 glucosamine quantities, orphan scan relinking,
unexpected probiotic ASINs, and idempotent preservation of physical scans.

Validation: 193 relevant tests, 179 passed and 14 environment-dependent skips;
TypeScript and Vite production build passed. No extension update is required.
