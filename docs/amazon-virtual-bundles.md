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


## Homogeneous Amazon multi-packs (0.1.199)

NC26815 exposed a different representation: the authorized replacement and cart
ASIN B0BV67RXQH is a two-pack, but checkout uses B076F324JN in its hidden
Item_asin field while retaining the pack title, price and quantity. Three packs
represent six single units. This is not permission to substitute a variant.

The product discovery path requires Amazon's pack-count badge, BUNDLE offer,
selected parent ASIN, and a unique single-pack option with the identical size
base. The server validates and persists the immutable per-pack composition.
The cart must additionally mark that exact purchased parent as a homogeneous
multi-pack. Final checkout accepts the component representation only with a
fresh owner-job cart verification, exact title, pack price, pack quantity and
an unambiguous one-to-one row match. Unknown accounts and ambiguous mixed
parent/component purchases remain blocked. Ordinary exact-ASIN validation,
consumer subscription choices and Business payment handling retain their own
existing paths.

A paused older job can load its server-verified pack evidence on Resume and
return to the cart once to obtain fresh proof; no cart quantity or ASIN is
changed by this recovery. A second failed check pauses rather than looping.

History matching requires the full component quantity (six for NC26815) when
Amazon reports component ASINs. Tracking deduplicates shipment evidence and
requires all six verified units; pre-shipment estimates cannot count as item
evidence. If Amazon history does not expose component quantities, reporting
remains blocked for review rather than assuming three components means six.

Validation: 245 tests, including Consumer and Business checkout matching and
rejection cases; live read-only inspection of both product experiences and
the Gurdev cart/checkout. No test purchase was placed.
