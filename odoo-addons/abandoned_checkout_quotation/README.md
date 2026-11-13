# Abandoned Checkout Quotations for Odoo 18

Install `abandoned_checkout_quotation` from your server's custom addons path. It requires Odoo eCommerce (`website_sale`) and the standard Sales quotation template. Your Odoo mail server and quotation PDF rendering must already be configured.

Open **Website → Configuration → Settings**, select the website, and look under **Email & Marketing** for **Automatically send abandoned quotations**. The setting is enabled by default. While enabled, the addon's quotation flow takes precedence over Odoo's built-in automatic cart reminder on that website.

Every 15 minutes Odoo checks up to 25 eligible website quotations created since installation. An order qualifies after six hours without a recorded order, order-line, or payment status update. It must be an unpaid, unexpired draft with a known customer email and at least one product. Pending, authorized, and completed payment transactions exclude the quotation. Each order gets its own email. A quotation already marked as sent, or tracked by Odoo as having received a cart recovery email, is skipped.

Odoo queues its **Sales: Send Quotation** email, with the standard quotation report and portal link, then marks the order **Quotation Sent** in the same transaction. The mail queue's schedule controls actual delivery. Failed delivery can be handled through the existing outgoing email record. The addon uses Odoo's existing scheduler and database pool.

For orders created before installation, choose **Review older quotations** in the same settings section. Select the earliest creation date to include in your Odoo timezone, then choose **Check quotations**. **Queue emails** queues the displayed orders that still qualify. Preview again for the next batch if more than 25 qualify. Repeat for each website you want to review.
