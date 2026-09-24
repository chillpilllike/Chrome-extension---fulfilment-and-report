# Notification-only maintenance

The independent worker runs every minute when `after_order_notifications_enabled`
is true and global email test mode is false. This is separate from financial
automation, which remains disabled in the manual-refund rollout.

Settings → Customer email approval includes notification scheduling and the
dispatch handling allowance. The confirmed allowance is two calendar days.
Read-only worker health: `GET /api/after-order/notifications/status`.

The worker prepares current tracking/delivery/dispatch/unavailability notices,
records notification-based response deadlines, queues review on expiry, sends
24/48/60-hour reminders, and recovers previously authorized email attempts.
Unavailability still needs team sourcing decisions. Blank tracking is never lost.
Every stage is isolated; one blocked website cannot prevent other stages.

The worker never executes a payment, refund, quotation creation, item removal or
replacement release. Selection expiry changes the selection to `needs_review`.
Refund acknowledgements require an actual current customer request. Manual refund
completion emails/SMS require the immutable staff-entered completion record.
Quotation SMS requires the linked current unpaid quotation; ambiguous payments
are held. Financial transaction approvals are independent of message approvals.

As of 2026-09-24, Settings → Customer SMS includes a separate approval bypass:
`after_order_sms_approval_required=false`. Saving it requires explicit
`confirm_release_pending=true`. It authorizes eligible queued and future live
messages, including verified financial notices, but never executes a financial
action. The notification worker releases only unattempted live drafts, rotating
blocked drafts by their last check time. Test drafts, attempted/uncertain sends,
cancelled source notifications, stale messages, suppressed sites and invalid
recipients are not released. MSG91 approval and all existing current-order,
source-review, delivery-delay, opt-out and duplicate guards still apply.

The user stopped owner-test SMS sending on 2026-09-23. Do not invoke test email
endpoints that also send companion test SMS without fresh authorization.

Email retry requires a persisted send authorization, the original payload/key,
one hour since the attempt, and fewer than four attempts within the 23-hour safe
deduplication window. Unknown acceptance is recovered only with that same key.
No draft is authorized by the recovery worker.

SMS recovery requires a definitive MSG91 failure receipt matching the current
provider reference and recipient. It waits one hour and permits at most three
attempts total. Rejected, blocked, pending and unknown outcomes are not blindly
retried. Late stored delivery receipts are reconciled without sending.

The owner-only test endpoint is
`POST /api/after-order/cases/{case_id}/test-notification/{kind}`.
It forces the fixed test inbox and SMS number, leaves global live mode unchanged,
and does not record a customer decision or perform financial operations.

MSG91 read-only audit on 2026-09-23: 166 of 168 configured English mappings match
approved active versions. Boostgo/Espot welcome text matches but has status 0
(not approved). These stay held; no other brand's template is substituted.

NC29769 and NC29714 have Australian numbers classified as FIXED_LINE, and the
provider reports rejection. The contact resolver now skips known fixed lines;
a mobile number must be supplied if neither stored contact number supports SMS.
NC29725 and NC29913 are New Zealand mobile numbers with definitive failure
receipts (generic provider error), eligible for bounded recovery if still current.
