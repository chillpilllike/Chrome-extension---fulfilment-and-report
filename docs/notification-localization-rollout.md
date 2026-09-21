# Notification localization rollout — 21 September 2026

Language priority: order language when available, customer language, website
default, then English. Catalogs are shared across websites; branding, customer
links, currency and order identifiers remain site/order-specific.

## Coverage

- The manifest records 97 installed Odoo locale variants from the audited stores.
- English and French static application email copy are ready. French includes
  subjects, actions, policy text and the separate Odoo payment-email body.
- Other languages deliberately use English. Registry inclusion does not mean
  translation completion. Complete, reviewed catalogs are still required.
- Product translations depend on Odoo product data. Carrier event text is carrier
  data and is not machine-translated. Saved alternative names may retain their
  original language.
- Twelve French Nutricity MSG91 templates have been registered. They are used
  only after exact sender/text and provider approval checks succeed; otherwise
  an approved English template is used. Other brands need their own approved
  localized MSG91 mappings. Odoo/Twilio use the local SMS catalog directly.

## Safety and operations

- No notification was sent by this rollout's tests or template registration.
- Existing approval, manual-refund and test-recipient guards remain unchanged.
- Existing queued/sent bodies are not silently rewritten. A newly detected
  customer-language change blocks an already localized approval/retry until the
  message is regenerated and reviewed.
- Email/SMS previews expose requested and actual language and fallback reasons.
- Settings lists catalog readiness. SMS previews estimate GSM/Unicode segments;
  actual billing remains provider/destination dependent.
- Addon version 18.0.2.2.8 must be upgraded on each Odoo installation for the
  separate replacement-payment template changes to take effect.

For additional languages, add complete static catalogs, verify interpolation
placeholders and render tests, review translations, then register brand-specific
MSG91 templates using scripts/setup_localized_sms.py (dry run by default).
Never send customer data to translation services.
