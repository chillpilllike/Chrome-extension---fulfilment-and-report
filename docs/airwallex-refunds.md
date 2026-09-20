# Airwallex refund transfers

The staff page `/airwallex-refunds` returns customer funds through the existing central Airwallex account. These are outgoing transfers with `Refund <order number>` references, not Payment Acceptance refunds against payment intents. The existing public Odoo operation proxy still cannot create transfers.

## Workflow and controls

Select an Odoo store/order. The server verifies completed transactions (and settled deposits for Airwallex payments) or fully paid single-order invoices. It retrieves the complete Airwallex transfer history with cursor pagination starting at `page=0`; the API otherwise defaults to only 30 days. Full order-token matching includes historical manual and partial refunds.

The cap is the lesser of order value and verified paid value, minus recorded outgoing transfers, pending local reservations, Odoo refund transactions and posted credit notes. The original locked payment conversion is used if the receiving currency differed from the order currency. Mixed or ambiguous payment allocation blocks submission. Credit notes and provider refunds are deducted conservatively when their overlap cannot be established. Refunds elsewhere without a matching order reference must be reconciled in Odoo; staff must confirm this check.

Amount and currency are populated automatically. Edit cost requires a warning acknowledgement and reason and cannot override the server cap. Beneficiary fields and LOCAL/SWIFT/clearing choices come from Airwallex's current schema; Canadian Interac is supported. Only bank-account payouts are supported; digital/stablecoin wallets are excluded. The source and payout currency are the verified collected currency, so automatic funding conversions are not performed. Insufficient funds block submission.

Review calls beneficiary/transfer validation endpoints only. A ten-minute encrypted review token binds recipient, order, amount, account and a server-generated request ID. Recipient banking data is not stored in the refund audit table or returned in errors. The final staff confirmation rechecks payment, history, balance and transfer validation. A PostgreSQL transaction advisory lock serializes reservations across workers. The reservation commits before the transfer request. Repeated requests reuse the same ID; unknown outcomes stay reserved and are only reconciled by GET, never automatically resent.

Transfer fees are additional and borne by the business. The review clearly states that exact fees are available after submission; they are not falsely shown as zero. The resulting fee/currency and provider transfer ID are recorded in the audit history.

Signed transfer webhooks trigger authoritative status reads. A background loop polls reservations, including PAID transfers that can later fail. Failed/unknown outcomes stay reserved pending finance reconciliation. Confirmed cancelled transfers release their amount. This page does not automatically cancel Odoo orders, generate credit notes or send customer messages.

## Verification

- `python -m unittest discover -s tests -p 'test_airwallex*.py'`
- `REFUND_TEST_POSTGRES_URL=... python -m unittest discover -s tests -p 'test_airwallex_refunds_postgres.py'` uses its own disposable schema and mocks all payouts.
- `npm run build --prefix frontend`
- Live read-only checks confirmed two previously refunded orders have zero remaining, an order lacking paid evidence is blocked, and an Interac review passes beneficiary and transfer validation. No live transfer was created during verification.

## Sources

- https://www.airwallex.com/docs/api/payouts/transfers
- https://www.airwallex.com/docs/api/payouts/beneficiaries
- https://www.airwallex.com/docs/developer-tools/webhooks/listen-for-webhook-events/transfers
