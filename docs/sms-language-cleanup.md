# SMS website-language cleanup — 2026-09-22

The app's SMS translation eligibility now checks the order's website.language_ids
and active res.lang records, not all installed languages. Only Nutricity and
GofinchKart are eligible for translated SMS. Unknown/unavailable website language
configuration falls back to approved English. Email catalogs are unchanged.

Production mapping cleanup retained 1,056 translated provider templates:
480 for Nutricity (40 shared catalogs) and 576 for GofinchKart (48). With the two
event aliases this is 1,232 database mappings. Removed 3,668 unused mappings.
FinchKart website 1:74 is disabled; all 66 GofinchKart websites remain enabled.
SMS history and other brands' English mappings are preserved.

Provider cleanup is **partial**: 133 PrimeSupps translations were recoverably
archived, verified by MSG91 confirmations and active count 4,392 to 4,259.
The archive manifest records these exact rows. 3,023 further templates remain
pending, including FinchKart's 12 English templates. The archive endpoint returns
401 for the saved API key; the logged-in dashboard repeatedly times out while
selecting rows and loading archives. Do not treat pending rows as archived.

Registration now audits websites first, permits only the two translated senders,
and refuses to silently restore retired IDs from the registration journal.
Re-enabling a retired language requires verified provider restoration first.
The legacy unjournaled registration script is dry-run only.

Verification: 48 SMS tests, 13 notification tests, and 189 after-order tests passed.
No customer messages were sent as part of this cleanup.
