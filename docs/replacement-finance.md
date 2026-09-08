# Replacement finance — 2026-09-08

This release is guarded implementation, not approval to enable real payments or refunds.

## Cheaper replacement

The customer selection closes after 24 hours. The case waits for refund approval.
The team confirms the exact amount and selection version in the care timeline.
Odoo prepares a unique refund operation, a draft difference credit note and a child
payment transaction in a separate committed RPC, before any provider call.

Only a single captured Stripe payment with an unambiguous paid invoice, matching
currency, website and company, and matching simple percentage taxes is supported.
Reserved refunds count against the remaining payment balance. Each order line can
have only one replacement refund; changing operation keys cannot refund it again.

The provider request uses a persistent random idempotency key. Once a provider
refund ID is known, recovery only retrieves that refund. Unknown requests older
than 23 hours are blocked for manual provider reconciliation rather than resent.
Successful provider refunds are linked to a posted credit note and native outbound
payment. Fulfilment requires a posted payment journal entry and zero credit-note
residual, and rechecks the refund immediately before release. Errors appear in
the app timeline and Odoo chatter. No separate refund should be made while an
operation has an uncertain outcome.

## More expensive replacement

The existing Odoo difference quotation is created after the 24-hour deadline.
Fulfilment waits for verified payment, rejects authorization-only payments,
partial/overpayments, changed pricing, cancelled/expired quotes and reversals.
Payment is checked again immediately before replacing the ASIN and queuing the
order. An undecodable ASIN remains a manual-fulfilment task.

## Deployment and outstanding work

Upgrade the authoritative after_order_portal addon to 18.0.2.2.0. Both
`after_order_portal.live_refunds_enabled` and
`after_order_portal.live_alternatives_enabled` reset to false on upgrade. Keep app
email test mode enabled and automation disabled until isolated sandbox acceptance.

Still required: native Odoo install/upgrade plus sandbox Stripe capture/refund,
credit-note tax/account reconciliation, payment-to-ASIN queue and restart testing.
Offline provider doubles are not payment-provider end-to-end verification.

The custom Shopify payment bridge and other providers remain unsupported for
automatic refunds until their source/API and refund identifiers are verified.
Split payments, partial quantities, prior line credits, differing/compound taxes,
item-removal refunds and whole-order cancellation refunds remain review-only.
Post-fulfilment chargeback monitoring is not provided by this replacement worker.

Verification: 144 app after-order tests; 16 authoritative-addon offline tests;
TypeScript/Vite production build. No real payment or refund was executed.
