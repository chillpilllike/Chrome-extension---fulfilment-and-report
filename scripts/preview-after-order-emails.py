"""Generate offline demo emails. Does not read credentials, DB or send email."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.after_order_email import render_after_order_email

destination = Path(__file__).resolve().parents[1] / "output" / "email-design-preview"
destination.mkdir(parents=True, exist_ok=True)
labels = {"proceed": "Yes, continue my order", "cancel_order": "Cancel my order", "offer_alternatives": "Choose an alternative", "exclude_item_and_proceed": "Remove this item and continue", "received": "Yes, I received it", "not_received": "No, I haven’t received it", "replacement": "Request a replacement", "refund": "Request a refund"}
examples = {
    "item_unavailable": ["offer_alternatives", "exclude_item_and_proceed", "cancel_order"],
    "expected_dispatch": ["proceed", "cancel_order"],
    "delivery_confirmation": ["received", "not_received"],
    "package_lost": ["replacement", "refund"], "tracking": [], "trustpilot_review": [],
}
for kind, actions in examples.items():
    case = {"case_type": kind, "odoo_order_name": "DEMO-1042", "store_name": "Nutricity Australia",
            "affected_items": [{"product_name": "Daily Essentials · Vitamin D3", "quantity": 1, "thumbnail_url": "https://placehold.co/128x128/f6f8f6/345244/png?text=Product", "odoo_product_url": "https://example.test/shop/demo"}],
            "context": {"website_name": "Nutricity Australia", "expected_dispatch_date": "12 September 2026", "latest_status": "Arrived at the local delivery facility", "latest_location": "Melbourne, Australia", "tracking_url": "https://example.test/my/orders/demo"}}
    _, markup, _ = render_after_order_email(case, "https://example.test/my/orders/demo", actions=actions, labels=labels, template_kind=kind, review_url="https://www.trustpilot.com/review/example.test", unsubscribe_url="https://example.test/unsubscribe/demo" if kind in {"tracking", "trustpilot_review"} else "")
    (destination / f"{kind}.html").write_text(markup)
print(destination)
