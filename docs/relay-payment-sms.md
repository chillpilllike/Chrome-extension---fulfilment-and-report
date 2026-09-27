# Relay payment-link SMS

Relay links now use the existing SMS outbox, MSG91 delivery/receipt integration, website sender mapping and phone suppression checks. Enable the Relay payment-link SMS checkbox in Customer SMS settings; the general SMS and Relay live switches also apply.

One SMS per store/order, as soon as the captured link is bound and a source payment email record exists, with a minute-worker fallback. Existing Relay orders are eligible only within the last 48 hours by current Odoo order date. Repeated uploads and branded email resends cannot reserve a second SMS. No recurring unpaid-order reminders or non-Relay orders are included.

Before preparing and sending, revalidate Relay identity/link plus the current unconfirmed Odoo order, billing email/phone/suppression, website, transactions and invoice payment state. Other completed/authorized or pending gateway payments, initiated Relay payments, confirmed/cancelled orders, changed customers and orders older than two days are blocked. Unknown provider acceptance is never blindly resent.

Dedicated MSG91 relay_request templates must be approved. Pending approval remains blocked and is checked by the minute fallback. SMS status and previews use the existing SMS log.
