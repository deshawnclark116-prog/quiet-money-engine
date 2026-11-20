"""Structural research causal/portfolio execution contract tests."""
from __future__ import annotations
import copy, unittest
from datetime import date,timedelta
from research.autopilot.structural_lab import (
    ARMS,PERIODS,features,scores,replay,find_candidates)


def make_rows(n=420, start=date(2024,1,1), close=5.0, volume=500000):
    rows=[]
    d=start
    while len(rows)<n:
        if d.weekday()<5:
            rows.append({"date":d.isoformat(),"open":close,
              "high":close*1.01,"low":close*.99,
              "close":close,"volume":volume})
        d+=timedelta(days=1)
    return rows


class StructuralResearchTests(unittest.TestCase):
    def test_hypotheses_frozen_and_distinct(self):
        self.assertEqual(len(ARMS),5)
        self.assertEqual(set(ARMS),{
            "incumbent","resilience","quiet_accumulation","reversion","placebo"})
        self.assertEqual(PERIODS["exposed_2026_diagnostic"][1],"2026-10-31")

    def test_no_stock_future_close_can_affect_past_signal(self):
        prices=make_rows()
        # Construct more SPY down days without touching the prefix.
        spy=make_rows()
        for i in range(1,len(spy)):
            spy[i]["close"]=spy[i-1]["close"]*.999
            spy[i]["open"]=spy[i]["close"]
            spy[i]["high"]=spy[i]["close"]*1.01
            spy[i]["low"]=spy[i]["close"]*.99
        benchmark={r["date"]:r for r in spy}
        at=150
        before=features(prices,at,benchmark)
        self.assertIsNotNone(before)
        altered=copy.deepcopy(prices)
        altered[at+1]["close"]=1000
        altered[at+1]["high"]=1100
        spy[at+1]["close"]=1
        after=features(altered,at,benchmark)
        self.assertEqual(before,after)

    def test_missing_spy_calendar_day_refuses_forecast(self):
        stock=make_rows()
        spy={r["date"]:r for r in make_rows()}
        del spy[stock[140]["date"]]
        self.assertIsNone(features(stock,150,spy))

    def test_no_liquidity_means_no_candidate(self):
        stock=make_rows(volume=10)
        spy={r["date"]:r for r in make_rows()}
        self.assertIsNone(features(stock,150,spy))

    def test_mechanisms_dont_depend_on_future_returns(self):
        f={"return5":0.,"return20":0.,"relative20":0.04,
           "close_location5":.8,"up_volume_share":.65,
           "compression":.6,"down_excess":.01,
           "down_outperform_rate":.8}
        s=scores(f)
        self.assertIn("resilience",s)
        self.assertIn("quiet_accumulation",s)
        self.assertNotIn("reversion",s)
        f["return20"]=-.10
        f["relative20"]=0.
        s2=scores(f)
        self.assertIn("reversion",s2)
        self.assertNotIn("quiet_accumulation",s2)

    def test_no_same_day_order_execution_and_strict_costs(self):
        stock=make_rows(140)
        spy=make_rows(140)
        d=stock[115]["date"]
        offers={d:[{"ticker":"ACME","signal_date":d,
                    "signal_close":5.0,"score":1.}]}
        output=replay({"SPY":spy,"ACME":stock},offers,d,stock[128]["date"])
        self.assertGreaterEqual(output["closed_trades"],1)
        x=output["entry_receipts"][0]
        self.assertGreater(x["entry_date"],d)
        self.assertAlmostEqual(x["entry_price"],5.0375,places=4)
        self.assertLess(output["end_equity_usd"],25.)

    def test_lower_than_stop_next_open_exit_uses_gap(self):
        stock=make_rows(140)
        spy=make_rows(140)
        d=stock[115]["date"]
        stock[117].update(open=4.0,low=3.95,high=4.05,close=4.)
        offers={d:[{"ticker":"ACME","signal_date":d,
                    "signal_close":5.0,"score":1.}]}
        result=replay({"SPY":spy,"ACME":stock},offers,d,stock[130]["date"])
        self.assertEqual(result["exit_receipts"][0]["exit_reason"],"gap_through_stop")
        self.assertLess(result["exit_receipts"][0]["pnl_usd"],0)

    def test_repeat_deterministic_no_hidden_market_side_effects(self):
        stock=make_rows(140)
        spy=make_rows(140)
        today=stock[115]["date"]
        offers={today:[{"ticker":"ACME","signal_date":today,
                    "signal_close":5.0,"score":1.}]}
        a=replay({"SPY":spy,"ACME":stock},offers,today,stock[131]["date"])
        b=replay({"SPY":spy,"ACME":stock},offers,today,stock[131]["date"])
        self.assertEqual(a,b)

    def test_missing_history_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"missing_SPY"):
            find_candidates({"SPY":make_rows(200)})


if __name__=="__main__": unittest.main()
