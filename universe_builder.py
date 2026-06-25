#!/usr/bin/env python3
"""
Quiet Money Engine — dynamic universe builder.

Goal:
Stop hardcoding only mega-cap names. Build a tradable daily universe from
listed U.S. stocks, while still allowing sub-$1 names when they are liquid
and likely tradeable on Webull.

Webull proxy gate v1:
- NYSE / NASDAQ / NYSE American / AMEX listed names
- no OTC by default
- no ETFs/funds/warrants/rights/units
- price can be below $1, default floor is $0.10
- enough dollar volume
- recent insider-buy tickers are included first when available

This does not guarantee a symbol is tradeable in your personal Webull account.
It is a conservative proxy until/unless we add a live Webull availability list.
"""
import os
import re
import time
import logging
from typing import Dict, List, Optional

import requests
import psycopg2
from psycopg2.extras import RealDictCursor


log = logging.getLogger("universe_builder")


FMP_API_KEY = os.getenv("FMP_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "")

FMP_SCREENER_URL = "https://financialmodelingprep.com/stable/company-screener"

HTTP_TIMEOUT = 30
MIN_INTERVAL = float(os.getenv("FMP_MIN_INTERVAL", "0.2"))
_last_call = 0.0


# Keep this small while on free-tier data.
MAX_UNIVERSE_SIZE = int(os.getenv("MAX_UNIVERSE_SIZE", "25"))

# User said sub-$1 is okay. Keep floor low, but not zero.
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.10"))

# Liquidity matters more than price for actually being able to enter/exit.
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "250000"))

# Very tiny names can be tradeable but often become trash/noise.
# Keep this adjustable.
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "10000000"))

# Avoid letting the universe become only mega-caps.
MAX_MARKET_CAP = float(os.getenv("MAX_MARKET_CAP", "25000000000"))

# Pull more candidates than final size, then rank/filter locally.
SCREENER_LIMIT_PER_EXCHANGE = int(os.getenv("SCREENER_LIMIT_PER_EXCHANGE", "100"))

# Default exchanges likely to map to Webull-tradeable listed equities.
UNIVERSE_EXCHANGES = [
    x.strip().upper()
    for x in os.getenv("UNIVERSE_EXCHANGES", "NASDAQ,NYSE,AMEX").split(",")
    if x.strip()
]

INCLUDE_RECENT_INSIDERS = os.getenv("INCLUDE_RECENT_INSIDERS", "true").lower() in {
    "1",
    "true",
    "yes",
    "y",
}

INSIDER_UNIVERSE_LOOKBACK_DAYS = int(os.getenv("INSIDER_UNIVERSE_LOOKBACK_DAYS", "30"))

# Keep OTC off by default because Webull only supports some OTC symbols and the
# allowed list can change. We can add an allowlist later.
ALLOW_OTC = os.getenv("ALLOW_OTC", "false").lower() in {"1", "true", "yes", "y"}

FALLBACK_UNIVERSE = [
    t.strip().upper()
    for t in os.getenv(
        "FALLBACK_UNIVERSE",
        "AAPL,MSFT,NVDA,AMD,INTC,F,GM,RIOT,SOFI,PLTR",
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
    " trust ii",
    " notes due",
    " bond",
    " debenture",
]


def _polite_get(url: str, params: Optional[dict] = None) -> requests.Response:
    global _last_call

    wait = MIN_INTERVAL - (time.monotonic() - _last_call)

    if wait > 0:
        time.sleep(wait)

    _last_call = time.monotonic()

    return requests.get(url, params=params or {}, timeout=HTTP_TIMEOUT)


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value, default=0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except Exception:
        return default


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _clean_exchange(value: str) -> str:
    text = str(value or "").strip().upper()

    if text in {"NYSE AMERICAN", "NYSEAMERICAN", "AMERICAN", "AMEX"}:
        return "AMEX"

    if "NASDAQ" in text:
        return "NASDAQ"

    if text == "NYSE" or "NEW YORK STOCK EXCHANGE" in text:
        return "NYSE"

    if "AMEX" in text or "NYSE AMERICAN" in text:
        return "AMEX"

    if "OTC" in text or "PINK" in text:
        return "OTC"

    return text


def _row_symbol(row: dict) -> str:
    return _clean_symbol(
        row.get("symbol")
        or row.get("ticker")
        or row.get("Symbol")
        or row.get("Ticker")
    )


def _row_exchange(row: dict) -> str:
    return _clean_exchange(
        row.get("exchangeShortName")
        or row.get("exchange")
        or row.get("exchangeName")
        or row.get("Exchange")
    )


def _row_name(row: dict) -> str:
    return str(
        row.get("companyName")
        or row.get("name")
        or row.get("company")
        or ""
    ).strip()


def _row_price(row: dict) -> float:
    return _safe_float(row.get("price") or row.get("lastPrice") or row.get("close"))


def _row_market_cap(row: dict) -> float:
    return _safe_float(
        row.get("marketCap")
        or row.get("market_cap")
        or row.get("mktCap")
    )


def _row_volume(row: dict) -> float:
    return _safe_float(
        row.get("volume")
        or row.get("avgVolume")
        or row.get("averageVolume")
        or row.get("volAvg")
    )


def _looks_like_etf_or_fund(row: dict) -> bool:
    for key in ["isEtf", "isETF", "etf"]:
        if key in row and str(row.get(key)).lower() in {"true", "1", "yes"}:
            return True

    for key in ["isFund", "fund"]:
        if key in row and str(row.get(key)).lower() in {"true", "1", "yes"}:
            return True

    name = _row_name(row).lower()

    return any(keyword in f" {name} " for keyword in BAD_NAME_KEYWORDS)


def _bad_symbol_shape(symbol: str) -> bool:
    if not symbol:
        return True

    if len(symbol) > 8:
        return True

    # Avoid weird symbols that usually break downstream feeds.
    if not re.match(r"^[A-Z0-9-]+$", symbol):
        return True

    # Avoid common non-common-share suffixes where possible.
    bad_suffixes = [
        "-WS",
        "-WT",
        "-W",
        "-U",
        "-R",
        "WS",
        "WTS",
        "WT",
    ]

    for suffix in bad_suffixes:
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            return True

    return False


def _is_actively_trading(row: dict) -> bool:
    for key in ["isActivelyTrading", "activelyTrading"]:
        if key in row:
            return str(row.get(key)).lower() not in {"false", "0", "no"}

    # If the provider does not tell us, do not reject.
    return True


def _passes_webull_proxy_gate(row: dict) -> bool:
    symbol = _row_symbol(row)
    exchange = _row_exchange(row)
    name = _row_name(row)
    price = _row_price(row)
    market_cap = _row_market_cap(row)
    volume = _row_volume(row)
    dollar_volume = price * volume if price > 0 and volume > 0 else 0.0

    if _bad_symbol_shape(symbol):
        return False

    if not _is_actively_trading(row):
        return False

    if _looks_like_etf_or_fund(row):
        return False

    if not ALLOW_OTC:
        if exchange not in {"NASDAQ", "NYSE", "AMEX"}:
            return False

    if ALLOW_OTC:
        if exchange not in {"NASDAQ", "NYSE", "AMEX", "OTC"}:
            return False

    if price < MIN_PRICE:
        return False

    if market_cap > 0 and market_cap < MIN_MARKET_CAP:
        return False

    if market_cap > 0 and market_cap > MAX_MARKET_CAP:
        return False

    if dollar_volume < MIN_DOLLAR_VOLUME:
        return False

    # Quick name-level filters for obvious garbage that can slip through.
    lowered_name = name.lower()
    if "acquisition corp" in lowered_name and ("unit" in lowered_name or "right" in lowered_name):
        return False

    return True


def _candidate_score(row: dict, insider_priority: bool = False) -> float:
    price = _row_price(row)
    market_cap = _row_market_cap(row)
    volume = _row_volume(row)
    dollar_volume = price * volume if price > 0 and volume > 0 else 0.0

    score = 0.0

    # Liquidity without letting the very largest stocks dominate.
    if dollar_volume > 0:
        score += min(max((dollar_volume / 1_000_000.0), 0.0), 10.0) * 0.25

    # Sweet spot: smaller companies can reprice harder, but avoid total trash.
    if 25_000_000 <= market_cap <= 2_000_000_000:
        score += 3.0
    elif 2_000_000_000 < market_cap <= 10_000_000_000:
        score += 2.0
    elif 10_000_000_000 < market_cap <= MAX_MARKET_CAP:
        score += 0.75

    # User is okay with sub-$1. Give low-priced listed names a slight discovery bump.
    if 0.10 <= price < 1.00:
        score += 0.75
    elif 1.00 <= price < 5.00:
        score += 0.50

    # Recent insider names are always worth testing.
    if insider_priority:
        score += 10.0

    return score


def _fetch_screener_for_exchange(exchange: str) -> List[dict]:
    if not FMP_API_KEY:
        return []

    # Primary filtered attempt.
    params = {
        "apikey": FMP_API_KEY,
        "exchange": exchange,
        "country": "US",
        "isEtf": "false",
        "isFund": "false",
        "isActivelyTrading": "true",
        "limit": SCREENER_LIMIT_PER_EXCHANGE,
    }

    try:
        resp = _polite_get(FMP_SCREENER_URL, params=params)

        if resp.status_code in {401, 403}:
            log.warning("FMP screener auth error %s for %s", resp.status_code, exchange)
            return []

        if resp.status_code != 200:
            log.warning("FMP screener HTTP %s for %s", resp.status_code, exchange)
            return []

        data = resp.json()

        if isinstance(data, dict):
            rows = data.get("data") or data.get("stocks") or data.get("results") or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []

        if rows:
            return rows

    except Exception as e:
        log.warning("FMP screener failed for %s: %s", exchange, e)

    # Fallback simpler attempt in case some filters are not accepted on a free tier.
    fallback_params = {
        "apikey": FMP_API_KEY,
        "exchange": exchange,
        "limit": SCREENER_LIMIT_PER_EXCHANGE,
    }

    try:
        resp = _polite_get(FMP_SCREENER_URL, params=fallback_params)

        if resp.status_code != 200:
            return []

        data = resp.json()

        if isinstance(data, dict):
            return data.get("data") or data.get("stocks") or data.get("results") or []

        if isinstance(data, list):
            return data

    except Exception as e:
        log.warning("FMP fallback screener failed for %s: %s", exchange, e)

    return []


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

        tickers = [_clean_symbol(row["ticker"]) for row in rows if row.get("ticker")]
        return [t for t in tickers if t]

    except Exception as e:
        log.warning("Could not load recent insider tickers: %s", e)
        return []


def build_dynamic_universe(max_size: int = MAX_UNIVERSE_SIZE) -> List[str]:
    if not FMP_API_KEY:
        log.warning("FMP_API_KEY missing; using fallback universe")
        return FALLBACK_UNIVERSE[:max_size]

    recent_insider_tickers = []

    if INCLUDE_RECENT_INSIDERS:
        recent_insider_tickers = load_recent_insider_tickers(
            days=INSIDER_UNIVERSE_LOOKBACK_DAYS
        )

    recent_insider_set = set(recent_insider_tickers)

    all_rows: Dict[str, dict] = {}

    for exchange in UNIVERSE_EXCHANGES:
        rows = _fetch_screener_for_exchange(exchange)

        log.info("Screener returned %s rows for %s", len(rows), exchange)

        for row in rows:
            symbol = _row_symbol(row)

            if not symbol:
                continue

            if symbol not in all_rows:
                all_rows[symbol] = row

    passed = []

    for symbol, row in all_rows.items():
        if _passes_webull_proxy_gate(row):
            passed.append(
                {
                    "symbol": symbol,
                    "row": row,
                    "score": _candidate_score(
                        row,
                        insider_priority=symbol in recent_insider_set,
                    ),
                }
            )

    # Add recent insider tickers even if they were not returned by screener.
    # The worker already tradability-gated them, so they deserve inclusion.
    for symbol in recent_insider_tickers:
        if symbol and symbol not in {x["symbol"] for x in passed}:
            passed.append(
                {
                    "symbol": symbol,
                    "row": {},
                    "score": 10.0,
                }
            )

    passed.sort(key=lambda x: x["score"], reverse=True)

    universe = []
    seen = set()

    for item in passed:
        symbol = item["symbol"]

        if symbol in seen:
            continue

        universe.append(symbol)
        seen.add(symbol)

        if len(universe) >= max_size:
            break

    if not universe:
        log.warning("Dynamic universe empty; using fallback universe")
        return FALLBACK_UNIVERSE[:max_size]

    log.info(
        "Dynamic universe built: %s names. Insider tickers included: %s",
        len(universe),
        [t for t in recent_insider_tickers if t in universe],
    )

    return universe


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(",".join(build_dynamic_universe()))
