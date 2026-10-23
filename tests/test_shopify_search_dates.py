import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app.services.tracking_options import shopify_updated_window
from app.services.shopify_scripts import tracking_sync

class ShopifySearchDateTests(unittest.TestCase):
    def test_india_midnight_is_quoted_as_the_exact_utc_instant(self):
        india=timezone(timedelta(hours=5,minutes=30))
        lower=datetime(2026,10,22,tzinfo=india)
        upper=datetime(2026,10,23,tzinfo=india)
        query=shopify_updated_window(lower,upper)
        terms=re.findall(r"updated_at:([<>]=)'([^']+)'",query)
        self.assertEqual(len(terms),2)
        actual=[datetime.fromisoformat(value.replace('Z','+00:00')) for _,value in terms]
        self.assertEqual(actual,[lower,upper])
        self.assertIn("2026-10-21T18:30:00Z",query)
        self.assertNotIn('+00:00',query)
        self.assertNotIn('+05:30',query)

    def test_naive_and_reversed_windows_are_not_guessed(self):
        with self.assertRaises(ValueError):
            shopify_updated_window(datetime(2026,10,22),datetime(2026,10,23))
        now=datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            shopify_updated_window(now,now-timedelta(seconds=1))

    def test_tracking_fetch_sends_two_whole_date_terms(self):
        lower='2026-10-21T18:30:00Z';upper='2026-10-22T18:30:00Z'
        with patch.object(tracking_sync,'gql',return_value={'orders':{'edges':[],'pageInfo':{'hasNextPage':False}}}) as gql:
            list(tracking_sync.fetch_source_orders('test','test','2025-10',lower,upper))
        query=gql.call_args.args[4]['q']
        self.assertEqual(re.findall(r"updated_at:([<>]=)'([^']+)'",query),[('>=',lower),('<=',upper)])
        self.assertIn('fulfillment_status:partial',query)
