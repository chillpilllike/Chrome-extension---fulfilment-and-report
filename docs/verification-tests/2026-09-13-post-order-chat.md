# Post-order chat integration

Native protected tools use a per-store/website/inbox HMAC key and require current native verified headers. The app rechecks the live conversation identity, Odoo conversation binding and current sale-order ownership. Secretgreen retains its existing portal binding; other configured assistants use the installed Odoo binding. Model parameters cannot choose customer, website or order.

Status combines current post-order cases and actual live email history. Unreviewed unavailable-item details are withheld. Customer decisions and team confirmation are separate from execution. Provider acceptance/delivery/click signals never establish whether a person read or replied. Historical test messages are excluded; rollout cutoff and existing post-order test mode remain enforced.

A ten-minute signed offer binds the exact email to the customer, order and conversation. The resend handler independently checks a new affirmative reply following a public resend offer, or a short explicit resend command. Arbitrary complaints, quoted commands, recipient changes and refusals are rejected. English and French commands/confirmations are supported; other unclear replies need clarification. The saved payload is reused unchanged, with current recipient/sender, review, request fingerprint and original action-link checks. Movement notifications require their original event revision to match. No new approval is needed for an unchanged eligible unavailable-item email. Reviews are excluded.

Resends share a rolling one-hour cooldown across conversations and copies of the same payload, capped at three per day. Uncertain acceptance blocks another attempt. Missing provider IDs are uncertain, never success. Each accepted chat resend adds a post-order audit event and a message-log row. The old Secretgreen resend endpoint is closed to avoid bypassing consent.

Validation: 11 consent/scope tests, 10 follow-up tests, 7 journey tests and 185 existing after-order tests passed. Tests use synthetic records and mocked mail delivery. No customer email was sent in this release's tests.

Production preflight: after_order_email_test_mode=true; automation=false; live readiness approval unset. Do not describe customer resending or unattended original sends as live until the post-order workflow passes its own go-live requirements. This release does not silently change those settings.
