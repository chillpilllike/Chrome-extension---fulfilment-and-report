import json
import unittest
from datetime import datetime,timedelta,timezone
from app.services.care_reminders import reminder_slot,Reminders
from unittest.mock import Mock


class ReminderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,6,tzinfo=timezone.utc)
        self.message={'test_mode':0,'status':'delivered','template_kind':'item_unavailable',
                      'payload_json':json.dumps({'_care_reminders':True})}
        self.case={'status':'needs_attention'}

    def slot(self,hours,message=None,case=None):
        return reminder_slot(message or self.message,case or self.case,
                             (self.now-timedelta(hours=hours)).isoformat(),self.now)

    def test_exact_boundaries_and_no_catchup_burst(self):
        for hours,expected in [(0,0),(23.99,0),(24,1),(47.99,1),(48,2),(59.99,2),(60,3),(71.99,3),(72,0),(100,0),(-1,0)]:
            self.assertEqual(expected,self.slot(hours))

    def test_stop_on_any_customer_response_confirmation_or_resolution(self):
        for change in ({'current_decision':'offer_alternatives'},{'current_decision':'refund'},
                       {'confirmed_at':'2026-09-06'},{'status':'resolved'},{'status':'execution_needs_review'}):
            self.assertEqual(0,self.slot(25,case={**self.case,**change}))

    def test_no_test_legacy_failed_or_recursive_reminders(self):
        for change in ({'test_mode':1},{'status':'sent'},{'status':'bounced'},{'status':'complained'},
                       {'payload_json':'{}'}, {'payload_json':json.dumps({'_care_reminders':True,'_care_reminder_parent':1})}):
            self.assertEqual(0,self.slot(25,message={**self.message,**change}))

    def test_tracking_updates_and_reviews_excluded(self):
        for kind in ('package_movement','trustpilot_review','alternative_payment','tracking'):
            self.assertEqual(0,self.slot(25,message={**self.message,'template_kind':kind}))
        self.assertEqual(1,self.slot(25,message={**self.message,'template_kind':'tracking'},
                                  case={**self.case,'context':{'risk_state':'suspected_lost'}}))

    def test_all_supported_decision_notifications(self):
        for kind in ('expected_dispatch','delivery_confirmation','package_lost','item_unavailable'):
            self.assertEqual(2,self.slot(49,message={**self.message,'template_kind':kind}))

    def test_test_mode_does_not_read_or_send(self):
        Reminders({'after_order_email_test_mode':lambda:True}).run_due(None)


class ReminderQueueTests(unittest.TestCase):
    def setUp(self):
        from test_after_order_completion import CompletionDB
        from app.services.care_reminders import SCHEMA
        self.h=CompletionDB();self.h.setUp()
        self.h.conn.executescript(SCHEMA)
        stamp=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat()
        self.h.conn.execute('''INSERT INTO after_order_messages
            (id,case_id,provider,provider_message_id,recipient,test_mode,status,template_kind,payload_json,request_fingerprint)
            VALUES(1,1,'resend','original','buyer@example.test',0,'delivered','item_unavailable',?,'fp')''',
            (json.dumps({'_care_reminders':True}),))
        self.h.conn.execute('INSERT INTO after_order_delivery_events(event_id,provider_message_id,event_type,occurred_at,created_at) VALUES(?,?,?,?,?)',
                           ('delivered','original','email.delivered',stamp,stamp))
        self.h.conn.commit()
        self.send=Mock(return_value={'ok':True})
        self.h.ns.update(send_after_order_email=self.send,clean_error_message=str)
        self.reminders=Reminders(self.h.ns)

    def tearDown(self):self.h.tearDown()

    def test_repeated_scheduler_sends_each_slot_only_once(self):
        self.reminders.run_due(None);self.reminders.run_due(None)
        self.send.assert_called_once_with(1,None,reminder_parent=1,reminder_number=1)
        stamp=(datetime.now(timezone.utc)-timedelta(hours=61)).isoformat()
        self.h.conn.execute('UPDATE after_order_delivery_events SET occurred_at=?',(stamp,));self.h.conn.commit()
        self.reminders.run_due(None);self.reminders.run_due(None)
        self.assertEqual(self.send.call_count,2)
        self.assertEqual(self.send.call_args.kwargs['reminder_number'],3)

    def test_recorded_response_prevents_next_slot(self):
        self.h.conn.execute("UPDATE after_order_cases SET current_decision='refund'");self.h.conn.commit()
        self.reminders.run_due(None)
        self.send.assert_not_called()

    def test_failed_dispatch_does_not_consume_slot(self):
        self.send.side_effect=ValueError('temporary failure')
        self.reminders.run_due(None)
        self.assertEqual(self.h.conn.execute('SELECT last_slot FROM after_order_reminder_checks').fetchone()[0],0)
        self.send.side_effect=None
        self.reminders.run_due(None)
        self.assertEqual(self.h.conn.execute('SELECT last_slot FROM after_order_reminder_checks').fetchone()[0],1)


if __name__=='__main__':
    unittest.main()
