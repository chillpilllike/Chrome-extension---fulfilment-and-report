# Line-item alternatives rollout

## Operator workflow

- In Orders, use **Choose customer alternatives** on a missing line. The same control is available in its after-order case review.
- Enter exact Odoo Internal References, one per line. These resolve against the order's Odoo database, website and company; ambiguous references are rejected. Recommend only one variant per template for a particular affected line.
- Confirm third-party/manual sourcing was checked, then **Save alternatives & notify customer**. All currently affected lines must have recommendations before a notice can be sent. An extension report alone does not send it. Identical live recommendation sets are deduplicated.
- Every after-order result has an expandable timeline, including line-specific extension reports, recommendations, email attempts and selections. Older historical email attempts without a recorded line snapshot are not retroactively attributed to today's affected items.

## Customer workflow

The standard Odoo order preview shows **Best alternatives**, followed by website search results under **More alternatives to choose from**. Search terms are used server-side only. The selected item stays at the top on revisits.

Each affected line gets an independent, fixed 24-hour window starting with its first selection. Reselecting changes that line's product and increments its version without extending the deadline. A processed/locked line cannot be changed by reusing an old POST or an old email. Other customer decisions still use the existing team confirmation flow; once alternative processing has locked, further changes need team assistance.

## Money and fulfilment

Odoo prices alternatives using the order's pricelist, quantity, currency and mapped taxes. Paid, posted, unambiguously allocated invoice lines supply the original amount, not the zeroed unavailable sale-order line. Ordinary line discounts are included. Unpaid/authorized-only orders, prior credits, order-level discounts, mixed allocation/units or unsupported taxes require accounting review. A recommendation and selection can still be recorded with **Price needs review**; an unknown amount is never replaced with zero.

After the deadline:

- A cheaper alternative records a pending difference refund for team review. This feature does **not** execute or pretend to execute a credit note or payment refund.
- A dearer alternative with verified pricing creates a separate, idempotent Odoo quotation for the exact difference, with the same customer, company, website, currency and tax treatment. Odoo queues its quotation email transactionally. Online payment is required; authorization alone does not release the replacement. Odoo's outgoing mail/payment configuration must be verified for each installation.
- Additional quotations are excluded from fulfilment imports. Their accounting lines must not become another physical order.
- A paid quotation is rechecked immediately before releasing the order, including refunds/reversals and edited quote amounts. All affected lines must be ready together. No partial release while another affected line is still being chosen or awaiting payment.
- ASINs resolve from internal notes or the encoded reference. Conflicting sources require review. A known ASIN uses the existing replacement fields and ready-order queue; the existing automatic Chrome queue setting still applies. Without an ASIN, the line is held for manual fulfilment.
- **Processed** means the replacement was applied to the app's order line; it does not mean shipped or delivered. Pending payment, manual fulfilment and exceptions use separate states.

## Safety and deployment

Both repositories must be updated. Upgrade the `after_order_portal` Odoo module to **18.0.2.1.0** (not just copy its files): the payment email now has a stored selected-product relation, and portal views have changed. The XML-RPC integration user must be an Odoo Settings administrator. Existing browser bridge settings are unchanged. Coordinate the addon code update and module upgrade; loading new Python fields without upgrading the database can break sale-order reads.

Keep the app in email test mode, with automation disabled. Admin-only portal test selections are stored separately from live selections. Test mode never creates quotations, captures payments, refunds money, changes fulfilment lines or queues live Odoo emails. Only an explicit test email action uses the configured test inbox.

The Odoo setting `after_order_portal.live_alternatives_enabled` defaults to disabled. Do **not** set it to `true` until isolated Odoo transaction tests and the website/payment/mail checks pass. The app's existing live-readiness lock and automation switch remain additional controls. No credentials are changed by this feature.

## Verification still required before live activation

### Three-day no-response policy: implemented behind a rollout gate

Unavailable-item email previews describe the requested three-day initial-choice
policy. The server-filtered removal action determines the wording: if another
fulfilable product remains, continue without the unavailable items and review
their refund; otherwise refer the whole order for cancellation/refund approval.
Row count alone must not decide this. The fixed 24-hour window after a first
alternative selection is separate. Dispatch-delay default-proceed, shipment,
delivery and review messages must not threaten unrelated cancellation.

The response ledger starts from a signed `email.delivered` receipt for a live
unavailable-item message tagged `three-day-v1`, never an old-policy or test email.
It protects existing choices and serializes timeout processing with customer
submissions. A partial timeout queues automatic exclusion; a whole-order timeout
goes to cancellation/refund review. Exclusion is a separate operation from refund:
only undelivered lines without active stock moves can be excluded, the order must
retain products, and all mixed choices/payment checks must be ready before release.
Previously paid money stays marked for refund approval; it is not silently treated
as returned. The live email policy gate `after_order_completion_enabled` remains
unset/false until refund completion and isolated integration tests are finished.
Test emails may preview the policy without creating live deadlines.

### Delivery receipts and suppression

Configure Resend to POST to `/api/public/after-order-webhooks/resend`, with its
endpoint signing secret in `RESEND_WEBHOOK_SECRET` (not the Resend API key).
The handler verifies raw-body HMAC signatures, a five-minute timestamp tolerance,
and a durable event ID before updating status. Delivery means acceptance by the
recipient mail server, not reading or inbox placement. Bounces/complaints suppress
further app sends and retries. Test-recipient suppression is separate from live.
Out-of-order receipts cannot downgrade a newer successful attempt. Unmatched
receipts are kept for send-acknowledgement reconciliation.

### Mixed choices, parcel contents and operational recovery

Each affected line can request removal or an alternative. A removal request is
replaceable until approved; switching back to an alternative retains its original
24-hour clock. Team-approved line removals cannot be overwritten by a whole-order
request. Email recommendations show titles/thumbnails, a separate public product
details link, and a GET link focusing the choice on the order page. Confirmation
requires the existing order-access check and CSRF-protected POST; email scanners
cannot confirm a replacement. Selected products remain green on revisits.

The timeline exposes line removals, response deadlines, verified payment and
replacement application, plus a guarded retry for an unchanged failed selection.
Retries reuse the original Odoo operation key and recheck pricing/payment.
Team-verified parcel quantities are scoped to the order, retained across tracking
case revisions, and cannot exceed the order quantity or other parcel allocations.
Changing a parcel mapping invalidates the old customer choice and links.
Notification and release batches rotate by persistent last-check time.
The all-email test suite includes a native Odoo payment-template preview rendered
against an existing order, without creating a quotation or payment; one template
failure is reported without preventing the remaining previews.

### Still blocked: actual refund execution and real provider tests

### Automatic send recovery (local, not deployed)

With live automation enabled, Resend failures and uncertain/stalled API sends are
checked by a rotating durable queue. Recovery starts at least one hour after the
last attempt, with at most three automatic retries. Each recovery reuses the last
provider idempotency key and unchanged public payload; ambiguous requests older
than 23 hours require reconciliation, since Resend deduplication lasts 24 hours.
Test mode, suppression, changed orders/recipients and customer responses block
live recovery. Attempts and blocked reasons are recorded in the case timeline.
Odoo-native quotation email retries are not part of this Resend worker.
New live decision emails opt in to at most three no-response reminders at 24, 48
and 60 hours after verified delivery. Tracking-movement updates, review invitations,
test messages and payment quotations are excluded. Suspected-loss decision emails
remain eligible. Any recorded customer decision, confirmation, changed issue or
suppression stops reminders. No open tracking is used. Existing pre-feature emails
are not retroactively enrolled. Missed scheduler slots collapse to the latest due
reminder instead of a catch-up burst, and nothing sends after 72 hours. Reminders
retain the original links and deadline; their delivery cannot start a new deadline
or reminder chain. Distinct message reservations prevent duplicate sends per slot.
Failed reminders enter the same hourly API recovery path, capped by the original
reminder expiry. Both live automation and verified delivery webhooks are required.

### Refund execution prerequisites

Automatic payment refunds and credit-note/payment reconciliation are **not
implemented by this release**. Read-only discovery found Stripe and a custom
`shopify_bridge` reporting partial refunds, plus transfer/crypto providers that
report no automatic refund support. The custom bridge source and an isolated
Odoo/payment sandbox are needed to validate its refund contract, idempotency,
ambiguous outcomes, reversals and accounting reconciliation. Never enable live
automation based on an advertised `support_refund` flag alone. Unsupported methods
must retain manual finance review. No production money movements were tested.

Use an isolated Odoo test database with payment and mail providers in test mode to verify: paid invoice pricing and taxes; quantity/discount allocation; exact SKU variant matching; duplicate RPC recovery; quotation mail queue failures/retries; successful, authorized-only, partial and reversed payments; a cheaper-line refund review; a no-ASIN manual line; and combined multi-line release. Confirm a schema upgrade and admin-only visibility on each website. Offline Python tests and the frontend build do not establish these live integration outcomes.

The Odoo 18 interfaces used are the standard [sale order model](https://github.com/odoo/odoo/blob/18.0/addons/sale/models/sale_order.py) and [pricelist model](https://github.com/odoo/odoo/blob/18.0/addons/product/models/product_pricelist.py).
