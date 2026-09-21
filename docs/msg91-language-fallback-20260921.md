# Live MSG91 language selection

The selected customer/order language is checked at preparation, preview and send.
Approved localized template content must exactly match the configured sender and
text. Missing, pending, rejected, disabled or changed localized versions fall back
to a separately verified, dedicated English template. If English cannot be
verified either, sending remains blocked. A provider acceptance timeout never
triggers a second send in English.

Pending and confirmed-failed snapshots may be updated. Accepted, sending,
delivered and uncertain snapshots are never rewritten. Existing retry limits,
recipient/domain validation, financial holds, team approval and once-only guards
remain in force. A language/content change invalidates a manual approval digest;
the team must approve the updated preview. Existing automatic welcome, review,
delivery-issue acknowledgement and test messages use the current approved choice
without adding a manual approval gate.

## Registration

`scripts/register_sms_catalogs.py` defaults to a dry run and never calls the SMS
send endpoint. It registers the complete, non-held static catalogs with fixed
brand text, records creation intent before provider calls, and persists returned
IDs. Unknown creation outcomes are held for manual reconciliation, not retried.
Public IDs/text are exported to `docs/msg91-localized-templates.json`; localization
mappings are imported separately without changing existing English mappings.

Completed batch: 70 shared language catalogs × 12 notification kinds × five senders
(Nutricity, GofinchKart, FinchKart, NutriMax, PrimeSupps) = 4,200 provider templates.
Other configured senders currently use English-only Odoo installations. Shared
catalogs can be registered for those brands when additional languages are enabled.
Registration does not mean provider approval or verified destination delivery.

Completion audit: 4,200 saved provider IDs, zero unresolved creation outcomes,
and 4,900 production lookup mappings including tracking/payment aliases. Each
sender has 70 catalogs and 980 mappings. The expected-key audit found zero
missing or invalid mappings. Sample German, Arabic, Chinese and Japanese versions
matched the exact saved text/sender and were pending when inspected.

Email catalogs remain at 95 enabled locale variants out of 98 configured. Kabyle
and Fijian drafts remain quality-held, and Dhivehi remains incomplete/held; all
three use English. Machine-generated copy is not represented as native-reviewed.

## Verification

46 SMS tests, 12 localization/registration tests and 189 after-order tests passed.
Tests cover pending→English, newly approved→localized, re-pending→English,
both versions unapproved→blocked, changed manual preview→fresh approval,
automatic live welcome fallback, wrong-brand exclusion and uncertain-send safety.

The code was deployed through Coolify deployment
`ta3ucbyqexfvffjmxu945zdq`; the live settings endpoint returned HTTP 200 and
`language_policy: live-approved-localized-else-approved-english-v2`.
The live locale endpoint confirmed 98 configured / 95 enabled variants.
Isolated synthetic preparation tests used real MSG91 approval responses for
German, Arabic and Simplified Chinese and selected approved English; no SMS
delivery endpoint was called by these tests.

The English audit checked 182 sender/event mappings (including aliases): 180
were approved and exact matches. Boostgo and Espot `new_order_welcome` were
active but pending (`status=0`, no rejection reason). They remain safely held.
See `docs/msg91-english-approval-audit.json` for this timestamped observation;
approval can change later and is not cached from this report.
