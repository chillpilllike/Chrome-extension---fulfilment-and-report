# Preserve expected shipments during tracking refresh

NC26931 has four order lines: Amazon orders 113-0764624-1889004 and 113-5491407-3133014, plus two third-party products from Order #69070. The latter have two independent assigned USPS tracking numbers.

The second Amazon purchase has delivered shipment Nfr8Nl1Yc but no carrier tracking number in its latest payload. `refresh_dispatch_packages_from_tracking` deleted every non-physical row before applying updates, including this legitimate expected shipment. The replacement now removes only aliases proven to be superseded by a physical package. Repeated refreshes preserve expected shipments and their source purchase links.

Both individual and bulk dispatch upserts previously allowed a shared/reported barcode to overwrite its purchase, Odoo order and line ownership while retaining the old receipt. Upserts now protect existing ownership. Conflicting evidence remains visible in a separate unconfirmed placeholder marked for association review; it never borrows another purchase's receipt. Bulk batches also preserve conflicting purchases before either exists in the database.

Pickup rows without a tracking number now explicitly link to the shipment for tracking capture. Amazon delivery is not treated as a warehouse scan. Third-party tracking and receipt states are unchanged.

Audit: fourteen source lines with delivered/no-barcode shipment evidence checked. Eight initially absent synthetic codes reduced to one truly missing shipment after matching existing shipment identities; seven were already represented and were not duplicated. Only NC26931 required restoration.

Validation: exact production-data replay in isolated PostgreSQL displays two Amazon and two third-party entries after three tracking refreshes; conflicting barcode preserves the original owner and receipt. Three focused ownership/visibility tests pass, 59 relevant existing/new tests pass with one optional PostgreSQL test skipped in the ordinary run, and frontend TypeScript/Vite build passes. Full suite ran 1,183 tests; all seven failures reproduced on the untouched production baseline.
