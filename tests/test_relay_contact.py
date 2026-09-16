import unittest
from app.services.relay_contact import contact_details

class ContactTests(unittest.TestCase):
 def detail(self, phone, code='AU', email='a@example.com'):
  return contact_details({'email':email,'phone':phone}, {'code':code,'name':'Australia'}, 'a@example.com')
 def test_au_local_and_international(self):
  for value in ('0479 046 169','+61 479 046 169','0061 479 046 169'):
   result=self.detail(value)
   self.assertEqual(result['national_number'],'479046169')
   self.assertEqual(result['phone_country_code'],'AU')
 def test_international_phone_can_differ_from_billing_country(self):
  result=self.detail('+44 20 8366 1177')
  self.assertEqual(result['phone_country_code'],'GB');self.assertEqual(result['country_code'],'AU')
 def test_italian_leading_zero_preserved(self):
  self.assertEqual(self.detail('+39 02 36618 300')['national_number'],'0236618300')
 def test_invalid_or_missing_blocks(self):
  for value in ('','123','0479x123','call me','+999123456789'):
   with self.subTest(value=value),self.assertRaises(ValueError):self.detail(value)
 def test_changed_email_blocks(self):
  with self.assertRaises(ValueError):self.detail('0479046169',email='other@example.com')
 def test_missing_country_blocks(self):
  with self.assertRaises(ValueError):self.detail('0479046169',code='')
