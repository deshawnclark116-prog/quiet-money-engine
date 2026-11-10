"""Unittest integrity gates for forward-only long-horizon company research."""
import copy
import unittest
from research.long_horizon.thesis import assess

ASOF="2026-11-10T19:00:00+00:00"
FIRST="2026-10-20T14:00:00+00:00"


def fact(metric,value,end="2026-09-30",basis="instant",first=FIRST,accession="0000123456-26-000001"):
    return {"metric":metric,"value":value,"period_end":end,
            "period_basis":basis,"unit":"shares" if metric=="shares_outstanding" else "USD",
            "filed_at":"2026-10-19T14:00:00+00:00" if first==FIRST else first,
            "first_seen_at":first,"source":"sec_xbrl","accession":accession}


def inputs():
    receipts=[fact("cash_usd",24_000_000), fact("total_debt_usd",5_000_000),
              fact("shares_outstanding",100_000_000),
              fact("shares_outstanding",95_000_000,"2025-09-30",accession="0000123456-25-000001"),
              fact("operating_cash_flow_usd",-4_000_000,"2026-09-30","annual"),
              fact("revenue_usd",30_000_000,"2026-09-30","annual"),
              fact("revenue_usd",20_000_000,"2025-09-30","annual",accession="0000123456-25-000001")]
    return dict(ticker="TEST",asof_utc=ASOF,financial_receipts=receipts,risk_events=[],
                business_milestones=[{"milestone_type":"customer_adoption", "verified":True,
                                      "source_id":"SEC-8K-2026-001", "primary_source":"sec_primary",
                                      "first_seen_at":FIRST}],
                risk_screen={"verified":True,"reviewed_at":"2026-11-09T18:00:00+00:00",
                             "coverage_start_at":"2026-01-01T00:00:00+00:00",
                             "coverage_end_at":"2026-11-09T18:00:00+00:00",
                             "source":"sec_primary","scope":"complete_filing_risk_review"},
                exchange_listing_verified=True)


class LongTermTests(unittest.TestCase):
    def test_good_company_research_flag_is_not_a_trade(self):
        x=assess(**inputs())
        self.assertEqual(x["classification"],"long_term_research_candidate")
        self.assertTrue(x["long_term_flag"])
        self.assertTrue(x["flag_for_deeper_research"])
        self.assertEqual(x["trade_action"],"none")
        self.assertFalse(x["automatic_hold"])
        self.assertFalse(x["override_short_term_exit"])
        self.assertEqual(x["metrics"]["estimated_cash_runway_months"],72)
    def test_preliminary_triage_can_still_flag_deeper_research(self):
        d=inputs();d["risk_screen"]={}
        x=assess(**d)
        self.assertTrue(x["flag_for_deeper_research"])
        self.assertFalse(x["long_term_flag"])
    def test_no_verified_business_progress_means_not_long_term(self):
        d=inputs();d["business_milestones"]=[]
        x=assess(**d)
        self.assertFalse(x["long_term_flag"])
        self.assertIn("verified_commercial_product_milestone",x["missing"])
    def test_generic_hype_is_not_verified_milestone(self):
        d=inputs();d["business_milestones"]=[{"milestone_type":"customer_adoption","verified":True,"first_seen_at":FIRST,"source_id":"some-blog","primary_source":"news_blog"}]
        self.assertFalse(assess(**d)["long_term_flag"])
    def test_low_cash_runway_blocks(self):
        d=inputs();d["financial_receipts"][0]["value"]=2_000_000
        x=assess(**d)
        self.assertEqual(x["classification"],"risk_blocked")
        self.assertIn("low_cash_runway",[r["risk_code"] for r in x["risks"]])
    def test_high_dilution_blocks(self):
        d=inputs();d["financial_receipts"][3]["value"]=50_000_000
        self.assertEqual(assess(**d)["classification"],"risk_blocked")
    def test_unverified_risk_audit_fails_closed(self):
        d=inputs();d["risk_screen"]={}
        x=assess(**d)
        self.assertFalse(x["long_term_flag"])
        self.assertIn("complete_recent_SEC_risk_screen_required",x["missing"])
    def test_verified_risk_alert_blocks(self):
        d=inputs();d["risk_events"]=[{"risk_code":"active_offering","source":"sec_primary",
                                       "accession":"0000123456-26-000002","first_seen_at":FIRST}]
        self.assertEqual(assess(**d)["classification"],"risk_blocked")
    def test_missing_facts_never_look_bullish(self):
        d=inputs();d["financial_receipts"]=[]
        x=assess(**d)
        self.assertEqual(x["classification"],"research_incomplete")
        self.assertFalse(x["long_term_flag"])
    def test_future_observed_companyfacts_cannot_leak(self):
        d=inputs()
        for r in d["financial_receipts"]:
            r["first_seen_at"]="2026-11-11T19:00:00+00:00"
        self.assertFalse(assess(**d)["long_term_flag"])
    def test_annual_vs_quarter_cannot_be_confused(self):
        d=inputs();d["financial_receipts"][-1]["period_basis"]="quarter"
        x=assess(**d)
        self.assertIn("comparable_prior_year_revenue",x["missing"])
    def test_no_short_position_override_even_with_strong_fundamentals(self):
        x=assess(**inputs())
        self.assertFalse(x["override_short_term_exit"])
        self.assertEqual(x["valuation_status"],"unreviewed")
    def test_offset_aware_asof_required(self):
        d=inputs();d["asof_utc"]="2026-11-10T19:00:00"
        self.assertEqual(assess(**d)["classification"],"insufficient_evidence")

if __name__=="__main__":
    unittest.main()

