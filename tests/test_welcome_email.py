import json
import unittest
from datetime import datetime, timedelta, timezone
from app.services.welcome_email import permitted, recent_confirmed, TEST_RECIPIENT
from app.services.after_order_email import render_after_order_email


class WelcomeTests(unittest.TestCase):
    def row(self, **changes):
        return dict({'provider':'resend','status':'awaiting_approval','attempt_count':0,'test_mode':0,
                     'template_kind':'new_order_welcome','recipient':'customer@example.com',
                     'payload_json':json.dumps({'to':['customer@example.com']})}, **changes)

    def test_only_welcome_live(self):
        self.assertTrue(permitted(self.row(),test_mode=False))
        self.assertFalse(permitted(self.row(),test_mode=True))
        for kind in ['item_unavailable','tracking','delivery_confirmation','refund','alternative_payment']:
            self.assertFalse(permitted(self.row(template_kind=kind),test_mode=False))
        for changes in [{'attempt_count':1},{'status':'failed'},{'status':'sent'},{'provider':'odoo'}]:
            self.assertFalse(permitted(self.row(**changes),test_mode=False))

    def test_test_exception_cannot_leak(self):
        row=self.row(test_mode=1,recipient=TEST_RECIPIENT,template_kind='item_unavailable',payload_json=json.dumps({'to':[TEST_RECIPIENT]}))
        self.assertTrue(permitted(row,test_mode=True))
        self.assertFalse(permitted({**row,'recipient':'customer@example.com'},test_mode=True))
        for payload in [{'to':['customer@example.com']},{'to':[TEST_RECIPIENT,'customer@example.com']},{'to':[TEST_RECIPIENT],'bcc':['customer@example.com']}]:
            self.assertFalse(permitted({**row,'payload_json':json.dumps(payload)},test_mode=True))

    def test_no_backfill_drafts_or_future(self):
        now=datetime.now(timezone.utc);start=(now-timedelta(minutes=5)).isoformat()
        order={'state':'sale','website_id':[1,'Store'],'date_order':(now-timedelta(minutes=1)).isoformat()}
        self.assertTrue(recent_confirmed(order,start))
        for change in [{'state':'draft'},{'state':'cancel'},{'website_id':False},{'date_order':(now-timedelta(days=1)).isoformat()},{'date_order':(now+timedelta(days=1)).isoformat()}]:
            self.assertFalse(recent_confirmed({**order,**change},start))
        self.assertFalse(recent_confirmed(order,None))

    def test_copy_brand_links_and_spacing(self):
        case={'case_type':'new_order_welcome','sender_domain':'nutricity.com.au','odoo_order_name':'NC-TEST','context':{'website_name':'Nutricity Australia'}}
        subject,markup,plain=render_after_order_email(case,'',actions=[],labels={})
        self.assertIn('NC-TEST',subject)
        for text in ['Friday','Monday','48 hours or longer','next business day','https://nutricity.com.au/contactus','mailto:support@nutricity.com.au']:
            self.assertIn(text,markup)
        self.assertNotIn('insurance',plain)
        self.assertNotIn('choice=',markup)
        self.assertIn('padding:0 0 24px',markup)


if __name__ == '__main__':
    unittest.main()
