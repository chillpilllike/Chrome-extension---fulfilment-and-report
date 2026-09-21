import ast
from collections import Counter
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from app.services.notification_i18n import (
    CATALOGS, Translator, catalog, language_inventory, sms_translation, valid_translation,
)
from app.services.after_order_email import render_after_order_email


class AllNotificationLocales(unittest.TestCase):
    def test_every_configured_locale_has_full_copy_or_an_explicit_review_hold(self):
        source=catalog('en_US')
        for row in language_inventory():
            with self.subTest(locale=row['code']):
                data=catalog(row['code'])
                expected='en_US' if data.get('delivery_blocked') else row['code']
                self.assertEqual(expected,Translator(row['code']).language)
                if data.get('delivery_blocked'):
                    self.assertTrue(data.get('review_reason'))
                    self.assertTrue(Translator(row['code']).fallback)
                else:self.assertTrue(data.get('complete'))
                for section in ('messages','sms','payment'):
                    if data.get('delivery_blocked'):self.assertLessEqual(set(data[section]),set(source[section]))
                    else:self.assertEqual(set(source[section]),set(data[section]))
                    for key,value in data[section].items():
                        self.assertTrue(value.strip())
                        if section!='sms':self.assertTrue(valid_translation(source[section][key],value),key)
                        self.assertNotIn('#CAREPH',value)
                        self.assertNotIn('__LD_KEEP_',value)
                        self.assertLessEqual(Counter(re.findall(r'\d+',source[section][key])),Counter(re.findall(r'\d+',value)))
                for kind in source['sms']:
                    body,meta=sms_translation(row['code'],kind,'Test Brand','NC-DEMO','https://example.com/my/orders/123')
                    self.assertEqual(expected,meta['sent_language'])
                    if data.get('delivery_blocked'):
                        self.assertIsNone(body)
                        continue
                    self.assertIn('NC-DEMO',body)
                    self.assertIn('https://example.com/my/orders/123',body)
                    self.assertNotIn('{',body)

    def test_all_email_kinds_render_in_every_locale_without_changing_links(self):
        tree=ast.parse(Path('app/services/after_order_email.py').read_text())
        kinds=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='content' for t in node.targets):
                kinds=list(ast.literal_eval(node.value))
        url='https://example.com/my/orders/123?access_token=demo-unchanged'
        for row in language_inventory():
            case={'odoo_order_name':'NC-DEMO','sender_domain':'example.com',
                  'context':{'requested_language':row['code'],'website_name':'Test Brand','three_day_policy_enabled':True,
                             'tracking_url':url,'website_url':'https://example.com',
                             'refund':{'amount':'10.25','currency':'USD','reference':'DEMO-REFUND','completed_at':'2026-09-21'},
                             'relay_payment':{'original_currency':'USD','items':[{'name':'Example <item>','quantity':2,'total':'10.25'}],
                                              'subtotal':'10.25','tax':'0','original_total':'10.25','amount_cents':1025,'due_date':'2026-09-24'}},
                  'affected_items':[{'product_name':'Example <item>','quantity':2}]}
            for kind in kinds:
                with self.subTest(locale=row['code'],kind=kind):
                    subject,html,plain=render_after_order_email(case,url,actions=['proceed','exclude_item_and_proceed'],labels={'proceed':'Proceed','exclude_item_and_proceed':'Remove this item and continue'},template_kind=kind,review_url=url)
                    self.assertIn('NC-DEMO',subject)
                    self.assertTrue((url if kind!='relay_received' else 'https://example.com/contactus') in html)
                    if kind not in {'relay_request','relay_received'}:
                        self.assertIn('Example &lt;item&gt;',html)
                    self.assertNotIn('#CAREPH',html)
                    self.assertIn('lang="'+Translator(row['code']).html_language+'"',html)
                    self.assertIn('dir="'+Translator(row['code']).direction+'"',html)
                    self.assertTrue(plain)

    def test_script_and_region_variants_are_not_collapsed(self):
        for code in ('zh_CN','zh_TW','zh_HK','pt_BR','pt_PT','pt_AO','sr@Cyrl','sr@latin','de_CH','ko_KP'):
            self.assertTrue((CATALOGS/(code+'.json')).is_file())
        self.assertEqual('sr-Cyrl',Translator('sr@Cyrl').html_language)
        self.assertEqual('sr-Latn',Translator('sr@latin').html_language)
        for code in ('ar_001','he_IL','fa_IR','dv_MV'):
            self.assertEqual('ltr' if catalog(code).get('delivery_blocked') else 'rtl',Translator(code).direction)

    def test_malformed_or_incomplete_translations_fail_closed(self):
        self.assertFalse(valid_translation('{amount}','{amount!r}'))
        self.assertFalse(valid_translation('{amount}','{amount:9999}'))
        self.assertFalse(valid_translation('{amount}','{amount'))
        source=catalog('en_US')
        for bad in ({'complete':True,'messages':{}}, {'complete':True,'messages':[]}, {'complete':True,'messages':{k:'{' for k in source['messages']}}):
            with patch('app.services.notification_i18n.catalog',side_effect=lambda code:source if code=='en_US' else bad):
                self.assertEqual('en_US',Translator('de_DE').language)


if __name__=='__main__':unittest.main()
