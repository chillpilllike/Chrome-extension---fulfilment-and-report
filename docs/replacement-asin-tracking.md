# Replacement ASIN purchase and shipment matching

Each replacement component has a real, stable app line ID. The original bundle link is grouping metadata, never purchase or shipment evidence.

| Stage | Required identity | Result |
| --- | --- | --- |
| Chrome job | Component line ID, authorized ASIN, requested quantity | All components stay in one claimed customer order |
| Purchase reporting | Recipient reference plus each history order's observed ASINs | Assign the Amazon order ID only to its matching component line |
| Tracking | Saved Amazon order ID, matching account, exact shipment ASIN | Attach the package tracking code only to matching components |
| Shared shipment | Shipment explicitly lists both ASINs | Both components may share the same tracking code |
| Split shipment | Distinct shipment identities for one ASIN | Preserve multiple tracking codes and merge repeat scans by shipment identity |

Example: original bundle X becomes A × 2 and B × 3. Amazon order 111 can contain A and order 222 can contain B. If both are in order 111, package TBA-A still updates only A and package TBA-B only B. One package explicitly listing A and B can legitimately update both.

The original bundle ASIN, product title, row position, and one remaining unmatched row are not evidence for assigning a replacement package. Order-wide product lists cannot prove the contents of one shipment. Missing shipment ASINs leave existing tracking intact and raise an actionable retry error on the app line and in the extension.

Purchase reports must supply `observed_asins` alongside each `asin`, `line_ids`, and `amazon_order_id` mapping. Reload fulfilment extension 0.1.197 or later before reporting replacement bundles. A rejected report must retry reporting, never place another order. The tracking extension's existing shipment payload supports these checks; this change does not require a new tracking extension package.

If the same replacement ASIN appears under multiple Amazon order IDs, automatic reporting pauses for manual quantity reconciliation. The current app stores one purchase order ID per component; it must not silently choose the first order or overwrite one purchase with another.

For a component quantity above one, automatic delivery requires verified package quantities covering the requested quantity. Otherwise the line remains at `ASIN quantity verification pending`. Scanning one delivered unit must not mark all requested units fulfilled.

Regression coverage includes separate/shared Amazon orders, swapped and conflicting mappings, partial/shared packages, original-ASIN rejection, missing/inferred evidence, split quantities, retry deduplication, and executable extension reporting logic. No test places a live Amazon order.
