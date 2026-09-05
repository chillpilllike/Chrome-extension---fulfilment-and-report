import unittest

from app.services.after_order_email import render_after_order_email


class EmailDesignTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "odoo_order_name": "DEMO-1042", "store_name": "Store fallback",
            "affected_items": [{"product_name": "Daily essentials", "quantity": 1, "asin": "B000000001", "thumbnail_url": "https://example.test/product.png", "odoo_product_url": "https://example.test/shop/item"}],
            "context": {"website_name": "Nutricity Australia", "website_logo_url": "https://example.test/logo.png", "expected_dispatch_date": "12 September", "latest_status": "Arrived at local facility", "latest_location": "Melbourne, Australia", "tracking_url": "https://example.test/my/orders/1"},
        }

    def render(self, kind="item_unavailable", **kwargs):
        return render_after_order_email({**self.case, "case_type": kind}, "https://example.test/my/orders/1?access_token=demo", actions=["offer_alternatives", "cancel_order"], labels={"offer_alternatives": "Choose an alternative", "cancel_order": "Cancel order"}, **kwargs)

    def test_all_six_templates_share_inter_layout_and_site_footer(self):
        for kind in ("item_unavailable", "expected_dispatch", "delivery_confirmation", "package_lost", "tracking", "trustpilot_review"):
            subject, markup, plain = self.render(kind, template_kind=kind, review_url="https://www.trustpilot.com/review/example.test")
            self.assertIn("DEMO-1042", subject)
            self.assertIn("family=Inter", markup)
            self.assertIn("'Inter', -apple-system", markup)
            self.assertIn('role="presentation"', markup)
            self.assertIn("max-width:600px", markup)
            self.assertIn("This message relates to order DEMO-1042 placed on Nutricity Australia.", markup)
            self.assertIn("Nutricity Australia", plain)
            self.assertNotIn("Amazon", markup)
            self.assertNotIn("B000000001", markup)
            self.assertNotIn("all action links expire", markup)

    def test_actions_preserve_existing_query_parameters(self):
        _, markup, plain = self.render()
        self.assertIn("access_token=demo&amp;choice=offer_alternatives", markup)
        self.assertIn("access_token=demo&choice=cancel_order", plain)
        self.assertIn("Choose an alternative", markup)

    def test_single_item_action_filter_is_not_overridden_by_design(self):
        _, markup, _ = render_after_order_email({**self.case, "case_type": "item_unavailable"}, "https://example.test/order", actions=["cancel_order"], labels={"cancel_order": "Cancel order"})
        self.assertNotIn("exclude_item_and_proceed", markup)
        self.assertNotIn("choice=offer_alternatives", markup)

    def test_unsubscribe_only_when_supplied(self):
        self.assertNotIn("Unsubscribe", self.render()[1])
        _, markup, plain = self.render("tracking", unsubscribe_url="https://example.test/unsubscribe/demo")
        self.assertIn("Unsubscribe from movement and review emails", markup)
        self.assertIn("https://example.test/unsubscribe/demo", plain)

    def test_dynamic_content_is_escaped_and_unsafe_images_are_omitted(self):
        self.case["context"]["website_name"] = '<script>alert("test")</script>'
        self.case["affected_items"][0]["thumbnail_url"] = "javascript:alert(1)"
        self.case["affected_items"][0]["product_name"] = "A < B & C"
        _, markup, _ = self.render()
        self.assertNotIn("<script>", markup)
        self.assertNotIn("javascript:", markup)
        self.assertIn("A &lt; B &amp; C", markup)

    def test_missing_logo_and_photo_have_text_fallbacks(self):
        self.case["context"]["website_logo_url"] = ""
        self.case["affected_items"][0]["thumbnail_url"] = ""
        _, markup, _ = self.render()
        self.assertIn("Nutricity Australia", markup)
        self.assertNotIn('src=""', markup)
