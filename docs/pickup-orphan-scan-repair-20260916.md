# Pickup scan and placeholder reconciliation

NC24586 had four delivered parcels and seven accepted scan events. Five scan events for the first two parcels pointed to deleted package IDs 51069 and 52712 instead of current IDs 56669 and 56701. A stale item-only "Arriving Monday" snapshot was also counted alongside TBA334595546738, inflating the total to five. Shopify recorded full fulfilment on 15 September.

The repair relinks only exact physical barcodes within the same store, Amazon order and Odoo order, where the old package is absent and the new match is unique. Original scan timestamps and daily pickup counts are preserved. Undo and later explicit non-receipt records block restoration. Normal scan-history and tracking reconciliation now run this repair automatically.

Item-only placeholders without a shared shipment/item ID may be superseded only when authoritative order-product evidence confirms exactly one purchased unit of the exact ASIN and there is exactly one physical candidate in that Amazon order. Unknown quantities, multiple units and ambiguous physical packages stay separate. Tracking updates canonicalize these snapshots before persisting status; dispatch rebuilds and reads use the same evidence.

Scan history now distinguishes full Shopify fulfilment from warehouse scan evidence. Fully fulfilled orders show "Already fulfilled in Shopify"; partial or cancelled fulfilments do not suppress a hold. Actual receipt counts are never fabricated from Shopify status.

Validation: 54 targeted tests pass, frontend build passes, and a replay of all NC24586 records in isolated PostgreSQL returns 4/4 received, fully fulfilled, unchanged historical scan times and idempotent repair. The broader suite's five existing baseline failures remain unchanged after adapting SQL test fixtures.

Audit found three proven stale aliases (NC24586, NC24567, NC23567) and five orphan accepted scan events (all NC24586). Backups and live verification are retained in /private/tmp/nc24586-* files.
