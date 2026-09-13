# Shared dispatch estimates — 13 September 2026

Runtime commit `05ae337` is deployed through Coolify. Native LibreDesk configuration covers 58 active website/email pairs: the original eight and 50 additional Nutricity multisites. Inactive email channels were excluded; existing conversations were not reassigned.

After native verification and order selection, the shared status tool checks the bound order, website, customer and current app evidence. For a confirmed order with fresh, complete item coverage, estimated dispatch is the latest required expected arrival plus one calendar day. The deterministic policy supplies the date; the assistant must not calculate its own. It also handles actual received dates, outbound shipment status, partial shipments and missing or conflicting evidence. Unconfirmed orders receive no estimate. Dates are estimates, not guarantees.

The dispatch contract returns public status, estimate and customer wording only. It excludes supplier identities, inbound dates, procurement, internal tracking identifiers, costs and margins. Assistant instructions prohibit naming internal systems or describing the calculation's source.

## Rollout

Run these scripts inside the fulfilment runtime with its existing environment and the verified multisite-widgets.json inventory:

1. `prepare-active-order-sites.py` checks live website/widget mapping, preserves scoped Odoo secrets, prepares native tools and clones the established assistant without routing.
2. `configure-post-order-chat.py` attaches protected shared tools and dispatch instructions to active assistants.
3. `activate-active-order-sites.py` verifies scoped tools before enabling native new-conversation routing and reading it back.
4. `audit-active-order-sites.py` checks active coverage, scoped routing and unverified-access rejection without sending messages.

## Verification and limits

92 automated tests passed: 83 support tests, seven post-order chat tests and two shared dispatch tests. Cases include stale/missing evidence, confirmation state, website isolation and private-data exclusion. The live audit passed for all 58 websites; see the adjacent JSON. The 50 added websites also passed native unmatched-email and unverified-order checks.

No real OTP was sent and no full customer conversation was completed on every website during this rollout. Real email resends remain subject to existing email test mode; live email automation was not enabled. The plus-one-day rule is fixed in the public policy, independent of email settings. No secrets are stored in these scripts or reports.
