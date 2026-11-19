"""Prospective shadow tape: strict no-future-outcomes and zero trade interference."""
from __future__ import annotations

from datetime import date, timedelta
import copy
import unittest

from research.autopilot.paper import fresh_state, step
from research.autopilot.shadow_tape import (
    update_tape, watched_symbols, ensure,
)


def history(n=120, final_close=5.25):
    dates=[]
    day=date(2026,4,1)
    while len(dates)<n:
        if day.weekday()<5:
            dates.append(day)
        day+=timedelta(days=1)
    rows=[]
    for i,d in enumerate(dates):
        close=final_close if i==119 else 5.
        rows.append({
            "date":d.isoformat(),"open":close,"high":close*1.01,
            "low":close*.99,"close":close,
            "volume":2000000 if 115<=i<=119 else 500000})
    return rows


def future_bars(previous, n=7):
    out=copy.deepcopy(previous)
    last=date.fromisoformat(out[-1]["date"])
    while len(out)<len(previous)+n:
        last+=timedelta(days=1)
        if last.weekday()>=5:continue
        out.append({
            "date":last.isoformat(),"open":5.3,"high":5.4,
            "low":5.2,"close":5.3,"volume":500000})
    return out


def asof(rows):
    return rows[-1]["date"]+"T23:30:00+00:00"


class ForwardTapeTests(unittest.TestCase):
    def test_qualifying_signal_captured_without_creating_orders(self):
        data=history()
        state=fresh_state()
        existing=copy.deepcopy(state)
        snap=update_tape(state,{"ACME":data,"SPY":history(final_close=5.)},
                         asof(data),data[-1]["date"])
        self.assertEqual(snap["new_signals"],1)
        event=state["signal_tape"]["events"][0]
        self.assertEqual(event["signal_date"],data[-1]["date"])
        self.assertEqual(event["status"],"awaiting_seven_sessions")
        self.assertIsNone(event["outcome"])
        self.assertTrue(event["hypotheses_recorded_before_outcomes"]["no_chase_8"])
        self.assertFalse(event["was_selected_by_live_paper"])
        self.assertEqual(state["cash"],existing["cash"])
        self.assertEqual(state["positions"],existing["positions"])
        self.assertEqual(state["pending_entries"],existing["pending_entries"])
        self.assertEqual(watched_symbols(state),["ACME"])

    def test_real_paper_queues_independently_and_shadow_reuses_only_prices(self):
        data=history()
        state=fresh_state()
        observation={"ACME":data,"SPY":history(final_close=5.)}
        step(state,observation,asof(data))
        self.assertEqual(len(state["pending_entries"]),1)
        cash=state["cash"]
        before=copy.deepcopy(state["events"])
        update_tape(state,observation,asof(data),data[-1]["date"])
        self.assertEqual(state["cash"],cash)
        self.assertEqual(state["events"],before)
        self.assertTrue(state["signal_tape"]["events"][0]["was_selected_by_live_paper"])

    def test_future_price_inside_observation_is_invisible_before_date(self):
        signal=history()
        extended=future_bars(signal)
        state=fresh_state()
        update_tape(state,{"ACME":extended,"SPY":history(final_close=5.)},
                    asof(signal),signal[-1]["date"])
        tape=state["signal_tape"]["events"]
        self.assertEqual(len(tape),1)
        self.assertIsNone(tape[0]["outcome"])
        self.assertEqual(tape[0]["status"],"awaiting_seven_sessions")

    def test_outcome_only_after_seven_completed_future_sessions(self):
        signal=history()
        extended=future_bars(signal)
        state=fresh_state()
        update_tape(state,{"ACME":signal,"SPY":history(final_close=5.)},
                    asof(signal),signal[-1]["date"])
        partial=extended[:-1]
        update_tape(state,{"ACME":partial,"SPY":history(final_close=5.)},
                    asof(partial),partial[-1]["date"])
        self.assertEqual(state["signal_tape"]["events"][0]["status"],
                         "awaiting_seven_sessions")
        update_tape(state,{"ACME":extended,"SPY":history(final_close=5.)},
                    asof(extended),extended[-1]["date"])
        event=state["signal_tape"]["events"][0]
        self.assertEqual(event["status"],"evaluated")
        self.assertEqual(event["outcome"]["exit_reason"],"time_exit")
        self.assertEqual(watched_symbols(state),[])

    def test_same_day_retries_do_not_duplicate_signals(self):
        data=history()
        state=fresh_state()
        observations={"ACME":data,"SPY":history(final_close=5.)}
        update_tape(state,observations,asof(data),data[-1]["date"])
        result=update_tape(state,observations,asof(data),data[-1]["date"])
        self.assertEqual(result["new_signals"],0)
        self.assertEqual(result["total_recorded"],1)

    def test_market_guard_is_unknown_not_pass_when_spy_unavailable(self):
        data=history()
        state=fresh_state()
        update_tape(state,{"ACME":data},asof(data),data[-1]["date"])
        event=state["signal_tape"]["events"][0]
        self.assertIsNone(event["spy_return_5d_pct"])
        self.assertFalse(event["hypotheses_recorded_before_outcomes"]["market_guard"])

    def test_later_missing_quote_does_not_close_hypothetical_position(self):
        data=history()
        state=fresh_state()
        update_tape(state,{"ACME":data,"SPY":history(final_close=5.)},
                    asof(data),data[-1]["date"])
        update_tape(state,{"SPY":data},asof(data),data[-1]["date"])
        self.assertEqual(state["signal_tape"]["events"][0]["status"],
                         "awaiting_seven_sessions")
        self.assertIn("ACME",watched_symbols(state))

    def test_chase_candidate_is_recorded_but_challenger_abstains(self):
        data=history(final_close=5.55)
        state=fresh_state()
        update_tape(state,{"ACME":data,"SPY":history(final_close=5.)},
                    asof(data),data[-1]["date"])
        event=state["signal_tape"]["events"][0]
        self.assertGreater(event["return_5d_pct"],8)
        self.assertFalse(event["hypotheses_recorded_before_outcomes"]["no_chase_8"])
        self.assertTrue(event["hypotheses_recorded_before_outcomes"]["incumbent"])

    def test_bad_tape_fails_closed(self):
        state=fresh_state()
        state["signal_tape"]={"schema":1,"version":"tampered","events":[]}
        with self.assertRaisesRegex(ValueError,"corrupt_signal_tape"):
            ensure(state)
        with self.assertRaisesRegex(ValueError,"corrupt_signal_tape"):
            watched_symbols(state)


if __name__=="__main__":
    unittest.main()
