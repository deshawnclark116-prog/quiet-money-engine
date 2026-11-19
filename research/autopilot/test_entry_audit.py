"""No-lookahead and exact-paper-mechanics tests for entry-quality research."""
from __future__ import annotations

from datetime import date, timedelta
import unittest

from research.autopilot.entry_audit import (
    COHORT, REFERENCE_20261112, clean_bars, paper_fill_outcome,
    historical_events, evaluate_all, summarize,
)


def bars(n=120, start=date(2026,1,1), close=5.0, volatility=.01):
    data=[]
    d=start
    while len(data)<n:
        if d.weekday()<5:
            data.append({"date":d.isoformat(),"open":close,
                "high":close*(1+volatility),
                "low":close*(1-volatility),
                "close":close,"volume":2000000})
        d+=timedelta(days=1)
    return data


class EntryAuditContractTests(unittest.TestCase):
    def test_frozen_original_scanner_70_symbols(self):
        self.assertEqual(len(COHORT),70)
        self.assertEqual(len(set(COHORT)),70)
        self.assertTrue({"ACHR","AMPX","NRGV"}.issubset(COHORT))

    def test_actual_two_losers_were_near_upper_5day_limit(self):
        self.assertGreater(REFERENCE_20261112["ACHR"]["five_day_pct"],11.)
        self.assertGreater(REFERENCE_20261112["AMPX"]["five_day_pct"],11.)

    def test_corrupt_ohlc_removed_not_patched(self):
        rows=bars(2)
        rows[1]["high"]=rows[1]["low"]-.01
        valid,invalid=clean_bars(rows)
        self.assertEqual(invalid,1)
        self.assertEqual(len(valid),1)

    def test_no_return_without_future_7_bars(self):
        history=bars(110)
        x=paper_fill_outcome(history,109)
        self.assertEqual(x["status"],"right_censored")

    def test_next_open_75bps_each_side_and_time_exit(self):
        data=bars(115)
        x=paper_fill_outcome(data,105)
        self.assertEqual(x["status"],"evaluated")
        self.assertEqual(x["entry_date"],data[106]["date"])
        self.assertEqual(x["exit_date"],data[112]["date"])
        self.assertEqual(x["exit_reason"],"time_exit")
        self.assertAlmostEqual(x["entry_price"],5.0375,places=4)
        self.assertAlmostEqual(x["exit_price"],4.9625,places=4)
        self.assertLess(x["net_return_pct"],0)

    def test_gap_down_cancels_next_open_no_faked_entry(self):
        data=bars(115)
        data[106].update(open=3.0,low=2.95,high=5.10,close=5.0)
        x=paper_fill_outcome(data,105)
        self.assertEqual(x,{"status":"cancelled","reason":"downward_gap_gt_25pct"})

    def test_stop_first_even_when_target_also_hit(self):
        data=bars(115)
        data[106].update(open=5.0,low=4.0,high=6.5,close=5.1)
        x=paper_fill_outcome(data,105)
        self.assertEqual(x["exit_reason"],"protective_stop")
        self.assertEqual(x["exit_date"],data[106]["date"])
        self.assertLess(x["net_return_pct"],-8.)

    def test_gap_through_stop_uses_open_not_stop(self):
        data=bars(115)
        data[107].update(open=4.0,low=3.9,high=4.2,close=4.1)
        x=paper_fill_outcome(data,105)
        self.assertEqual(x["exit_reason"],"gap_through_stop")
        self.assertLess(x["net_return_pct"],-15.)

    def test_target_credit_after_buy_cost(self):
        data=bars(115)
        data[106].update(open=5.,low=4.95,high=6.3,close=5.5)
        x=paper_fill_outcome(data,105)
        self.assertEqual(x["exit_reason"],"profit_target")
        self.assertGreater(x["net_return_pct"],14.)
        self.assertLess(x["net_return_pct"],16.)

    def test_future_spy_prices_do_not_enter_candidate_decision(self):
        data=bars(125)
        # A rise last 5 days is visible as of day 119, so only past SPY
        # observations may be used to label same-day regime.
        from research.autopilot.entry_audit import spy_five_day
        spy={"bars":data,"by_date":{r["date"]:i for i,r in enumerate(data)}}
        before=spy_five_day(spy,data[119]["date"])
        data[120]["close"]=1000.
        after=spy_five_day(spy,data[119]["date"])
        self.assertEqual(before,after)

    def test_market_regime_arm_abstains_when_spy_history_missing(self):
        data=bars(120)
        # With flat closing prices no signal occurs, and market arm never
        # invents qualifying events or future returns.
        spy={"bars":[],"by_date":{}}
        events,_=historical_events("TEST",data,spy)
        self.assertEqual(events,[])
        self.assertEqual(summarize(events,"market_guard")["complete_fills"],0)

    def test_missing_benchmark_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"SPY history"):
            evaluate_all({"AMPX":bars()}, {}, actual_portfolio=None)

    def test_short_spy_history_cannot_claim_regime_performance(self):
        with self.assertRaisesRegex(ValueError,"historical price basis or coverage"):
            evaluate_all({"SPY":bars(120)}, {}, actual_portfolio=None)

    def test_reference_portfolio_is_read_only(self):
        original={"mode":"PAPER_ONLY_NO_BROKER","closed_trades":[
            {"ticker":"ACHR","entry_date":"2026-11-13",
             "exit_date":"2026-11-13","signal_date":"2026-11-12",
             "entry_price":4.382625,"exit_price":4.001775,
             "pnl_usd":-1.14255,"return_pct":-8.69,
             "exit_reason":"protective_stop"}]}
        result=evaluate_all({"SPY":bars(520)}, {}, actual_portfolio=original)
        self.assertEqual(result["known_first_losers"][0]["ticker"],"ACHR")
        self.assertNotIn("paper_order",result)
        self.assertEqual(original["closed_trades"][0]["pnl_usd"],-1.14255)


if __name__=="__main__":
    unittest.main()
