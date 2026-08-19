import re

import requests

from odoo import http
from odoo.http import request


def _clean(value):
    return str(value or "").strip()


def _slug_from_website(website):
    configured = _clean(website.sudo().final_mile_tracking_slug)
    if configured:
        return configured
    source = _clean(website.domain) or _clean(website.name) or "website"
    source = source.lower().replace("https://", "").replace("http://", "").strip("/")
    source = re.sub(r"[^a-z0-9]+", "-", source).strip("-")
    return source or "website"


class FinalMileTrackingController(http.Controller):
    @http.route("/track-order", type="http", auth="public", website=True, sitemap=True)
    def track_order(self, q="", page=1, **kwargs):
        website = request.website.sudo()
        app_url = _clean(website.final_mile_tracking_app_url).rstrip("/")
        query = _clean(q)
        rows = []
        error = ""
        searched = bool(query)
        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError):
            page = 1
        per_page = 25
        total = 0
        total_pages = 1

        if not website.final_mile_tracking_enabled:
            error = "Tracking is not enabled for this website."
        elif not app_url:
            error = "Tracking is not configured for this website."
        elif not website.final_mile_tracking_store_id:
            error = "Tracking store is not mapped for this website."
        else:
            try:
                params = {"page": page, "per_page": per_page}
                if query:
                    params["q"] = query
                params["store_id"] = int(website.final_mile_tracking_store_id)
                response = requests.get(
                    f"{app_url}/api/public/track-orders/{_slug_from_website(website)}",
                    params=params,
                    timeout=12,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("ok"):
                    rows = payload.get("rows") or []
                    total = int(payload.get("total") or len(rows))
                    page = int(payload.get("page") or page)
                    per_page = int(payload.get("per_page") or per_page)
                    total_pages = int(payload.get("total_pages") or 1)
                else:
                    error = payload.get("message") or "Tracking is temporarily unavailable."
            except Exception:
                error = "Tracking is temporarily unavailable."

        return request.render(
            "odoo_final_mile_tracking.track_order_page",
            {
                "query": query,
                "rows": rows,
                "searched": searched,
                "error": error,
                "website": website,
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        )
