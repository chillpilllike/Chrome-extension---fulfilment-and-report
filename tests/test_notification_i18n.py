import ast
import json
import unittest
from pathlib import Path
from string import Formatter

from app.services.notification_i18n import Translator, catalog, resolve_language, normalize_language, language_inventory, sms_translation, sms_segments
from app.services.after_order_email import render_after_order_email


class LanguageTests(unittest.TestCase):
    def test_sms_segment_estimates(self):
        self.assertEqual(1,sms_segments('a'*160)['parts'])
        self.assertEqual(2,sms_segments('a'*161)['parts'])
        self.assertEqual(2,sms_segments('a'*71,True)['parts'])
        self.assertEqual('Unicode',sms_segments('漢')['encoding'])
        self.assertEqual('GSM-7',sms_segments('confirmée')['encoding'])

    def test_order_customer_website_precedence(self):
        self.assertEqual('fr_CA',resolve_language('fr-CA','de_DE','en_US')['requested_language'])
        self.assertEqual('fr_FR',resolve_language(None,'fr_FR','en_US')['requested_language'])
        self.assertEqual('de_DE',resolve_language(None,None,'de_DE')['requested_language'])
        self.assertEqual('en_US',resolve_language()['requested_language'])
        self.assertEqual('',normalize_language('../../etc/passwd'))
        self.assertEqual('sr@Cyrl',normalize_language('sr@Cyrl'))

    def test_french_variants_and_fallback(self):
        for locale in ('fr_FR','fr_CA','fr_BE','fr_CH'):
            t=Translator(locale)
            self.assertEqual(locale,t.language)
            self.assertEqual('Quantité',t('Quantity'))
        t=Translator('de_DE')
        self.assertEqual('en_US',t.language)
        self.assertTrue(t.fallback)
        self.assertEqual('Quantity',t('Quantity'))
        self.assertGreaterEqual(len(language_inventory()),97)

    def test_complete_catalog_covers_all_renderer_copy(self):
        source=set(catalog('en_US')['messages'])
        french=catalog('fr_FR')['messages']
        tree=ast.parse(Path('app/services/after_order_email.py').read_text())
        required=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='t' and node.args and isinstance(node.args[0],ast.Constant):
                required.add(node.args[0].value)
            if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='content' for x in node.targets):
                for row in ast.literal_eval(node.value).values():required.update(row)
            if isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='sections' for x in node.targets):
                for _,title,body in ast.literal_eval(node.value):required.update((title,body))
        self.assertFalse(required-source)
        self.assertEqual(source,set(french))
        for key,value in french.items():
            fields=lambda text:{name for _,name,_,_ in Formatter().parse(text) if name is not None}
            self.assertEqual(fields(key),fields(value),key)
            self.assertNotIn('<',value)

    def test_french_email_preserves_links_ids_and_dynamic_values(self):
        case={'odoo_order_name':'NC29517','sender_domain':'nutricity.ca',
              'context':{'requested_language':'fr_CA','website_name':'Nutricity Canada'},
              'affected_items':[{'product_name':'Protected product <name>','quantity':2,'thumbnail_url':'https://nutricity.ca/image.png'}]}
        url='https://nutricity.ca/my/orders/123?access_token=untouched'
        subject,html,plain=render_after_order_email(case,url,actions=['proceed'],labels={'proceed':'Proceed'},template_kind='new_order_welcome')
        self.assertIn('Merci pour votre commande',subject)
        self.assertIn('NC29517',subject)
        self.assertIn('lang="fr-CA"',html)
        self.assertIn('Poursuivre',html)
        self.assertIn('Quantité 2',html)
        self.assertIn('Protected product &lt;name&gt;',html)
        self.assertIn('access_token=untouched',html)
        self.assertIn('Ce message concerne la commande NC29517 passée sur Nutricity Canada.',plain)
        self.assertNotIn('Thank you for your order',html)

    def test_french_three_day_and_24_hour_terms(self):
        case={'odoo_order_name':'NC1','context':{'requested_language':'fr_FR','three_day_policy_enabled':True}}
        _,html,plain=render_after_order_email(case,'https://example.com/my/orders/1',actions=['exclude_item_and_proceed'],labels={'exclude_item_and_proceed':'Remove this item and continue'},template_kind='item_unavailable')
        self.assertIn('3 jours',plain)
        self.assertIn('24 heures',plain)
        self.assertIn('Retirer cet article et poursuivre',html)
        self.assertNotIn('If you make no choice',plain)

    def test_sms_french_and_missing_language_fallback(self):
        body,meta=sms_translation('fr_CA','new_order_welcome','Nutricity','NC1','https://nutricity.ca/my/orders/1')
        self.assertIn('commande NC1 confirmée',body)
        self.assertEqual('fr_CA',meta['sent_language'])
        body,meta=sms_translation('de_DE','new_order_welcome','Nutricity','NC1','https://nutricity.ca/my/orders/1')
        self.assertIsNone(body)
        self.assertEqual('en_US',meta['sent_language'])


if __name__=='__main__':unittest.main()
