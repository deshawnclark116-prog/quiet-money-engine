"""Structural signal falsification lab. READ-ONLY; no orders.

Registered before tests, 2026-11-20:
Resilience to market down-days, quiet volume accumulation and compression,
and oversold stabilization. Compare original deployed _signal and a
deterministic random-price/liquidity-matched placebo under the SAME
$25 portfolio rules. Current-listing 70-symbol cohort has survivorship
bias, and 2026 was previously inspected: NO automatic promotion.
"""
from __future__ import annotations
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from statistics import mean,median
from pathlib import Path
import argparse,hashlib,json,math
from data_layer import get_price_history
from research.autopilot.entry_audit import COHORT,clean_bars
from research.autopilot.paper import (
    _signal,_fill_pending,_evaluate,_equity,fresh_state,MAX_POSITIONS)

VERSION="structural_response_lab_v1"
ARMS=("incumbent","resilience","quiet_accumulation","reversion","placebo")
PERIODS={"exploration_2024_2025":("2024-01-01","2025-12-31"),
         "exposed_2026_diagnostic":("2026-01-01","2026-10-31")}


def features(rows,i,spy):
    """All values use the given session or earlier; missing SPY abstains."""
    if i<100:return None
    win=rows[i-25:i+1]
    if len(win)!=26:return None
    bench=[spy.get(r["date"]) for r in win]
    if any(x is None for x in bench):return None
    close=win[-1]["close"]
    dv=median(r["close"]*r["volume"] for r in win[-20:])
    if not (.25<=close<=15 and dv>=1_000_000):return None
    c=[r["close"] for r in win]
    r5=c[-1]/c[-6]-1
    r20=c[-1]/c[-21]-1
    if not -.45<=r20<=.35:return None
    spy20=bench[-1]["close"]/bench[-21]["close"]-1
    loc=mean((r["close"]-r["low"])/(r["high"]-r["low"])
             if r["high"]>r["low"] else .5 for r in win[-5:])
    up=sum(win[k]["volume"] for k in range(1,26) if c[k]>c[k-1])
    down=sum(win[k]["volume"] for k in range(1,26) if c[k]<c[k-1])
    rs=[(r["high"]-r["low"])/r["close"] for r in win]
    compress=median(rs[-5:])/max(1e-9,median(rs[:-5]))
    shocks=[(c[k]/c[k-1]-1,bench[k]["close"]/bench[k-1]["close"]-1)
            for k in range(6,26)
            if bench[k]["close"]<bench[k-1]["close"]]
    if len(shocks)<3:return None
    return {"return5":r5,"return20":r20,"relative20":r20-spy20,
            "close_location5":loc,"up_volume_share":up/max(1,up+down),
            "compression":compress,
            "down_excess":mean(a-b for a,b in shocks),
            "down_outperform_rate":sum(a>b for a,b in shocks)/len(shocks)}


def scores(f):
    """Three independent structural mechanisms, NO parameter fitting."""
    out={}
    if (-.10<=f["return20"]<=.18 and -.04<=f["return5"]<=.06 and
        f["down_excess"]>.002 and f["down_outperform_rate"]>=.60
        and f["close_location5"]>=.54):
        out["resilience"]=100*f["down_excess"]+3*f["down_outperform_rate"]+f["close_location5"]
    if (-.10<=f["return20"]<=.16 and -.03<=f["return5"]<=.06 and
        f["relative20"]>.015 and f["compression"]<=.85 and
        f["up_volume_share"]>=.56 and f["close_location5"]>=.56):
        out["quiet_accumulation"]=3*f["up_volume_share"]+2*(1-f["compression"])+f["close_location5"]
    if (-.30<=f["return20"]<=-.06 and -.035<=f["return5"]<=.035
        and f["close_location5"]>=.60 and f["up_volume_share"]>=.53):
        out["reversion"]=4*f["close_location5"]+3*f["up_volume_share"]-2*abs(f["return5"])
    return out


def find_candidates(history):
    if len(history.get("SPY",[]))<400:raise ValueError("missing_SPY_history")
    spy={r["date"]:r for r in history["SPY"]}
    out={arm:{} for arm in ARMS}
    coverage=Counter()
    for ticker in COHORT:
        bars=history.get(ticker,[])
        if len(bars)<110:
            coverage["missing_history_symbols"]+=1
            continue
        for i in range(100,len(bars)):
            day=bars[i]["date"]
            if day<"2024-01-01":continue
            f=features(bars,i,spy)
            if f is None:continue
            coverage["eligible_ticker_days"]+=1
            values=scores(f)
            old=_signal(ticker,bars[:i+1],day+"T23:30:00+00:00",day)
            if old:values["incumbent"]=old["score"]
            values["placebo"]=0.
            for arm,score in values.items():
                if not math.isfinite(score):raise ValueError("invalid_score")
                event={"ticker":ticker,"signal_date":day,
                       "signal_close":round(bars[i]["close"],6),
                       "score":round(score,6)}
                out[arm].setdefault(day,[]).append(event)
                coverage[arm+"_candidates"]+=1
    for arm,group in out.items():
        for day,items in group.items():
            if arm=="placebo":
                items.sort(key=lambda e:hashlib.sha256(
                    (day+"|"+e["ticker"]+"|frozen_placebo_v1").encode()).digest())
            else:items.sort(key=lambda e:(-e["score"],e["ticker"]))
    return out,dict(coverage)


def replay(history,offers,start,end):
    """Same production fills/stops/costs and $25 whole-share cash limit."""
    state=fresh_state()
    lookup={t:{b["date"]:b for b in bars} for t,bars in history.items()
            if t!="SPY"}
    days=[b["date"] for b in history["SPY"] if start<=b["date"]<=end]
    pending=[]
    last_exit={}
    n_signals=0
    cancels=0
    equity_curve=[]
    peak=25.
    max_dd=0.
    for day in days:
        for ticker in sorted(list(state["positions"])):
            bar=lookup.get(ticker,{}).get(day)
            if bar:
                _evaluate(state,ticker,bar)
                if ticker not in state["positions"]:last_exit[ticker]=day
        remaining=[]
        for order in sorted(pending,key=lambda e:(-e["score"],e["ticker"])):
            bar=lookup.get(order["ticker"],{}).get(day)
            if bar is None:
                remaining.append(order)
                continue
            assert day>order["signal_date"],"same_day_fill_forbidden"
            result=_fill_pending(state,order,bar)
            if result=="filled":
                _evaluate(state,order["ticker"],bar)
                if order["ticker"] not in state["positions"]:last_exit[order["ticker"]]=day
            else:cancels+=1
        pending=remaining
        latest={t:byday[day] for t,byday in lookup.items() if day in byday}
        equity,_,_=_equity(state,latest,day)
        peak=max(peak,equity)
        max_dd=max(max_dd,(peak-equity)/peak)
        equity_curve.append({"date":day,"equity":equity})
        held=set(state["positions"])|{r["ticker"] for r in pending}
        for event in offers.get(day,[]):
            ticker=event["ticker"]
            if len(held)>=MAX_POSITIONS:break
            if ticker in held or day not in lookup.get(ticker,{}):continue
            if ticker in last_exit and (datetime.fromisoformat(day)-
                datetime.fromisoformat(last_exit[ticker])).days<=7:continue
            pending.append({"ticker":ticker,"signal_date":day,
                            "signal_close":event["signal_close"],"score":event["score"]})
            n_signals+=1
            held.add(ticker)
    closed=state["closed_trades"]
    final=equity_curve[-1]["equity"] if equity_curve else 25.
    return {"signal_count":n_signals,"closed_trades":len(closed),
            "wins":sum(t["pnl_usd"]>0 for t in closed),
            "cancelled":cancels,"open_end":len(state["positions"]),
            "pending_end":len(pending),"end_equity_usd":round(final,4),
            "net_usd":round(final-25,4),
            "return_pct":round(100*(final/25-1),3),
            "mean_closed_trade_pct":round(mean(t["return_pct"] for t in closed),3)
                  if closed else None,
            "max_close_to_close_drawdown_pct":round(max_dd*100,3),
            "entry_receipts":[e for e in state["events"] if e["kind"]=="ENTRY"],
            "exit_receipts":closed,"equity_curve":equity_curve}


def evaluate(history,invalid):
    offers,coverage=find_candidates(history)
    results={}
    for name,(start,end) in PERIODS.items():
        results[name]={arm:replay(history,offers[arm],start,end) for arm in ARMS}
    return {"version":VERSION,"mode":"RESEARCH_ONLY",
            "generated_at_utc":datetime.now(timezone.utc).isoformat(),
            "hypotheses_frozen_before_testing":True,
            "cohort":"Nov 12 2026 70 active listing sample",
            "historical_survivorship_bias":True,
            "2026_not_pristine_holdout":True,
            "costs":"75bps buy plus 75bps sell; real broker quotes unavailable",
            "coverage":coverage,"invalid_bars":invalid,"results":results,
            "promotion":"BLOCKED until independent future replication and"
               " historical delisted-inclusive universe, costs and robustness"}


def markdown(r):
    lines=["# Structural entry hypothesis comparison",
           "**Research only — official $25 paper state and model unchanged.**",
           "",
           "| Period | Rule | Trades | Wins | Net USD | Return | Avg net/trade |",
           "|---|---|---:|---:|---:|---:|---:|"]
    for period,arms in r["results"].items():
        for name,x in arms.items():
            avg=x["mean_closed_trade_pct"]
            avgtxt=(f"{avg:+.2f}%" if avg is not None else "N/A")
            lines.append(f"| {period} | {name} | {x['closed_trades']} | "+
                         f"{x['wins']} | USD {x['net_usd']:+.2f} | "+
                         f"{x['return_pct']:+.2f}% | {avgtxt} |")
    lines.extend(["","Eligible stock-days: "+str(r["coverage"].get("eligible_ticker_days",0)),
      "Candidate counts: "+", ".join(
        name+"="+str(r["coverage"].get(name+"_candidates",0)) for name in ARMS),
      "","**Do not promote.** Stock sample excludes historical delistings;"
      " 2026 was inspected in earlier tests, and free OHLC is not a live quote.",
      "Windows reset to independent $25 cash-only whole-share portfolios."
      " Open end-window holdings are marked, not fictitiously liquidated.",
      "Need forward unseen dates and quotes plus independent replication."])
    return "\n".join(lines)+"\n"


def run(provider=get_price_history,days=1000,outdir="structural_research_output"):
    history={};invalid={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs={t:pool.submit(provider,t,days) for t in [*COHORT,"SPY"]}
        for t,job in jobs.items():
            try:history[t],invalid[t]=clean_bars(job.result())
            except Exception:history[t],invalid[t]=[],-1
    if len(history.get("SPY",[]))<400 or sum(
        len(rows)>=110 for rows in history.values())<20:
        raise RuntimeError("insufficient_market_data")
    report=evaluate(history,invalid)
    target=Path(outdir);target.mkdir(parents=True,exist_ok=True)
    (target/"structural_results.json").write_text(
       json.dumps(report,indent=2,sort_keys=True)+"\n")
    md=markdown(report)
    (target/"structural_summary.md").write_text(md)
    print(md)
    return report


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--days",type=int,default=1000)
    p.add_argument("--output-dir",default="structural_research_output")
    args=p.parse_args()
    run(days=max(400,min(1200,args.days)),outdir=args.output_dir)


if __name__=="__main__":main()
