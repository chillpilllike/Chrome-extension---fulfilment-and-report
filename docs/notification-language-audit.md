# Customer notification language audit — 21 September 2026

Read-only production Odoo and source-code inspection. No notifications sent or production settings changed during this audit.

## Current implementation

- App-rendered email text, subjects, buttons and footer are hard-coded English.
- Email HTML language is hard-coded `en`.
- Recipient hydration reads customer email but not customer language.
- MSG91 mappings select by website and event, not language; currently mapped texts are English.
- Native Odoo email translations do not automatically translate the separate fulfilment app templates.
- The separate Odoo replacement-payment email path also needs inspection and localization.

## Odoo website language inventory

Installed languages and website-enabled languages are different. Counts below include regional variants, not distinct languages.

| Connection | Installed language records | Website-enabled language codes |
| --- | --- | --- |
| Nutricity USA (multiwebsite connection) | 83 | 56 |
| Gofinchkart (multiwebsite connection) | 77 | 60 |
| NutriMax Australia | 83 | en_AU only |
| Nutrihub | 2 | en_CA only |
| Secretgreen, Wildkart, Boostgo, Suppcity, Vitagen, Vitashop | 1 each | en_US |
| Espot | 1 confirmed in first query | Website query timed out; requires recheck |

### Nutricity website-enabled locales

bg_BG, cs_CZ, da_DK, de_CH, de_DE, el_GR, en_AU, en_CA, en_GB, en_IN, en_NZ, en_US, es_AR, es_CL, es_CO, es_CR, es_ES, es_MX, es_UY, et_EE, fi_FI, fr_BE, fr_CA, fr_CH, fr_FR, he_IL, hr_HR, hu_HU, id_ID, it_IT, ja_JP, ko_KR, lb_LU, lo_LA, lt_LT, lv_LV, mk_MK, ms_MY, nb_NO, nl_BE, nl_NL, pl_PL, pt_BR, pt_PT, ro_RO, ru_RU, sk_SK, sl_SI, sq_AL, sr@Cyrl, sv_SE, th_TH, tr_TR, uk_UA, zh_CN, zh_TW

### Gofinchkart website-enabled locales

af_ZA, bg_BG, ca_ES, cnr_ME, cs_CZ, cy_GB, da_DK, de_CH, de_DE, dv_MV, el_GR, en_AU, en_GB, en_IN, en_NZ, en_US, es_AR, es_ES, et_EE, fi_FI, fj_FJ, fr_BE, fr_CA, fr_CH, fr_FR, ga_IE, gd_GB, hi_IN, hr_HR, hu_HU, id_ID, is_IS, it_IT, ja_JP, ko_KP, la_LA, lb_LU, lo_LA, lt_LT, lv_LV, mk_MK, ms_MY, mt_MT, nl_BE, nl_NL, pl_PL, pt_AO, ro_RO, ru_RU, sk_SK, sl_SI, sq_AL, sr@Cyrl, sv_SE, th_TH, tr_TR, uk_UA, zh_CN, zh_TW, zu_ZA

## Actual recent customer language sample

Confirmed Odoo orders from 1 September 2026, up to 1,000 newest per connection.
Counts are distinct customer records in that sample, not order counts.

Nutricity: en_AU 156, en_CA 82, en_NZ 47, en_US 29, fr_CA 19, en_GB 5,
de_DE 3, nl_NL 2, pl_PL 2, lt_LT 2, fr_BE 1, hu_HU 1, en_IN 1.
Secretgreen: en_US 4. Vitashop: en_US 2.
Other successfully sampled connections returned no matching confirmed orders.

## Required implementation

1. Resolve the explicit order language when available, otherwise the order customer's Odoo language; use website default only when no customer language is set. Never infer language from domain or phone country.
2. Translate complete subject, HTML, plain text, buttons, notices and footer. Preserve order IDs, URLs, references and amounts.
3. Use translated Odoo product names where available. Preserve raw carrier evidence separately.
4. Store selected locale and template version on each prepared notification. Changing language/content invalidates prior send approval.
5. Support regional variants and RTL layout where needed.
6. Register and verify language-specific MSG91 templates per configured sender/event. Never reuse an English template ID for translated content.
7. Show requested language, actual sent language, fallback reason and SMS segment estimate in previews/logs.
8. Test every template with representative localized customer records without sending to customers.

## Decision required before live rollout

If a translation or approved SMS template is missing: send English with a visible fallback warning, or hold the notification for the team?
Neither silently switching important messages to a hold nor silently promising fully localized delivery is appropriate.

MSG91 Unicode SMS can consume more segments (70 characters for a single Unicode SMS, 67 per concatenated segment).
Sources:
- https://docs.msg91.com/sms/add-flow
- https://msg91.com/help/template/how-to-create-flow-id-to-send-sms-via-api
- https://msg91.com/help/more/what-is-the-character-limit-for-a-single-credit-in-english-unicode-how-is-credit-calculated

