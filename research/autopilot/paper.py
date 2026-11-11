"""GitHub-only, daily-bar, forward paper portfolio. NEVER communicates with a broker.

Signals are recorded after an observed completed daily session. An entry is
simulated at the NEXT session's open, once that future completed OHLCV bar
becomes available. Stops/targets use conservative stop-first OHLC ambiguity.
A simulated execution price is NOT a real-time quote or executable fill.
"""
from __future__ import annotations

from datetime import date, datetime
from math import floor, isfinite
from statistics import median
from zoneinfo import ZoneInfo

from research.autopilot.decision import completed, triage

VERSION = "github_paper_v1"
ET = ZoneInfo("America/New_York")
STARTING_CASH = 25.0
IMPACT_BPS = 75  # per side: combined hypothetical spread + market impact
MAX_POSITIONS = 2
MAX_POSITION_FRACTION = 0.60
MAX_EQUITY_RISK_PER_TRADE = 0.05
STOP_FRACTION = 0.08
TARGET_FRACTION = 0.16
MAX_HOLD_SESSIONS = 7
MAX_PENDING_CALENDAR_DAYS = 8


def fresh_state():
    return {
        "schema": 1,
        "version": VERSION,
        "mode": "PAPER_ONLY_NO_BROKER",
        "starting_cash": STARTING_CASH,
        "cash": STARTING_CASH,
        "positions": {},
        "pending_entries": [],
        "closed_trades": [],
        "events": [],
        "equity_history": [],
        "last_signal_date": {},
        "last_run_at_utc": None,
    }


def validate_state(state):
    if not isinstance(state, dict) or state.get("schema") != 1:
        raise ValueError("portfolio_schema_invalid_refuse_reset")
    if state.get("mode") != "PAPER_ONLY_NO_BROKER":
        raise ValueError("non_paper_state_refused")
    if state.get("starting_cash") != STARTING_CASH:
        raise ValueError("starting_capital_changed_refuse_reset")
    if not isinstance(state.get("positions"), dict):
        raise ValueError("positions_invalid")
    if not isinstance(state.get("pending_entries"), list):
        raise ValueError("pending_entries_invalid")
    if not isinstance(state.get("events"), list):
        raise ValueError("events_invalid")
    cash = float(state["cash"])
    if not isfinite(cash) or cash < -0.001:
        raise ValueError("negative_or_nonfinite_cash")
    for t, p in state["positions"].items():
        if p["ticker"] != t or int(p["shares"]) < 1:
            raise ValueError("corrupt_open_position")
    return state


def _price_row(row):
    try:
        values = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
        o, h, l, c, v = values
        if not all(isfinite(x) for x in values):
            return None
        if min(o, c, l) <= 0 or v < 0:
            return None
        # Never combine adjusted close with unadjusted open/high/low.
        if not l <= min(o, c) <= max(o, c) <= h:
            return None
        date.fromisoformat(str(row["date"])[:10])
        return {"date": str(row["date"])[:10], "open": o, "high": h,
                "low": l, "close": c, "volume": v}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _valid_complete(bars, asof):
    by_date = {}
    for row in completed(bars or [], asof):
        valid = _price_row(row)
        if valid:
            by_date[valid["date"]] = valid
    return [by_date[d] for d in sorted(by_date)]


def _event(state, kind, **fields):
    state["events"].append({"seq": len(state["events"]) + 1,
                            "kind": kind, **fields})


def _equity(state, latest, reference_date=None):
    total = float(state["cash"])
    stale = []
    unrealized = 0.
    for ticker, p in state["positions"].items():
        last = latest.get(ticker)
        mark = last["close"] if last else float(p["last_mark_price"])
        total += mark * p["shares"]
        unrealized += (mark - p["entry_price"]) * p["shares"]
        if not last or (reference_date and last["date"] != reference_date):
            stale.append(ticker)
    return round(total, 4), round(unrealized, 4), stale


def _close(state, ticker, date_str, reference_price, reason):
    p = state["positions"].pop(ticker)
    exit_price = reference_price * (1 - IMPACT_BPS / 10000)
    proceeds = round(p["shares"] * exit_price, 6)
    state["cash"] = round(float(state["cash"]) + proceeds, 6)
    pnl = round(proceeds - p["entry_cost"], 6)
    closed = {"ticker": ticker, "shares": p["shares"],
              "signal_date": p["signal_date"], "entry_date": p["entry_date"],
              "exit_date": date_str, "entry_price": p["entry_price"],
              "exit_price": round(exit_price, 6), "pnl_usd": pnl,
              "return_pct": round(100 * pnl / p["entry_cost"], 4),
              "exit_reason": reason}
    state["closed_trades"].append(closed)
    _event(state, "EXIT", **closed)


def _evaluate(state, ticker, bar):
    """Assess today's high/low, stop first if both could be hit."""
    p = state["positions"].get(ticker)
    if not p or bar["date"] <= p["last_evaluated_date"]:
        return
    p["sessions_held"] += 1
    p["last_evaluated_date"] = bar["date"]
    p["last_mark_price"] = bar["close"]
    if bar["open"] <= p["stop_price"]:
        _close(state, ticker, bar["date"], bar["open"], "gap_through_stop")
    elif bar["low"] <= p["stop_price"]:
        _close(state, ticker, bar["date"], p["stop_price"], "protective_stop")
    elif bar["high"] >= p["target_price"]:
        _close(state, ticker, bar["date"], p["target_price"], "profit_target")
    elif p["sessions_held"] >= MAX_HOLD_SESSIONS:
        _close(state, ticker, bar["date"], bar["close"], "time_exit")


def _fill_pending(state, order, bar):
    ticker = order["ticker"]
    if ticker in state["positions"] or len(state["positions"]) >= MAX_POSITIONS:
        return "slot_unavailable"
    if bar["open"] > order["signal_close"] * 1.08:
        return "upward_gap_gt_8pct"
    if bar["open"] < order["signal_close"] * 0.75:
        return "downward_gap_gt_25pct"
    equity, _, _ = _equity(state, {})
    cap = min(state["cash"], equity * MAX_POSITION_FRACTION,
              equity * MAX_EQUITY_RISK_PER_TRADE / STOP_FRACTION)
    entry = bar["open"] * (1 + IMPACT_BPS / 10000)
    shares = int(floor((cap + 1e-9) / entry))
    if shares < 1:
        return "insufficient_cash_for_whole_share"
    cost = round(shares * entry, 6)
    if cost > state["cash"] + 1e-7:
        return "insufficient_cash"
    state["cash"] = round(state["cash"] - cost, 6)
    p = {"ticker": ticker, "shares": shares, "entry_date": bar["date"],
         "signal_date": order["signal_date"], "entry_price": round(entry, 6),
         "entry_cost": cost, "stop_price": round(entry * (1 - STOP_FRACTION), 6),
         "target_price": round(entry * (1 + TARGET_FRACTION), 6),
         "last_mark_price": bar["close"], "last_evaluated_date": "",
         "sessions_held": 0}
    state["positions"][ticker] = p
    _event(state, "ENTRY", ticker=ticker, signal_date=order["signal_date"],
           entry_date=bar["date"], shares=shares,
           entry_price=p["entry_price"], entry_cost=cost)
    return "filled"


def _signal(ticker, bars, asof, today):
    # This is an EXPERIMENTAL OHLCV hypothesis, not a calibrated return edge.
    if not bars or bars[-1]["date"] != today:
        return None
    data = triage(bars, asof)
    if data["status"] != "price_research_candidate":
        return None
    if not (.02 <= data["return_5d"] <= .12 and
            -.10 <= data["return_20d"] <= .20 and
            data["volume_ratio"] >= 1.25):
        return None
    # No single feature or write-up qualifies a stock.
    if bars[-1]["volume"] < median(b["volume"] for b in bars[-20:]):
        return None
    return {"ticker": ticker, "signal_date": today,
            "signal_close": bars[-1]["close"],
            "score": round(data["research_score"] + min(data["volume_ratio"], 4), 4),
            "signal_type": "unvalidated_price_volume_acceleration"}


def step(state, observations, asof_utc):
    """Deterministically advance a portfolio through ONLY completed bars.

    All symbols in open positions and pending orders MUST be present in the
    observations mapping. Missing data freezes that symbol; never fabricates
    exits or entries. A new signal may be queued only after today's full bar.
    """
    validate_state(state)
    moment = datetime.fromisoformat(asof_utc.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("asof_must_have_timezone")
    today = moment.astimezone(ET).date().isoformat()
    bars = {str(t).upper(): _valid_complete(rows, asof_utc)
            for t, rows in observations.items()}
    latest = {t: rows[-1] for t, rows in bars.items() if rows}

    for ticker in sorted(list(state["positions"])):
        p = state["positions"][ticker]
        for bar in bars.get(ticker, []):
            if bar["date"] > p["last_evaluated_date"]:
                _evaluate(state, ticker, bar)
                if ticker not in state["positions"]:
                    break

    remaining = []
    for order in sorted(state["pending_entries"],
                        key=lambda o: (o["signal_date"], -o["score"], o["ticker"])):
        ticker = order["ticker"]
        age = (date.fromisoformat(today) -
               date.fromisoformat(order["signal_date"])).days
        if age > MAX_PENDING_CALENDAR_DAYS:
            _event(state, "CANCEL", ticker=ticker, signal_date=order["signal_date"],
                   reason="pending_expired", on_date=today)
            continue
        next_bars = [b for b in bars.get(ticker, [])
                     if b["date"] > order["signal_date"]]
        if not next_bars:
            remaining.append(order)
            continue
        first = next_bars[0]
        outcome = _fill_pending(state, order, first)
        if outcome != "filled":
            _event(state, "CANCEL", ticker=ticker, signal_date=order["signal_date"],
                   reason=outcome, on_date=first["date"])
            continue
        # This future day's OHLC is known only AFTER the session is complete.
        for bar in next_bars:
            _evaluate(state, ticker, bar)
            if ticker not in state["positions"]:
                break
    state["pending_entries"] = remaining

    # Pre-close/manual runs cannot backdate a signal to yesterday.
    candidates = []
    for ticker, rows in bars.items():
        if state["last_signal_date"].get(ticker) == today:
            continue
        if ticker in state["positions"] or any(
                o["ticker"] == ticker for o in state["pending_entries"]):
            continue
        signal = _signal(ticker, rows, asof_utc, today)
        if signal:
            candidates.append(signal)
    # Pending slots bound future exposure even if many attractive screens exist.
    free_slots = max(0, MAX_POSITIONS - len(state["positions"]) -
                     len(state["pending_entries"]))
    for order in sorted(candidates, key=lambda o: (-o["score"], o["ticker"]))[:free_slots]:
        state["pending_entries"].append(order)
        state["last_signal_date"][order["ticker"]] = today
        _event(state, "SIGNAL", **order)

    equity, unrealized, stale = _equity(state, latest, today)
    # Record at most one equity mark per market session; re-runs update it.
    sessions = [r for r in state["equity_history"] if r["date"] != today]
    if any(r["date"] > today for r in sessions):
        raise ValueError("cannot_run_earlier_than_recorded_portfolio")
    if any(x["date"] == today for x in latest.values()):
        sessions.append({"date": today, "equity": equity,
                         "cash": round(state["cash"], 4),
                         "unrealized_pnl": unrealized,
                         "stale_positions": stale})
    state["equity_history"] = sessions
    state["last_run_at_utc"] = asof_utc
    validate_state(state)
    return {"date": today, "equity": equity, "cash": state["cash"],
            "unrealized_pnl": unrealized,
            "stale_positions": stale,
            "signals": [o["ticker"] for o in candidates],
            "pending": len(state["pending_entries"]),
            "open": len(state["positions"]),
            "closed": len(state["closed_trades"])}
