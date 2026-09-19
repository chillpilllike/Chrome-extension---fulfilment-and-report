# USPS routing-prefix pickup matching

NC27691 has two parcels under Amazon purchase 114-1893985-0445053. Both were delivered September 16. September 17 event 553 matched TBA334608917114 at 15:37:23 UTC. Event 558, thirty seconds later, scanned `420112349361289691068393847592` and was incorrectly rejected as not found: the stored USPS tracking number is `9361289691068393847592`.

Normalize only the recognized concatenated USPS format: domestic routing AI 420, five- or nine-digit ZIP, then a full 22-digit tracking number beginning with 9. Preserve arbitrary identifiers and require the resulting complete tracking number to match exactly. This shared normalizer serves new hardware scans, manual matching and existing scan reconciliation. Saved raw historical scans remain untouched; reconciliation preserves their original timestamp/error and counts a receipt only once, respecting undo, delivery date and store constraints.

Format reference: https://pe.usps.com/text/dmm300/204.htm (postal routing / concatenated barcode standards).

Validation: 97 pickup, shared-parcel and routing tests passed (one optional PostgreSQL test skipped). Tests include ZIP5/ZIP9, malformed/unrelated codes, reconciliation of original scan evidence and no duplicate increment. Shopify already reports NC27691 fulfilled September 17; this repair must not send it to Shopify again.
