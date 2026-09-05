import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from odoo import http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.addons.sale.controllers.portal import CustomerPortal


_logger = logging.getLogger(__name__)


class AfterOrderPortal(CustomerPortal):
    @http.route()
    def portal_order_page(self, order_id, **kwargs):
        response = super().portal_order_page(order_id, **kwargs)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def _is_after_order_admin(self):
        return request.env.user.has_group("base.group_system")

    def _configuration(self):
        parameters = request.env["ir.config_parameter"].sudo()
        return (
            (parameters.get_param("after_order_portal.api_base_url") or "").rstrip("/"),
            parameters.get_param("after_order_portal.bridge_key") or "",
        )

    def _bridge(self, token, method="GET", payload=None, order_id=None):
        if order_id is None and not re.fullmatch(r"[a-f0-9]{64}", token or ""):
            raise RuntimeError("This order-update link is invalid.")
        base_url, bridge_key = self._configuration()
        if not base_url or not bridge_key:
            raise RuntimeError("After-order portal configuration is incomplete.")
        path = "orders/%s" % int(order_id) if order_id is not None else "action/%s" % token
        target = "%s/api/public/after-order-bridge/%s" % (base_url, path)
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
                "X-Odoo-Database": request.env.cr.dbname,
                "X-After-Order-Admin": "true" if self._is_after_order_admin() else "false",
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
        except (URLError, ValueError, TimeoutError) as exc:
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
        pinned = Product.search(domain + [("id", "in", pinned_ids)])
        pinned = Product.browse([pid for pid in pinned_ids if pid in pinned.ids])
        ordered = pinned + (searched - pinned)
        return ordered[:24]

    def _validate_payload(self, payload, order):
        if int((payload.get("order") or {}).get("id") or 0) != order.id:
            raise RuntimeError("This request belongs to another order.")
        if order.website_id and order.website_id != request.website:
            raise RuntimeError("This request belongs to another website.")
        if payload.get("test_mode") and not self._is_after_order_admin():
            raise RuntimeError("Test actions are available to website administrators only.")

    def _sale_order_get_page_view_values(self, order_sudo, access_token, values, history_session_key, **kwargs):
        values = super()._sale_order_get_page_view_values(order_sudo, access_token, values, history_session_key, **kwargs)
        values["after_order_panels"] = []
        values["after_order_error"] = ""
        values["after_order_access_token"] = access_token or ""
        values["after_order_message"] = request.session.pop("after_order_message_%s" % order_sudo.id, "")
        if order_sudo.website_id and order_sudo.website_id != request.website:
            return values
        try:
            token = request.params.get("after_order_token")
            entries = [{"token": token}] if token else self._bridge(None, order_id=order_sudo.id).get("actions", [])
            for entry in entries:
                payload = self._bridge(entry["token"])
                self._validate_payload(payload, order_sudo)
                values["after_order_panels"].append({
                    "token": entry["token"], "payload": payload,
                    "alternatives": [] if payload.get("locked") else self._alternative_products(payload),
                })
        except RuntimeError:
            # A missing bridge or hidden test panel must never break order viewing.
            if self._is_after_order_admin():
                values["after_order_error"] = "Order updates are temporarily unavailable. Check the bridge configuration or link."
        return values

    @http.route("/order-update/<string:token>", type="http", auth="public", website=True, sitemap=False)
    def order_update(self, token, **query):
        try:
            payload = self._bridge(token)
            order_id = int(payload["order"]["id"])
            target = "/my/orders/%s?%s" % (order_id, urlencode({"after_order_token": token}))
            if request.env.user._is_public():
                return request.redirect("/web/login?" + urlencode({"redirect": target}))
            return request.redirect(target)
        except (RuntimeError, ValueError, KeyError) as exc:
            _logger.info("After-order link rejected: %s", exc)
            response = request.render("after_order_portal.order_update_error", {"message": str(exc)})
            response.status_code = 410
            return response

    @http.route("/order-update/<string:token>/decision", type="http", auth="public", website=True, methods=["POST"], sitemap=False)
    def order_update_decision(self, token, decision="", selected_product_id=None, **post):
        try:
            payload = self._bridge(token)
            order_id = int(payload["order"]["id"])
            order = self._document_check_access("sale.order", order_id, access_token=post.get("access_token"))
            self._validate_payload(payload, order)
            if payload.get("locked"):
                raise RuntimeError("This request has already been completed.")
            selected_id = int(selected_product_id or 0) or None
            result = self._bridge(token, "POST", {"decision": decision, "selected_product_id": selected_id})
            request.session["after_order_message_%s" % order_id] = result.get("message") or "Your request was recorded."
            query = {"after_order_token": token}
            if post.get("access_token"):
                query["access_token"] = post["access_token"]
            return request.redirect("/my/orders/%s?%s#after-order-updates" % (order_id, urlencode(query)), code=303)
        except (RuntimeError, ValueError, AccessError, MissingError) as exc:
            _logger.info("After-order decision rejected: %s", exc)
            response = request.render("after_order_portal.order_update_error", {"message": str(exc)})
            response.status_code = 400
            return response
