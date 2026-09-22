# Customer email approval and interrupted-send recovery

Settings → Customer email approval provides an email-only bypass switch and an
explicit option to include existing approval-held messages. Switching on requires
confirmation. Settings are read directly from PostgreSQL at sending boundaries so
switching off does not wait for the normal settings cache.

The default remains approval-required for other care messages. Welcome emails,
verified DTC dispatch notices, eligible delivery follow-ups and manually confirmed
refund acknowledgements retain their existing automatic exceptions. SMS policy is
separate. Financial execution and business-decision approval are unchanged.

The independent 30-second dispatcher recovers only `awaiting_approval` records with
zero attempts and no attempt-history evidence. It never resends accepted, uncertain,
failed or delivered attempts. Existing row locks and provider idempotency still apply.
Messages older than three days require a current preview/manual review. Cutoff,
suppression, opt-out, sourcing, cancelled/resolved actions, tracking, recipient,
language and template checks still apply. Quotation emails retain their Odoo
quote/choice checks. Errors appear in Email log and the case timeline without
repeating identical events on every polling cycle.

NC29769 was reserved at 2026-09-22 14:24:13 UTC, its SMS branch ran, but the email
had zero attempts. The welcome scanner skipped any order with an existing message,
leaving this interrupted send stranded. Automatic emails now start before companion
SMS preparation, and the dispatcher recovers unattempted reservations. This does not
retry NC29769's separately rejected SMS.

The live DTC audit found five delivered emails and five delivered SMS messages.
Pre-cutoff orders remain excluded, but are now skipped without reporting normal
cutoff exclusions as a failed scanner cycle.

API: GET/POST `/api/after-order/settings/email-approval`. POST requires boolean
`bypass_approval`, optional boolean `include_existing`, and `confirm_live_sends: true`
when enabling. The endpoint does not modify test mode, SMS approval, refund mode,
financial approval or broad case automation.
