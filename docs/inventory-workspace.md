# Inventory queues and movement history

Inventory now reuses the ePost workspace styling and provides store-scoped counts,
server-side search and pagination for Active, Available, Awaiting release,
Reserved/to send and Archived. Search is literal and includes archive reasons.
The authoritative PostgreSQL list avoids stale search-index allocation visibility.

## Archive rules

- `used` items remain `used`, but are visible in Archived with their sent reason.
- Empty `incoming` stock from the exact confirmed cancelled Amazon purchase is
  archived only without inventory or matching package delivery/receipt evidence.
  Manual, received, positive-quantity and reserved stock are not auto-archived.
- Reserved inventory automatically archives when the linked destination order is
  `fulfilled` in Shopify, without requiring physical dispatch confirmation. This
  matches the reserved order's store (which may differ from the stock's store).
  Partial/unfulfilled states do not trigger it. The reason records the Shopify
  fulfilment date, or the status observation date if Shopify supplies no date.
  Existing reservations are reconciled at deployment; later Shopify status syncs
  trigger the same rule. Archived allocations remain deducted from source stock.
- Manual Archive requires a reason and explicitly removes the recorded stock
  from allocation. Reserved stock is rejected. Quantities and source information
  are retained, not deleted. This release does not provide automatic restoration.
- Sync preserves `archived` status and quantity so archived stock cannot silently
  become available. Existing receipt, expiry and allocation guards remain intact.
- Cancellation processing rechecks this purchase's incoming inventory immediately.

## Timeline

`inventory_movements` captures inserts and meaningful updates in the same database
transaction as the stock change, including background sync and split allocations.
Updates to `updated_at` alone produce no duplicate event. Each event preserves the
before/after record, timestamp and reason. The UI includes the source and its child
allocations, with the target Odoo order where still available.

Old records get an explicitly labelled baseline when history is enabled, not a
fabricated historical event. Saved delivery/receipt timestamps remain visible.
Already consumed records and safely cancelled incoming rows are reconciled on
startup. The migration is repeatable and does not erase existing events.

## Validation

Run `tests.test_inventory_history` with `INVENTORY_TEST_DSN` pointing to a disposable
local PostgreSQL database. Tests cover archive guards, reasons, repeatable backfill,
timeline linkage, stock splitting, consumed quantity, filtering and pagination.
Existing allocation, manual fulfilment and tracking-cancellation tests remain in use.
