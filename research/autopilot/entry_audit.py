"""Causal, read-only entry-quality research for the GitHub stock PAPER bot.

Pre-registered on 2026-11-18 after ACHR/AMPX were already known losers.
No model promotion from this retrospective study; 2026 is NOT pristine.
The historical universe is the 70 symbols actually examined on Nov 12, 2026.
This is a survivor-biased CURRENT-LISTING cohort, not a delisted-inclusive
universe backtest. Never conflate event returns with a capacity-limited portfolio.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median
from typing import Any

from data_layer import get_price_history
from research.autopilot.paper import (
    _price_row, _signal, IMPACT_BPS, MAX_HOLD_SESSIONS,
    STOP_FRACTION, TARGET_FRACTION,
)

VERSION = "entry_quality_retrospective_v1"
# Frozen from the immutable Nov 12 research artifact, run 43311945446.
COHORT = tuple(("ZOM,SOFI,OPEN,PLUG,RKLB,JOBY,ENVX,ACHR,RIOT,CLSK,TCBS,"
    "OGEN,DNMX,TRNO,MWG,AMRC,BOKF,PRSU,GRDN,PSIG,EZGO,EDBL,PSQL,BHB,"
    "ANNX,TBN,SRXH,GERN,JYD,PLSM,AG,OLED,LOMA,ELLO,BDRX,GNW,BNGO,"
    "APLD,BFRI,ESAB,XGN,KBH,CTO,DAC,CNK,AEON,VVV,VIRT,NRGV,ICU,"
    "AMPH,RXRX,FTRA,UPBD,AMPX,MAYS,ALT,PFX,OCG,ITIC,INKT,CFFN,MCO,"
    "TY,ZSQR,NWAX,RLMD,SKE,OSK,BTCS").split(","))
PAPERS = ("ACHR", "AMPX")
# Declared BEFORE examining historical outcomes. Hypotheses, NOT champions.
ARM_DESCRIPTIONS = {
    "incumbent": "original paper._signal, unmodified",
    "no_chase_8": "same signal plus observed 5d price gain <=8%",
    "market_guard": "same signal plus same-day SPY 5d return >=-1%",
    "both": "same signal plus no_chase_8 AND market_guard",
}
REFERENCE_20261112 = {
    # Directly from primary November 12 research-scan artifact; not new finds.
    "ACHR": {"five_day_pct":11.886, "twenty_day_pct":1.405, "score":12.4159},
    "AMPX": {"five_day_pct":11.037, "twenty_day_pct":-0.595, "score":12.6351},
    "NRGV": {"five_day_pct":28.929, "twenty_day_pct":17.974, "score":21.1293},
    "GERN": {"five_day_pct":4.632, "twenty_day_pct":-19.187, "score":9.6912},
}
MIN_EVENT_COUNT_FOR_RESEARCH = 100
HISTORY_BARS = 900


def clean_bars(raw):
    """Do not adjust malformed OHLC or fabricate missing provider observations."""
    if not isinstance(raw, list):
        return [], 0
    good = {}
    invalid = 0
    for original in raw:
        row = _price_row(original)
        if row is None:
            invalid += 1
            continue
        good[row["date"]] = row
    return [good[d] for d in sorted(good)], invalid


def paper_fill_outcome(bars, index):
    """Reproduce original next-open and same-bar stop-first exit in %-of-stake.

    Exact same 75 bps per side, 8% stop, 16% target, 7 sessions,
    and upward/downward opening gap refusals as the active paper bot.
    No assumed intraday ordering and no best-candle-price exits.
    """
    if index + MAX_HOLD_SESSIONS >= len(bars):
        return {"status": "right_censored"}
    signal_close = bars[index]["close"]
    entry_bar = bars[index+1]
    opening = entry_bar["open"]
    if opening > signal_close * 1.08:
        return {"status":"cancelled", "reason":"upward_gap_gt_8pct"}
    if opening < signal_close * 0.75:
        return {"status":"cancelled", "reason":"downward_gap_gt_25pct"}
    impact = IMPACT_BPS / 10000.
    entry = opening*(1+impact)
    stop = entry*(1-STOP_FRACTION)
    target = entry*(1+TARGET_FRACTION)
    for n in range(1,MAX_HOLD_SESSIONS+1):
        bar=bars[index+n]
        reason=None
        if bar["open"] <= stop:
            reference=bar["open"]
            reason="gap_through_stop"
        elif bar["low"] <= stop:
            reference=stop
            reason="protective_stop"
        elif bar["high"] >= target:
            reference=target
            reason="profit_target"
        elif n == MAX_HOLD_SESSIONS:
            reference=bar["close"]
            reason="time_exit"
        if reason is not None:
            exit_price=reference*(1-impact)
            return {
                "status":"evaluated", "exit_reason":reason,
                "entry_date":entry_bar["date"], "exit_date":bar["date"],
                "sessions_held":n, "signal_close":round(signal_close,6),
                "entry_price":round(entry,6), "exit_price":round(exit_price,6),
                "net_return_pct":round(100*(exit_price/entry-1),5),
                "entry_gap_pct":round(100*(opening/signal_close-1),5),
                "first_session_low_vs_signal_pct":
                    round(100*(entry_bar["low"]/signal_close-1),5),
            }
    raise AssertionError("terminal bar must resolve")


def spy_five_day(spy, signal_date):
    """Same-date completed SPY bars only, never nearest future bar."""
    i=spy["by_date"].get(signal_date)
    if i is None or i < 5:
        return None
    past=spy["bars"][i-5]["close"]
    return spy["bars"][i]["close"]/past-1 if past>0 else None


def historical_events(ticker, bars, spy):
    events=[]
    next_allowed=100
    skipped_censored=0
    considered=0
    for i in range(100,len(bars)):
        if i < next_allowed: continue
        day=bars[i]["date"]
        # Preregister prefilter that is a necessary condition for original _signal.
        if bars[i-5]["close"]<=0 or bars[i-20]["close"]<=0:
            continue
        r5=bars[i]["close"]/bars[i-5]["close"]-1
        r20=bars[i]["close"]/bars[i-20]["close"]-1
        if not (.02<=r5<=.12 and -.10<=r20<=.20):
            continue
        vl=[x["volume"] for x in bars[i-25:i+1]]
        vr=median(vl[-5:])/max(1.,median(vl[-25:-5]))
        if vr<1.25:
            continue
        asof=day+"T23:30:00+00:00"
        signal=_signal(ticker,bars[:i+1],asof,day)
        if signal is None:continue
        considered+=1
        # Non-overlapping, deterministic per-symbol research event blocks.
        next_allowed=i+MAX_HOLD_SESSIONS+1
        outcome=paper_fill_outcome(bars,i)
        if outcome["status"]=="right_censored":
            skipped_censored+=1
        spy5=spy_five_day(spy,day)
        event={
            "ticker":ticker,"signal_date":day,"signal_score":signal["score"],
            "signal_close":round(bars[i]["close"],6),
            "five_day_pct":round(100*r5,5),
            "twenty_day_pct":round(100*r20,5),
            "volume_ratio":round(vr,5),
            "spy_five_day_pct":round(100*spy5,5) if spy5 is not None else None,
            "arms":{
                "incumbent":True,
                "no_chase_8":r5<=.08,
                "market_guard":spy5 is not None and spy5>=-.01,
                "both":r5<=.08 and spy5 is not None and spy5>=-.01,
            },
            **outcome,
        }
        events.append(event)
    return events,{"qualified":considered,"right_censored":skipped_censored}


def summarize(events, arm):
    included=[e for e in events if e["arms"][arm]]
    filled=[e for e in included if e["status"]=="evaluated"]
    net=[e["net_return_pct"] for e in filled]
    ordered=sorted(net)
    count=len(net)
    return {
        "signals":len(included),
        "complete_fills":count,
        "cancelled":sum(e["status"]=="cancelled" for e in included),
        "right_censored":sum(e["status"]=="right_censored" for e in included),
        "unique_tickers":len(set(e["ticker"] for e in filled)),
        "unique_signal_dates":len(set(e["signal_date"] for e in filled)),
        "net_win_rate_pct":round(100*sum(x>0 for x in net)/count,2) if count else None,
        "mean_net_return_pct":round(mean(net),3) if count else None,
        "median_net_return_pct":round(median(net),3) if count else None,
        "worst_net_pct":round(ordered[0],3) if count else None,
        "best_net_pct":round(ordered[-1],3) if count else None,
        "stop_exit_count":sum("stop" in e.get("exit_reason","")
                              for e in filled),
        "caution":"event-level independent ticker cooldown; not whole-share $25 portfolio"
    }


def evaluate_all(history, invalid_counts, *, actual_portfolio=None):
    if "SPY" not in history or len(history["SPY"]) < 110:
        raise ValueError("SPY history absent; market-regime experiment must fail closed")
    spy_rows=history["SPY"]
    spy={"bars":spy_rows,
         "by_date":{x["date"]:i for i,x in enumerate(spy_rows)}}
    events=[]
    counts={}
    for ticker in COHORT:
        if ticker not in history:continue
        e,diagnostic=historical_events(ticker,history[ticker],spy)
        counts[ticker]=diagnostic
        events.extend(e)
    events.sort(key=lambda x:(x["signal_date"],x["ticker"]))
    years=sorted(set(x["signal_date"][:4] for x in events))
    cohorts={
        year:{arm:summarize([e for e in events if e["signal_date"][:4]==year],arm)
              for arm in ARM_DESCRIPTIONS}
        for year in years
    }
    cohorts["ALL_RETROSPECTIVE"]={
        arm:summarize(events,arm) for arm in ARM_DESCRIPTIONS
    }
    paper_trades=[]
    if isinstance(actual_portfolio,dict):
        for t in actual_portfolio.get("closed_trades",[]):
            if t.get("ticker") in PAPERS:
                paper_trades.append({
                    key:t.get(key) for key in (
                        "ticker","signal_date","entry_date","exit_date",
                        "entry_price","exit_price","pnl_usd",
                        "return_pct","exit_reason"
                    )})
    return {
        "version":VERSION,"mode":"RESEARCH_ONLY_NO_ORDERS",
        "asof_utc":datetime.now(timezone.utc).isoformat(),
        "hypotheses":ARM_DESCRIPTIONS,
        "frozen_cohort_date":"2026-11-12",
        "frozen_cohort_size":len(COHORT),
        "observed_bars_by_ticker":{t:len(history.get(t,[])) for t in COHORT},
        "invalid_bars_by_ticker":invalid_counts,
        "known_first_losers":paper_trades,
        "source_reference":"Nov 12 research-scan artifact 43311945446, and authoritative paper-state ledger",
        "prior_signal_context":REFERENCE_20261112,
        "yearly_evidence":cohorts,
        "all_signal_events":events,
        "fail_closed_policy":"NO_PROMOTION. Retrospective thresholds informed by 2026 losses. "
            "2026 is already consumed; require fresh prospective dates and stable costs.",
        "validity_warnings":[
            "Current-listing cohort is survivor-biased; delisted and past unavailable stocks are missing.",
            "Some corporate-action adjusted closes may mismatch raw OHLC. Invalid rows are dropped, not repaired.",
            "Signals from this sample are correlated within dates, sectors and symbols; 100 events is only a research minimum.",
            "Same-day OHLC stop first is deliberately pessimistic but may not represent executable fills.",
            "A filtered event sample is not a $25 two-position whole-share portfolio backtest.",
            "Multiple arms are exploratory comparisons, not a search-backed right to promote.",
            "No verified company events, marketable quotes, broker fees, taxes or real fills.",
        ]
    }


def markdown(result):
    lines=[
        "# Quiet Money: Entry Quality Forensic Lab",
        "",
        "**NOT NEW TRADES. The original paper bot is unchanged.**",
        "",
        "Actual first two losses from authoritative paper-state:",
        "",
        "| Stock | Entry date | Exit date | Net P&L USD | Net return | Reason |",
        "|---|---|---|---:|---:|---|",
    ]
    for t in result["known_first_losers"]:
        lines.append(
            f"| {t['ticker']} | {t['entry_date']} | {t['exit_date']} | "
            f"{t['pnl_usd']:+.2f} | {t['return_pct']:+.2f}% | "
            f"{t['exit_reason']} |")
    lines+=["","## Known signal conditions (Nov 12, BEFORE the losing fills)",
            "",
            "| Stock | Past 5d gain | Past 20d gain | Original research score |",
            "|---|---:|---:|---:|"]
    for ticker,data in result["prior_signal_context"].items():
        lines.append(
            f"| {ticker} | {data['five_day_pct']:+.2f}% | "
            f"{data['twenty_day_pct']:+.2f}% | {data['score']:+.2f} |")
    lines+=["","## Frozen 70-symbol walk-forward historical diagnostic","",
            "Each signal is constructed ONLY from daily bars available on its date;",
            "fill is NEXT trading session open; OHLC ambiguity always stops first.",
            "75 bps spread/impact per side, 8% stop, 16% target, seven-session",
            "exit, and a no-repeat-per-ticker 8-session block.",
            "",
            "| Signal year | Rule | Completed paper events | Mean net/event | Win rate | Stops |",
            "|---|---|---:|---:|---:|---:|"]
    for year, arms in result["yearly_evidence"].items():
        for arm, stats in arms.items():
            def fmt(x,suf=""):
                return f"{x:+.2f}{suf}" if x is not None else "n/a"
            lines.append(
                f"| {year} | {arm} | {stats['complete_fills']} | "
                f"{fmt(stats['mean_net_return_pct'],'%')} | "
                f"{fmt(stats['net_win_rate_pct'],'%')} | "
                f"{stats['stop_exit_count']} |")
    lines+=["","## Coverage, warnings and promotion decision","",
            f"Original historical snapshot: {result['source_reference']}.",
            f"Frozen symbols: {result['frozen_cohort_size']}.",
            f"Zero/low-history symbols: {sum(n<110 for n in result['observed_bars_by_ticker'].values())}.",
            "", "**PROMOTION: BLOCKED — retrospective diagnostic, not independent validation.**",
            "", *("- "+x for x in result["validity_warnings"]),
            "",
            "Next gate: track all new prospective signals, declined candidates,",
            "future net outcomes, costs, liquidity, and market-relative behavior",
            "without retroactive selection. No rule promotion until superiority",
            "is established on data not used to invent the challenger."]
    return "\n".join(lines)+"\n"


def run(output_dir, portfolio=None, *, provider=get_price_history, days=HISTORY_BARS):
    symbols=[*COHORT,"SPY"]
    history={}
    invalid={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs={t:pool.submit(provider,t,days) for t in symbols}
        for ticker in symbols:
            try:
                raw=jobs[ticker].result()
                history[ticker],invalid[ticker]=clean_bars(raw)
            except Exception:
                history[ticker]=[]
                invalid[ticker]=-1
    if sum(len(x)>=110 for x in history.values()) < 10:
        raise RuntimeError("insufficient_historical_price_coverage_refuse_promotion")
    actual=None
    if portfolio is not None and Path(portfolio).exists():
        actual=json.loads(Path(portfolio).read_text())
        if actual.get("mode")!="PAPER_ONLY_NO_BROKER":
            raise ValueError("authoritative_state_is_not_paper")
    report=evaluate_all(history,invalid,actual_portfolio=actual)
    out=Path(output_dir)
    out.mkdir(parents=True,exist_ok=True)
    (out/"entry_quality.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    md=markdown(report)
    (out/"entry_quality.md").write_text(md)
    print(md)
    return report


def main():
    p=argparse.ArgumentParser(description="Read-only historical entry-quality research")
    p.add_argument("--output-dir",default="entry_quality_output")
    p.add_argument("--portfolio",default=None)
    p.add_argument("--days",type=int,default=HISTORY_BARS)
    args=p.parse_args()
    run(args.output_dir,args.portfolio,days=max(180,min(1200,args.days)))


if __name__=="__main__":
    main()
