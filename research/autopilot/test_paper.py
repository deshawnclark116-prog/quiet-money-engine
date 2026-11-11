"""Paper trading contract tests: causal entry, losses, persistence, abstention."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from datetime import date, timedelta
import unittest

from research.autopilot.paper import (
    fresh_state, step, validate_state, MAX_POSITIONS, STARTING_CASH,
)


ASOF_TUE = "2026-11-10T21:45:00+00:00"
ASOF_WED = "2026-11-11T21:45:00+00:00"


def history(last=date(2026, 11, 10)):
    days = []
    current = last
    while len(days) < 120:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    days.reverse()
    rows = []
    for i, day in enumerate(days):
        close = 5.0 if i < 119 else 5.25
        volume = 1000000 if i >= 115 else 500000
        rows.append({
            "date": day.isoformat(),
            "open": close,
            "high": round(close * 1.01, 6),
            "low": round(close * .99, 6),
            "close": close,
            "volume": volume,
        })
    return rows


def bar(day, op=5.30, hi=5.4, lo=5.20, close=5.30):
    return {"date": day, "open": op, "high": hi,
            "low": lo, "close": close, "volume": 1000000}


class PaperEngineTests(unittest.TestCase):
    def initial(self):
        state = fresh_state()
        step(state, {"ACME": history()}, ASOF_TUE)
        self.assertEqual(state["cash"], STARTING_CASH)
        self.assertEqual(len(state["positions"]), 0)
        self.assertEqual(len(state["pending_entries"]), 1)
        self.assertEqual(state["pending_entries"][0]["signal_date"], "2026-11-10")
        return state

    def test_first_signal_does_not_fill_same_day(self):
        self.initial()

    def test_next_session_open_fills_but_no_real_broker(self):
        state=self.initial()
        obs={"ACME":history()+[bar("2026-11-11")]}
        step(state,obs,ASOF_WED)
        self.assertEqual(len(state["positions"]),1)
        p=state["positions"]["ACME"]
        self.assertEqual(p["entry_date"],"2026-11-11")
        self.assertGreater(p["entry_price"],5.30)
        self.assertGreater(state["cash"],0)
        self.assertEqual(state["mode"],"PAPER_ONLY_NO_BROKER")

    def test_no_future_data_leak_before_next_close(self):
        state=self.initial()
        obs={"ACME":history()+[bar("2026-11-11")]}
        step(state,obs,ASOF_TUE)
        self.assertEqual(len(state["positions"]),0)
        self.assertEqual(len(state["pending_entries"]),1)
        self.assertEqual(len(state["closed_trades"]),0)

    def test_stop_first_if_daily_high_and_low_cross(self):
        state=self.initial()
        both=bar("2026-11-11",op=5.3,hi=7.5,lo=4.0,close=6.5)
        step(state,{"ACME":history()+[both]},ASOF_WED)
        self.assertEqual(len(state["closed_trades"]),1)
        self.assertEqual(state["closed_trades"][0]["exit_reason"],"protective_stop")
        self.assertLess(state["closed_trades"][0]["pnl_usd"],0)

    def test_gap_through_stop_exits_at_worse_open(self):
        state=self.initial()
        day1=bar("2026-11-11",op=5.3,hi=5.4,lo=5.2,close=5.3)
        step(state,{"ACME":history()+[day1]},ASOF_WED)
        day2=bar("2026-11-12",op=3.8,hi=4,lo=3.7,close=3.8)
        step(state,{"ACME":history()+[day1,day2]},"2026-11-12T21:45:00+00:00")
        self.assertEqual(state["closed_trades"][-1]["exit_reason"],"gap_through_stop")
        self.assertLess(state["closed_trades"][-1]["exit_price"],4)

    def test_big_upward_gap_cancels_entry(self):
        state=self.initial()
        gap=bar("2026-11-11",op=6.1,hi=6.3,lo=6.0,close=6.2)
        step(state,{"ACME":history()+[gap]},ASOF_WED)
        self.assertEqual(len(state["positions"]),0)
        self.assertEqual(len(state["closed_trades"]),0)
        self.assertTrue(any(e.get("reason")=="upward_gap_gt_8pct" for e in state["events"]))

    def test_invalid_ohlc_never_trades(self):
        state=self.initial()
        broken=bar("2026-11-11",op=5.3,hi=4.0,lo=5.2,close=5.3)
        step(state,{"ACME":history()+[broken]},ASOF_WED)
        self.assertEqual(len(state["positions"]),0)
        self.assertEqual(len(state["pending_entries"]),1)

    def test_missing_provider_does_not_invent_fill(self):
        state=self.initial()
        step(state,{"ACME":[]},ASOF_WED)
        self.assertEqual(len(state["positions"]),0)
        self.assertEqual(len(state["pending_entries"]),1)

    def test_same_session_rerun_is_idempotent(self):
        state=self.initial()
        data={"ACME":history()+[bar("2026-11-11")]}
        step(state,data,ASOF_WED)
        event_count=len(state["events"])
        cash=state["cash"]
        shares=state["positions"]["ACME"]["shares"]
        step(state,data,ASOF_WED)
        self.assertEqual(len(state["events"]),event_count)
        self.assertEqual(state["cash"],cash)
        self.assertEqual(state["positions"]["ACME"]["shares"],shares)

    def test_state_corruption_refused_not_reset(self):
        state=self.initial()
        state["mode"]="LIVE"
        with self.assertRaises(ValueError):
            validate_state(state)

    def test_capital_never_negative_or_fractional_shares(self):
        state=self.initial()
        step(state,{"ACME":history()+[bar("2026-11-11")]},ASOF_WED)
        self.assertGreaterEqual(state["cash"],0)
        self.assertIsInstance(state["positions"]["ACME"]["shares"],int)
        self.assertLessEqual(len(state["positions"]),MAX_POSITIONS)

    def test_signal_is_not_claimed_to_be_calibrated(self):
        state=self.initial()
        signal=[e for e in state["events"] if e["kind"]=="SIGNAL"][0]
        self.assertEqual(signal["signal_type"],
                         "unvalidated_price_volume_acceleration")
        self.assertNotIn("probability",signal)



    def test_runner_writes_complete_recoverable_portfolio(self):
        from research.autopilot.paper_runner import run, save
        state=fresh_state()
        res,errors=run(state,now=ASOF_TUE,tickers=["ACME"],
                       provider=lambda ticker, days: history())
        self.assertEqual(res["pending"],1)
        with tempfile.TemporaryDirectory() as temp:
            save(state,res,errors,temp)
            expected={"portfolio.json","dashboard.md","latest_run.json",
                      "closed_trades.csv","equity_history.csv"}
            self.assertTrue(expected.issubset(
                {p.name for p in Path(temp).iterdir()}))
            restored=json.loads((Path(temp)/"portfolio.json").read_text())
            self.assertEqual(restored["cash"],25.0)
            self.assertEqual(len(restored["pending_entries"]),1)
            self.assertIn("PAPER", (Path(temp)/"dashboard.md").read_text())

    def test_completely_missing_data_fails_no_write(self):
        from research.autopilot.paper_runner import run
        state=fresh_state()
        with self.assertRaises(RuntimeError):
            run(state,now=ASOF_TUE,tickers=["ACME"],
                provider=lambda ticker, days: [])
        self.assertEqual(state,fresh_state())



    def test_spy_benchmark_is_not_an_order(self):
        state=fresh_state()
        step(state,{"SPY":history()},ASOF_TUE)
        self.assertEqual(len(state["pending_entries"]),0)
        self.assertEqual(state["benchmark"]["start_close"],5.25)
        self.assertEqual(state["benchmark"]["last_date"],"2026-11-10")
        step(state,{"SPY":history()+[bar("2026-11-11",
             op=5.40,hi=5.50,lo=5.35,close=5.45)]},ASOF_WED)
        self.assertEqual(state["benchmark"]["last_close"],5.45)
        self.assertEqual(len(state["positions"]),0)


if __name__=="__main__":
    unittest.main()
