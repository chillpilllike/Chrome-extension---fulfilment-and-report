import unittest
from unittest.mock import Mock
from fastapi import HTTPException
from app.services.alternative_workflow import Workflow, Recommendations
from app.services.after_order_email import render_after_order_email


class NoAlternativesTests(unittest.TestCase):
    def workflow(self):
        workflow=Workflow({'request_fingerprint':lambda case:'current'})
        workflow.rows=Mock(return_value=[])
        return workflow

    def test_explicit_flag_required_and_every_line_reviewed(self):
        workflow=self.workflow();case={'id':1,'affected_items':[{'line_id':10},{'line_id':20}]}
        empty={'line_id':10,'issue_fingerprint':'current','recommendations':[]}
        other={'line_id':20,'issue_fingerprint':'current','recommendations':[{'name':'Alternative'}]}
        workflow.rows.return_value=[empty,other]
        self.assertFalse(workflow.ready(case))
        workflow.rows.return_value=[{**empty,'no_alternatives':True},other]
        self.assertTrue(workflow.ready(case))
        self.assertEqual(workflow.unavailable_without_alternatives(case),{10})
        workflow.rows.return_value=[{**empty,'no_alternatives':True,'issue_fingerprint':'old'},other]
        self.assertFalse(workflow.ready(case))
        self.assertEqual(workflow.unavailable_without_alternatives(case),set())

    def test_choose_rejects_no_alternatives_before_product_lookup(self):
        workflow=self.workflow();case={'id':1,'affected_items':[{'line_id':10}]}
        workflow.r.namespace['after_order_email_test_mode']=lambda:False
        workflow.case_line=Mock(return_value=(case,{}))
        workflow.rows.return_value=[{'line_id':10,'issue_fingerprint':'current','recommendations':[],'no_alternatives':True}]
        workflow.product=Mock()
        with self.assertRaises(HTTPException):
            workflow.choose(case,{},10,123)
        workflow.product.assert_not_called()

    def test_acknowledgment_is_required_to_publish(self):
        workflow=self.workflow();workflow.case_line=Mock(return_value=({},{}))
        with self.assertRaises(HTTPException):
            workflow.publish(1,10,Recommendations(no_alternatives=True),None)
        with self.assertRaises(HTTPException):
            workflow.publish(1,10,Recommendations(sourcing_checked=True),None)
        with self.assertRaises(HTTPException):
            workflow.publish(1,10,Recommendations(references=['ITEM'],sourcing_checked=True,no_alternatives=True),None)

    def test_no_alternatives_copy_and_safe_actions(self):
        case={'case_type':'item_unavailable','odoo_order_name':'TEST','affected_items':[{'line_id':10,'product_name':'Original','no_alternatives':True}],
              'context':{'no_alternative_line_ids':[10],'three_day_policy_enabled':True}}
        for actions in (['cancel_order'],['exclude_item_and_proceed','cancel_order']):
            _,markup,plain=render_after_order_email(case,'https://example.com/my/orders/1',actions=actions,labels={})
            self.assertNotIn('choice=offer_alternatives',markup)
            self.assertNotIn('Our team has prepared alternatives',plain)
            self.assertIn('No suitable alternatives available',plain)
            self.assertIn('3 days',plain)
