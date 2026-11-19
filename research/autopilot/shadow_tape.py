"""Prospective SHADOW entry-quality tape, completely isolated from paper orders.

Records all qualifying model signals as of their first observed completed
session, including those not bought due to two-slot portfolio capacity.
Each point-in-time event keeps hypothesis eligibility before future prices
are observed. Hypothetical outcomes are attached ONLY after seven completed
future daily bars. Missing future bars stay unknown, never zero or winners.
This module never calls a broker and never creates actual paper orders.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

from research.autopilot.paper import _valid_complete, _signal
from research.autopilot.entry_audit import paper_fill_outcome, spy_five_day

VERSION="prospective_shadow_entry_v1"
MAX_UNRESOLVED_SYMBOLS=90
OUTCOME_HORIZON_SESSIONS=7


def ensure(state):
    tape=state.setdefault("signal_tape",{
        "schema":1,
        "version":VERSION,
        "events":[],
        "last_observation_utc":None,
    })
    if (not isinstance(tape,dict) or tape.get("schema")!=1
            or tape.get("version")!=VERSION
            or not isinstance(tape.get("events"),list)):
        raise ValueError("corrupt_signal_tape_fail_closed")
    return tape


def watched_symbols(state):
    if "signal_tape" not in state:
        return []  # First-ever deployment, not corrupted prior history.
    tape=ensure(state)  # Also check schema and experiment version.
    # In order of original first observation, not later measured winner.
    symbols=[]
    for event in tape["events"]:
        if event.get("status")=="awaiting_seven_sessions":
            ticker=event["ticker"]
            if ticker not in symbols:
                symbols.append(ticker)
    if len(symbols)>MAX_UNRESOLVED_SYMBOLS:
        # Never silently drop price tracking from a different experiment.
        raise ValueError("shadow_tracking_capacity_exceeded")
    return symbols


def update_tape(state, observations, asof_utc, session_date):
    """Record signal-time facts, then evaluate ONLY matured prior predictions."""
    tape=ensure(state)
    complete={
        ticker:_valid_complete(rows,asof_utc)
        for ticker,rows in observations.items()
    }
    spy=complete.get("SPY",[])
    benchmark={"bars":spy,"by_date":{r["date"]:i for i,r in enumerate(spy)}}
    known={(e["ticker"],e["signal_date"]) for e in tape["events"]}
    paper_selected={
        (e.get("ticker"),e.get("signal_date"))
        for e in state.get("events",[])
        if e.get("kind")=="SIGNAL"
    }
    created=0
    for ticker in sorted(complete):
        if ticker=="SPY":continue
        bars=complete[ticker]
        if len(bars)<100 or bars[-1]["date"]!=session_date:
            continue
        key=(ticker,session_date)
        if key in known:continue
        asof_signal=_signal(ticker,bars,asof_utc,session_date)
        if asof_signal is None:continue
        r5=bars[-1]["close"]/bars[-6]["close"]-1
        r20=bars[-1]["close"]/bars[-21]["close"]-1
        volume_ratio=median(x["volume"] for x in bars[-5:])/max(
            1,median(x["volume"] for x in bars[-25:-5]))
        spy5=spy_five_day(benchmark,session_date)
        no_chase=r5<=.08
        market_ok=spy5 is not None and spy5>=-.01
        tape["events"].append({
            "ticker":ticker,
            "signal_date":session_date,
            "first_observed_utc":asof_utc,
            "score":asof_signal["score"],
            "signal_close":round(bars[-1]["close"],6),
            "return_5d_pct":round(100*r5,5),
            "return_20d_pct":round(100*r20,5),
            "volume_ratio":round(volume_ratio,5),
            "spy_return_5d_pct":round(100*spy5,5) if spy5 is not None else None,
            "was_selected_by_live_paper":key in paper_selected,
            "hypotheses_recorded_before_outcomes":{
                "incumbent":True,
                "no_chase_8":no_chase,
                "market_guard":market_ok,
                "both":no_chase and market_ok,
            },
            "status":"awaiting_seven_sessions",
            "outcome":None,
            "note":"Shadow only: not an executed or funded trade",
        })
        known.add(key)
        created+=1

    resolved=0
    for event in tape["events"]:
        if event["status"]!="awaiting_seven_sessions":continue
        rows=complete.get(event["ticker"],[])
        by_date={bar["date"]:i for i,bar in enumerate(rows)}
        i=by_date.get(event["signal_date"])
        if i is None or len(rows)<=i+OUTCOME_HORIZON_SESSIONS:
            continue
        outcome=paper_fill_outcome(rows,i)
        if outcome["status"]=="right_censored":
            continue
        event["outcome"]=outcome
        event["status"]=outcome["status"]
        event["observed_outcome_at_utc"]=asof_utc
        resolved+=1
    tape["last_observation_utc"]=asof_utc
    return {
        "version":VERSION,
        "new_signals":created,
        "newly_resolved":resolved,
        "total_recorded":len(tape["events"]),
        "pending":sum(e["status"]=="awaiting_seven_sessions"
                      for e in tape["events"]),
        "resolved":sum(e["status"]!="awaiting_seven_sessions"
                       for e in tape["events"]),
    }
