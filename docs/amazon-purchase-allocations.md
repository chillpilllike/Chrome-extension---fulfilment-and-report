# Multiple Amazon purchases for one Odoo line

Use **Amazon purchases / split quantity** on a selected Orders line, or **Manage purchases** beside its Amazon orders. Enter each already-placed Amazon order ID and its actual unit quantity. Saving does not place an Amazon order.

Each purchase has its own account, cost, tracking payload and delivery state. The original Odoo line and quantity are retained. Search, history matching, invoice discovery, tracking queues and dispatch rebuilds use all purchase IDs. The existing tracking extension API accepts the individual purchases without an extension release.

Partial allocations may be saved, but Shopify export is blocked until active purchased quantities equal demand. Dispatch readiness additionally requires complete ASIN/quantity delivery evidence and physical receipt scans for every package. A scan cannot complete an order while another allocated purchase is still missing. One purchase containing multiple units requires verified quantity evidence.

Existing IDs cannot silently be removed. Tracking-recorded cancellations retain history and free units for replacement purchases. Whole-line reset/replacement and inventory reassignment are blocked while allocations exist. Existing local-inventory allocations and virtual multipacks require reconciliation before using this action. Ordinary replacement ASIN lines are supported.

Purchase costs are optional. Total cost/profit remains unconfirmed until every purchase cost is supplied. Existing source values are retained in the allocation audit.

Validation: frontend TypeScript/Vite build; eight local PostgreSQL/pure rule tests including actual tracking/receipt API requests, independent four-purchase readiness, multi-unit evidence, incomplete Shopify coverage, item placeholder promotion, retries, cancellations, search and queue inclusion. The full suite ran 957 tests with 40 skipped and five failures reproduced on the pre-change baseline.
