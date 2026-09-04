import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from odoo import http
from odoo.http import request


_logger = logging.getLogger(__name__)


class AfterOrderPortal(http.Controller):
    def _configuration(self):
        parameters = request.env["ir.config_parameter"].sudo()
        return (
            (parameters.get_param("after_order_portal.api_base_url") or "").rstrip("/"),
            parameters.get_param("after_order_portal.bridge_key") or "",
        )

    def _bridge(self, token, method="GET", payload=None):
        base_url, bridge_key = self._configuration()
        if not base_url or not bridge_key:
            raise RuntimeError("After-order portal configuration is incomplete.")
        target = "%s/api/public/after-order-bridge/action/%s" % (base_url, token)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        bridge_request = Request(
            target,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-After-Order-Bridge-Key": bridge_key,
                "X-Website-Host": request.httprequest.host,
            },
        )
        try:
            with urlopen(bridge_request, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except Exception:
                detail = None
            raise RuntimeError(detail or "This order-update link is unavailable.") from exc
        except (URLError, ValueError) as exc:
            raise RuntimeError("The order-update service is temporarily unavailable.") from exc

    def _alternative_products(self, payload):
        case = payload.get("case") or {}
        if case.get("type") != "item_unavailable":
            return []
        Product = request.env["product.template"].sudo()
        domain = [("active", "=", True), ("sale_ok", "=", True)]
        if "is_published" in Product._fields:
            domain.append(("is_published", "=", True))
        if "website_id" in Product._fields:
            domain += ["|", ("website_id", "=", False), ("website_id", "=", request.website.id)]
        words = [word for word in (payload.get("alternative_search_terms") or "").split() if len(word) >= 3][:6]
        search_domain = list(domain)
        if words:
            search_domain += ["|"] * (len(words) - 1) + [("name", "ilike", word) for word in words]
        searched = Product.search(search_domain, limit=24)
        pinned_ids = [int(item.get("product_tmpl_id")) for item in payload.get("pinned_alternatives") or [] if item.get("product_tmpl_id")]
        pinned = Product.browse(pinned_ids).exists()
        ordered = pinned + (searched - pinned)
        return ordered[:24]

    def _page_values(self, token, payload, message=""):
        order_data = payload.get("order") or {}
        order = request.env["sale.order"].sudo().browse(int(order_data.get("id") or 0)).exists()
        return {
            "token": token,
            "payload": payload,
            "order": order,
            "alternatives": self._alternative_products(payload),
            "message": message,
        }

    @http.route("/order-update/<string:token>", type="http", auth="public", website=True, sitemap=False)
    def order_update(self, token, **query):
        try:
            payload = self._bridge(token)
            payload["suggested_action"] = query.get("choice") or ""
            return request.render("after_order_portal.order_update_page", self._page_values(token, payload))
        except RuntimeError as exc:
            _logger.info("After-order link rejected: %s", exc)
            response = request.render("after_order_portal.order_update_error", {"message": str(exc)})
            response.status_code = 410
            return response

    @http.route("/order-update/<string:token>/decision", type="http", auth="public", website=True, methods=["POST"], sitemap=False)
    def order_update_decision(self, token, decision="", selected_product_id=None, **post):
        try:
            selected_id = int(selected_product_id or 0) or None
            result = self._bridge(token, "POST", {"decision": decision, "selected_product_id": selected_id})
            payload = self._bridge(token)
            return request.render(
                "after_order_portal.order_update_page",
                self._page_values(token, payload, result.get("message") or "Your request was recorded."),
            )
        except (RuntimeError, ValueError) as exc:
            _logger.info("After-order decision rejected: %s", exc)
            response = request.render("after_order_portal.order_update_error", {"message": str(exc)})
            response.status_code = 400
            return response
