# Country-specific refund bank methods

The refund form uses the shared Airwallex account's beneficiary form schema for
countries, transfer methods, local clearing systems and recipient identifiers.
Country/currency/account-holder availability comes from Airwallex, not a universal
hard-coded list. This flow sends bank-account payouts, including supported local
identifier routes; it does not implement card, stablecoin or digital-wallet payouts.

Australian AUD PayID: select Local → NPP → identifier type. The account schema
checked on 26 September 2026 offers email and phone for personal recipients, and
also ABN/organisation ID for business recipients. The account did not offer BPAY.
Canada Interac email/phone and other countries' schema refresh selectors use the
same mechanism. Bank-code option lists show both code and bank name with search.

Both the browser and server resolve fields marked `refresh` before constructing
the beneficiary. Country/method/identifier changes clear obsolete destinations.
Only fields present in the resolved schema enter the beneficiary payload. The
server validates the beneficiary and transfer through Airwallex before review.
Provider form regexes are not locally enforced: the observed PayID form schema
still returned a BSB regex for email/phone, while its fields correctly changed.
Provider validation endpoints remain authoritative.

Review and email destinations are masked, using the actual account or recipient
identifier rather than a contact email. Amount, daily-limit, duplicate, idempotency,
funding and site-specific notification guards continue to run unchanged.

Sources:
- https://www.airwallex.com/docs/payouts/payout-network/bank-accounts/australia
- https://www.airwallex.com/docs/payouts/beneficiaries/using-api-and-form-schemas
- https://www.airwallex.com/docs/api/payouts/beneficiaries/generate_beneficiary_form_schemas

Verification:
- `python -m unittest tests.test_refund_schema tests.test_airwallex_refunds tests.test_manual_refunds tests.test_airwallex_refund_errors tests.test_airwallex_api tests.test_refund_emails tests.test_refund_odoo tests.test_refund_performance`
- `node --test tests/refund_schema.test.cjs` (after frontend npm install)
- `npm run build --prefix frontend`
- Fixture-based browser check of BSB → NPP → email → phone; no payout submitted.
- Fixtures contain public schema definitions/example values, not customer data.
