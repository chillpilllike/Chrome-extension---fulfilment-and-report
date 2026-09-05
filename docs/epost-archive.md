# Reversible ePost archive

Use Archive on a shipment to remove it from the normal ePost work queues. Confirm the action, then use the Archived queue to search, review or Unarchive it later. Unarchive returns it to whichever normal queue matches its saved carrier evidence; it does not necessarily put it in Needs attention.

Archive stores a nullable `archived_at` timestamp on the existing tracking record. Existing installations receive the column through the application's idempotent startup migration. Repeating Archive preserves the first archive timestamp; Unarchive clears it. The operation changes no carrier status, event history, refund fields or shipment identity. Imports and carrier updates do not clear the flag.

Archive visibility applies before pagination and search, to normal queue counts and filtered exports. Archived has its own count and search. Explicit export selections still follow the existing selection/export rules. The UI clears selection after archive changes. The API uses the existing authenticated mutation access rules; no public write permission is added.

This is an ePost workspace archive, not a cancellation or a global order closure. Existing background carrier checks and After-order care cases are unchanged. No age threshold or automatic archiving is introduced.

Validation: 19 workflow/archive/pagination tests, Python compilation and production frontend build. Tests use an isolated in-memory database and never archive live shipments.
