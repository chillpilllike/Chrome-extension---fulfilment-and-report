# Warehouse dispatch-delay notice

The dedicated monitor prepares drafts only in approval-only live mode, with test
mode off and `after_order_warehouse_delay_enabled=true`. It runs at most once per
five minutes per process. It does not enable broad after-order automation, perform
refunds, or send messages. Every send attempt requires the existing individual
authenticated email approval and exact preview digest.

Receipt day is excluded. Two complete Monday–Friday days must pass in
`America/New_York`, excluding standard US federal holidays and their Friday/Monday
observances. Eligibility begins at midnight on the next business day. Example:
Friday 4 September 2026 receipt, Monday Labor Day, Tuesday/Wednesday elapsed,
Thursday 10 September eligibility. One-off closures are not automatically added.

All imported active product lines require explicit delivered evidence and a
parseable delivery date; ETAs and scan-check timestamps are not delivery proof.
The latest receipt determines the deadline. Receipt dates before the order date,
missing evidence, excluded sourcing engines, prior-cutoff orders, recorded EPG or
Shopify dispatch, partial fulfilment and cancellation are excluded. Fresh Odoo
checks require a confirmed order, coverage of all physical product lines, no
delivered quantities, and open outgoing transfers without tracking references.
Unavailable Odoo evidence blocks preparation/sending rather than guessing.

A high-priority After-order care case and timeline event notify the team. The
branded email says dispatch has hit a hurdle, the team expects dispatch within
24–48 hours, and no customer action is needed. There are no decision links or
reminders. A unique case key and a fixed live message idempotency key reserve one
notice per store/order. Failures use the same email record and approval workflow.
Changing order data does not create another notice.

Fresh Odoo eligibility is checked again inside the approval lock before provider
submission. A queued notice that became ineligible is blocked (HTTP 409), not
automatically sent. Existing drafts stay visible in the audit log.

Admin endpoints:

- `POST /api/after-order/warehouse-dispatch-delay/settings` with `{"enabled":true}`
  (requires approval-only live mode; false pauses the monitor).
- `POST /api/after-order/warehouse-dispatch-delay/check` prepares eligible drafts;
  returns checked/queued/blocked counts, or the last result when throttled.

The current global order cutoff continues to apply. The separate expected-arrival
dispatch-estimate feature and its handling-day configuration are unchanged.
