"""Carry first-observed SEC events forward across otherwise stateless Actions runs.

A re-fetch cannot reset an event's information clock. Only immutable
(event ID, first-observed UTC, primary source) receipts are retained.
"""
from __future__ import annotations
from datetime import datetime,timezone,timedelta

VERSION="sec_event_ledger_v0_1"

def _ts(raw):
    try:
        t=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        return t.astimezone(timezone.utc) if t.tzinfo else None
    except (TypeError,ValueError):return None

def reconcile(previous, ticker, new_events, asof):
    now=_ts(asof)
    if now is None:raise ValueError("offset_aware_asof_required")
    stored=(previous or {}).get("events") or {}
    if not isinstance(stored,dict):stored={}
    kept={}
    for sym, rows in stored.items():
        if not isinstance(sym,str) or not isinstance(rows,list):continue
        filtered=[]
        for row in rows:
            if not isinstance(row,dict):continue
            dt=_ts(row.get("observed_at"))
            if (dt is None or dt>now or now-dt>timedelta(days=35)
                or row.get("source")!="sec_primary"
                or row.get("evidence_status")!="verified"
                or row.get("polarity")!="positive" or not row.get("event_id")):
                continue
            filtered.append(dict(row))
        if filtered:kept[sym]=filtered
    old={str(r["event_id"]):r for r in kept.get(ticker,[])}
    for candidate in new_events or []:
        if not isinstance(candidate,dict):continue
        eid=str(candidate.get("event_id") or "")
        seen=_ts(candidate.get("observed_at"))
        if not eid or seen is None or seen>now:continue
        if candidate.get("source")!="sec_primary" or candidate.get("evidence_status")!="verified":continue
        if candidate.get("polarity")!="positive":continue
        if eid in old:continue  # immutable old first-seen time, no retroactive reset
        old[eid]=dict(candidate)
    if old:kept[ticker]=list(old.values())
    return {"version":VERSION,"updated_at_utc":now.isoformat(),"events":kept}

def current_events(ledger,ticker,asof):
    now=_ts(asof)
    if now is None:return []
    return [r for r in ((ledger or {}).get("events") or {}).get(ticker,[])
            if _ts(r.get("observed_at")) and
               timedelta(0)<=now-_ts(r.get("observed_at"))<=timedelta(days=21)]
