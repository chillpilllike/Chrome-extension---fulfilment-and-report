# ePost shipment operations

The ePost page is a work queue for imported EPG shipments, not a list of confirmed losses. It starts on **Needs attention**. Queue totals cover the selected store across every page; search narrows the table, not the sidebar totals. A shipment has one carrier-evidence queue. Refund queues are separate and can overlap delivery queues.

| Queue | Evidence | Next step |
| --- | --- | --- |
| Not checked yet | No carrier status or recorded check | Tracking team checks the carrier |
| Tracking not found | Portal lookup error | Fulfilment team verifies code, carrier and handover |
| Awaiting carrier scan | Checked blank, electronic/manifest record, or transit to ePost processing center | Fulfilment team verifies physical handover |
| Movement stalled | Physical-movement status with a dated event older than the chosen threshold | Carrier support investigates |
| Delivery exceptions | Failed attempt, return, damage, customs/KYC or other recognized exception | Customer support follows the specific carrier instruction |
| Carrier-reported loss | Explicit loss in the carrier message | Verify evidence and review customer resolution approval |
| Status needs review | Unrecognized or expected-event message | Tracking team reads the carrier record |
| In transit | Physical-movement message not beyond threshold, or without usable date | Monitor and verify missing dates |
| Delivered | Positive delivery message with no negation | No action unless receipt is disputed |

The default stalled threshold is ten days. Age is measured from the carrier event, never the import timestamp. Invalid/future dates have no computed age. Date-only/offset-free carrier timestamps use UTC for age calculations, while the UI preserves the supplied text and does not assert a carrier timezone. The current snapshot is classified; this is not a full event-history possession audit.

The old stored `lost` flag is no longer evidence of loss. Existing periodic status refresh reclassifies stored records using the same snapshot classifier. `lost` is reserved for an explicit carrier report, `suspected_lost` for stalled physical movement, and exceptions/lookup errors remain separate. No notification or refund is triggered by classification.

## Team workflow

1. Select a store and a work queue. Search an order/code if needed. Adjust the stalled threshold without silently switching queues.
2. Review the carrier message and last event, then use **Review shipment** for links, suggested next action, shipping charges and claim history.
3. Use **Import & carrier checks** to distinguish Odoo code import from carrier scanning. Odoo imports do not promise direct Shopify coverage. Orders without an imported code require the related fulfilment workflow.
4. Copy up to 25 explicitly selected visible codes into the carrier portal and use the existing ePost extension to save scans. Reload saved results afterwards. Selecting every matching record applies to export, not a scan.
5. Open After-order care for customer decisions and approvals. A suggested team is guidance, not a stored assignee.
6. Record a submitted carrier claim only after actually submitting it externally. Record refund received only after payment. These actions require confirmation and do not submit claims, issue customer payments, or send emails.

Exports use the same queue filtering as the page and include work queue, suggested team and next action. Selection is cleared when the store, queue, search or threshold changes. Carrier scan failures remain visible rather than being called lost.

## Validation

`tests/test_epost_workflow.py` covers blank/lookup cases, labels, processing-center messages, negated delivery, exceptions, explicit loss, date boundaries, invalid dates, refunds and exclusive queues. No production shipment is modified by these tests.
