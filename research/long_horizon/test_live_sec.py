import os
import unittest
from unittest.mock import patch
from research.long_horizon.live_sec import extract_receipts,extract_regulatory_alerts,research_ticker

RECEIVED="2026-11-10T20:00:00+00:00"

class SecAdapterTests(unittest.TestCase):
    def test_sec_xbrl_cash_and_ytd_filter(self):
        raw={"facts":{"us-gaap":{
            "CashAndCashEquivalentsAtCarryingValue":{"units":{"USD":[
                {"end":"2026-09-30","filed":"2026-10-30","accn":"a1","form":"10-Q","val":21000000}]}},
            "NetCashProvidedByUsedInOperatingActivities":{"units":{"USD":[
                {"start":"2026-01-01","end":"2026-09-30","filed":"2026-10-30","accn":"a1","form":"10-Q","val":-2000000},
                {"start":"2025-01-01","end":"2025-12-31","filed":"2026-03-01","accn":"a2","form":"10-K","val":-3000000}]}},
        }}}
        out=extract_receipts(raw,RECEIVED)
        self.assertEqual(len(out),2)
        self.assertEqual({x["period_basis"] for x in out},{"instant","annual"})
        self.assertTrue(all(x["first_seen_at"]==RECEIVED for x in out))
    def test_obvious_SEC_alert_is_recorded_not_generic_8k(self):
        s={"filings":{"recent":{"form":["8-K","8-K","25-NSE"],
                                   "items":["2.01","4.02",""],
                                   "accessionNumber":["a1","a2","a3"]}}}
        alerts=extract_regulatory_alerts(s,RECEIVED)
        self.assertEqual({x["risk_code"] for x in alerts},{"accounting_restatement","exchange_delisting"})
    def test_missing_EDGAR_contact_fails_closed_without_fetch(self):
        with patch.dict(os.environ,{"EDGAR_CONTACT":"example@invalid.example"}):
            x=research_ticker("TEST")
        self.assertEqual(x["classification"],"insufficient_evidence")
        self.assertFalse(x["long_term_flag"])

if __name__=="__main__":unittest.main()

