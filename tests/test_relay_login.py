import base64,json,time,unittest,sys,types
from pathlib import Path
from urllib.parse import quote
pkg=types.ModuleType('login_test');pkg.__path__=[str(Path(__file__).resolve().parents[1]/'app/services')];sys.modules['login_test']=pkg
from login_test.relay_login import login_link,recent_login_summaries
NOW=1789656350
SETTINGS={'forwarders':'am-it@outlook.com','receiving_address':'relay-payments@taloofalut.resend.app','authserv_ids':'receiver.test'}
def link(journey='current-journey',host='app.relayfi.com',**claims):
 payload=base64.urlsafe_b64encode(json.dumps({'iat':NOW-20,'exp':NOW+280,'flow':'confirm',**claims}).encode()).decode().rstrip('=')
 return f'https://{host}/magic-link/confirm?token=header.{payload}.signature&journeyId={journey}'
def message(url):
 return {'from':'Am-it@outlook.com','to':[SETTINGS['receiving_address']],'subject':'FW: Securely Log In to Relay - 10:45 AM EDT','headers':{'authentication-results':'receiver.test; dmarc=pass header.from=outlook.com;'},'text':url}
class LoginTests(unittest.TestCase):
 def parse(self,msg):return login_link(msg,SETTINGS,'current-journey','am-it@outlook.com',NOW)
 def test_exact_journey(self):self.assertEqual(self.parse(message(link())),link())
 def test_outlook_wrapping(self):self.assertEqual(self.parse(message('https://apac01.safelinks.protection.outlook.com/?url='+quote(link(),safe=''))),link())
 def test_unrelated_stale_and_wrong_destination(self):
  for url in (link('other-journey'),link(exp=NOW-1),link(iat=NOW-301),link(flow='reset'),link(host='app.relayfi.com.evil.test'),link().replace('https:','http:'),link()+'&journeyId=other'):
   with self.subTest(url=url):self.assertIsNone(self.parse(message(url)))
 def test_duplicate_plain_html_one_link(self):
  m=message(link());m['html']='<a href="'+link().replace('&','&amp;')+'">Log in</a>';self.assertEqual(self.parse(m),link())
 def test_two_different_links_fail(self):
  with self.assertRaises(ValueError):self.parse(message(link()+' '+link(exp=NOW+270)))
 def test_sender_and_authentication(self):
  for change in ({'from':'attacker@example.com'},{'headers':{}},{'headers':{'authentication-results':'evil.test; dmarc=pass header.from=outlook.com'}}):
   with self.assertRaises(ValueError):self.parse({**message(link()),**change})
 def test_account_mismatch(self):
  with self.assertRaises(ValueError):login_link(message(link()),SETTINGS,'current-journey','someone@outlook.com',NOW)
 def test_wrong_subject(self):self.assertIsNone(self.parse({**message(link()),'subject':'Payment is on the way for your invoice!'}))
 def test_bounds_recent_email_reads(self):
  from datetime import datetime,timezone
  rows=[{'id':i,'subject':'FW: Securely Log In to Relay','created_at':datetime.fromtimestamp(NOW-i,timezone.utc).isoformat()} for i in range(8)]
  rows.append({'subject':'FW: Securely Log In to Relay','created_at':'2020-01-01T00:00:00Z'})
  self.assertEqual([r['id'] for r in recent_login_summaries({'data':rows},NOW)],[0,1,2,3,4])
