"""Scheduled SEC-first research radar: does not depend on Render or a broker."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from data_layer import get_price_history
from research.autopilot.decision import VERSION,decide,triage

SYMBOL=re.compile(r"^[A-Z]{1,5}$")
DEFAULT_ANCHORS="ZOM,SOFI,OPEN,PLUG,RKLB,JOBY,ENVX,ACHR,RIOT,CLSK"

def universe(now,max_symbols=70,anchors=""):
    chosen=list(dict.fromkeys(s.strip().upper() for s in anchors.split(",")
                              if SYMBOL.fullmatch(s.strip().upper())))
    try:
        from universe_builder import _fetch_nasdaq_traded_rows,_passes_symbol_gate
        symbols={str(r.get("Symbol") or "").upper().strip()
                 for r in _fetch_nasdaq_traded_rows() if _passes_symbol_gate(r)}
        week=now.strftime("%G-W%V")
        ordered=sorted(symbols,key=lambda s:hashlib.sha256((week+":"+s).encode()).digest())
        for ticker in ordered:
            if ticker not in chosen:chosen.append(ticker)
            if len(chosen)>=max_symbols:break
    except Exception as exc:
        print("Listed-symbol directory unavailable:",type(exc).__name__,file=sys.stderr)
    return chosen[:max_symbols]

def load_events(path):
    """Externally reviewed/attested primary facts only; no news sentiment."""
    if not path:return {}
    raw=json.loads(Path(path).read_text())
    if not isinstance(raw,list):raise ValueError("verified_events_must_be_array")
    output={}
    for item in raw:
        if not isinstance(item,dict):continue
        s=str(item.get("ticker") or "").upper()
        if not SYMBOL.fullmatch(s):continue
        if item.get("source")!="sec_primary":continue
        if item.get("evidence_status")!="verified" or item.get("polarity")!="positive":continue
        if not re.fullmatch(r"\d{10}-\d{2}-\d{6}",str(item.get("event_id") or "")):continue
        if not all(item.get(x) for x in ("reviewer","reviewed_at","observed_at","source_url")):continue
        if "sec.gov/" not in item["source_url"]:continue
        output.setdefault(s,[]).append(item)
    return output

def run(*,now=None,tickers=None,events=None,max_symbols=70,max_sec=12,
        prices=None,companies=None):
    live_clock=now is None
    now=now or datetime.now(timezone.utc).isoformat()
    moment=datetime.fromisoformat(now.replace("Z","+00:00"))
    if moment.tzinfo is None:raise ValueError("offset_aware_asof_required")
    prices=prices or get_price_history
    if companies is None:
        from research.long_horizon.live_sec import research_ticker
        companies=research_ticker
    if tickers is None:
        tickers=universe(moment,max_symbols,
                         os.getenv("QME_AUTOPILOT_ANCHORS",DEFAULT_ANCHORS))
    tickers=list(dict.fromkeys(s.strip().upper() for s in tickers
                               if SYMBOL.fullmatch(s.strip().upper())))[:max_symbols]
    errors=[]
    try:spy=prices("SPY",180) or []
    except Exception as exc:
        spy=[]
        errors.append({"ticker":"SPY","source":"market_data",
                       "error_type":type(exc).__name__})
    all_bars={};screen=[]
    # Bounded concurrency is for daily price feeds only, never SEC requests.
    # This keeps the scheduled job from blocking on dozens of slow symbols.
    workers=max(1,min(5,int(os.getenv("QME_PRICE_FETCH_WORKERS","5"))))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={ticker:pool.submit(prices,ticker,180) for ticker in tickers}
        for ticker in tickers:
            try:rows=futures[ticker].result() or []
            except Exception as exc:
                rows=[]
                errors.append({"ticker":ticker,"source":"market_data",
                               "error_type":type(exc).__name__})
            all_bars[ticker]=rows
            screen.append({"ticker":ticker,**triage(rows,now)})
    top=sorted((r for r in screen if r["status"]=="price_research_candidate"),
               key=lambda r:(-r["research_score"],r["ticker"]))[:max_sec]
    decisions=[]
    automatic_events_count=0
    for item in top:
        ticker=item["ticker"]
        try:company=companies(ticker)
        except Exception as exc:
            company=None
            errors.append({"ticker":ticker,"source":"SEC",
                           "error_type":type(exc).__name__})
        if not isinstance(company,dict):
            company={"ticker":ticker,"classification":"insufficient_evidence",
                     "reason":"SEC_data_unavailable","long_term_flag":False,
                     "flag_for_deeper_research":False}
        # New, objectively verified SEC financial changes are automatically
        # discovered from current XBRL; editorial news never qualifies.
        auto_events=company.get("verified_primary_events") or []
        automatic_events_count+=len(auto_events)
        all_events=list(auto_events)+list((events or {}).get(ticker,[]))
        # Decision time follows SEC retrieval; never backdate newly observed facts.
        decision_asof=(datetime.now(timezone.utc).isoformat() if live_clock else now)
        decisions.append(decide(ticker,all_bars[ticker],spy,decision_asof,company,
                                all_events))
    has_price=any(s["status"]!="abstain" for s in screen)
    contact=os.getenv("EDGAR_CONTACT","")
    usable_contact="@" in contact and "example" not in contact.lower()
    status=("degraded_market_data" if not has_price else
            "price_scan_only_missing_SEC_contact" if not usable_contact else
            "research_scan_completed")
    return {"version":VERSION,"run_at_utc":now,
            "completed_at_utc":datetime.now(timezone.utc).isoformat() if live_clock else now,
            "mode":"research_only_no_orders",
            "status":status,"universe_count":len(tickers),
            "price_candidates_count":sum(r["status"]=="price_research_candidate" for r in screen),
            "sec_review_count":len(decisions),
            "verified_event_count":sum(len(r) for r in (events or {}).values())+automatic_events_count,
            "automatic_sec_financial_events_count":automatic_events_count,
            "quote_type":"completed_daily_bars_not_executable_bid_ask",
            "source_errors":errors,"all_scanned":screen,"deep_research":decisions}

def markdown(data):
    out=["# Quiet Money autonomous research radar","",
         "UTC: "+data["run_at_utc"],"Status: "+data["status"],"",
         "Research only. No verified trading edge, real-time orders or fills.","",
         "Scanned: {} | price candidates: {} | SEC-reviewed: {}".format(
             data["universe_count"],data["price_candidates_count"],data["sec_review_count"]),
         "","## Every ticker inspected","",
         "| Ticker | Price screen | Reason | Score |",
         "|---|---|---|---:|"]
    for r in data["all_scanned"]:
        out.append("| {} | {} | {} | {} |".format(
            r["ticker"],r["status"],r["reason"],
            r["research_score"] if r["research_score"] is not None else "—"))
    out+=["","## Trading-vs-company classification","",
          "| Ticker | Research bucket | Two-Clock | Company | Valuation |",
          "|---|---|---|---|---|"]
    for r in data["deep_research"]:
        out.append("| {} | {} | {} | {} | {} |".format(
            r["ticker"],r["classification"],r["two_clock"]["status"],
            r["company"].get("classification","unavailable"),r["valuation"]["status"]))
    out+=["","## Integrity","",
          "Source errors: "+str(len(data["source_errors"])),
          "Positive SEC events require independently attested first-observed receipts.",
          "No business quality or fair value is inferred from missing information.",
          "All stock orders disabled until separate prospective execution validation.",""]
    return "\n".join(out)

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--output-dir",default="autopilot_output")
    p.add_argument("--tickers",default="")
    p.add_argument("--max-symbols",type=int,default=70)
    p.add_argument("--max-sec-reviews",type=int,default=12)
    p.add_argument("--verified-events",default="")
    args=p.parse_args(argv)
    report=run(tickers=args.tickers.split(",") if args.tickers else None,
               events=load_events(args.verified_events),
               max_symbols=max(1,min(200,args.max_symbols)),
               max_sec=max(0,min(50,args.max_sec_reviews)))
    path=Path(args.output_dir);path.mkdir(parents=True,exist_ok=True)
    (path/"research_scan.json").write_text(json.dumps(report,indent=2,sort_keys=True))
    (path/"research_scan.md").write_text(markdown(report))
    print("Radar status:",report["status"])
    print("Tickers:",report["universe_count"],"candidates:",report["price_candidates_count"])
    return 2 if report["status"]=="degraded_market_data" else 0

if __name__=="__main__":
    sys.exit(main())
