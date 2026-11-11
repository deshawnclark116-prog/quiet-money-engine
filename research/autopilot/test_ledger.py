"""Evidence-ledger continuity is more important than repeated SEC fetch times."""
import unittest
from datetime import datetime,timedelta,timezone
from research.autopilot.ledger import reconcile,current_events

EVENT={"event_id":"0000123456-26-000001",
       "source":"sec_primary","evidence_status":"verified","polarity":"positive",
       "observed_at":"2026-11-10T19:00:00+00:00",
       "kind":"fresh_sec_revenue_and_cashflow_inflection",
       "source_url":"https://www.sec.gov/Archives/edgar/data/123456/000012345626000001/"}

class EventLedgerTests(unittest.TestCase):
    def test_refetch_never_restarts_information_clock(self):
        a=reconcile(None,"ZOM",[EVENT],"2026-11-10T20:00:00+00:00")
        second={**EVENT,"observed_at":"2026-11-11T20:00:00+00:00"}
        b=reconcile(a,"ZOM",[second],"2026-11-11T21:00:00+00:00")
        self.assertEqual(len(b["events"]["ZOM"]),1)
        self.assertEqual(b["events"]["ZOM"][0]["observed_at"],
                         EVENT["observed_at"])

    def test_other_ticker_events_retained(self):
        a=reconcile(None,"ZOM",[EVENT],"2026-11-10T20:00:00+00:00")
        b=reconcile(a,"SOFI",[{**EVENT,"event_id":"0000123456-26-000002"}],
                    "2026-11-11T20:00:00+00:00")
        self.assertEqual(set(b["events"]),{"ZOM","SOFI"})

    def test_historical_events_expire_before_old_event_reentry(self):
        old=reconcile(None,"ZOM",[EVENT],"2026-11-10T20:00:00+00:00")
        self.assertEqual(current_events(old,"ZOM","2026-12-05T19:00:00+00:00"),[])
        later=reconcile(old,"SOFI",[],"2026-12-20T19:00:00+00:00")
        self.assertNotIn("ZOM",later["events"])

    def test_future_or_unverified_never_logged(self):
        future={**EVENT,"observed_at":"2026-11-12T20:00:00+00:00"}
        unverified={**EVENT,"evidence_status":"unverified"}
        v=reconcile(None,"ZOM",[future,unverified],"2026-11-10T20:00:00+00:00")
        self.assertEqual(v["events"],{})

if __name__=="__main__":unittest.main()
