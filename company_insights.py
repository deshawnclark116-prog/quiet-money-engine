#!/usr/bin/env python3
"""
Quiet Money Engine — company/news insight layer.

Standalone test module first. This does NOT change the scorer yet.

What it adds:
- filing_catalyst_score
- dilution_risk_score
- news_catalyst_score
- company_quality_score
- company_insight_composite

Uses:
- SEC EDGAR company tickers + submissions API
- Optional Finnhub company-news API if FINNHUB_API_KEY is set

Test:
python company_insights.py FTH YYGH TOI RIOT INTC AMD OPEN LILA OESX

Environment:
EDGAR_CONTACT=Your Name your@email.com
FINNHUB_API_KEY=optional
COMPANY_INSIGHT_LOOKBACK_DAYS=90
NEWS_LOOKBACK_DAYS=14
"""

import os
import re
import json
import time
import math
import logging
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("company_insights")


EDGAR_CONTACT = os.getenv("EDGAR_CONTACT", "QuietMoneyEngine contact@example.com")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

COMPANY_INSIGHT_LOOKBACK_DAYS = int(os.getenv("COMPANY_INSIGHT_LOOKBACK_DAYS", "90"))
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "14"))

CACHE_DIR = Path(os.getenv("CACHE_DIR", ".cache"))
CACHE_DIR.mkdir(exist_ok=True)

SEC_TICKERS_CACHE = CACHE_DIR / "sec_company_tickers.json"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"

FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

HTTP_TIMEOUT = 30
SEC_SLEEP_SECONDS = float(os.getenv("SEC_SLEEP_SECONDS", "0.15"))
FINNHUB_SLEEP_SECONDS = float(os.getenv("FINNHUB_SLEEP_SECONDS", "0.15"))


SEC_HEADERS = {
    "User-Agent": EDGAR_CONTACT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,text/plain,*/*",
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def clamp(value: float, low: float = -3.0, high: float = 3.0) -> float:
    try:
        value = float(value)
    except Exception:
        return 0.0

    return max(low, min(high, value))


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None

    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def days_ago(d: Optional[date]) -> Optional[int]:
    if not d:
        return None

    return (today_utc() - d).days


def recency_points(days: Optional[int], fresh: int = 7, medium: int = 30, old: int = 90) -> float:
    if days is None:
        return 0.0

    if days <= fresh:
        return 1.0

    if days <= medium:
        return 0.65

    if days <= old:
        return 0.25

    return 0.0


def sec_get_json(url: str) -> Optional[dict]:
    try:
        time.sleep(SEC_SLEEP_SECONDS)
        resp = requests.get(url, headers=SEC_HEADERS, timeout=HTTP_TIMEOUT)

        if resp.status_code != 200:
            log.warning("SEC HTTP %s for %s", resp.status_code, url)
            return None

        return resp.json()

    except Exception as exc:
        log.warning("SEC fetch failed for %s: %s", url, exc)
        return None


def finnhub_get_json(url: str, params: dict) -> Optional[Any]:
    if not FINNHUB_API_KEY:
        return None

    params = dict(params)
    params["token"] = FINNHUB_API_KEY

    try:
        time.sleep(FINNHUB_SLEEP_SECONDS)
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)

        if resp.status_code != 200:
            log.warning("Finnhub HTTP %s for %s", resp.status_code, params.get("symbol", ""))
            return None

        return resp.json()

    except Exception as exc:
        log.warning("Finnhub fetch failed: %s", exc)
        return None


def load_sec_ticker_map(force_refresh: bool = False) -> dict:
    """
    Returns:
    {
        "AAPL": {"cik": 320193, "cik10": "0000320193", "title": "Apple Inc."}
    }
    """
    if SEC_TICKERS_CACHE.exists() and not force_refresh:
        try:
            raw = json.loads(SEC_TICKERS_CACHE.read_text())
            if isinstance(raw, dict) and raw:
                return raw
        except Exception:
            pass

    data = sec_get_json(SEC_TICKERS_URL)

    if not data:
        if SEC_TICKERS_CACHE.exists():
            try:
                return json.loads(SEC_TICKERS_CACHE.read_text())
            except Exception:
                return {}
        return {}

    out = {}

    for _, row in data.items():
        ticker = str(row.get("ticker") or "").upper().strip()
        cik = safe_int(row.get("cik_str"), 0)
        title = str(row.get("title") or "").strip()

        if ticker and cik:
            out[ticker] = {
                "ticker": ticker,
                "cik": cik,
                "cik10": str(cik).zfill(10),
                "title": title,
            }

    SEC_TICKERS_CACHE.write_text(json.dumps(out, indent=2, sort_keys=True))
    return out


def get_cik_for_ticker(ticker: str) -> Optional[dict]:
    ticker = str(ticker or "").upper().strip()
    ticker_map = load_sec_ticker_map()
    return ticker_map.get(ticker)


def fetch_submissions_by_cik10(cik10: str) -> Optional[dict]:
    return sec_get_json(SEC_SUBMISSIONS_URL.format(cik10=cik10))


def fetch_companyfacts_by_cik10(cik10: str) -> Optional[dict]:
    return sec_get_json(SEC_COMPANYFACTS_URL.format(cik10=cik10))


def recent_filings_from_submissions(submissions: dict, lookback_days: int = COMPANY_INSIGHT_LOOKBACK_DAYS) -> list[dict]:
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}

    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    accessions = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    descs = recent.get("primaryDocDescription") or []

    cutoff = today_utc() - timedelta(days=lookback_days)

    rows = []

    n = max(
        len(forms),
        len(filing_dates),
        len(report_dates),
        len(accessions),
        len(docs),
        len(descs),
    )

    for i in range(n):
        form = str(forms[i] if i < len(forms) else "").upper().strip()
        filing_date = parse_date(filing_dates[i] if i < len(filing_dates) else None)

        if not filing_date or filing_date < cutoff:
            continue

        rows.append(
            {
                "form": form,
                "filing_date": filing_date.isoformat(),
                "days_ago": days_ago(filing_date),
                "report_date": str(report_dates[i] if i < len(report_dates) else ""),
                "accession": str(accessions[i] if i < len(accessions) else ""),
                "primary_doc": str(docs[i] if i < len(docs) else ""),
                "description": str(descs[i] if i < len(descs) else ""),
            }
        )

    return rows


POSITIVE_CATALYST_FORMS = {
    "4",
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
}

NORMAL_REPORT_FORMS = {
    "10-K",
    "10-Q",
    "8-K",
    "6-K",
}

DILUTION_RISK_FORMS = {
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "F-1",
    "F-1/A",
    "F-3",
    "F-3/A",
    "424B1",
    "424B2",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "424B8",
    "FWP",
    "EFFECT",
    "RW",
}

REVERSE_SPLIT_FORMS = {
    "DEF 14A",
    "PRE 14A",
    "DEFR14A",
    "8-K",
}


DILUTION_WORDS = [
    "offering",
    "registered direct",
    "public offering",
    "private placement",
    "shelf",
    "atm",
    "at-the-market",
    "prospectus",
    "warrant",
    "convertible",
    "resale",
    "securities purchase agreement",
    "dilution",
]

POSITIVE_NEWS_WORDS = [
    "contract",
    "award",
    "approval",
    "fda",
    "partnership",
    "collaboration",
    "merger",
    "acquisition",
    "buyout",
    "strategic",
    "launch",
    "positive",
    "beat",
    "record revenue",
    "guidance",
    "upgrade",
    "settlement",
]

NEGATIVE_NEWS_WORDS = [
    "offering",
    "dilution",
    "bankruptcy",
    "delisting",
    "going concern",
    "investigation",
    "lawsuit",
    "sec charges",
    "downgrade",
    "misses",
    "reverse split",
    "layoff",
    "termination",
    "halt",
    "warning",
]


def contains_any(text: str, words: list[str]) -> bool:
    text = (text or "").lower()

    return any(word.lower() in text for word in words)


def filing_catalyst_score(filings: list[dict]) -> tuple[float, list[str]]:
    score = 0.0
    reasons = []

    for f in filings:
        form = f.get("form", "")
        d_ago = f.get("days_ago")
        recency = recency_points(d_ago)

        if form in POSITIVE_CATALYST_FORMS:
            pts = 0.65 * recency
            score += pts
            reasons.append(f"{form} filed {d_ago}d ago (+{pts:.2f})")

        elif form == "8-K":
            pts = 0.20 * recency
            score += pts
            reasons.append(f"8-K filed {d_ago}d ago (+{pts:.2f})")

        elif form in {"10-Q", "10-K"}:
            pts = 0.10 * recency
            score += pts
            reasons.append(f"{form} filed {d_ago}d ago (+{pts:.2f})")

    return clamp(score, -2.0, 2.0), reasons[:8]


def dilution_risk_score(filings: list[dict]) -> tuple[float, list[str]]:
    """
    Returns a negative or zero score.
    More negative = more risk.
    """
    score = 0.0
    reasons = []

    for f in filings:
        form = f.get("form", "")
        desc = f.get("description", "")
        d_ago = f.get("days_ago")
        recency = recency_points(d_ago)

        text = f"{form} {desc}"

        if form in DILUTION_RISK_FORMS:
            penalty = -1.00 * recency
            score += penalty
            reasons.append(f"{form} filed {d_ago}d ago ({penalty:.2f})")

        if contains_any(text, DILUTION_WORDS):
            penalty = -0.45 * recency
            score += penalty
            reasons.append(f"dilution keyword in {form} {d_ago}d ago ({penalty:.2f})")

    return clamp(score, -3.0, 0.0), reasons[:10]


def reverse_split_risk_score(filings: list[dict], price: Optional[float] = None) -> tuple[float, list[str]]:
    """
    First version is conservative.
    Without filing text, it only penalizes cheap names with proxy/8-K activity that may require review.
    Later we can fetch filing text and search for exact reverse-split language.
    """
    score = 0.0
    reasons = []

    price = safe_float(price, 0.0)

    if 0 < price < 1.00:
        score -= 0.35
        reasons.append("price under $1 requires reverse-split/delisting review (-0.35)")

    for f in filings:
        form = f.get("form", "")
        desc = f.get("description", "")
        d_ago = f.get("days_ago")
        recency = recency_points(d_ago)

        text = f"{form} {desc}".lower()

        if "reverse split" in text or "reverse stock split" in text:
            penalty = -1.25 * recency
            score += penalty
            reasons.append(f"reverse split language in {form} {d_ago}d ago ({penalty:.2f})")

        elif form in {"DEF 14A", "PRE 14A", "DEFR14A"} and price and price < 1.50:
            penalty = -0.35 * recency
            score += penalty
            reasons.append(f"proxy filing near low price {d_ago}d ago ({penalty:.2f})")

    return clamp(score, -2.5, 0.0), reasons[:8]


def latest_fact_value(companyfacts: dict, tags: list[str], units_preference: list[str]) -> Optional[dict]:
    facts = ((companyfacts or {}).get("facts") or {})

    for taxonomy in ["us-gaap", "dei"]:
        tax = facts.get(taxonomy) or {}

        for tag in tags:
            obj = tax.get(tag) or {}
            units = obj.get("units") or {}

            for unit in units_preference:
                rows = units.get(unit) or []

                clean = []

                for r in rows:
                    val = safe_float(r.get("val"), None)
                    end = parse_date(r.get("end"))

                    if val is None or not end:
                        continue

                    clean.append(
                        {
                            "tag": tag,
                            "taxonomy": taxonomy,
                            "unit": unit,
                            "value": val,
                            "end": end,
                            "form": r.get("form"),
                            "filed": r.get("filed"),
                            "fy": r.get("fy"),
                            "fp": r.get("fp"),
                        }
                    )

                if clean:
                    clean.sort(key=lambda x: x["end"], reverse=True)
                    return clean[0]

    return None


def company_quality_score(companyfacts: Optional[dict], filings: list[dict]) -> tuple[float, list[str], dict]:
    """
    Basic first-pass company quality from available XBRL facts + filing recency.

    This intentionally stays conservative because small-company XBRL tags can vary.
    """
    score = 0.0
    reasons = []
    facts_out = {}

    has_recent_10q_or_10k = any(f.get("form") in {"10-Q", "10-K"} for f in filings)

    if has_recent_10q_or_10k:
        score += 0.35
        reasons.append("recent 10-Q/10-K available (+0.35)")
    else:
        score -= 0.35
        reasons.append("no recent 10-Q/10-K in lookback (-0.35)")

    if not companyfacts:
        reasons.append("companyfacts unavailable/unsupported")
        return clamp(score, -2.0, 2.0), reasons, facts_out

    cash = latest_fact_value(
        companyfacts,
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalentsFairValueDisclosure",
        ],
        ["USD"],
    )

    revenue = latest_fact_value(
        companyfacts,
        [
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "SalesRevenueNet",
        ],
        ["USD"],
    )

    operating_cf = latest_fact_value(
        companyfacts,
        [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ],
        ["USD"],
    )

    shares = latest_fact_value(
        companyfacts,
        [
            "EntityCommonStockSharesOutstanding",
            "CommonStocksIncludingAdditionalPaidInCapital",
            "CommonStockSharesOutstanding",
        ],
        ["shares", "USD"],
    )

    if cash:
        facts_out["cash"] = {
            "value": cash["value"],
            "end": cash["end"].isoformat(),
            "tag": cash["tag"],
        }

        if cash["value"] >= 50_000_000:
            score += 0.35
            reasons.append("cash >= $50M (+0.35)")
        elif cash["value"] >= 10_000_000:
            score += 0.15
            reasons.append("cash >= $10M (+0.15)")
        elif cash["value"] > 0:
            score -= 0.15
            reasons.append("cash below $10M (-0.15)")

    if revenue:
        facts_out["revenue"] = {
            "value": revenue["value"],
            "end": revenue["end"].isoformat(),
            "tag": revenue["tag"],
        }

        if revenue["value"] > 0:
            score += 0.20
            reasons.append("reported revenue exists (+0.20)")

    if operating_cf:
        facts_out["operating_cash_flow"] = {
            "value": operating_cf["value"],
            "end": operating_cf["end"].isoformat(),
            "tag": operating_cf["tag"],
        }

        if operating_cf["value"] > 0:
            score += 0.30
            reasons.append("positive operating cash flow (+0.30)")
        else:
            score -= 0.20
            reasons.append("negative operating cash flow (-0.20)")

    if shares:
        facts_out["shares_or_equity_fact"] = {
            "value": shares["value"],
            "end": shares["end"].isoformat(),
            "tag": shares["tag"],
            "unit": shares["unit"],
        }

    return clamp(score, -2.0, 2.0), reasons[:10], facts_out


def fetch_finnhub_news(ticker: str, lookback_days: int = NEWS_LOOKBACK_DAYS) -> list[dict]:
    if not FINNHUB_API_KEY:
        return []

    to_date = today_utc()
    from_date = to_date - timedelta(days=lookback_days)

    data = finnhub_get_json(
        FINNHUB_COMPANY_NEWS_URL,
        {
            "symbol": ticker.upper(),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
        },
    )

    if not isinstance(data, list):
        return []

    rows = []

    for item in data:
        ts = safe_int(item.get("datetime"), 0)

        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            d = dt.date()
        else:
            d = None

        rows.append(
            {
                "datetime": ts,
                "date": d.isoformat() if d else "",
                "days_ago": days_ago(d) if d else None,
                "headline": str(item.get("headline") or ""),
                "summary": str(item.get("summary") or ""),
                "source": str(item.get("source") or ""),
                "url": str(item.get("url") or ""),
            }
        )

    rows.sort(key=lambda x: x.get("datetime") or 0, reverse=True)
    return rows


def news_catalyst_score(news: list[dict]) -> tuple[float, list[str]]:
    if not FINNHUB_API_KEY:
        return 0.0, ["FINNHUB_API_KEY not set; news score neutral"]

    if not news:
        return 0.0, ["no recent company news found"]

    score = 0.0
    reasons = []

    for item in news[:15]:
        text = f"{item.get('headline', '')} {item.get('summary', '')}"
        d_ago = item.get("days_ago")
        recency = recency_points(d_ago, fresh=3, medium=10, old=30)

        if contains_any(text, POSITIVE_NEWS_WORDS):
            pts = 0.50 * recency
            score += pts
            reasons.append(f"positive news keyword {d_ago}d ago: {item.get('headline', '')[:80]} (+{pts:.2f})")

        if contains_any(text, NEGATIVE_NEWS_WORDS):
            penalty = -0.75 * recency
            score += penalty
            reasons.append(f"negative news keyword {d_ago}d ago: {item.get('headline', '')[:80]} ({penalty:.2f})")

    if len(news) >= 5:
        score += 0.15
        reasons.append("elevated recent news flow (+0.15)")

    return clamp(score, -2.5, 2.5), reasons[:10]


def analyze_ticker(ticker: str, price: Optional[float] = None) -> dict:
    ticker = str(ticker or "").upper().strip()

    cik_info = get_cik_for_ticker(ticker)

    if not cik_info:
        return {
            "ticker": ticker,
            "ok": False,
            "reason": "ticker not found in SEC company_tickers map",
            "scores": {
                "filing_catalyst_score": 0.0,
                "dilution_risk_score": 0.0,
                "reverse_split_risk_score": 0.0,
                "company_quality_score": 0.0,
                "news_catalyst_score": 0.0,
                "company_insight_composite": 0.0,
            },
            "reasons": {},
            "recent_filings": [],
            "recent_news": [],
            "facts": {},
        }

    cik10 = cik_info["cik10"]

    submissions = fetch_submissions_by_cik10(cik10)
    companyfacts = fetch_companyfacts_by_cik10(cik10)

    filings = recent_filings_from_submissions(
        submissions or {},
        lookback_days=COMPANY_INSIGHT_LOOKBACK_DAYS,
    )

    news = fetch_finnhub_news(ticker, lookback_days=NEWS_LOOKBACK_DAYS)

    filing_score, filing_reasons = filing_catalyst_score(filings)
    dilution_score, dilution_reasons = dilution_risk_score(filings)
    split_score, split_reasons = reverse_split_risk_score(filings, price=price)
    quality_score, quality_reasons, facts = company_quality_score(companyfacts, filings)
    news_score, news_reasons = news_catalyst_score(news)

    composite = (
        filing_score * 0.45
        + quality_score * 0.35
        + news_score * 0.30
        + dilution_score * 0.80
        + split_score * 0.65
    )

    scores = {
        "filing_catalyst_score": filing_score,
        "dilution_risk_score": dilution_score,
        "reverse_split_risk_score": split_score,
        "company_quality_score": quality_score,
        "news_catalyst_score": news_score,
        "company_insight_composite": clamp(composite, -4.0, 4.0),
    }

    return {
        "ticker": ticker,
        "ok": True,
        "company": cik_info.get("title"),
        "cik": cik_info.get("cik"),
        "scores": scores,
        "reasons": {
            "filing_catalyst": filing_reasons,
            "dilution_risk": dilution_reasons,
            "reverse_split_risk": split_reasons,
            "company_quality": quality_reasons,
            "news_catalyst": news_reasons,
        },
        "recent_filings": filings[:15],
        "recent_news": news[:10],
        "facts": facts,
    }


def print_result(result: dict) -> None:
    print("")
    print("=" * 100)
    print(f"{result.get('ticker')} COMPANY / NEWS INSIGHT")
    print("=" * 100)

    if not result.get("ok"):
        print("ok: False")
        print("reason:", result.get("reason"))
        return

    print("company:", result.get("company"))
    print("cik:", result.get("cik"))

    print("")
    print("Scores:")
    for k, v in result.get("scores", {}).items():
        print(f"- {k}: {float(v):+.2f}")

    print("")
    print("Reasons:")
    reasons = result.get("reasons") or {}

    for bucket, items in reasons.items():
        print(f"  {bucket}:")
        if not items:
            print("    - none")
        else:
            for item in items:
                print(f"    - {item}")

    filings = result.get("recent_filings") or []

    print("")
    print("Recent filings:")
    if not filings:
        print("  none")
    else:
        for f in filings[:10]:
            print(
                f"  {f.get('filing_date')} | {f.get('form')} | "
                f"{f.get('description') or f.get('primary_doc')}"
            )

    news = result.get("recent_news") or []

    print("")
    print("Recent news:")
    if not news:
        print("  none")
    else:
        for n in news[:5]:
            print(f"  {n.get('date')} | {n.get('source')} | {n.get('headline')[:120]}")


def main():
    import sys

    tickers = [x.upper().strip() for x in sys.argv[1:] if x.strip()]

    if not tickers:
        tickers = ["FTH", "YYGH", "TOI", "RIOT", "INTC", "AMD", "OPEN", "LILA", "OESX"]

    print("Company insight settings:")
    print(f"EDGAR_CONTACT: {EDGAR_CONTACT}")
    print(f"FINNHUB_API_KEY set: {bool(FINNHUB_API_KEY)}")
    print(f"COMPANY_INSIGHT_LOOKBACK_DAYS: {COMPANY_INSIGHT_LOOKBACK_DAYS}")
    print(f"NEWS_LOOKBACK_DAYS: {NEWS_LOOKBACK_DAYS}")

    for ticker in tickers:
        result = analyze_ticker(ticker)
        print_result(result)


if __name__ == "__main__":
    main()
