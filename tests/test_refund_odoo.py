import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from app.services.refund_odoo import annotate_refund

class RefundOdooTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock();self.client.store=SimpleNamespace(website_id=2)
        self.client.read.return_value=[{'name':'NC1','website_id':[2,'Canada']}]
        self.client.execute.return_value={'tag_ids':{'relation':'crm.tag'}}
        self.row={'order_id':1,'order_name':'NC1','transfer_id':'transfer-1','amount':'10.00','currency':'CAD'}
    def test_existing_tag_added_and_internal_note_contains_amount(self):
        self.client.search_read.side_effect=[[{'id':4}],[]]
        annotate_refund(self.client,self.row)
        self.client.write.assert_called_once_with('sale.order',[1],{'tag_ids':[(4,4)]})
        call=self.client.execute.call_args
        self.assertEqual(call.args[1],'message_post')
        self.assertEqual(call.args[3]['subtype_xmlid'],'mail.mt_note')
        self.assertEqual(call.args[3]['partner_ids'],[])
        self.assertIn('10.00 CAD',call.args[3]['body'])
        self.assertIn('transfer-1',call.args[3]['body'])
    def test_retry_does_not_duplicate_note(self):
        self.client.search_read.side_effect=[[{'id':4}],[{'id':8}]]
        annotate_refund(self.client,self.row)
        self.assertFalse(any(c.args[1]=='message_post' for c in self.client.execute.call_args_list))
    def test_wrong_order_or_website_cannot_be_updated(self):
        for order in [{'name':'OTHER','website_id':[2,'Canada']},{'name':'NC1','website_id':[3,'Other']}]:
            self.client.read.return_value=[order]
            with self.assertRaises(ValueError):annotate_refund(self.client,self.row)
        self.client.write.assert_not_called()
