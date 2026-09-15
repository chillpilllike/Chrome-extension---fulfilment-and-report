import unittest
from unittest.mock import Mock, patch
from app.services import shopify_title_review as review
from app.services.asin import encode_asin, decode_asin_reference
from test_shopify_title_review import ReviewTests

class ReferenceTests(unittest.TestCase):
    def client(self, description='<p>B01M1989NK</p>', code=False):
        client = Mock()
        row = {'id':13284, 'default_code':code, 'description':description}
        client.read.side_effect = lambda *args: [dict(row)]
        client._exec.side_effect = lambda model, method, args: row.update(args[1]) or True
        return client, row
    def test_verified_notes_encoded_persisted_and_read_back(self):
        client, row = self.client()
        product = {'id':13284}
        sku = review.ensure_internal_reference(client, product, ['B01M1989NK'])
        self.assertEqual(decode_asin_reference(sku), 'B01M1989NK')
        self.assertEqual(product['default_code'], row['default_code'])
        client._exec.assert_called_once_with('product.product','write',[[13284],{'default_code':encode_asin('B01M1989NK')}])
    def test_existing_reference_preserved(self):
        client, _ = self.client()
        self.assertEqual(review.ensure_internal_reference(client, {'id':13284,'default_code':'CUSTOM'}), 'CUSTOM')
        client.read.assert_not_called(); client._exec.assert_not_called()
    def test_concurrently_added_reference_preserved(self):
        client, _ = self.client(code='CUSTOM')
        self.assertEqual(review.ensure_internal_reference(client, {'id':13284}), 'CUSTOM')
        client._exec.assert_not_called()
    def test_conflicting_source_evidence_does_not_write(self):
        client, _ = self.client()
        self.assertEqual(review.ensure_internal_reference(client, {'id':13284}, ['B08FF4J72S']), '')
        client._exec.assert_not_called()
    def test_no_identity_does_not_write(self):
        client, _ = self.client('f49f6dc6-657f-40fc-ab89-a123072b6397')
        self.assertEqual(review.ensure_internal_reference(client, {'id':13284}), '')
        client._exec.assert_not_called()
    def test_explicit_label_and_no_title_guess(self):
        self.assertEqual(review.reference_asins({'description':'Amazon ASIN: b01m1989nk'}), {'B01M1989NK'})
        self.assertEqual(review.reference_asins({'description':'Some product B01M1989NK text'}), set())
    def test_unverified_write_fails(self):
        client, _ = self.client(); client._exec.side_effect=None
        with self.assertRaisesRegex(ValueError, 'could not be verified'):
            review.ensure_internal_reference(client, {'id':13284})
    def test_virtual_product_never_written(self):
        client, _ = self.client()
        self.assertEqual(review.ensure_internal_reference(client, {'id':-123}), '')
        client._exec.assert_not_called()

class ApprovalRepairTests(ReviewTests):
    def test_missing_reference_repair_preserves_submitted_title_and_new_fingerprint(self):
        self.snapshot['items'][0]['sku']=''
        self.hold()
        fresh = dict(self.snapshot, fingerprint='repaired', items=[dict(self.snapshot['items'][0],sku='ENCODED')])
        from app import main
        with patch.object(main,'repair_shopify_review_references',return_value=fresh):
            self.approve('Blue Box')
        saved=self.conn.execute('SELECT * FROM shopify_title_reviews').fetchone()
        import json
        self.assertEqual(saved['fingerprint'],'repaired')
        self.assertEqual(json.loads(saved['snapshot_json'])['items'][0]['prepared_title'],'Blue Box')
        self.assertEqual(saved['status'],'approved')
    def test_unresolvable_reference_remains_pending(self):
        self.snapshot['items'][0]['sku']=''; self.hold()
        from app import main
        with patch.object(main,'repair_shopify_review_references',return_value=self.snapshot):
            with self.assertRaises(main.HTTPException): self.approve()
        self.assertEqual(self.conn.execute('SELECT status FROM shopify_title_reviews').fetchone()['status'],'pending')
