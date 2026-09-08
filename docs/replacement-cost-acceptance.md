# Team-funded replacement uplift

The care timeline now offers **Accept extra cost — no quotation** for a selected,
more expensive replacement before a quotation exists. The team must type the
exact difference and provide a reason. This is not an automatic dollar threshold.

Approval is stored in Odoo and the app timeline against the order, line, selected
product, selection version, pricing signature and currency. A customer change
requires a fresh approval. Approval does not shorten the 24-hour window, bypass
another line's unresolved decision, or override fulfilment/sourcing guards.

After the deadline, Odoo verifies the approval under the same order lock used by
quotation creation. An approved uplift creates no quotation and is explicitly
marked as a business-absorbed cost, never as a customer payment. Existing quotes
(including cancelled quotes) and refunds block this control; resolving them is a
separate finance action. Test mode makes no live approval or fulfilment changes.

Cheaper replacements still retain their refund workflow. The user has been asked
whether to permit fulfilment while preserving the refund due, or require recorded
customer agreement for any refund waiver; neither is silently inferred here.

Upgrade after_order_portal to 18.0.2.2.1 for the native cost-approval field and RPC.
Live processing remains disabled pending sandbox verification.
