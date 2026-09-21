# New-order SMS

Confirmed website orders now receive an automatic welcome SMS alongside the
welcome email, subject to global SMS enablement, website opt-in, destination
requirements, valid customer phone, suppression and current confirmed-order checks.
This is a narrow approval exception; other SMS approval rules are unchanged.
Test-mode messages use only the configured fixed owner test number.

MSG91 uses dedicated fixed-brand `new_order_welcome` templates listed in
`msg91-welcome-templates.json`. Creation is not approval: active approval, exact
sender and exact content are checked before preparation and before dispatch.
Unapproved templates are blocked, never replaced with an unrelated template.

Unique per-store/order/mode reservations prevent duplicate welcomes even when
multiple source emails exist. Uncertain sends are not automatically retried.
The welcome worker recovers only unattempted messages after the runtime setting
`after_order_welcome_sms_started_at`, within the last 24 hours. It does not backfill
older orders or resend accepted messages. Provider delivery is not guaranteed.

Provider template registration: https://docs.msg91.com/sms/add-flow
