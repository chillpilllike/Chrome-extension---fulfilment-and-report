import unittest,sqlite3,json
from datetime import datetime,timezone
from unittest.mock import patch,Mock
from app.support.journey import shipment_history,email_history,tracking_url
NOW=datetime(2026,9,6,12,tzinfo=timezone.utc)
class JourneyTests(unittest.TestCase):
 def setUp(self):
  self.c=sqlite3.connect(':memory:');self.c.row_factory=sqlite3.Row
  self.c.executescript('''CREATE TABLE after_order_cases(id INTEGER,store_id INTEGER,odoo_order_id INTEGER,website_id INTEGER,customer_email TEXT,case_type TEXT,current_decision TEXT,decision_updated_at TEXT,confirmed_at TEXT,status TEXT,updated_at TEXT);
CREATE TABLE after_order_messages(id INTEGER,case_id INTEGER,provider TEXT,provider_message_id TEXT,recipient TEXT,status TEXT,created_at TEXT);
CREATE TABLE after_order_case_events(case_id INTEGER,event_type TEXT,actor_type TEXT,details_json TEXT,created_at TEXT);
CREATE TABLE epost_global_tracking(store_id INTEGER,odoo_order_id INTEGER,website_id INTEGER,archived_at TEXT,tracking_code TEXT,tracking_url TEXT,status TEXT,last_update_at TEXT,location TEXT,final_mile_tracking_number TEXT,final_mile_tracking_url TEXT,final_mile_carrier TEXT,events_json TEXT,last_checked_at TEXT);''')
  self.c.execute("INSERT INTO after_order_cases VALUES(1,8,4,1,'test@example.com','item_unavailable',NULL,NULL,NULL,'needs_attention',?)",(NOW.isoformat(),))
 def history(self,**kwargs):return email_history(self.c,8,4,'test@example.com',now=NOW,provider_key=kwargs.pop('provider_key',''),**kwargs)
 def message(self,status='sent',email='test@example.com'):
  self.c.execute('INSERT INTO after_order_messages VALUES(1,1,?,?,?,?,?)',('resend','12345678-1234-1234-1234-123456789abc',email,status,NOW.isoformat()))
 def test_test_failed_and_other_recipient_excluded(self):
  self.message('sent_test');self.message('failed');self.message(email='other@example.com')
  self.assertEqual([],self.history()['cases'][0]['emails'])
 def test_order_email_store_and_website_are_all_required(self):
  self.message()
  for field,value in [('store_id',9),('odoo_order_id',5),('website_id',2),('customer_email','other@example.com')]:
   self.c.execute('SAVEPOINT scope');self.c.execute('UPDATE after_order_cases SET '+field+'=?',(value,));self.assertEqual([],self.history()['cases']);self.c.execute('ROLLBACK TO scope')
 def test_decision_is_request_not_executed_refund(self):
  self.c.execute("UPDATE after_order_cases SET current_decision='refund',confirmed_at='2026-09-06'")
  row=self.history()['cases'][0];self.assertEqual('request a refund',row['recorded_request']);self.assertNotIn('refunded',json.dumps(row))
 def test_provider_observation_is_saved_without_raw_body(self):
  self.message();response=Mock();response.json.return_value={'id':'12345678-1234-1234-1234-123456789abc','to':['test@example.com'],'last_event':'opened','html':'PRIVATE'}
  with patch('app.support.journey.requests.get',return_value=response) as get:
   one=self.history(provider_key='secret');self.history(provider_key='secret');self.assertEqual(1,get.call_count)
  self.assertNotIn('PRIVATE',json.dumps(one));self.assertEqual('opened',one['cases'][0]['emails'][0]['provider_observation']['event'])
 def test_provider_recipient_mismatch_is_rejected(self):
  self.message();r=Mock();r.json.return_value={'id':'12345678-1234-1234-1234-123456789abc','to':['other@example.com'],'last_event':'delivered'}
  with patch('app.support.journey.requests.get',return_value=r):self.assertIsNone(self.history(provider_key='secret')['cases'][0]['emails'][0]['provider_observation'])
 def test_tracking_is_order_scoped_and_old_location_is_not_current(self):
  for order in [4,5]:self.c.execute('INSERT INTO epost_global_tracking VALUES(8,?,1,NULL,?,?,?,?,?,?,?,?,?,?)',(order,'TRACK','https://epgtrack.com/TRACK','In transit','2026-09-04','Sydney','','','',json.dumps([{'date':'2026-09-04','status':'Moved','location':'Sydney','private':'SECRET'}]),'2026-09-04'))
  r=shipment_history(self.c,8,4,now=NOW);self.assertEqual(1,len(r['shipments']));self.assertFalse(r['shipments'][0]['fresh']);self.assertNotIn('SECRET',json.dumps(r))
 def test_tracking_urls_reject_internal_credentials_and_lookalikes(self):
  for u in ['https://amazon.com/x','https://epgtrack.com.evil.com/x','https://u:p@epgtrack.com/x','javascript:alert(1)','https://185.194.236.161/x']:self.assertIsNone(tracking_url(u))
  self.assertEqual('https://epgtrack.com/ABC',tracking_url('https://epgtrack.com/ABC'))
