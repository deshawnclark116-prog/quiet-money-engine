"""Company Conviction Research v0.1 — separate from short-term entry decisions.

A *long_term_research_candidate* is NOT permission to buy, hold, or cancel
an existing short-term exit. A source-backed company thesis still needs an
independently validated valuation/price, tradeability and risk-allocation model.

Inputs are point-in-time receipts. Every financial fact carries a first-seen
timestamp, SEC accession, disclosed filing time, period end and standardized
measurement basis. Missing/malformed evidence stays unknown, never bullish.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from math import isfinite
from typing import Any, Mapping, Sequence

VERSION = "company_conviction_v0_1"
DANGEROUS = {"going_concern", "bankruptcy", "exchange_delisting", "reverse_split",
             "active_offering", "material_dilution", "accounting_restatement"}
METRICS = {"cash_usd", "operating_cash_flow_usd", "revenue_usd",
           "shares_outstanding", "total_debt_usd"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else None


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _valid_receipts(records: Sequence[Mapping[str, Any]], asof: datetime) -> dict[str, list[dict]]:
    series = {name: [] for name in METRICS}
    for r in records:
        if not isinstance(r, Mapping) or r.get("metric") not in METRICS:
            continue
        first = _time(r.get("first_seen_at"))
        filed = _time(r.get("filed_at"))
        end = _date(r.get("period_end"))
        if not first or not filed or not end or first > asof or filed > first:
            continue
        if end > first.date() or not r.get("accession") or r.get("source") != "sec_xbrl":
            continue
        try:
            value = float(r.get("value"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not isfinite(value):
            continue
        metric = r["metric"]
        if r.get("unit") != ("shares" if metric == "shares_outstanding" else "USD"):
            continue
        if metric in {"cash_usd", "revenue_usd", "shares_outstanding", "total_debt_usd"} and value < 0:
            continue
        basis = r.get("period_basis")
        if (metric in {"cash_usd", "total_debt_usd", "shares_outstanding"}
                and basis != "instant"):
            continue
        if metric in {"revenue_usd", "operating_cash_flow_usd"} and basis not in {"annual", "quarter"}:
            continue
        # A metric must be from a completed period, and a reported receipt
        # cannot first appear before its purported filed timestamp.
        series[metric].append({"value": value, "period_end": end, "filed_at": filed,
                               "first_seen_at": first, "period_basis": basis,
                               "accession": str(r["accession"])})
    for arr in series.values():
        arr.sort(key=lambda x: (x["period_end"], x["filed_at"], x["first_seen_at"]), reverse=True)
    return series


def _current(arr: list[dict], asof: datetime, max_age_days: int) -> dict | None:
    for r in arr:
        if 0 <= (asof.date()-r["period_end"]).days <= max_age_days:
            return r
    return None


def _comparison(latest: dict, arr: list[dict]) -> dict | None:
    # Same comparable period one year earlier, not arbitrary point-to-point.
    matches = [r for r in arr if r["period_basis"] == latest["period_basis"]
               and 330 <= (latest["period_end"]-r["period_end"]).days <= 400]
    return matches[0] if matches else None


def _filing_risks(events: Sequence[Mapping[str, Any]], asof: datetime) -> list[dict]:
    out = []
    for r in events:
        if not isinstance(r, Mapping) or r.get("risk_code") not in DANGEROUS:
            continue
        seen = _time(r.get("first_seen_at"))
        if seen is None or seen > asof or r.get("source") != "sec_primary" or not r.get("accession"):
            continue
        # Do not quietly ignore a prior bankruptcy or delisting just because
        # the initial report aged out of a 90-day news window.
        out.append({"risk_code": r["risk_code"], "accession": str(r["accession"]),
                    "first_seen_at": seen.isoformat()})
    return out


def _business_progress(records: Sequence[Mapping[str, Any]], asof: datetime) -> list[dict]:
    out = []
    for r in records:
        if not isinstance(r, Mapping):
            continue
        first = _time(r.get("first_seen_at"))
        if first is None or first > asof or asof-first > timedelta(days=365):
            continue
        if (r.get("primary_source") not in {"sec_primary", "regulator_primary"}
                or not r.get("source_id") or not r.get("verified")
                or r.get("milestone_type") not in {"commercial_contract", "customer_adoption",
                                                       "regulatory_approval", "verified_product_sales"}):
            continue
        out.append({"type": r["milestone_type"], "source_id": r["source_id"],
                    "first_seen_at": first.isoformat()})
    return out


def assess(*, ticker: str, asof_utc: str, financial_receipts: Sequence[Mapping[str, Any]],
           risk_events: Sequence[Mapping[str, Any]], business_milestones: Sequence[Mapping[str, Any]],
           risk_screen: Mapping[str, Any] | None,
           exchange_listing_verified: bool = False) -> dict:
    """Classify company *research*, not actual trading; strict unknown handling.

    `risk_screen` is an independently verified complete SEC/risk review with
    start/end coverage. Passing it is not inferred from an empty `risk_events`.
    """
    result = {"version": VERSION, "ticker": str(ticker or "").upper(),
              "classification": "insufficient_evidence", "long_term_flag": False,
              "research_only": True, "automatic_hold": False,
              "override_short_term_exit": False, "trade_action": "none",
              "missing": [], "risks": [], "strengths": [], "metrics": {},
              "valuation_status": "unreviewed", "evidence_sources": [],
              "flag_for_deeper_research": False}
    asof = _time(asof_utc)
    if asof is None:
        result["missing"].append("offset_aware_asof_required")
        return result
    if not result["ticker"].isascii() or not result["ticker"].replace(".", "").isalpha():
        result["missing"].append("valid_exchange_ticker_required")
        return result
    result["asof_utc"] = asof.isoformat()
    if not exchange_listing_verified:
        result["missing"].append("current_exchange_listing_unverified")
    screen_ok = False
    if isinstance(risk_screen, Mapping) and risk_screen.get("verified") is True:
        reviewed = _time(risk_screen.get("reviewed_at"))
        start = _time(risk_screen.get("coverage_start_at"))
        end = _time(risk_screen.get("coverage_end_at"))
        screen_ok = bool(reviewed and start and end and
                         reviewed <= asof and asof-reviewed <= timedelta(days=14) and
                         start <= asof-timedelta(days=120) and
                         end >= asof-timedelta(days=7) and end <= reviewed and
                         risk_screen.get("source") == "sec_primary" and
                         risk_screen.get("scope") == "complete_filing_risk_review")
    if not screen_ok:
        result["missing"].append("complete_recent_SEC_risk_screen_required")
    risks = _filing_risks(risk_events, asof)
    result["risks"] = risks
    if risks:
        result["classification"] = "risk_blocked"
        return result
    series = _valid_receipts(financial_receipts, asof)
    cash = _current(series["cash_usd"], asof, 200)
    debt = _current(series["total_debt_usd"], asof, 200)
    shares = _current(series["shares_outstanding"], asof, 200)
    operating = _current(series["operating_cash_flow_usd"], asof, 430)
    revenue = _current(series["revenue_usd"], asof, 430)
    for name, val in (("cash_usd", cash), ("total_debt_usd", debt),
                      ("shares_outstanding", shares), ("operating_cash_flow_usd", operating),
                      ("revenue_usd", revenue)):
        if val is None:
            result["missing"].append("recent_verified_"+name)
        else:
            result["metrics"][name] = val["value"]
            result["evidence_sources"].append(val["accession"])
    if cash is not None and operating is not None:
        if operating["value"] < 0:
            months = (3 if operating["period_basis"] == "quarter" else 12)
            monthly_burn = abs(operating["value"])/months
            runway = cash["value"] / monthly_burn if monthly_burn > 0 else None
            result["metrics"]["estimated_cash_runway_months"] = round(runway, 2) if runway is not None else None
            if runway is None or runway < 12:
                result["risks"].append({"risk_code": "low_cash_runway", "runway_months": runway})
            elif runway >= 18:
                result["strengths"].append("cash_runway_at_least_18_months_on_reported_burn")
        else:
            result["metrics"]["estimated_cash_runway_months"] = None
            result["strengths"].append("positive_reported_operating_cash_flow")
    if cash is not None and debt is not None and cash["value"] > 0:
        result["metrics"]["debt_to_cash"] = round(debt["value"]/cash["value"],3)
        if debt["value"] > 2*cash["value"]:
            result["risks"].append({"risk_code": "debt_exceeds_2x_cash"})
    if shares is not None:
        prior = _comparison(shares,series["shares_outstanding"])
        if prior and prior["value"] > 0:
            dilution = shares["value"]/prior["value"]-1
            result["metrics"]["yoy_share_growth_pct"] = round(100*dilution,2)
            if dilution > .20:
                result["risks"].append({"risk_code": "share_count_growth_over_20pct"})
        else:
            result["missing"].append("comparable_prior_year_shares")
    if revenue is not None:
        prior = _comparison(revenue,series["revenue_usd"])
        if prior and prior["value"] > 0:
            growth = revenue["value"]/prior["value"]-1
            result["metrics"]["yoy_revenue_growth_pct"] = round(100*growth,2)
            if growth > 0:
                result["strengths"].append("positive_yoy_comparable_revenue_growth")
        else:
            result["missing"].append("comparable_prior_year_revenue")
    progress = _business_progress(business_milestones,asof)
    result["business_milestones"] = progress
    if progress:
        result["strengths"].append("primary_source_verified_commercial_progress")
        result["evidence_sources"].extend(r["source_id"] for r in progress)
    else:
        result["missing"].append("verified_commercial_product_milestone")
    # Broad automated triage, distinct from an investment-quality flag:
    # preliminary candidates still require a full primary risk review,
    # independent business verification, valuation and suitable price.
    result["flag_for_deeper_research"] = bool(
        cash and debt and shares and operating and revenue and not result["risks"]
        and ((operating["value"] > 0) or (
            result["metrics"].get("estimated_cash_runway_months") is not None and
            result["metrics"].get("estimated_cash_runway_months") >= 18 and
            result["metrics"].get("yoy_revenue_growth_pct",-1) > 0))
    )
    if result["risks"]:
        result["classification"] = "risk_blocked"
    elif result["missing"]:
        result["classification"] = "research_incomplete"
    elif (operating and operating["value"] > 0) or (
            result["metrics"].get("estimated_cash_runway_months",0) is not None and
            result["metrics"].get("estimated_cash_runway_months",0) >= 18 and
            result["metrics"].get("yoy_revenue_growth_pct", -1) > 0):
        result["classification"] = "long_term_research_candidate"
        result["long_term_flag"] = True
    else:
        result["classification"] = "speculative_watch_only"
    result["evidence_sources"] = sorted(set(result["evidence_sources"]))
    return result

