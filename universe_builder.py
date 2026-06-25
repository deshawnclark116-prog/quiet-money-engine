#!/usr/bin/env python3
"""
Quiet Money Engine — dynamic universe builder.

Uses Nasdaq Trader's public symbol directory instead of relying only on FMP
screener, because FMP screener may return HTTP 402 on limited plans.

Universe logic:
- recent insider-buy tickers first
- fallback/core names second
- rotating slice of listed U.S. common-stock-like symbols third
- allows sub-$1 names; liquidity gate happens later after price history loads
"""
import os
import re
import csv
import io
import time
import logging
from datetime import date
from typing import List

import requests
import psycopg2
from psycopg2.extras import RealDictCursor


log = logging.getLogger("universe_builder")

DATABASE_URL = os.getenv("DATABASE_URL", "")

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

MAX_UNIVERSE_SIZE = int(os.getenv("MAX_UNIVERSE_SIZE", "25"))
UNIVERSE_CANDIDATE_MULTIPLIER = int(os.getenv("UNIVERSE_CANDIDATE_MULTIPLIER", "3"))
MAX_UNIVERSE_CANDIDATES = int(
    os.getenv(
        "MAX_UNIVERSE_CANDIDATES",
        str(MAX_UNIVERSE_SIZE * UNIVERSE_CANDIDATE_MULTIPLIER),
    )
)

INCLUDE_RECENT_INSIDERS = os.getenv("INCLUDE_RECENT_INSIDERS", "true").lower() in {
    "1",
    "true",
    "yes",
    "y",
}
INSIDER_UNIVERSE_LOOKBACK_DAYS = int(os.getenv("INSIDER_UNIVERSE_LOOKBACK_DAYS", "30"))

# Nasdaqtraded listing exchange codes:
# Q = Nasdaq, N = NYSE, A = NYSE American.
# We leave P/Arca out by default because that is heavily ETF/fund territory.
LISTING_EXCHANGES = {
    x.strip().upper()
    for x in os.getenv("LISTING_EXCHANGES", "Q,N,A").split(",")
    if x.strip()
}

FALLBACK_UNIVERSE = [
    t.strip().upper()
    for t in os.getenv(
        "FALLBACK_UNIVERSE",
        "AAPL,MSFT,NVDA,AMD,INTC,F,GM,RIOT,SOFI,PLTR,"
        "MARA,CLSK,HOOD,AFRM,UPST,OPEN,LCID,RIVN,CHPT,IONQ,"
        "SOUN,BBAI,ACHR,JOBY,ASTS,RKLB,ENVX,QS,PLUG,FCEL",
    ).split(",")
    if t.strip()
]

BAD_NAME_KEYWORDS = [
    " warrant",
    "warrants",
    " wt",
    " right",
    " rights",
    " unit",
    " units",
    " preferred",
    " preference",
    " etf",
    " etn",
    " fund",
    " trust",
    " notes due",
    " bond",
    " debenture",
    " acquisition corp",
]


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _bad_symbol_shape(symbol: str) -> bool:
    if not symbol:
        return True

    if len(symbol) > 5:
        return True

    if not re.match(r"^[A-Z]{1,5}$", symbol):
        return True

    return False


def _bad_name(name: str) -> bool:
    lowered = f" {str(name or '').lower()} "
    return any(keyword in lowered for keyword in BAD_NAME_KEYWORDS)


def _fetch_nasdaq_traded_rows() -> List[dict]:
    try:
        r = requests.get(NASDAQ_TRADED_URL, timeout=30)

        if r.status_code != 200:
            log.warning("Nasdaq Trader symbol file HTTP %s", r.status_code)
            return []

        text = r.text.strip()

        if not text:
            return []

        rows = []

        for row in csv.DictReader(io.StringIO(text), delimiter="|"):
            # Last row is usually File Creation Time.
            symbol = _clean_symbol(row.get("Symbol"))

            if not symbol or symbol.startswith("FILE CREATION TIME"):
                continue

            rows.append(row)

        return rows

    except Exception as e:
        log.warning("Could not fetch Nasdaq Trader symbols: %s", e)
        return []


def _passes_symbol_gate(row: dict) -> bool:
    symbol = _clean_symbol(row.get("Symbol"))
    name = str(row.get("Security Name") or "")
    listing_exchange = str(row.get("Listing Exchange") or "").strip().upper()
    etf = str(row.get("ETF") or "").strip().upper()
    test_issue = str(row.get("Test Issue") or "").strip().upper()
    financial_status = str(row.get("Financial Status") or "").strip().upper()
    nasdaq_traded = str(row.get("Nasdaq Traded") or "").strip().upper()

    if _bad_symbol_shape(symbol):
        return False

    if nasdaq_traded and nasdaq_traded != "Y":
        return False

    if listing_exchange not in LISTING_EXCHANGES:
        return False

    if etf == "Y":
        return False

    if test_issue == "Y":
        return False

    if financial_status not in {"", "N"}:
        return False

    if _bad_name(name):
        return False

    return True


def load_recent_insider_tickers(days: int = 30) -> List[str]:
    if not DATABASE_URL:
        return []

    sql = """
        SELECT DISTINCT UPPER(ticker) AS ticker
        FROM insider_buys
        WHERE seen_at >= NOW() - (%s || ' days')::interval
          AND ticker IS NOT NULL
        ORDER BY ticker
    """

    try:
        with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [str(days)])
                rows = cur.fetchall()

        return [_clean_symbol(row["ticker"]) for row in rows if row.get("ticker")]

    except Exception as e:
        log.warning("Could not load recent insider tickers: %s", e)
        return []


def _rotating_slice(symbols: List[str], needed: int) -> List[str]:
    if not symbols or needed <= 0:
        return []

    symbols = sorted(set(symbols))

    today_number = date.today().timetuple().tm_yday
    start = (today_number * needed) % len(symbols)

    rotated = symbols[start:] + symbols[:start]

    return rotated[:needed]


def build_dynamic_universe(max_size: int = MAX_UNIVERSE_SIZE) -> List[str]:
    target_candidates = max(max_size, MAX_UNIVERSE_CANDIDATES)

    universe = []
    seen = set()

    def add(symbol: str):
        symbol = _clean_symbol(symbol)
        if not symbol or symbol in seen:
            return
        if _bad_symbol_shape(symbol):
            return
        universe.append(symbol)
        seen.add(symbol)

    recent_insiders = []

    if INCLUDE_RECENT_INSIDERS:
        recent_insiders = load_recent_insider_tickers(days=INSIDER_UNIVERSE_LOOKBACK_DAYS)

    for symbol in recent_insiders:
        add(symbol)

    for symbol in FALLBACK_UNIVERSE:
        add(symbol)

    rows = _fetch_nasdaq_traded_rows()
    passed_symbols = []

    for row in rows:
        if _passes_symbol_gate(row):
            passed_symbols.append(_clean_symbol(row.get("Symbol")))

    remaining = max(0, target_candidates - len(universe))

    for symbol in _rotating_slice(passed_symbols, remaining):
        add(symbol)

    if not universe:
        log.warning("Dynamic universe empty; using fallback universe")
        return FALLBACK_UNIVERSE[:target_candidates]

    log.info(
        "Dynamic universe built: %s candidates. Recent insider tickers included: %s",
        len(universe),
        [t for t in recent_insiders if t in universe],
    )

    return universe[:target_candidates]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(",".join(build_dynamic_universe()))
