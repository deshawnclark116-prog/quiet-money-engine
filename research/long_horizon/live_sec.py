"""Live SEC discovery adapter, read-only: turn companyfacts into observed receipts.

Not a historical replay: first_seen_at is when our worker obtained the fact,
never the older SEC filing date. No filing keyword is treated as evidence of
commercial traction, and a complete risk review is NOT inferred from the
absence of a scary form. This is a discovery/review queue, not an order bot.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import os
import sys
from typing import Mapping

from .thesis import assess

FIELDS = {
    "cash_usd": ("instant", "USD", (
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"))),
    "total_debt_usd": ("instant", "USD", (
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities"),
        ("us-gaap", "LongTermDebtAndCapitalLeaseObligations"),
        ("us-gaap", "LongTermDebt"))),
    "shares_outstanding": ("instant", "shares", (
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"))),
    "revenue_usd": ("flow", "USD", (
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"), ("us-gaap", "SalesRevenueNet"))),
    "operating_cash_flow_usd": ("flow", "USD", (
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),)),
}


def extract_receipts(companyfacts: Mapping, received_at_utc: str, *, cap=35) -> list[dict]:
    """Normalize SEC XBRL observations into point-in-time financial receipts.

    Only annual/quarter flows are eligible; YTD cash flow is not annualized
    by silently treating a nine-month figure as one quarter. If multiple
    taxonomy tags exist for a metric use a single primary tag consistently.
    """
    facts = companyfacts.get("facts") or {} if isinstance(companyfacts, Mapping) else {}
    out = []
    for metric, (shape, unit, alternatives) in FIELDS.items():
        candidates = []
        for taxonomy, tag in alternatives:
            source = ((facts.get(taxonomy) or {}).get(tag) or {})
            raw = (source.get("units") or {}).get(unit) or []
            eligible = []
            for row in raw:
                end = row.get("end")
                accession = row.get("accn")
                filed = row.get("filed")
                form = row.get("form")
                try:
                    d_end = date.fromisoformat(end)
                    d_filed = date.fromisoformat(filed)
                    value = float(row["val"])
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
                if not accession or form not in {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A"}:
                    continue
                if shape == "instant":
                    basis = "instant"
                    if row.get("start"):
                        continue
                else:
                    try:
                        start = date.fromisoformat(row["start"])
                        days = (d_end-start).days
                    except (ValueError, TypeError, KeyError):
                        continue
                    if 330 <= days <= 395:
                        basis = "annual"
                    elif 70 <= days <= 112:
                        basis = "quarter"
                    else:
                        continue  # YTD and odd reporting durations unsupported
                eligible.append({"metric": metric, "value": value,
                                 "unit": unit, "period_basis": basis,
                                 "period_end": end,
                                 "filed_at": datetime.combine(d_filed, time.min,tzinfo=timezone.utc).isoformat(),
                                 "first_seen_at": received_at_utc,
                                 "accession": accession,
                                 "source": "sec_xbrl"})
            if eligible:
                candidates = eligible
                break
        candidates.sort(key=lambda r: (r["period_end"],r["filed_at"]),reverse=True)
        # Keep previous comparable periods, but not an unlimited SEC dump.
        out.extend(candidates[:cap])
    return out


def extract_regulatory_alerts(submissions: Mapping, received_at_utc: str) -> list[dict]:
    """High-confidence FORM-based red alerts only, not blanket sentiment."""
    recent = ((submissions.get("filings") or {}).get("recent") or {}) if isinstance(submissions,Mapping) else {}
    forms, nums, items = (recent.get(k) or [] for k in ("form", "accessionNumber", "items"))
    output = []
    for i, form in enumerate(forms):
        accession = nums[i] if i < len(nums) else None
        if not accession:
            continue
        codes = {x.strip() for x in str(items[i] if i < len(items) else "").split(",")}
        risks = []
        if form in {"25", "25-NSE"}:
            risks.append("exchange_delisting")
        if form.startswith("8-K"):
            if "1.03" in codes:
                risks.append("bankruptcy")
            if "4.02" in codes:
                risks.append("accounting_restatement")
        for risk in risks:
            output.append({"risk_code": risk,"first_seen_at": received_at_utc,
                           "source":"sec_primary","accession": accession})
    return output


def research_ticker(ticker: str, *, now_utc: str | None = None,
                    risk_screen=None, verified_milestones=None,
                    listing_verified=False) -> dict:
    """Read SEC feeds and return an evidence-limited long-horizon research flag.

    Required human/operator SEC User-Agent is a real contact. A sample
    placeholder must never trigger automated network polling.
    """
    ticker = str(ticker or "").upper().strip()
    contact = os.getenv("EDGAR_CONTACT", "")
    if (not contact or "@" not in contact or "example" in contact.lower()
            or "your name" in contact.lower()):
        return {"ticker":ticker,"classification":"insufficient_evidence",
                "reason":"EDGAR_CONTACT_real_contact_required", "trade_action":"none",
                "long_term_flag":False}
    # Lazy load: importing the production helper shouldn't touch live systems
    # unless this explicit isolated collector is invoked.
    import company_insights
    # company_insights sets SEC_HEADERS at import using the EDGAR_CONTACT env.
    mapped = company_insights.get_cik_for_ticker(ticker)
    if not mapped:
        return {"ticker":ticker,"classification":"insufficient_evidence",
                "reason":"SEC_ticker_cik_mapping_unavailable","trade_action":"none",
                "long_term_flag":False}
    raw_facts = company_insights.fetch_companyfacts_by_cik10(mapped["cik10"])
    raw_submissions = company_insights.fetch_submissions_by_cik10(mapped["cik10"])
    if not raw_facts or not raw_submissions:
        return {"ticker":ticker,"classification":"insufficient_evidence",
                "reason":"SEC_companyfacts_or_submissions_unavailable","trade_action":"none",
                "long_term_flag":False}
    # Time stamped only AFTER both network responses arrive.
    seen_at = datetime.now(timezone.utc).isoformat() if now_utc is None else now_utc
    receipts = extract_receipts(raw_facts, seen_at)
    alerts = extract_regulatory_alerts(raw_submissions, seen_at)
    from .verified_financial_events import extract_verified_inflections
    # A financial event needs fresh filing + verified growth and cash-flow
    # comparisons. This event is research evidence, not a business milestone.
    fresh_events = extract_verified_inflections(
        receipts, raw_submissions, seen_at, cik=mapped.get("cik"))
    result = assess(ticker=ticker,asof_utc=seen_at,financial_receipts=receipts,
                    risk_events=alerts,business_milestones=verified_milestones or [],
                    risk_screen=risk_screen,exchange_listing_verified=listing_verified)
    result["verified_primary_events"] = fresh_events
    return result


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("tickers",nargs="+")
    args=p.parse_args(argv)
    for ticker in args.tickers:
        print(json.dumps(research_ticker(ticker),sort_keys=True))


if __name__ == "__main__":
    main()

