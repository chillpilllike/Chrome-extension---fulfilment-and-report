"""Bounded, read-only order queries using the existing Odoo client."""
ORDER_FIELDS = ["id", "name", "state", "date_order", "amount_total", "amount_untaxed", "amount_tax", "currency_id", "partner_id", "website_id", "write_date", "margin", "margin_percent"]
LINE_FIELDS = ["id", "name", "product_uom_qty", "price_unit", "discount", "price_subtotal", "price_total", "display_type", "product_id", "is_delivery", "purchase_price", "margin"]


class ScopeError(ValueError):
    pass


class SupportOrders:
    def __init__(self, client, website_id):
        if not isinstance(website_id, int) or isinstance(website_id, bool) or website_id < 1:
            raise ScopeError("An explicit website is required")
        self.client = client
        self.website_id = website_id

    def domain(self):
        # Do not fall back to an unscoped query if website_id is unavailable.
        if "website_id" not in self.client.fields_get("sale.order"):
            raise ScopeError("This source cannot enforce website scope")
        return [["website_id", "=", self.website_id]]

    def list(self, query="", page=1, per_page=25, partner_id=None):
        domain = self.domain()
        if partner_id is not None:
            if not isinstance(partner_id, int) or isinstance(partner_id, bool) or partner_id < 1:
                raise ScopeError("Invalid verified customer")
            domain.append(["partner_id", "=", partner_id])
        if query:
            # Staff search only. An email match does not establish customer identity.
            domain.extend(["|", ["name", "ilike", query[:120]], ["partner_id.email", "=ilike", query[:120]]])
        fields = self.client.existing_fields("sale.order", ORDER_FIELDS)
        rows = self.client.search_read("sale.order", domain, fields, limit=per_page+1,
                                       offset=(page-1)*per_page, order="date_order desc, id desc")
        return {"orders": rows[:per_page], "has_more": len(rows) > per_page, "page": page}

    def detail(self, order_id, partner_id=None):
        domain = self.domain() + [["id", "=", order_id]]
        if partner_id is not None:
            if not isinstance(partner_id, int) or isinstance(partner_id, bool) or partner_id < 1:
                raise ScopeError("Invalid verified customer")
            domain.append(["partner_id", "=", partner_id])
        rows = self.client.search_read("sale.order", domain, self.client.existing_fields("sale.order", ORDER_FIELDS), limit=1)
        if not rows:
            raise LookupError("Order not found in this scope")
        order = rows[0]
        order["items"] = self.client.search_read("sale.order.line", [["order_id", "=", order_id]],
                                                self.client.existing_fields("sale.order.line", LINE_FIELDS), limit=501, order="sequence, id")
        order["items_truncated"] = len(order["items"]) > 500
        order["items"] = order["items"][:500]
        order["odoo_url"] = self.client.order_url(order_id)
        partner = order.get("partner_id")
        order["customer"] = {}
        if isinstance(partner, (list, tuple)) and partner:
            fields = self.client.existing_fields("res.partner", ["id", "name", "email", "phone", "mobile", "street", "street2", "city", "zip", "country_id"])
            matches = self.client.search_read("res.partner", [["id", "=", partner[0]]], fields, limit=1)
            order["customer"] = matches[0] if matches else {}
        order["financials"] = {"available": False, "reason": "Odoo order margin is not available. Historical cost must not be inferred from today's product cost."}
        if isinstance(order.get("margin"), (int, float)) and not isinstance(order.get("margin"), bool):
            revenue = float(order.get("amount_untaxed") or 0)
            margin = float(order["margin"])
            order["financials"] = {"available": True, "basis": "Odoo recorded margin, excluding tax", "cost": revenue-margin, "margin": margin, "margin_percent": margin/revenue*100 if revenue else None}
        return order

    def timeline(self, order_id, page=1):
        # Check website membership before querying chatter; all history remains internal.
        if not self.client.search_read("sale.order", self.domain()+[["id", "=", order_id]], ["id"], limit=1):
            raise LookupError("Order not found in this scope")
        fields = self.client.existing_fields("mail.message", ["id", "date", "author_id", "subject", "body", "message_type", "subtype_id"])
        rows = self.client.search_read("mail.message", [["model", "=", "sale.order"], ["res_id", "=", order_id]],
                                       fields, limit=51, offset=(page-1)*50, order="date desc, id desc")
        return {"events": [{**row, "visibility": "internal", "source": "odoo"} for row in rows[:50]],
                "has_more": len(rows) > 50, "page": page}
