import unittest
from unittest.mock import patch
from app.services.website_email_policy import require_email_enabled
from app.services.after_order import ResendEmailProvider


class WebsiteEmailPolicyTests(unittest.TestCase):
    def test_finchkart_sender_variants_blocked(self):
        for sender in ('notifications@finchkart.com', 'FinchKart <notifications@FINCHKART.COM>', 'notifications@backend.finchkart.com'):
            with self.assertRaises(ValueError):
                require_email_enabled(message={'from':sender})

    def test_site_identity_blocks_even_wrong_or_test_sender(self):
        with self.assertRaises(ValueError):
            require_email_enabled(case={'store_id':1,'website_id':74})

    def test_other_sites_unaffected(self):
        for domain in ('gofinchkart.com','gofinchkart.com.au','nutricity.ca','finchkart.com.example.org'):
            require_email_enabled(message={'from':'notifications@'+domain})

    def test_no_provider_request_for_paused_sender(self):
        with patch('app.services.after_order.requests.post') as post:
            with self.assertRaises(ValueError):
                ResendEmailProvider('test').send({'from':'notifications@finchkart.com'},idempotency_key='test')
            post.assert_not_called()
