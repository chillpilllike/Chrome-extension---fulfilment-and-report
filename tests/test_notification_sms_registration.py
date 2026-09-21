import unittest
import re
import json
from pathlib import Path
from scripts.register_sms_catalogs import plan


class RegistrationTests(unittest.TestCase):
    def test_all_safe_catalogs_fixed_brand_valid_variables_and_aliases(self):
        locales=list(json.loads(Path('app/services/notification_locales/manifest.json').read_text()))
        rows=plan(['nutricity','GofinchKart'],{'nutricity':locales,'GofinchKart':locales})
        self.assertEqual(1680,len(rows))
        keys={(r['sender'],r['language'],r['kind']) for r in rows}
        self.assertEqual(len(rows),len(keys))
        self.assertTrue({'fr','de','ar','ja','zh_CN','zh_TW','sr@Cyrl','sr@latin'}.issubset({r['language'] for r in rows}))
        for row in rows:
            self.assertNotIn(row['language'],{'kab','fj','dv','en'})
            self.assertIn('Nutricity' if row['sender']=='nutricity' else row['sender'],row['text'])
            self.assertEqual(1,row['text'].count('##order##'))
            self.assertEqual(1,row['text'].count('##url##'))
            self.assertFalse(row['text'].rstrip().endswith('##'))
            self.assertLess(len(row['text'])+150,1000)
            url='https://nutricity.ca/my/orders/123'
            body=row['text'].replace('##order##','NC123').replace('##url##',url)
            self.assertEqual([url],re.findall(r'https?://[^\s<>"\']+',body))

    def test_only_enabled_website_languages_and_permitted_senders(self):
        rows=plan(['nutricity','GofinchKart','PrimeSupps'],{
            'nutricity':['en_US','fr_CA','fr_FR'], 'GofinchKart':['en_AU','de_DE'],
            'PrimeSupps':['en_AU','fr_FR']})
        self.assertEqual(24,len(rows))
        self.assertEqual({('nutricity','fr'),('GofinchKart','de')}, {(r['sender'],r['language']) for r in rows})
        self.assertEqual([],plan(['nutricity'],{'nutricity':['en_US']}))


if __name__=='__main__':unittest.main()
