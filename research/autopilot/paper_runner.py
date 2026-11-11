"""Scheduled GitHub Actions runner: price-only paper trades and complete audit.

Runs do NOT depend on Render, a database, EDGAR credentials, or any broker.
State is provided by the persistent GitHub paper-state branch, not artifacts.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from data_layer import get_price_history
from research.autopilot.run import DEFAULT_ANCHORS, universe
from research.autopilot.paper import VERSION, fresh_state, step, validate_state


def report(state, result, failures, benchmark=None):
    trades = state["closed_trades"]
    wins = [t for t in trades if t["pnl_usd"] > 0]
    equity = result["equity"]
    hist = state["equity_history"]
    peak = state["starting_cash"]
    max_drawdown = 0.
    for snapshot in hist:
        peak = max(peak, snapshot["equity"])
        max_drawdown = max(max_drawdown, (peak - snapshot["equity"]) / peak)
    today = result["date"]
    week = datetime.fromisoformat(today).date().isocalendar()
    first_week = next((i for i, x in enumerate(hist)
                       if datetime.fromisoformat(x["date"]).date().isocalendar()[:2] ==
                       week[:2]), len(hist))
    baseline = hist[first_week - 1]["equity"] if first_week else state["starting_cash"]
    weekly_pnl = equity - baseline
    realized = sum(t["pnl_usd"] for t in trades)
    lines = [
        "# Quiet Money | GitHub paper portfolio",
        "",
        "**SIMULATED ONLY — no broker or real orders.**",
        "Executed with completed daily bars, next-session hypothetical opening fills,",
        "75 bps assumed cost EACH way, and stop-first when OHLC order is unknown.",
        "The strategy is unvalidated. This is NOT an investment performance claim.",
        "",
        "| Metric | Value |", "|---|---:|",
        f"| Latest evaluation date | {today} |",
        f"| Initial bankroll | ${state['starting_cash']:.2f} |",
        f"| Marked portfolio equity | ${equity:.2f} |",
        f"| Cash | ${result['cash']:.2f} |",
        f"| Total net P&L (marked) | ${equity - state['starting_cash']:+.2f} |",
        f"| Total return | {(equity / state['starting_cash'] - 1)*100:+.2f}% |",
        f"| Current week net P&L (marked) | ${weekly_pnl:+.2f} |",
        f"| Weekly target (not guaranteed) | +$100.00 |",
        f"| Progress against weekly target | {(weekly_pnl / 100)*100:.1f}% |",
        f"| Realized P&L | ${realized:+.2f} |",
        f"| Unrealized P&L, price marks | ${result['unrealized_pnl']:+.2f} |",
        f"| Closed trades | {len(trades)} |",
        f"| Winning trades | {len(wins)} |",
        f"| Closed-trade win rate | {len(wins)/len(trades)*100:.1f}% |" if trades else
            "| Closed-trade win rate | Not yet measurable |",
        f"| Max snapshot-to-snapshot drawdown | {max_drawdown * 100:.2f}% |",
        f"| Open positions | {len(state['positions'])} |",
        f"| Pending next-session entries | {len(state['pending_entries'])} |",
        f"| Price feed errors | {len(failures)} |",
        f"| Stale position marks | {', '.join(result['stale_positions']) or 'None'} |",
        "",
        "## Open positions", "",
        "| Ticker | Shares | Entry | Stop | Target | Opened |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for p in sorted(state["positions"].values(), key=lambda x:x["ticker"]):
        lines.append(f"| {p['ticker']} | {p['shares']} | ${p['entry_price']:.4f} | "
                     f"${p['stop_price']:.4f} | ${p['target_price']:.4f} | "
                     f"{p['entry_date']} |")
    if not state["positions"]: lines.append("| None | — | — | — | — | — |")
    lines += ["", "## Pending signals", "",
              "| Ticker | Recorded session | Close when signaled |",
              "|---|---|---:|"]
    for p in state["pending_entries"]:
        lines.append(f"| {p['ticker']} | {p['signal_date']} | ${p['signal_close']:.4f} |")
    if not state["pending_entries"]: lines.append("| None | — | — |")
    lines += ["", "## Most recent closed paper trades", "",
              "| Ticker | Entry | Exit | Shares | Net P&L | Exit trigger |",
              "|---|---|---|---:|---:|---|"]
    for t in trades[-15:][::-1]:
        lines.append(f"| {t['ticker']} | {t['entry_date']} | {t['exit_date']} | "
                     f"{t['shares']} | ${t['pnl_usd']:+.2f} | {t['exit_reason']} |")
    if not trades: lines.append("| None | — | — | — | — | — |")
    lines += ["", "## Evidence and operating rules", "",
              "- Generated from a GitHub Actions scheduled run; portfolio history is "
              "committed to the `paper-state` branch.",
              "- Market scans use price/volume data only; financial company research is "
              "separately available in the research radar.",
              "- Cash-only, whole-share, long-only portfolio; 2 open slots and 60% "
              "maximum nominal exposure per position.",
              "- Stops are simulated from daily OHLC. Actual gap losses and broker "
              "execution may be worse.",
              "- Broken/stale OHLC bars, provider outages, and an unavailable "
              "position price must not invent an order or price.",
              "- A first-day equity snapshot is not evidence of strategy quality. "
              "Independent out-of-sample testing, more trades, and liquidity/quotes "
              "are required before real-money consideration.",
              "- A +$100 weekly target from $25 starting capital "
              "would be a 400% weekly return; it is an aspirational "
              "measurement, not a plausible guarantee.",
              ""]
    return "\n".join(lines)


def save(state, result, errors, outdir):
    folder=Path(outdir)
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"portfolio.json").write_text(json.dumps(state,indent=2,sort_keys=True)+"\n")
    (folder/"dashboard.md").write_text(report(state,result,errors)+"\n")
    (folder/"latest_run.json").write_text(json.dumps(
        {"version":VERSION,"evaluation":result,"source_errors":errors,
         "source":"daily_bars_not_real_time_bid_ask", "paper_only":True},indent=2,
        sort_keys=True)+"\n")
    cols=["ticker","shares","signal_date","entry_date","exit_date",
          "entry_price","exit_price","pnl_usd","return_pct","exit_reason"]
    with (folder/"closed_trades.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=cols)
        writer.writeheader()
        for trade in state["closed_trades"]:
            writer.writerow({k:trade.get(k,"") for k in cols})
    with (folder/"equity_history.csv").open("w",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["date","equity","cash",
                                             "unrealized_pnl","stale_positions"])
        writer.writeheader()
        for row in state["equity_history"]:
            writer.writerow({**row,"stale_positions":",".join(row["stale_positions"])})


def run(state, *, now=None, tickers=None, provider=None, max_symbols=70):
    now=now or datetime.now(timezone.utc).isoformat()
    moment=datetime.fromisoformat(now.replace("Z","+00:00"))
    if moment.tzinfo is None:
        raise ValueError("asof_timezone_required")
    provider=provider or get_price_history
    tickers=tickers if tickers is not None else universe(
        moment,max_symbols,os.getenv("QME_AUTOPILOT_ANCHORS",DEFAULT_ANCHORS))
    extra=list(state["positions"]) + [p["ticker"] for p in state["pending_entries"]]
    all_tickers=list(dict.fromkeys([*tickers,*extra]))
    observations={}
    errors=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs={t:pool.submit(provider,t,180) for t in all_tickers}
        for ticker in all_tickers:
            try:
                observations[ticker]=jobs[ticker].result() or []
            except Exception as exc:
                observations[ticker]=[]
                errors.append({"ticker":ticker,"error_type":type(exc).__name__})
    if not any(observations.values()):
        raise RuntimeError("all_price_providers_failed_no_portfolio_state_change")
    result=step(state,observations,now)
    result["scan_count"]=len(all_tickers)
    result["nonempty_feeds"]=sum(bool(v) for v in observations.values())
    return result, errors


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--state", default="previous_portfolio.json")
    parser.add_argument("--output-dir",default="paper_output")
    parser.add_argument("--max-symbols",type=int,default=70)
    args=parser.parse_args(argv)
    state_path=Path(args.state)
    state=json.loads(state_path.read_text()) if state_path.exists() else fresh_state()
    validate_state(state)
    result,errors=run(state,max_symbols=max(1,min(200,args.max_symbols)))
    save(state,result,errors,args.output_dir)
    print("PAPER ONLY",json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    sys.exit(main())
