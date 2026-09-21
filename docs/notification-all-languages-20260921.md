# All configured notification languages — 21 September 2026

## Inventory

Fresh read-only `res.lang` queries across all 11 active Odoo connections found
98 distinct configured/active locale codes. Nutricity and NutriMax each have 83;
Gofinchkart has 77; Nutrihub has 2; the other seven have English only.
Vietnamese (`vi_VN`) was absent from the earlier 97-locale registry and is now
included. These are installed/active Odoo languages, not a claim that each
language is published on every individual website.

The manifest maps these locales to shared catalogs, retaining separate Chinese
Simplified/Traditional/Hong Kong, Serbian Cyrillic/Latin, Portuguese
Brazil/Portugal/Angola, Swiss German and North Korean variants. Customer/order
language takes precedence over website default; an unknown or incomplete
catalog continues to fall back to English.

## Translation provenance and boundaries

New catalogs are machine-generated using the existing LibreDesk **unsaved draft
translation** endpoint. Only static template wording was submitted. No customer
names, addresses, orders, conversations or messages were submitted, created,
changed or sent. The existing provider/settings were not changed. Credentials
were read from the approved Keychain entries in memory only.

Each generated catalog records `translation_method: machine_generated` and
`human_reviewed: false`. Automated coverage/placeholder/numeric/render checks
are not a native-speaker or legal review. Low-resource-language copy in
particular should be reviewed by a fluent speaker. The Settings coverage list
exposes that distinction; it must not describe these as human-reviewed.

Coverage includes 154 shared email strings, 12 SMS event templates, and 16
standalone Odoo replacement-payment strings per new catalog. Financial actions,
approval guards, customer URLs and payment/refund workflows are unchanged.
Odoo product names depend on store translations. Raw carrier events and
staff-entered alternative descriptions are not automatically translated.

### Explicit quality holds

Kabyle and Fijian have complete machine-generated drafts but are blocked from
sending after inconsistent/incorrect cancellation/refund back-translations.
Dhivehi has only a partial draft: repeated translation/validation failures and
unreliable wording prevented completion. These three locales remain English
fallbacks in the app, SMS and Odoo payment emails. They are **not** represented as
completed live translations. Native-language translators must supply/review the
copy before their `delivery_blocked` flags are removed. The other 95 configured
locale variants have complete catalogs enabled, including existing English and
French. Structural/render tests do not establish native-level linguistic quality.

## SMS is a separate approval gate

Local SMS catalogs are ready for Odoo/Twilio and for MSG91 template registration.
MSG91 needs its own approved fixed-brand template ID for each language/event/
sender. This change does not invent IDs, bypass approval, automatically
register thousands of provider templates, or resend pending customer messages.
The existing approved-English fallback remains while localized IDs are missing
or unapproved. `scripts/setup_localized_sms.py` provides dry-run registration
output and appends a fixed brand suffix when a translated URL variable would
otherwise be the final content.

## New-order SMS diagnosis during this work

Live settings had SMS enabled, MSG91 selected, test mode off and Nutricity Canada
transactional SMS enabled. Welcome emails 861–864 (NC29517, NC29552, NC29375,
NC29566) were delivered but had no SMS rows; case events recorded repeated
preparation blocks. Their dedicated welcome template
`6ab11db923f3be0218044692` matched the configured content/sender but returned
active_status=1, status=0, not the approved status=1 required by the sender.
No customer retry was manually triggered. Automatic recovery only considers
eligible welcomes in the existing 24-hour window.

## Deployment

App catalogs are bundled at build time: no live translation dependency exists.
The authoritative after_order_portal repository receives payment-only copies
via `scripts/sync_notification_locales.py`. Upgrade Odoo addon 18.0.2.2.9 for its
payment-email changes; a Git push alone is not proof of module upgrade.
Existing queued/sent email snapshots are not silently rewritten.

## Verification

- 98-locale coverage/hold assertions and 1,274 synthetic email renders (13
  template kinds per locale), plus SMS placeholder/URL and payment-copy checks.
- Arabic and Japanese synthetic emails inspected visually in the browser.
- 189 existing after-order tests, 40 SMS tests, 11 localization tests,
  45 Odoo addon tests, and the frontend production build passed.
- 69 catalogs received automated back-translation screening of the critical
  three-day cancellation/refund paragraph; results are saved separately.
  Screening is diagnostic only, not proof of linguistic accuracy.
