import unittest
import json
from tests import test_airwallex_refunds as fixtures

class ManualExceptionTests(unittest.TestCase):
    def test_checkbox_required_server_side_and_audited_in_signed_review(self):
        w=fixtures.WorkflowTests();w.setUp();w.snap['manual_payment_required']=True
        with self.assertRaisesRegex(ValueError,'tick the manual-payment'):
            w.service.prepare(w.form())
        review=w.service.prepare(w.form(manual_payment_confirmed=True))
        payload=json.loads(w.service.cipher(w.cfg).decrypt(review['review_token'].encode()))
        self.assertTrue(payload['manual_payment_confirmed'])
        self.assertIn('Manual payment exception',payload['edit_reason'])
        result=w.service.submit(review['review_token'])
        self.assertEqual(result['status'],'PROCESSING')
        w.service.submit(review['review_token'])
        self.assertEqual(sum(p.endswith('/create') for _,p,_ in w.calls),1)

    def test_payment_evidence_lost_after_review_requires_new_confirmation(self):
        w=fixtures.WorkflowTests();w.setUp()
        review=w.service.prepare(w.form(manual_payment_confirmed=True))
        w.snap['manual_payment_required']=True
        with self.assertRaisesRegex(ValueError,'Review again'):
            w.service.submit(review['review_token'])
        self.assertFalse(any(p.endswith('/create') for _,p,_ in w.calls))

    def test_manual_exception_still_deducts_prior_refunds_and_limits_amount(self):
        f=fixtures.SnapshotTests();f.setUp();f.tx['state']='pending'
        f.external=[{'id':'t1','reference':'Refund NC100','transfer_amount':30,'transfer_currency':'CAD','status':'PAID'}]
        result=f.svc.snapshot(1,1)
        self.assertTrue(result['manual_payment_required'])
        self.assertEqual(result['paid'],'0')
        self.assertEqual(result['remaining'],'70')
        w=fixtures.WorkflowTests();w.setUp();w.snap.update(manual_payment_required=True,remaining='70')
        with self.assertRaises(ValueError):
            w.service.prepare(w.form(manual_payment_confirmed=True))

    def test_known_completed_payment_keeps_received_amount_cap(self):
        f=fixtures.SnapshotTests();f.setUp();f.tx['amount']=40
        result=f.svc.snapshot(1,1)
        self.assertFalse(result['manual_payment_required'])
        self.assertEqual(result['remaining'],'40')
