# Team operations workspaces — UI-only redesign

## Analysis and boundaries

The four pages already have distinct business responsibilities, but their controls did not clearly communicate those boundaries:

| Page | Existing confusion | UI treatment |
| --- | --- | --- |
| Orders | Import stores, working-store scope, purchase setup, row actions and destructive corrections were mixed together. | Working store and supported queue shortcuts first; separate filter bar and selected-line action panel; collapsible import/setup and correction tools. The current account, environment and ship-to remain visible next to selection actions. |
| After-order care | Daily case review shared a toolbar with automation and email settings. The resolved total included approved cases. | Case queues first, persistent test/live indicator, separate administrative tools, accurate “Approved / resolved” label and readable cards without nested scrolling. |
| Package Pickup Check | Amazon delivery and physical team receipt can be mistaken for one another; exact search was hidden. | Two existing package views exposed as queues, visible exact search, scope/match counts, clear scan-received action and empty-state guidance. |
| Package Tracker | Delivery progress, Shopify status and blocked verification checks need different responses. | Supported work queues, visible date/sort controls, cross-store scope and status-sync explanation, links to the other workspaces. |

Queue shortcuts call existing filter setters with existing option values. All original filter options remain available. No API payloads, effects, backend classifications, scanner matching, confirmations, action conditions or execution rules were changed.

## How the team should work

1. **Orders:** choose the working store, select a queue, review matching lines and select the intended rows. Confirm the purchase account/environment and ship-to address before placing. Use import/setup tools for pulling Odoo data or store-wide operations. Corrections are grouped separately.
2. **Pickup:** choose store and delivery dates. Scan only physically received packages. Reconcile missing/extra counts on each date card. Search with a complete order or tracking number; it is not a partial-text search. The existing Brooklyn scan-date rule still applies.
3. **Tracker:** find the order across stores or choose a follow-up queue. Inspect missing packages, quantities, recipient/product guards and history. Shopify sync refreshes status; it does not itself dispatch an order.
4. **After-order care:** review confirmation, attention, execution-pending or tracking queues. Read the latest customer decision and execution status before using the case's existing actions. Approval is not execution completion. Keep testing/automation configuration separate from daily case review.

## Existing limitations left unchanged

- After-order care requests only the first 50 cases and has no pagination controls. The UI now exposes that limit rather than implying the whole result set is displayed. Pagination requires a separate functional change.
- After-order search applies through Search or the existing queue/store reload; typing alone does not trigger a request.
- Pickup summary totals describe the selected date/store scope, not necessarily the narrowed exact-search subset.
- Package Tracker is cross-store; the global working-store selection does not filter its request.
- No first carrier scan is not automatically a lost shipment. Existing risk classifications and care-action gates remain unchanged.

## Verification

- Production frontend build (`tsc -b && vite build`).
- Source contract comparison against the pre-edit App.tsx: existing event/disabled/checked bindings retained, API/fetch/confirmation calls identical, effects identical.
- `scripts/check-team-ui-contract.cjs` accepts a before and after TSX file and uses the project's TypeScript dependency. It does not access a server or execute any business action.
- Local Vite route and module compilation checks. No live scanning, purchasing, dispatch, refunds, email, automation changes or browser interaction tests were run.

This release contains only the UI changes for the existing application. Unfinished local automation controls and execution-state changes are excluded; production keeps its existing email settings and case-action rules. Hosting is unchanged.
