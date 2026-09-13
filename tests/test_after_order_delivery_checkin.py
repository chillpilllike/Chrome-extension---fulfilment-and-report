import unittest
from datetime import datetime, timedelta, timezone
from app.services.delivery_checkin import carrier_moment, delivery_details, require_due, parcel_destination, destination_postal_code
from app.services.after_order_email import render_after_order_email


class DeliveryCheckinTests(unittest.TestCase):
    def test_parcel_destination_field_not_other_numbers(self):
        markup='''<p class="ParcelDetails-header">AWB Number</p><p class="ParcelDetails-Body">12345678</p>
        <p class="ParcelDetails-header">Destination Country, Zip Code</p>
        <p class="ParcelDetails-Body">AUSTRALIA <span>, 2540</span></p>'''
        self.assertEqual(parcel_destination(markup),{'destination':'AUSTRALIA, 2540','destination_postal_code':'2540'})
        self.assertEqual(parcel_destination('<p class="ParcelDetails-Body">AUSTRALIA, 2540</p>'),{})
        self.assertEqual(destination_postal_code('UNITED STATES, 00501'),'00501')
        self.assertEqual(destination_postal_code('CANADA, K1A 0B1'),'K1A 0B1')
        self.assertEqual(destination_postal_code('AUSTRALIA'),'')
    def test_explicit_timezone_exact_boundary(self):
        details=delivery_details([{'status':'Delivered','date':'2026-09-13T10:00:00+10:00'}])
        due=datetime(2026,9,14,0,0,tzinfo=timezone.utc)
        with self.assertRaises(ValueError):
            require_due(details, due-timedelta(seconds=1))
        require_due(details,due)

    def test_unknown_timezone_waits_conservatively(self):
        details=delivery_details([{'status':'Parcel Delivered with Safe Drop','date':'9/1/2026 7:53:00 PM','location':'Boronia,AU'}])
        self.assertEqual(details['delivery_checkin_due_at'],'2026-09-03T07:53:00+00:00')
        self.assertEqual(details['delivery_postal_code'],'Not provided by carrier')

    def test_missing_date_and_attempt_do_not_qualify(self):
        for events in ([],[{'status':'Not delivered','date':'2026-09-01'}],[{'status':'Delivered','date':'unknown'}]):
            with self.assertRaises(ValueError):
                require_due(delivery_details(events))

    def test_latest_delivery_and_postcode(self):
        details=delivery_details([{'status':'Delivered','date':'2026-09-01T12:00:00Z'},
            {'status':'Delivered','date':'2026-09-02T12:00:00Z','postal_code':'3155','location':'Boronia'}])
        self.assertEqual(details['delivery_postal_code'],'3155')
        self.assertEqual(details['delivery_checkin_due_at'],'2026-09-03T12:00:00+00:00')

    def test_render_details_and_tracking_without_losing_choices(self):
        context=delivery_details([{'status':'Delivered','date':'9/1/2026 7:53:00 PM','postal_code':'3155','location':'Boronia,AU'}])
        context['tracking_url']='https://nutricity.com.au/my/orders/123'
        _,markup,plain=render_after_order_email({'case_type':'delivery_confirmation','context':context},
            'https://nutricity.com.au/my/orders/123',actions=['received','not_received'],labels={})
        for text in (markup,plain):
            for value in ('9/1/2026 7:53:00 PM','3155','Boronia,AU','Track all details','choice=received','choice=not_received'):
                self.assertIn(value,text)
