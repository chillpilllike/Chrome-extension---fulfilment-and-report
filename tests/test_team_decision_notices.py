import ast,json,sqlite3,unittest,copy,os
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import Mock,patch
from datetime import datetime,timedelta,timezone
from app.services.team_decision_notices import recipients,message,Notices,SCHEMA

class NoticesTests(unittest.TestCase):
 def setUp(self):
  self.conn=sqlite3.connect(':memory:');self.conn.row_factory=sqlite3.Row
  self.conn.executescript('''CREATE TABLE stores(id INTEGER PRIMARY KEY,name TEXT,odoo_url TEXT);
   CREATE TABLE after_order_cases(id INTEGER PRIMARY KEY,store_id INTEGER,odoo_order_id INTEGER,odoo_order_name TEXT,customer_email TEXT,sender_domain TEXT,title TEXT,context_json TEXT,affected_items_json TEXT);
   CREATE TABLE after_order_case_events(id INTEGER PRIMARY KEY,event_type TEXT,case_id INTEGER,actor_type TEXT,actor_label TEXT,decision TEXT,details_json TEXT,created_at TEXT);
   INSERT INTO stores VALUES(1,'Test store','https://shop.example');
   INSERT INTO after_order_case_events(id) VALUES(1);
   INSERT INTO after_order_case_events(id) VALUES(2);
   INSERT INTO after_order_cases VALUES(1,1,10,'TEST-ORDER','customer@example.com','shop.example','Dispatch update','{"expected_dispatch_date":"2026-11-01"}','[{"product_name":"Test item","quantity":2}]');''')
  self.conn.executescript(SCHEMA)
  self.settings={'after_order_team_emails':'amit@gofinch.com\nsecond@example.com'}
  self.mode=False
  self.r={'get_service_settings':lambda:self.settings,'after_order_email_test_mode':lambda:self.mode,'utc_now':lambda:'2026-10-21T12:00:00+00:00','after_order_sender':lambda case:('Test <notifications@shop.example>','shop.example')}
  self.n=Notices(self.r)
 def queue(self,kind='customer_decision_received',**kw):
  self.n.enqueue(self.conn,kw.pop('event_id',1),1,kind,kw.pop('actor','customer'),kw.pop('decision','proceed'),kw)
 def test_actual_event_writer_enqueues_in_same_transaction(self):
  source=Path(__file__).parents[1]/'app/main.py';fn=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='record_after_order_event')
  ns=dict(Any=object,Optional=__import__('typing').Optional,json=json,utc_now=self.r['utc_now'],team_decision_notices=self.n)
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<event>','exec'),ns)
  ns['record_after_order_event'](self.conn,1,'customer_decision_received',actor_type='customer',decision='proceed')
  self.assertEqual(2,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_multiple_recipients_once_per_event(self):
  self.queue();self.queue()
  self.assertEqual(2,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_later_changed_choice_gets_new_alert(self):
  self.queue();self.queue('customer_decision_changed',event_id=2,decision='cancel_order',previous_decision='proceed')
  self.assertEqual(4,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_test_events_never_queue(self):
  self.queue('alternative_selected',test_mode=True);self.mode=True;self.queue()
  self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_system_team_and_navigation_never_queue(self):
  self.queue(actor='team');self.queue(actor='system');self.queue('action_link_created');self.queue('team_decision_notice_sent')
  self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_disabled_recipients_no_queue(self):
  self.settings={};self.queue();self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_every_customer_choice_event_is_supported(self):
  for event,expected in [('alternative_selected','offer_alternatives'),('alternative_changed','offer_alternatives'),('line_removal_requested','remove_line')]:
   self.conn.execute('DELETE FROM after_order_team_notices');self.queue(event,decision='',product_name='Chosen product',line_id=12)
   snap=json.loads(self.conn.execute('SELECT snapshot_json FROM after_order_team_notices').fetchone()[0]);self.assertEqual(expected,snap['decision']);self.assertEqual('Chosen product',snap['details']['product_name'])
 def test_snapshot_and_email_are_escaped_and_have_next_step(self):
  self.queue(product_name='<script>bad</script>');snap=json.loads(self.conn.execute('SELECT snapshot_json FROM after_order_team_notices').fetchone()[0]);mail=message(snap,'amit@gofinch.com',{'proceed':'Proceed'})
  for fact in ['customer@example.com','TEST-ORDER','Proceed','Next action:','Test item','2026-11-01']:self.assertIn(fact,mail['text'])
  self.assertNotIn('<script>',mail['html']);self.assertEqual(['amit@gofinch.com'],mail['to'])
 def test_cancel_and_refund_copy_does_not_claim_execution(self):
  self.queue(decision='cancel_order');snap=json.loads(self.conn.execute('SELECT snapshot_json FROM after_order_team_notices').fetchone()[0]);self.assertIn('has not cancelled or refunded',message(snap,'amit@gofinch.com',{})['text'])
 def test_transaction_rollback_also_removes_alert(self):
  self.conn.commit();self.queue();self.conn.rollback();self.assertEqual(0,self.conn.execute('SELECT COUNT(*) FROM after_order_team_notices').fetchone()[0])
 def test_worker_commits_reservation_and_uses_stable_idempotency(self):
  self.queue();self.settings['after_order_team_emails']='amit@gofinch.com'
  @contextmanager
  def db():
   class Adapter:
    def execute(inner,sql,args=()):return self.conn.execute(sql.replace(' FOR UPDATE SKIP LOCKED',''),args)
   yield Adapter();self.conn.commit()
  self.r.update(db=db,os=SimpleNamespace(getenv=lambda *a:'test-key'),AFTER_ORDER_ACTION_LABELS={},record_after_order_event=Mock())
  self.n.enrich=lambda x:x
  def send(*a,**kw):
   self.assertEqual('sending',self.conn.execute("SELECT status FROM after_order_team_notices WHERE recipient='amit@gofinch.com'").fetchone()[0]);self.assertTrue(kw['idempotency_key'].startswith('team-decision-'));return {'id':'provider-test'}
  with patch('app.services.team_decision_notices.ResendEmailProvider') as provider:
   provider.return_value.send.side_effect=send;self.n.run();self.n.run();self.assertEqual(1,provider.return_value.send.call_count)
  self.assertEqual(['sent','suppressed'],[r[0] for r in self.conn.execute('SELECT status FROM after_order_team_notices ORDER BY id')])
 def test_ambiguous_send_stays_reserved(self):
  self.queue();self.settings['after_order_team_emails']='amit@gofinch.com'
  @contextmanager
  def db():
   class Adapter:
    def execute(inner,sql,args=()):return self.conn.execute(sql.replace(' FOR UPDATE SKIP LOCKED',''),args)
   yield Adapter();self.conn.commit()
  self.r.update(db=db,os=SimpleNamespace(getenv=lambda *a:'test-key'),AFTER_ORDER_ACTION_LABELS={},record_after_order_event=Mock());self.n.enrich=lambda x:x
  with patch('app.services.team_decision_notices.ResendEmailProvider') as provider:
   provider.return_value.send.side_effect=TimeoutError();self.n.run();self.n.run();self.assertEqual(1,provider.return_value.send.call_count)
  self.assertEqual('delivery_unknown',self.conn.execute('SELECT status FROM after_order_team_notices ORDER BY id').fetchone()[0])
 def test_provider_suppression_is_visible_and_not_reported_delivered(self):
  self.queue();self.conn.execute("UPDATE after_order_team_notices SET status='sent',provider_message_id='provider-test'")
  @contextmanager
  def db():yield self.conn;self.conn.commit()
  self.r.update(db=db,os=SimpleNamespace(getenv=lambda *a:'test-key'),record_after_order_event=Mock())
  response=Mock();response.json.return_value={'last_event':'suppressed'}
  with patch('requests.get',return_value=response),patch('app.services.team_decision_notices.time.sleep'):
   self.n.reconcile()
  rows=self.conn.execute('SELECT status,last_error FROM after_order_team_notices').fetchall()
  self.assertTrue(all(row['status']=='suppressed' and 'suppressed' in row['last_error'] for row in rows))

class RecipientTests(unittest.TestCase):
 def test_normalizes_blank_lines_case_and_duplicates(self):self.assertEqual(['amit@gofinch.com','second@example.com'],recipients(' Amit@Gofinch.com\n\namit@gofinch.com\nsecond@example.com'))
 def test_rejects_invalid_list_and_injection(self):
  for value in ['not-an-email','a@example.com,b@example.com','Name <a@example.com>','a@example.com; b@example.com','a@example.com\nBcc: bad@example.com']:
   with self.assertRaises(ValueError):recipients(value)

class StaleLinkTests(unittest.TestCase):
 def test_navigation_only_never_weakens_post_validation(self):
  source=Path(__file__).parents[1]/'app/main.py';tree=ast.parse(source.read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='after_order_action_link');fn.decorator_list=[]
  class HTTPError(Exception):
   def __init__(self,status,detail):self.status_code=status
  case={'id':1};link={'case_id':1,'test_mode':0,'expires_at':'old','request_fingerprint':'old'}
  @contextmanager
  def db():yield SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchone=lambda:link))
  ns=dict(Any=object,db=db,hashlib=__import__('hashlib'),clean_text=lambda v:v,row_to_dict=dict,after_order_case_by_id=lambda i:case,require_after_order_case_in_scope=lambda c:None,after_order_case_is_in_scope=lambda c:True,parse_any_datetime=lambda x:datetime.now(timezone.utc)-timedelta(days=1),datetime=datetime,timezone=timezone,HTTPException=HTTPError,request_fingerprint=lambda c:'new')
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<link>','exec'),ns)
  with self.assertRaises(HTTPError):ns['after_order_action_link']('a'*64)
  _,navigation=ns['after_order_action_link']('a'*64,allow_stale_navigation=True);self.assertTrue(navigation['navigation_only'])

class RouteOrderingTests(unittest.TestCase):
 def test_staff_notification_routes_precede_frontend_fallback(self):
  source=(Path(__file__).parents[1]/'app/main.py').read_text()
  self.assertLess(source.index('app.include_router(team_decision_notices.router())'),source.index('@app.get("/{frontend_path:path}")'))

class BridgeRoundTripTests(unittest.TestCase):
 def test_host_check_never_calls_back_into_odoo(self):
  source=Path(__file__).parents[1]/'app/main.py'
  fn=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='require_after_order_website_host')
  class HTTPError(Exception):pass
  ns=dict(Any=object,Request=object,clean_text=lambda v:str(v or ''),hmac=__import__('hmac'),HTTPException=HTTPError,hydrate_after_order_recipient_and_domain=Mock(side_effect=AssertionError('Recursive Odoo RPC')))
  exec(compile(ast.Module(body=[fn],type_ignores=[]),'<host>','exec'),ns)
  case={'sender_domain':'shop.example'}
  request=SimpleNamespace(headers={'x-website-host':'shop.example'})
  self.assertEqual(case,ns['require_after_order_website_host'](request,case))
  with self.assertRaises(HTTPError):ns['require_after_order_website_host'](SimpleNamespace(headers={'x-website-host':'other.example'}),case)
  ns['hydrate_after_order_recipient_and_domain'].assert_not_called()
 def test_portal_generated_links_use_already_verified_context(self):
  source=Path(__file__).parents[1]/'app/main.py'
  fn=next(n for n in ast.parse(source.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='api_after_order_bridge_order')
  calls=[n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='create_after_order_action_link']
  self.assertTrue(calls)
  self.assertTrue(all(any(k.arg=='refresh_context' and isinstance(k.value,ast.Constant) and k.value.value is False for k in n.keywords) for n in calls))
