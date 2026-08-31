# Amazon account-type invariants

These rules are mandatory for every change in this Chrome extension.

- Treat Amazon consumer and Amazon Business as separate fulfilment experiences. Never merge their product, cart, or checkout behavior merely because they share Amazon DOM selectors.
- Detect the account experience positively with `amazonAccountExperience()`. Never infer Amazon Business from a missing consumer control.
- Consumer account (any Chrome profile): when Subscribe & Save is cheaper, select it and, for multi-ASIN orders, wait for and click **Add subscription to cart**. A late or temporarily missing button must pause/retry the consumer path; it must never silently switch that line to One-time purchase.
- Amazon Business account (any Chrome profile): **Add subscription to cart** is not available. Business orders stay on the separate One-time purchase/normal-cart path.
- Amazon Business payment selection can expose Prime Business Rewards/Points as a competing `ppw-instrumentRowSelection` radio even while its rewards checkbox is off. The Business branch must select a credit-card radio, verify that exact native radio remains checked, and use the primary Business payment Continue control. Do not apply this Business-only guard to consumer checkout.
- Never route by a person's name or hard-coded Chrome profile. Every extension instance must positively detect its current Amazon account experience before it asks the server to claim work.
- When account-type routing is enabled, multi-ASIN Odoo orders require a consumer account and single-ASIN orders require a Business account. Multiple source lines consolidated to the same effective purchasable ASIN count as one ASIN. The first idle compatible extension wins the atomic server claim, owns the entire order through Amazon placement and reporting, and releases it only after success or a safe pre-submit stop.
- Never split one Odoo order into separate ASIN jobs. All source lines in the order share one claim and one worker.
- Treat each job item's current `item.asin` as the sole purchase identity authorized by the app. A replacement is allowed only when the app explicitly puts that replacement in `item.asin`; browser-derived variants, pricing state, redirects, titles, pack sizes, or cheaper alternatives must never authorize another ASIN. Require exact readable ASIN verification before Add to cart, in the full cart before checkout, before Place Order, and across the complete Amazon order-history result before reporting completion. Fail closed for unreadable or unexpected ASINs in both Consumer and Business flows.
- Account-specific decisions must be guarded by an explicit `accountExperience === "consumer"` or `accountExperience === "business"` branch and covered by regression tests for both experiences.
- Before shipping product/cart/checkout changes, test at least one live consumer profile and one live Business profile when available.
