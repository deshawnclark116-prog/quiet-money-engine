#!/usr/bin/env python3
"""
Quiet Money Engine — Filing Intelligence v2.

Replaces keyword-count filing scoring with a deterministic EVENT taxonomy.

Why this works in OUR universe: filing lag is proportional to attention.
Covered large caps are parsed by algorithms in milliseconds — filings lag
there. In uncovered small caps nobody reads EDGAR at all, so material
events sit unpriced for days. And several filing types LEAD by
construction: a 13D is an accumulator forced to confess, a registration
withdrawal (RW) removes a dilution threat before price reflects it, an
NT 10-K announces trouble before the bad news itself.

Every SEC filing carries a form type; 8-Ks carry numbered item codes
that state exactly what happened (1.03 = bankruptcy, 4.02 = past
financials unreliable). No sentiment guessing: each event type has a
direction, weight, and recency decay. Output is a signed score in
[-15, +15] plus named events — SUPPORT evidence for conviction, never
sole qualification (mission rule: generic filing activity can never
qualify a stock by itself).

Form 4/3/5 are deliberately weight-0 here: the insider cluster detector
owns insider signals, and double counting would inflate conviction.

Pure classification is unit-testable offline; get_filing_events(ticker)
is the network wrapper over the SEC submissions feed already used by
company_insights (fails open to 0.0 / no events).
"""

import logging
from datetime import date, datetime

log = logging.getLogger("filing_intelligence")

LOOKBACK_DAYS = 90
SCORE_CAP = 15.0

# (weight, label) by form type. Checked most-specific first.
FORM_RULES = [
    # Leading positives
    (("RW", "RW WD", "AW"), +8.0, "registration WITHDRAWN — dilution threat removed"),
    (("SC 13D/A",), +3.0, "13D amended — active 5%+ holder still engaged"),
    (("SC 13D",), +7.0, "NEW 13D — someone crossed 5% ownership and must disclose"),
    (("8-A12B", "8-A12G"), +5.0, "exchange listing registration (uplisting)"),
    # Leading negatives
    (("25", "25-NSE"), -7.0, "delisting notification"),
    (("NT 10-K", "NT 10-Q", "NT 20-F"), -7.0, "late-filing notice — trouble before the news"),
    (("424B",), -7.0, "offering priced — dilution landing now"),
    (("S-1", "S-3", "F-1", "F-3", "S-11"), -6.0, "new shelf/offering registration — dilution supply coming"),
    (("S-8",), -1.0, "employee comp registration"),
    (("144",), -2.0, "insider intent to sell"),
    # Neutral here (owned by other layers)
    (("4", "3", "5"), 0.0, None),          # insider cluster detector owns these
    (("10-K", "10-Q", "20-F"), 0.0, None), # fundamentals handled by quality layer
]

# 8-K item codes -> (weight, label)
ITEM_RULES = {
    "1.01": (+3.0, "entered a material agreement"),
    "1.02": (-3.0, "terminated a material agreement"),
    "1.03": (-10.0, "bankruptcy / receivership"),
    "2.01": (+2.0, "completed acquisition or disposition"),
    "2.05": (-3.0, "exit / restructuring costs"),
    "2.06": (-3.0, "material impairment"),
    "3.01": (-7.0, "listing deficiency notice"),
    "3.02": (-4.0, "unregistered share sales (dilution)"),
    "4.01": (-5.0, "auditor change"),
    "4.02": (-8.0, "past financials can no longer be relied on"),
    "5.02": (-1.0, "officer/director change"),
    "5.03": (-2.0, "charter/bylaws amendment (often reverse-split related)"),
}


def _recency_mult(days_old):
    if days_old is None or days_old < 0:
        return 0.0
    if days_old <= 7:
        return 1.0
    if days_old <= 21:
        return 0.8
    if days_old <= 45:
        return 0.5
    if days_old <= LOOKBACK_DAYS:
        return 0.25
    return 0.0


def _form_rule(form):
    form = str(form or "").upper().strip()
    # Longest/most-specific prefixes first, as ordered in FORM_RULES.
    for prefixes, weight, label in FORM_RULES:
        for p in prefixes:
            if form == p or form.startswith(p + "/") or form.startswith(p):
                return weight, label
    return 0.0, None


def classify_filing_events(filings):
    """filings: [{form, days_ago, items?}, ...] ->
    (score in [-SCORE_CAP, +SCORE_CAP], [event strings, strongest first])."""
    hits = []

    for f in filings or []:
        form = str(f.get("form") or "").upper().strip()
        days_old = f.get("days_ago")
        mult = _recency_mult(days_old)
        if mult == 0.0:
            continue

        if form.startswith("8-K"):
            for code in str(f.get("items") or "").split(","):
                code = code.strip()
                if code in ITEM_RULES:
                    weight, label = ITEM_RULES[code]
                    hits.append(
                        (weight * mult,
                         f"8-K item {code}: {label} ({days_old}d ago)")
                    )
            continue

        weight, label = _form_rule(form)
        if weight != 0.0 and label:
            hits.append((weight * mult, f"{form}: {label} ({days_old}d ago)"))

    if not hits:
        return 0.0, []

    score = max(-SCORE_CAP, min(SCORE_CAP, sum(w for w, _ in hits)))
    events = [text for _, text in sorted(hits, key=lambda h: -abs(h[0]))]
    return score, events


def _days_ago(iso_date, today=None):
    try:
        d = datetime.strptime(str(iso_date)[:10], "%Y-%m-%d").date()
        return ((today or date.today()) - d).days
    except ValueError:
        return None


def fetch_recent_filings(ticker, today=None):
    """SEC submissions feed -> [{form, days_ago, items}, ...] within
    LOOKBACK_DAYS. Includes 8-K item codes, which company_insights'
    parser drops. Fails open to []."""
    try:
        from company_insights import fetch_submissions_by_cik10, get_cik_for_ticker

        info = get_cik_for_ticker(ticker)
        if not info:
            return []

        submissions = fetch_submissions_by_cik10(info["cik10"]) or {}
        recent = (submissions.get("filings") or {}).get("recent") or {}

        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        items = recent.get("items") or []

        out = []
        for i, form in enumerate(forms):
            age = _days_ago(dates[i] if i < len(dates) else None, today)
            if age is None or age > LOOKBACK_DAYS:
                continue
            out.append(
                {
                    "form": str(form).upper().strip(),
                    "days_ago": age,
                    "items": str(items[i]) if i < len(items) else "",
                }
            )
        return out

    except Exception as exc:
        log.warning("%s filing fetch failed; no filing events: %s", ticker, exc)
        return []


# Registered investment companies (closed-end funds, mutual funds) are
# structurally incapable of a pre-pop move and must be excluded as a
# class. SIC codes are unreliable for them (often blank), but their
# Investment Company Act filings are a decisive fingerprint: only funds
# file N-series forms (N-2, N-CSR, N-PORT, N-14, ...) or fund
# prospectus updates (485/486BPOS, 24F-2NT). Verified live: KYN, IGR,
# FINS, FUND all carry N-forms; operating companies never do.
FUND_VEHICLE_SICS = {"6722", "6726", "6770"}
FUND_FORM_EXACT = {"24F-2NT", "485BPOS", "486BPOS", "485APOS", "486APOS"}


def _looks_like_fund(forms, sic):
    if sic in FUND_VEHICLE_SICS:
        return True
    for f in forms or []:
        form = str(f).upper().strip()
        if form.startswith("N-") or form in FUND_FORM_EXACT:
            return True
    return False


def get_filing_intel(ticker, today=None):
    """End-to-end: ticker -> {score, events, sic, sic_description,
    is_fund_vehicle}. One submissions fetch serves both the filing-event
    classification and the instrument-type check. Fails open (score 0,
    is_fund_vehicle False)."""
    out = {
        "score": 0.0,
        "events": [],
        "sic": "",
        "sic_description": "",
        "is_fund_vehicle": False,
    }

    try:
        from company_insights import fetch_submissions_by_cik10, get_cik_for_ticker

        info = get_cik_for_ticker(ticker)
        if not info:
            return out

        submissions = fetch_submissions_by_cik10(info["cik10"]) or {}

        sic = str(submissions.get("sic") or "").strip()
        out["sic"] = sic
        out["sic_description"] = str(submissions.get("sicDescription") or "").strip()

        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        items = recent.get("items") or []

        # Fund detection scans the FULL recent filing history (years),
        # not just the 90-day event window.
        out["is_fund_vehicle"] = _looks_like_fund(forms, sic)

        filings = []
        for i, form in enumerate(forms):
            age = _days_ago(dates[i] if i < len(dates) else None, today)
            if age is None or age > LOOKBACK_DAYS:
                continue
            filings.append(
                {
                    "form": str(form).upper().strip(),
                    "days_ago": age,
                    "items": str(items[i]) if i < len(items) else "",
                }
            )

        out["score"], out["events"] = classify_filing_events(filings)
        return out

    except Exception as exc:
        log.warning("%s filing intel failed; neutral: %s", ticker, exc)
        return out


def get_filing_events(ticker, today=None):
    """Back-compat wrapper: ticker -> (score, events)."""
    intel = get_filing_intel(ticker, today)
    return intel["score"], intel["events"]


if __name__ == "__main__":
    import sys

    for t in [x.upper() for x in sys.argv[1:]] or ["PLUG"]:
        score, events = get_filing_events(t)
        print(f"\n{t}: filing_events_score {score:+.1f}")
        for e in events[:8]:
            print(f"  {e}")
        if not events:
            print("  no scored filing events in the last 90 days")
