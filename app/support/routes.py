"""Staff-only integration API. No endpoint sends messages or exposes guest order data."""
from datetime import datetime, timezone
import hmac
from html.parser import HTMLParser
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from app.support.odoo import SupportOrders, ScopeError
from app.support.policy import Evidence, public_order


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, value):
        self.parts.append(value)


def create_router(*, get_store, list_stores, client_factory, db, admin_token):
    def staff(request: Request, response: Response):
        # Require a configured staff credential even if legacy app auth is disabled.
        expected = admin_token()
        supplied = request.headers.get("X-Admin-Token") or request.cookies.get("admin_access_token") or ""
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            raise HTTPException(401, "Staff authentication required")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie, X-Admin-Token"
    router = APIRouter(prefix="/api/support", dependencies=[Depends(staff)], tags=["support"])

    def source(store_id, website_id):
        return SupportOrders(client_factory(get_store(store_id)), website_id)

    @router.get("/status")
    def status():
        return {"mode": "staff_preview", "customer_delivery_enabled": False,
                "stores": [{"id": row["id"], "name": row["name"]} for row in list_stores()],
                "readiness": [
                    {"name": "Scoped Odoo lookup and original order lines", "ready": True},
                    {"name": "Staff-only chatter and fulfilment evidence", "ready": True},
                    {"name": "Verified customer identity and conversation binding", "ready": False},
                    {"name": "LibreDesk inline panel and website inbox mapping", "ready": False},
                    {"name": "Normalized outbound shipment evidence", "ready": False},
                    {"name": "Customer autopilot delivery", "ready": False}]}

    @router.get("/websites")
    def websites(store_id: int = Query(gt=0)):
        try:
            client = client_factory(get_store(store_id))
            rows = client.search_read("website", [], ["id", "name", "domain"], limit=501, order="id")
            return {"websites": rows[:500], "truncated": len(rows) > 500}
        except Exception:
            raise HTTPException(503, "Website lookup is temporarily unavailable") from None

    @router.get("/orders")
    def orders(store_id: int = Query(gt=0), website_id: int = Query(gt=0), q: str = Query("", max_length=120),
               page: int = Query(1, ge=1, le=10000), per_page: int = Query(25, ge=1, le=50)):
        try:
            return source(store_id, website_id).list(q, page, per_page)
        except ScopeError:
            raise HTTPException(409, "This source cannot enforce website scope") from None
        except Exception:
            raise HTTPException(503, "Order lookup is temporarily unavailable. This does not mean there are no orders.") from None

    @router.get("/orders/{order_id}")
    def detail(order_id: int, store_id: int = Query(gt=0), website_id: int = Query(gt=0)):
        try:
            service = source(store_id, website_id)
            order = service.detail(order_id)
        except LookupError:
            raise HTTPException(404, "Order not found in this website") from None
        except Exception:
            raise HTTPException(503, "Order details are temporarily unavailable") from None
        now = datetime.now(timezone.utc)
        # Raw app states are NOT sufficient proof of outbound handover or complete receipt.
        # Until normalized evidence is available, confirmed orders have no ETA promise.
        preview = public_order(order, Evidence(observed_at=now))
        internal = []; warnings = []
        try:
            with db() as conn:
                internal = [dict(row) for row in conn.execute(
                    """SELECT id, asin, quantity, state, ordered_at, amazon_order_id, amazon_status
                       FROM order_lines WHERE store_id=? AND odoo_order_id=? ORDER BY id LIMIT 501""",
                    (store_id, order_id)).fetchall()]
        except Exception:
            warnings.append("Internal fulfilment evidence is temporarily unavailable.")
        return {"order": order, "internal_fulfilment": internal[:500], "internal_truncated": len(internal) > 500,
                "customer_preview": preview, "observed_at": now.isoformat(), "warnings": warnings,
                "automation": {"mode": "draft", "can_send": False,
                               "reason": "Verified conversation binding and shipment evidence are not configured."}}

    @router.get("/orders/{order_id}/timeline")
    def timeline(order_id: int, store_id: int = Query(gt=0), website_id: int = Query(gt=0), page: int = Query(1, ge=1, le=10000)):
        try:
            data = source(store_id, website_id).timeline(order_id, page)
            for event in data["events"]:
                parser = PlainText(); parser.feed(str(event.get("body") or ""))
                event["body"] = " ".join(parser.parts)[:20000]
            return data
        except LookupError:
            raise HTTPException(404, "Order not found in this website") from None
        except Exception:
            raise HTTPException(503, "Order history is temporarily unavailable") from None
    return router
