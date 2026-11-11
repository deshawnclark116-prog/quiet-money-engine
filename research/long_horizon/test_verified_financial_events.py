"""Objective SEC primary financial event tests, including strong negative controls."""
import unittest
from copy import deepcopy
from research.long_horizon.verified_financial_events import extract_verified_inflections

SEEN="2026-11-10T21:00:00+00:00"
ACC="0000123456-26-000001"

def receipt(metric,value,end,accn,basis="quarter",filed="2026-11-10"):
    return {"metric":metric,"value":value,"period_end":end,
            "period_basis":basis,"accession":accn,
            "filed_at":filed+"T00:00:00+00:00","first_seen_at":SEEN,
            "source":"sec_xbrl","unit":"USD"}

def data():
    facts=[
        receipt("revenue_usd",40_000_000,"2026-09-30",ACC),
        receipt("revenue_usd",25_000_000,"2025-09-30","0000123456-25-000001",filed="2025-11-01"),
        receipt("operating_cash_flow_usd",5_000_000,"2026-09-30",ACC),
        receipt("operating_cash_flow_usd",1_000_000,"2025-09-30","0000123456-25-000001",filed="2025-11-01"),
    ]
    filings={"filings":{"recent":{"form":["10-Q"],"filingDate":["2026-11-10"],
                                   "accessionNumber":[ACC]}}}
    return facts,filings

class SecFinancialEventsTests(unittest.TestCase):
    def test_verified_new_company_change_is_research_not_buy(self):
        facts,filings=data()
        events=extract_verified_inflections(facts,filings,SEEN,cik="123456")
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["event_id"],ACC)
        self.assertEqual(events[0]["source"],"sec_primary")
        self.assertTrue(events[0]["research_only"])
        self.assertEqual(events[0]["trade_action"],"none")
        self.assertEqual(events[0]["evidence"]["revenue_growth_pct"],60)

    def test_old_filing_is_not_repackaged_as_fresh(self):
        facts,filings=data()
        filings["filings"]["recent"]["filingDate"]=["2026-09-01"]
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

    def test_cashflow_required(self):
        facts,filings=data()
        facts=[f for f in facts if f["metric"]!="operating_cash_flow_usd"]
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

    def test_weak_revenue_not_positive(self):
        facts,filings=data()
        facts[0]["value"]=27_000_000
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

    def test_incomparable_annual_cashflow_cannot_confirm_quarter(self):
        facts,filings=data()
        facts[2]["period_basis"]="annual"
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

    def test_future_first_seen_not_backdated(self):
        facts,filings=data()
        facts[0]["first_seen_at"]="2026-11-11T21:00:00+00:00"
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

    def test_press_release_and_generic_8k_never_qualify(self):
        facts,filings=data()
        filings["filings"]["recent"]["form"]=["8-K"]
        self.assertEqual(extract_verified_inflections(facts,filings,SEEN),[])

if __name__=="__main__":
    unittest.main()
