# Amazon account-type invariants

These rules are mandatory for every change in this Chrome extension.

- Treat Amazon consumer and Amazon Business as separate fulfilment experiences. Never merge their product, cart, or checkout behavior merely because they share Amazon DOM selectors.
- Detect the account experience positively with `amazonAccountExperience()`. Never infer Amazon Business from a missing consumer control.
- Consumer account (any Chrome profile): when Subscribe & Save is cheaper, select it and, for multi-ASIN orders, wait for and click **Add subscription to cart**. A late or temporarily missing button must pause/retry the consumer path; it must never silently switch that line to One-time purchase.
- Amazon Business account (any Chrome profile): **Add subscription to cart** is not available. Business orders stay on the separate One-time purchase/normal-cart path.
- Never route by a person's name or hard-coded Chrome profile. Every extension instance must positively detect its current Amazon account experience before it asks the server to claim work.
- When account-type routing is enabled, multi-line Odoo orders require a consumer account and single-line orders require a Business account. The first idle compatible extension wins the atomic server claim, owns the entire order through Amazon placement and reporting, and releases it only after success or a safe pre-submit stop.
- Never split one Odoo order into separate ASIN jobs. All source lines in the order share one claim and one worker.
- Account-specific decisions must be guarded by an explicit `accountExperience === "consumer"` or `accountExperience === "business"` branch and covered by regression tests for both experiences.
- Before shipping product/cart/checkout changes, test at least one live consumer profile and one live Business profile when available.
