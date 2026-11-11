"""Fail-closed decision gateway: research candidates never become orders."""
from __future__ import annotations
from datetime import datetime, date, time, timezone
from statistics import median
from math import isfinite
from zoneinfo import ZoneInfo
from research.two_clock.two_clock_research import analyze as analyze_two_clock

ET=ZoneInfo("America/New_York")
VERSION="autopilot_radar_v0_1"

def completed(bars, asof):
    moment=datetime.fromisoformat(asof.replace("Z","+00:00")).astimezone(timezone.utc)
    output=[]
    for bar in bars or []:
        try:
            d=date.fromisoformat(str(bar["date"])[:10])
            release=datetime.combine(d,time(16,30),ET).astimezone(timezone.utc)
            if release<=moment:
                output.append(bar)
        except (TypeError,ValueError,KeyError):
            continue
    return sorted(output,key=lambda r:r["date"])

def triage(bars,asof):
    rows=completed(bars,asof)
    base={"status":"abstain","reason":"insufficient_daily_history","research_score":None}
    if len(rows)<100: return base
    try:
        close=[float(r["close"]) for r in rows]
        vol=[float(r["volume"]) for r in rows]
        if not all(isfinite(p) and p>0 for p in close): raise ValueError()
        if not all(isfinite(v) and v>=0 for v in vol): raise ValueError()
        d=date.fromisoformat(str(rows[-1]["date"])[:10])
    except (ValueError,KeyError,TypeError,OverflowError):
        return {**base,"reason":"invalid_daily_bar"}
    if (datetime.fromisoformat(asof.replace("Z","+00:00")).date()-d).days>5:
        return {**base,"reason":"stale_daily_bar","last_complete_date":d.isoformat()}
    last=close[-1]
    dv=median(close[i]*vol[i] for i in range(len(close)-20,len(close)))
    common={"last_complete_date":d.isoformat(),"last_close":round(last,4),
            "median_dollar_volume_20":round(dv,2)}
    if not (.25<=last<=15 and dv>=1_000_000):
        return {**base,**common,"status":"screen_rejected","reason":"price_or_liquidity"}
    r5=last/close[-6]-1
    r20=last/close[-21]-1
    r60=last/close[-61]-1
    drawdown=last/max(close[-60:])-1
    if r20>.30 or r60>.70 or drawdown<-.4:
        return {**base,**common,"status":"screen_rejected","reason":"extended_or_deep_drawdown",
                "return_20d":round(r20,5),"return_60d":round(r60,5)}
    vr=median(vol[-5:])/max(1,median(vol[-25:-5]))
    score=100*max(-.2,min(.2,r5-r20/4))+2*min(3,vr)+6*max(-.4,drawdown)
    return {**base,**common,"status":"price_research_candidate",
            "reason":"early_price_change_proxy_not_a_validated_edge",
            "research_score":round(score,4),"return_5d":round(r5,5),
            "return_20d":round(r20,5),"return_60d":round(r60,5),
            "volume_ratio":round(vr,3)}

def value_screen(company,price):
    out={"status":"unreviewed","fair_value":None,"buy_approved":False,
         "reason":"TTM_basis_comparables_dilution_downside_and_intraday_quotes_missing"}
    m=company.get("metrics") or {}
    try:
        shares=float(m["shares_outstanding"]);cash=float(m["cash_usd"])
        debt=float(m["total_debt_usd"]);p=float(price["last_close"])
        if not all(isfinite(v) for v in (shares,cash,debt,p)): return out
        if shares<=0 or p<=0 or cash<0 or debt<0:return out
    except (KeyError,ValueError,TypeError):return out
    cap=shares*p
    return {**out,"status":"capital_structure_screened",
            "market_cap_proxy_usd":round(cap,2),
            "enterprise_value_proxy_usd":round(cap+debt-cash,2),
            "net_cash_per_share_proxy_usd":round((cash-debt)/shares,4),
            "cash_to_market_cap_proxy":round(cash/cap,4)}

def category(two_clock,company):
    if company.get("classification")=="risk_blocked":return "risk_blocked"
    short=two_clock.get("status")=="dislocation_candidate"
    long_=company.get("long_term_flag") is True
    if short and long_: return "dual_research_candidate"
    if short:return "short_term_research_only"
    if long_:return "long_term_research_only"
    if company.get("flag_for_deeper_research") is True:return "financial_triage_only"
    return "insufficient_verified_evidence"

def decide(ticker,stock_bars,spy_bars,asof,company,events=()):
    proxy=triage(stock_bars,asof)
    clock=analyze_two_clock(stock_bars=completed(stock_bars,asof),
                            benchmark_bars=completed(spy_bars,asof),
                            observed_events=list(events),asof_utc=asof,
                            exchange_verified=False,broker_eligible_verified=False,
                            dilution_clear_verified=False)
    return {"ticker":ticker,"version":VERSION,"asof_utc":asof,
            "classification":category(clock,company),"price_triage":proxy,
            "two_clock":clock,"company":company,"valuation":value_screen(company,proxy),
            "short_term_action":"none","long_term_action":"none",
            "broker_order":None,"paper_order":None,"trade_approved":False,
            "probability_of_profit":None,
            "blockers":["edge_unvalidated","real_time_executable_quotes_absent",
                        "broker_not_integrated","capital_at_risk_not_authorized"]}
