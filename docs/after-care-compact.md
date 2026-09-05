# Compact after-order case review

The page is a triage queue, not a dashboard. Review a case's evidence and current customer decision, then use the existing permitted actions. The previous large header, duplicated summary cards and expanded card grid have been replaced with a compact queue bar and expandable rows.

Closed rows show order/store, issue/latest request, case status/priority and a suggested next review step. The next-step text is guidance only; it never changes eligibility or executes an action. Open rows retain the complete evidence, refund notes, unavailable-email warnings, carrier-scan caveats and original action buttons.

Backend rules inspected and preserved:

- Open excludes approved and resolved, but includes approved-pending-execution.
- Cases are ordered by severity, then update time.
- Search matches order, tracking and case title; it does not query product names.
- Queue counts cover the store/cutoff scope, not the text search.
- Earlier and undated orders are excluded by the backend cutoff.
- Confirmation validates sourcing approval, permitted removal, latest decision version and existing confirmation. Received delivery-confirmation cases may resolve; other confirmations may require further action. Approval is not completion.
- Requests still load the first 50 cases; pagination behavior is unchanged.

Validation: production build and AST contract comparison retain all original API/confirmation calls, effects and action bindings. No live business action was used for testing. The release also includes the previously requested pickup filter/search/action-panel layout.
