"""Automated, strict, first-observed primary-source financial inflection receipts.

A positive event is only an objectively verified SEC financial change. It is
NOT an announcement sentiment classifier, company valuation, or trade signal.
Only freshly filed 10-Q/10-K annual/quarter values with comparable prior years
can qualify. Any missing operating-cash-flow proof causes abstention.
"""
from __future__ import annotations
from datetime import date,datetime,timezone
from math import isfinite

VERSION="sec_financial_inflection_v0_1"

def _utc(ts):
    try:
        d=datetime.fromisoformat(str(ts).replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,TypeError):
        return None

def _fresh_submissions(submissions,seen):
    recent=((submissions.get("filings") or {}).get("recent") or {})
    accessions=recent.get("accessionNumber") or []
    forms=recent.get("form") or []
    dates=recent.get("filingDate") or []
    confirmed={}
    for i,accession in enumerate(accessions):
        if i>=len(forms) or i>=len(dates):continue
        if forms[i] not in {"10-Q","10-K"}:continue
        try:d=date.fromisoformat(dates[i])
        except (ValueError,TypeError):continue
        lag=(seen.date()-d).days
        if 0<=lag<=4:
            confirmed[str(accession)]=forms[i]
    return confirmed

def _comparable(latest,all_rows):
    basis=latest.get("period_basis")
    try: end=date.fromisoformat(latest["period_end"])
    except (ValueError,TypeError,KeyError):return None
    prior=[]
    for r in all_rows:
        if r.get("period_basis")!=basis:continue
        try:diff=(end-date.fromisoformat(r["period_end"])).days
        except (ValueError,TypeError,KeyError):continue
        if 330<=diff<=400:prior.append(r)
    return max(prior,key=lambda r:(r.get("period_end",""),r.get("filed_at",""))) if prior else None

def extract_verified_inflections(receipts,submissions,seen_at,*,cik=None):
    """Return SEC-attested research events, never orders or probabilities.

    A new quarterly/annual result must satisfy all predeclared tests:
    revenue >= $20m, prior comparable revenue >= $10m,
    revenue increase >= $10m and >=25%, and same-accession operating cash
    flow >= $0 that improved by >= $3m year over year. Both flow periods
    must use the SAME reporting basis. Filing must be seen within 4 days.
    """
    seen=_utc(seen_at)
    if seen is None or not isinstance(receipts,list) or not isinstance(submissions,dict):
        return []
    fresh=_fresh_submissions(submissions,seen)
    if not fresh:return []
    revenue=[r for r in receipts if r.get("metric")=="revenue_usd" and
             r.get("period_basis") in ("quarter","annual")]
    operating=[r for r in receipts if r.get("metric")=="operating_cash_flow_usd" and
               r.get("period_basis") in ("quarter","annual")]
    events=[]
    for r in revenue:
        accession=str(r.get("accession") or "")
        if accession not in fresh:continue
        if r.get("first_seen_at")!=seen_at:continue
        prior=_comparable(r,revenue)
        if prior is None:continue
        try:
            rev=float(r["value"]);old=float(prior["value"])
            if not isfinite(rev) or not isfinite(old):continue
            if old<10_000_000 or rev<20_000_000:continue
            increase=rev-old
            if increase<10_000_000 or increase/old<.25:continue
        except (KeyError,TypeError,ValueError,OverflowError,ZeroDivisionError):
            continue
        # Require independently quantified cash-generation improvement too.
        same=[x for x in operating if x.get("accession")==accession
              and x.get("period_end")==r.get("period_end")
              and x.get("period_basis")==r.get("period_basis")
              and x.get("first_seen_at")==seen_at]
        if not same:continue
        cashflow=same[0]
        old_cf=_comparable(cashflow,operating)
        if old_cf is None:continue
        try:
            cf=float(cashflow["value"]);prior_cf=float(old_cf["value"])
            if not isfinite(cf) or not isfinite(prior_cf):continue
            if cf<0 or cf-prior_cf<3_000_000:continue
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
        source_url=None
        if cik and str(cik).isdigit() and accession.count("-")==2:
            source_url="https://www.sec.gov/Archives/edgar/data/{}/{}/".format(
                int(cik),accession.replace("-",""))
        events.append({"version":VERSION,"event_id":accession,
                       "kind":"fresh_sec_revenue_and_cashflow_inflection",
                       "source":"sec_primary","evidence_status":"verified",
                       "polarity":"positive","observed_at":seen_at,
                       "filed_form":fresh[accession],"source_url":source_url,
                       "evidence":{"period_end":r["period_end"],
                                   "period_basis":r["period_basis"],
                                   "current_revenue_usd":rev,
                                   "prior_comparable_revenue_usd":old,
                                   "revenue_growth_pct":round(100*(rev/old-1),2),
                                   "operating_cash_flow_usd":cf,
                                   "prior_comparable_operating_cash_flow_usd":prior_cf},
                       "research_only":True,"trade_action":"none"})
    # Multiple quarterly and annual values in one filing are not independent
    # new events; choose the most recent/longest economic period only.
    return sorted(events,key=lambda e:e["evidence"]["period_end"],reverse=True)[:1]
