# Delivery follow-ups

The independent minute worker checks these paths even while broad automation
and financial execution remain approval-gated:

- Received: review invitation is immediately eligible (next healthy worker tick,
  targeting dispatch within five minutes; carrier/provider delivery is not guaranteed).
- No answer: five days after the carrier delivery event. Unknown timezones use
  the existing conservative carrier parser, not the time the app fetched tracking.
- Not received: cancel unsent reviews; put the case in Needs attention and record
  a delivery issue team notification in its timeline. Acknowledge by email and SMS.
- Later received: a fresh order-portal link can correct non-delivery even after
  team acknowledgement. Previously invalidated links stay invalid. Financial
  decisions cannot be reopened through this exception.

Per-order, per-kind, per-channel reservations prevent duplicate dispatch across
workers and case changes. An uncertain attempt is held for investigation, never
blindly retried. Pending review cancellation cannot recall an in-flight message.
Existing pre-feature email attempts are included in duplicate checks.

Email and SMS are separately logged. Missing SMS setup does not block email.
Customer SMS still requires the global enable flag and the website opt-in flag.
Review invitations respect existing notification opt-outs. Live sending stays
blocked in test mode. The automatic historical scanner does not run in test mode;
the explicit test-email suite includes both kinds and retains owner-only recipients.

MSG91: two new fixed-brand templates for each of 13 senders are registered in
`msg91-delivery-followup-templates.json`. Recorded approval status is a snapshot;
the SMS adapter verifies current provider approval, exact content and sender
before preparing or dispatching these messages. Pending/rejected templates cannot
send. The review URL is the order website's Trustpilot review page; issue messages
link to the customer order portal, retaining normal login/ownership checks.

Checks: `python -m unittest discover -s tests -p 'test_delivery_followup.py'`,
plus the after-order, SMS and welcome-email regression suites. Production receipt,
carrier delivery and real inbox/handset delivery require a controlled end-to-end
test; unit tests do not establish provider delivery or site-specific portal rendering.
