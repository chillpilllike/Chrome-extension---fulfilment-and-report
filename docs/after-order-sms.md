# After-order SMS integration

SMS is disabled by default. Settings → Customer SMS selects Odoo, MSG91 or Twilio.
Existing Odoo dispatch automations are unchanged; this outbox does not send dispatch confirmations or review/marketing invitations.

Each eligible email reserves at most one companion SMS. Email Log → open email → Companion SMS shows the exact text, recipient, provider, attempt count and status. Approving SMS never resends the email. Test preparation automatically attempts the initial SMS to **+19296526393 only**. Live messages need individual approval, except new-order welcome after email acceptance. Switching to test mode blocks pending customer SMS. Previously queued test messages addressed to another number are blocked, not silently redirected.

## Configuration

Website mappings use `store_id:website_id` keys. A live website requires `transactional_sms_enabled: true`, confirmed matching Odoo order, explicit international phone number and an unblocked Odoo partner. Review consent, destination restrictions and provider registration before enabling. Missing Odoo suppression support fails closed.

- Odoo: uses the matching store's existing RPC connection and official `sms.sms` queue/credits. Deterministic UUID prevents duplicate queue creation. No new addon is required. Its scheduled queue performs the actual send after app authorization. Switching app test mode cannot recall an SMS already authorized into Odoo's queue.
- Twilio: runtime secrets `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`; each website maps `sender` or `messaging_service_sid`.
- MSG91: runtime secret `MSG91_AUTH_KEY`; each website maps `sender` and `templates` keyed by notification kind. Each template needs `template_id`, exact approved `text`, and for Indian destinations `dlt_template_id`. Configure the matching DLT template in MSG91 itself. Supported text substitutions are `##order##`, `##brand##`, `##url##`. Do not invent approved text or IDs. India URL/header registration must be verified in the provider account before sending.

Do not put API keys in mappings, source files or screenshots. Existing queued messages retain their original provider and text when settings change.

## Delivery and retries

`accepted` is not delivery. The refresh button reads Twilio or Odoo status without sending. Odoo can remove old queue records; disappearance is not proof of delivery. MSG91 receipt integration is not connected: consult its request ID in the MSG91 dashboard. No unverified public callback endpoint is exposed.

Definitive pre-send/configuration or HTTP 4xx failure can be retried with a fresh preview approval, maximum three attempts. Network timeout, server failure or crash after reservation stays uncertain and must be reconciled; no automatic provider fallback or blind retry. Once handed off, provider failures cannot be resent through the approval button. Provider-side opt-outs remain authoritative; app also checks Odoo suppression for every live send.

## Required before enabling live SMS

Configure credentials and approved mappings; test delivery to the owner number with each intended engine. Verify country/sender eligibility, credits, Odoo queue/cron and suppression, Twilio opt-out behavior, MSG91 delivery receipts and DLT mappings. Unit tests use mocked providers, not actual carrier delivery. The feature must not be described as live-ready until these account-specific checks are complete.
