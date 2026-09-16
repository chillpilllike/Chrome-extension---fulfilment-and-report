from test_shopify_fulfilment_search import ShopifyFulfilmentSearchTests
from app import main


class ShopifyTitleSearchTests(ShopifyFulfilmentSearchTests):
    def setUp(self):
        super().setUp()
        self.conn.executescript('''
            CREATE TABLE shopify_title_reviews (job_id TEXT, status TEXT, snapshot_json TEXT);
            INSERT INTO shopify_title_reviews SELECT id, 'pending', '{"items":[]}' FROM shopify_fulfilment_jobs;
        ''')

    def title_search(self, **kwargs):
        self.conn.execute("UPDATE shopify_fulfilment_jobs SET status='pending_review'")
        return main.api_shopify_title_reviews(**kwargs)

    def test_title_order_and_reference_search(self):
        for term, expected in [('nc24059', 2), ('#987654321', 1), ('dtc1050', 1), ('_%', 1), ('missing', 0)]:
            with self.subTest(term=term):
                self.assertEqual(self.title_search(search=term)['total'], expected)
        self.assertEqual(self.title_search(search='24059', store_id=1)['total'], 1)

    def test_only_pending_reviews_are_returned(self):
        self.title_search()
        self.conn.execute("UPDATE shopify_title_reviews SET status='approved' WHERE job_id='a'")
        self.conn.execute("UPDATE shopify_fulfilment_jobs SET status='completed' WHERE id='c'")
        self.assertEqual(main.api_shopify_title_reviews(search='24059')['total'], 0)

    def test_title_search_filters_before_pagination(self):
        for i in range(30):
            self.conn.execute("INSERT INTO shopify_fulfilment_jobs VALUES (?,1,?,'dtc','pending_review',?,'',1)", (f'p{i}', f'PAGE{i:02}', f'2026-10-{i+1:02}'))
            self.conn.execute("INSERT INTO shopify_title_reviews VALUES (?,'pending','{\"items\":[]}')", (f'p{i}',))
        result = main.api_shopify_title_reviews(search='page', page=2)
        self.assertEqual(result['total'], 30)
        self.assertEqual(len(result['reviews']), 5)
        self.assertEqual(result['reviews'][0]['odoo_order_name'], 'PAGE25')
