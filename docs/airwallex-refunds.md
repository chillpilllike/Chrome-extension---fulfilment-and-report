# Airwallex refund transfers

The staff page `/airwallex-refunds` returns customer funds through the existing central Airwallex account. These are outgoing transfers with `Refund <order number>` references, not Payment Acceptance refunds against payment intents. The existing public Odoo operation proxy still cannot create transfers.

## Workflow and controls

Select an Odoo store/order. The server verifies completed transactions (and settled deposits for Airwallex payments) or fully paid single-order invoices. It retrieves the complete Airwallex transfer history with cursor pagination starting at `page=0`; the API otherwise defaults to only 30 days. Full order-token matching includes historical manual and partial refunds.

The cap is the lesser of order value and verified paid value, minus recorded outgoing transfers, pending local reservations, Odoo refund transactions and posted credit notes. The original locked payment conversion is used if the receiving currency differed from the order currency. Mixed or ambiguous payment allocation blocks submission. Credit notes and provider refunds are deducted conservatively when their overlap cannot be established. Refunds elsewhere without a matching order reference must be reconciled in Odoo; staff must confirm this check.

The page displays the original order amount/currency, its equivalent in the refund currency at the original payment rate, verified received funds, and remaining refundable funds. Payment details show the original provider and method (including non-Airwallex providers), transaction reference and matched customer. Airwallex deposits must be settled, have an exact order-token reference and matching amount/currency, and not be reused by another Odoo transaction. The payment customer must be the order customer or its billing contact with the same commercial customer. The bank payer name is shown separately for staff verification; a linked payment does not independently prove the bank payer's identity. For invoice-only payments, the reconciled payment widget supplies the method/journal when available; unavailable methods are labelled explicitly.

A shared limit allows five successful refunds from each calendar day’s submissions across all stores and staff, using Asia/Kolkata (midnight India time). Pending and uncertain transfers reserve a slot immediately; confirmed failed and cancelled transfers free their daily slot. The UI separates successful and pending counts. Live Airwallex status takes precedence over cached app status when counting, including failures not yet reconciled locally. Tagged Airwallex transfers containing the word `refund` also count, deduplicated against app requests. A global PostgreSQL advisory lock serializes the count and reservation, including different orders and workers; replaying a submitted review does not spend another slot. The counter is checked during review and again immediately before reserving submission. This restricts this application's submissions; it cannot prevent someone transferring directly in Airwallex or identify unlabelled manual refunds.

Amount and currency are populated automatically. Edit cost requires a warning acknowledgement and reason and cannot override the server cap. Beneficiary fields and LOCAL/SWIFT/clearing choices come from Airwallex's current schema; Canadian Interac is supported. Only bank-account payouts are supported; digital/stablecoin wallets are excluded. The payout currency remains the verified collected currency. Staff select a separate funding currency from the shared Airwallex wallet. The server returns only currency names and available/empty flags; exact wallet amounts never reach the refund page API, including encrypted review contents. Empty currencies show “0 balance” and are disabled. A funded currency has no displayed balance amount and is not a promise of sufficient funds.

When source and payout currencies differ, Airwallex performs the conversion as part of the final transfer. A read-only current FX rate calculates the estimated source debit before fees. The review shows that transaction cost, not the wallet balance, and explicitly states that rates can change and fees are additional. Source selection is bound to the encrypted review token and saved in refund history. Funds and FX are checked again on submission; unavailable rates, zero balances and insufficient estimated funding block submission without automatically selecting another currency. Airwallex makes the final funding/fee decision at creation; uncertain outcomes retain reservations as before.

Review calls beneficiary/transfer validation endpoints only. A ten-minute encrypted review token binds recipient, order, amount, account and a server-generated request ID. Recipient banking data is not stored in the refund audit table or returned in errors. The final staff confirmation rechecks payment, history, balance and transfer validation. A PostgreSQL transaction advisory lock serializes reservations across workers. The reservation commits before the transfer request. Repeated requests reuse the same ID; unknown outcomes stay reserved and are only reconciled by GET, never automatically resent.

Transfer fees are additional and borne by the business. The review clearly states that exact fees are available after submission; they are not falsely shown as zero. The resulting fee/currency and provider transfer ID are recorded in the audit history.

A dedicated transfer-event subscription uses the existing webhook URL with its own private signing secret. Airwallex does not support changing the event list of an existing subscription. Signed transfer webhooks trigger authoritative status reads. A background loop polls reservations, including PAID transfers that can later fail. Failed/unknown outcomes stay reserved pending finance reconciliation. Confirmed cancelled transfers release their amount. This page does not automatically cancel Odoo orders, generate credit notes or send customer messages.

## Manual refund history

The main history combines app reservations and imported Airwallex transfers with refund references. `Refund <order>` and `Partial Refund <order>` map by exact order name across registered Odoo databases, including archived orders. Legacy transfers with a bare order reference and a refund remark are also mapped. Imports use an upsert keyed by Airwallex account and transfer ID, and the merged list deduplicates by transfer ID/request ID. Only display fields are persisted; bank account numbers and full beneficiary data are excluded. Sync runs every five minutes and through the staff-only Sync Airwallex history button.

Orders on websites without an app store registration are still mapped to their Odoo order and labelled; they cannot be opened through another store's scope. Missing or ambiguous orders remain visible and explicitly unresolved. A failed database lookup does not produce a guessed match.

The refund cap continues to read live, complete Airwallex history on review and submission, independently of the imported display cache. Prior paid and pending partial refunds plus app reservations are deducted cumulatively. A full refund leaves no refundable amount. Repeated imports cannot add duplicate deductions, and repeated submission of a review cannot create another transfer. Cancelled transfers remain in history but release their monetary reservation; failed/unknown outcomes remain reserved.

## Verification

Provider validation and transfer failures are translated into staff-facing explanations and correction steps. Name mismatch, unsupported receiving currency, closed accounts, IBAN/bank-country mismatch and insufficient funding have specific messages. Field errors use readable labels. Failed app and imported transfers retain the translated reason in history. Unknown errors ask finance to check Airwallex instead of guessing the cause. Raw upstream messages, bank details, and wallet amounts are never echoed; uncertain submission outcomes remain reserved against duplicate refunds.

- `python -m unittest discover -s tests -p 'test_airwallex*.py'`
- `REFUND_TEST_POSTGRES_URL=... python -m unittest discover -s tests -p 'test_airwallex_refunds_postgres.py'` uses its own disposable schema and mocks all payouts.
- `npm run build --prefix frontend`
- Live read-only checks confirmed two previously refunded orders have zero remaining, an order lacking paid evidence is blocked, and an Interac review passes beneficiary and transfer validation. No live transfer was created during verification.

## Sources

- https://www.airwallex.com/docs/api/payouts/transfers
- https://www.airwallex.com/docs/api/payouts/beneficiaries
- https://www.airwallex.com/docs/developer-tools/webhooks/listen-for-webhook-events/transfers

- Deposit reference and payer fields: https://www.airwallex.com/docs/api/2025-02-14/core_resources/deposits

- Read-only FX rates: https://www.airwallex.com/docs/api/transactional_fx/rates
- Error fields and validation rules: https://www.airwallex.com/docs/payouts/errors/transfer-error-codes
- Transfer failure reasons: https://www.airwallex.com/docs/payouts/transfers/manage-transfers/failure-reasons

## Successful-refund customer email

Only newly submitted app refunds opt in to automatic confirmations. Existing refunds and manual history imports do not enqueue emails. The payout and durable notification reservation are recorded atomically when an authoritative Airwallex result is PAID. Pending, failed and uncertain payouts never trigger a success email. Each partial refund has its own once-only request key; copy describes the refunded amount without claiming the entire order was refunded.

The refund worker verifies the current transfer, Odoo order customer and actual sale.order.website_id before sending. Cancelled website orders are supported. Sender, reply-to, website name and logo derive from that website, never the database-wide connection name. When an unambiguous registered connection exists for that website/database, the email log case belongs to it; otherwise the originating connection keeps the case with the actual website ID/domain. Email log supports website filtering inside a connection and displays website branding separately from connection names.

Only masked account details (last four characters or masked e-transfer email), account holder and bank name are retained for the email. The message includes amount/currency, transfer reference and a 24–72 business hour estimated credit-arrival explanation. Customer emails and saved previews use the existing after_order_messages and delivery-attempt log, with provider delivery tracking/suppression.

Sending is independently controlled by app_settings.airwallex_refund_email_enabled=true; it does not enable other after-order email workflows. A PostgreSQL advisory lock excludes concurrent email workers. Uncertain acceptance retries reuse the exact saved payload and Resend idempotency key within 23 hours; older uncertain deliveries are held. Confirmed email failures require individual approval in Email log (maximum five attempts, same safe window). Sender/customer/site changes hold retries. Email problems never retry a payout and appear separately in refund history.

Verification: test_refund_emails.py covers routing, masking, cancelled orders, eligibility, deduplication, uncertain acceptance, approval and site changes. A disposable PostgreSQL schema with a mocked email provider verifies concurrent worker exclusion and actual log SQL. Live identity/transfer reads generate a local preview only; historical customers are not emailed by verification.
