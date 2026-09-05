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

Both repositories must be updated. Upgrade the `after_order_portal` Odoo module to **18.0.2.0.0** (not just copy its files): it adds stored quotation fields and an internal service product. The XML-RPC integration user must be an Odoo Settings administrator. Existing browser bridge settings are unchanged.

Keep the app in email test mode, with automation disabled. Admin-only portal test selections are stored separately from live selections. Test mode never creates quotations, captures payments, refunds money, changes fulfilment lines or queues live Odoo emails. Only an explicit test email action uses the configured test inbox.

The Odoo setting `after_order_portal.live_alternatives_enabled` defaults to disabled. Do **not** set it to `true` until isolated Odoo transaction tests and the website/payment/mail checks pass. The app's existing live-readiness lock and automation switch remain additional controls. No credentials are changed by this feature.

## Verification still required before live activation

Use an isolated Odoo test database with payment and mail providers in test mode to verify: paid invoice pricing and taxes; quantity/discount allocation; exact SKU variant matching; duplicate RPC recovery; quotation mail queue failures/retries; successful, authorized-only, partial and reversed payments; a cheaper-line refund review; a no-ASIN manual line; and combined multi-line release. Confirm a schema upgrade and admin-only visibility on each website. Offline Python tests and the frontend build do not establish these live integration outcomes.

The Odoo 18 interfaces used are the standard [sale order model](https://github.com/odoo/odoo/blob/18.0/addons/sale/models/sale_order.py) and [pricelist model](https://github.com/odoo/odoo/blob/18.0/addons/product/models/product_pricelist.py).
