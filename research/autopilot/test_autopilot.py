"""Offline tests enforce no trades, no future candles and no invented SEC facts."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from research.autopilot.decision import completed,triage,value_screen,category,decide
from research.autopilot.run import run,load_events,markdown

NOW="2026-11-10T22:00:00+00:00"

def bars(start=datetime(2026,1,20,tzinfo=timezone.utc),n=210):
    d=start;out=[]
    while len(out)<n:
        if d.weekday()<5:
            p=1+len(out)*.0004
            out.append({"date":d.date().isoformat(),"open":p,
                        "high":p*1.01,"low":p*.99,"close":p,"volume":2_000_000})
        d+=timedelta(days=1)
    return out

class AutopilotTests(unittest.TestCase):
    def test_current_candle_cannot_look_ahead(self):
        hist=bars()
        hist.append({"date":"2026-11-10","open":1,"high":2,"low":1,
                     "close":2,"volume":999_999_999})
        self.assertEqual(completed(hist,"2026-11-10T20:00:00+00:00")[-1]["date"],
                         "2026-11-09")
        self.assertNotEqual(triage(hist,"2026-11-10T20:00:00+00:00")["last_close"],2)

    def test_screen_non_extended_liquid(self):
        result=triage(bars(),NOW)
        self.assertEqual(result["status"],"price_research_candidate")
        self.assertIsNotNone(result["research_score"])

    def test_no_data_no_broker_or_paper_order(self):
        decision=decide("ZOM",[],[],NOW,{"classification":"research_incomplete"})
        self.assertIsNone(decision["broker_order"])
        self.assertIsNone(decision["paper_order"])
        self.assertFalse(decision["trade_approved"])

    def test_ratio_is_not_fair_value(self):
        ratio=value_screen({"metrics":{"shares_outstanding":100_000_000,
                "cash_usd":10_000_000,"total_debt_usd":1_000_000}},
                {"last_close":1.2})
        self.assertEqual(ratio["market_cap_proxy_usd"],120_000_000)
        self.assertFalse(ratio["buy_approved"])
        self.assertIsNone(ratio["fair_value"])

    def test_four_research_intersections(self):
        for short,long_,expected in [
            (True,True,"dual_research_candidate"),
            (True,False,"short_term_research_only"),
            (False,True,"long_term_research_only"),
            (False,False,"insufficient_verified_evidence")]:
            self.assertEqual(category({"status":"dislocation_candidate" if short else "abstain"},
                                      {"long_term_flag":long_}),expected)

    def test_risk_block(self):
        self.assertEqual(category({"status":"abstain"},
                    {"classification":"risk_blocked","long_term_flag":False}),"risk_blocked")

    def test_operator_event_attestation_required(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"events.json"
            path.write_text(json.dumps([{"ticker":"ZOM","source":"sec_primary",
                 "evidence_status":"verified","polarity":"positive",
                 "event_id":"0000000000-26-123456","observed_at":NOW}]))
            self.assertEqual(load_events(str(path)),{})

    def test_scan_lists_all_stocks_and_no_fake_buys(self):
        def prices(ticker,count):return bars()
        def company(ticker):return {"ticker":ticker,"classification":"research_incomplete",
                     "long_term_flag":False,"flag_for_deeper_research":False,
                     "metrics":{}}
        report=run(now=NOW,tickers=["ZOM","XYZZ"],prices=prices,companies=company)
        self.assertEqual(report["universe_count"],2)
        self.assertEqual(len(report["all_scanned"]),2)
        self.assertEqual(len(report["deep_research"]),2)
        for item in report["deep_research"]:
            self.assertIsNone(item["broker_order"])
            self.assertIsNone(item["paper_order"])
            self.assertNotEqual(item["classification"],"dual_research_candidate")
        self.assertIn("Every ticker inspected",markdown(report))

if __name__=="__main__":unittest.main()
