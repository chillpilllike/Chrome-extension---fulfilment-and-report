# Relay invoice payment bridge — 16 September 2026

## Flow and safeguards

Odoo exports a frozen USD invoice to QuickBooks. A minute worker imports pending Relay requests into After-order care, independently of paid-order fulfilment imports. The extension resolves invoice number, order reference, exact billing name/email and USD cents before issuing a Share invoice. It uploads the captured relay.cash link with the same evidence. The app binds that link to the Odoo transaction before queueing one payment email containing items, totals and a Pay button. Repeated checkout/payment attempts reuse the existing request/link, subject to a current Odoo fingerprint check.

A forwarded Relay payment-initiation notice is matched by the bound Relay payment token and exact USD amount. Odoo confirms the sale using its normal stock/order hooks while leaving the payment transaction pending settlement. The app sends the confirmation only after Odoo acknowledges initiation. Duplicate receipts, captures and lost RPC acknowledgements are idempotent. Generic tracking-email sends/retries are excluded from Relay cases.

Forwarder default: am-it@outlook.com. Receiving default: relay-payments@taloofalut.resend.app. Both are editable under After-order care > Relay invoice payments. Select stores by name. Selected stores include all accessible Odoo websites when the store website ID is blank. An explicit website ID restricts sync to that website. Every returned order must match its requested website, and its database, website, customer, and amount are frozen and rechecked.

## Activation

1. Update/upgrade payment_relay_qbo to 18.0.1.1.0 on each participating Odoo database. The app's Odoo service user needs Accounting manager access and access to the selected company's transactions.
2. Deploy the scoped app changes. New Relay settings default disabled and test mode ON. Existing unrelated email controls remain intact.
3. Configure public app URL https://fulfilment.gofinch.com and select participating stores. Start in test mode. The worker polls approximately every 60 seconds; API outages/long backlogs can increase latency.
4. Verify a genuine message actually delivered through Outlook to Resend. Configure ONLY the receiver-generated Authentication-Results authserv ID after establishing header provenance. Original Outlook headers pasted into chat do not validate Resend's forwarding path. Unknown/unauthenticated templates go to review; correcting settings and Recheck receipt retries them.
5. Resend runtime requires RESEND_API_KEY for sending and receiving permission on RESEND_RECEIVING_API_KEY (falling back to RESEND_API_KEY). Verified sending domains must cover notifications@ each selected website. The existing production API key was successfully checked against the receiving list API; no matching forwarded message was present in its latest 100 at verification time.
6. Generate the dedicated extension upload token in the app. Update the existing installed extension to 1.1.0 without clearing storage, configure app origin/token, grant that exact origin, enable Copy payment link only. Test one matching order without releasing email/confirmation holds.
7. Verify identity, amount, recorded link, receipt and email preview/content, then release Relay test mode and the app's global email test mode as appropriate. The extension's session must be signed in and the PC awake. A logged-out Relay tab cannot be fixed by refreshing.

## Recovery and limits

Link uploads retain their original app origin and retry from Chrome storage, including while invoice processing is stopped. Old extension records without the new matching evidence are not guessed or auto-imported. An uncertain final invoice creation needs operator review, not a second Create click.

Resend emails persist their exact payload and idempotency key before dispatch. Temporary failures/lost acknowledgements retry only within 23 hours of the first attempt (inside Resend's documented 24-hour idempotency window). Afterwards they are held as delivery_unknown for provider review. Customer/order changes block delivery. Relay email states and errors are visible next to each order; attempts also appear in Email log.

The parser recognizes the supplied “Payment is on the way for your invoice” / “initiated a payment” template. It ignores footer insurance amounts. Other settlement/refund/failure templates require explicit support and review; no refunds or settlement accounting are automated here. Browser-created Share invoice links with absent customer/order evidence are held.

## Verification

App policy, workflow, outbox and route-auth tests pass; addon isolated tests pass; Chrome state-machine tests pass. The actual supplied MIME body parsed as USD 26.00 using a synthetic test envelope (not a live authentication check). Frontend TypeScript checks pass. Full Odoo database integration and a live forwarded receipt remain required before automatic confirmation is activated.

No customer email or order confirmation was sent during this implementation. A direct database preflight was blocked by automatic approval review and was not bypassed.

Verified a genuine Outlook-forwarded Relay notice through Resend: receiver authserv amazonses.com, aligned Outlook DMARC pass, USD 26.00, and one receipt token. Outlook rewrites the direct receipt URL to a Relay click tracker; only the authenticated notice’s unique View details in Relay button is resolved. Redirect requests are limited to HTTPS links.relayfi.com/s/c/ and stop at relay.cash without fetching the payment page. Unrecognized destinations or ambiguous buttons are held for review. This validates receipt parsing, not a complete live order-confirmation test.
