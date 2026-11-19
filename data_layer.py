#!/usr/bin/env python3
"""
Quiet Money Engine - price/volume data layer.

Returns daily bars oldest -> newest as:
[{date, open, high, low, close, volume}, ...]

Provider chain:
  1. Stooq  - free CSV historical data
  2. Yahoo  - free chart endpoint fallback, no key
  3. FMP    - optional, but may 402 on free plan
  4. Tiingo - optional fallback if TIINGO_API_KEY is set

Recommended for dynamic universe:
PRICE_PROVIDER_ORDER=stooq,yahoo
"""
import os
import re
import csv
import io
import time
import logging
import math
from datetime import date, datetime, timedelta, timezone

import requests

log = logging.getLogger("data_layer")

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "")

PRICE_PROVIDER_ORDER = [
    x.strip().lower()
    for x in os.getenv("PRICE_PROVIDER_ORDER", "stooq,yahoo").split(",")
    if x.strip()
]

_MIN_INTERVAL = float(os.getenv("FMP_MIN_INTERVAL", "0.2"))
_last = 0.0
_HTTP_TIMEOUT = 30

LOG_PRICE_MISSES = os.getenv("LOG_PRICE_MISSES", "false").lower() in {
    "1",
    "true",
    "yes",
    "y",
}


def _polite_get(url: str, params: dict = None, headers: dict = None) -> requests.Response:
    global _last

    wait = _MIN_INTERVAL - (time.monotonic() - _last)

    if wait > 0:
        time.sleep(wait)

    _last = time.monotonic()

    return requests.get(url, params=params or {}, headers=headers or {}, timeout=_HTTP_TIMEOUT)


def _window(days: int) -> tuple[str, str]:
    to = date.today()
    frm = to - timedelta(days=int(days * 1.8) + 60)
    return frm.isoformat(), to.isoformat()


def _yyyymmdd(value: str) -> str:
    return value.replace("-", "")


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _normalize(rows: list, days: int) -> list[dict]:
    """Return ONLY internally consistent real OHLCV bars.

    Do not mix dividend-adjusted closes with quote/raw OHLC. That had
    silently invalidated hundreds of SPY and equity history bars, skewing
    historic momentum / simulated intraday stops. Never invent OHLC values
    for missing fields: daily-bar execution requires genuine high/low/open.
    """
    bars = []

    def field(row, *keys):
        for key in keys:
            value = row.get(key)
            if value is not None and value != "":
                return value
        return None

    for r in rows or []:
        if not isinstance(r, dict):
            continue

        d = field(r, "date", "Date")
        if not d:
            continue

        # Prefer complete raw (or split-adjusted-by-provider) OHLC. Yahoo's
        # adjclose is dividend-adjusted but quote open/high/low is NOT.
        raw = [field(r, "Open", "open"), field(r, "High", "high"),
               field(r, "Low", "low"), field(r, "Close", "close")]
        adj = [field(r, "adjOpen", "adjustedOpen"),
               field(r, "adjHigh", "adjustedHigh"),
               field(r, "adjLow", "adjustedLow"),
               field(r, "adjClose", "adjustedClose", "dividendAdjustedClose")]
        source = raw if all(x is not None for x in raw) else (
            adj if all(x is not None for x in adj) else None)
        if source is None:
            continue
        try:
            op, high, low, close = [float(x) for x in source]
            volume_raw = field(r, "Volume", "volume", "adjVolume")
            volume = float(volume_raw) if volume_raw is not None else 0.0
            date.fromisoformat(str(d)[:10])
        except (ValueError, TypeError, OverflowError):
            continue
        if (not all(math.isfinite(x) for x in (op,high,low,close,volume))
                or low<=0 or volume<0
                or not low <= min(op,close) <= max(op,close) <= high):
            continue

        bars.append({
            "date":str(d)[:10],
            "open":op,"high":high,"low":low,"close":close,"volume":volume
        })

    bars.sort(key=lambda bar:bar["date"])
    return bars[-days:]


def _from_stooq(ticker: str, days: int) -> list[dict]:
    frm, to = _window(days)

    symbol = ticker.lower().replace("-", ".")
    if not symbol.endswith(".us"):
        symbol = f"{symbol}.us"

    url = "https://stooq.com/q/d/l/"
    params = {
        "s": symbol,
        "d1": _yyyymmdd(frm),
        "d2": _yyyymmdd(to),
        "i": "d",
    }

    try:
        r = _polite_get(url, params=params)

        if r.status_code != 200:
            if LOG_PRICE_MISSES:
                log.info("Stooq HTTP %s for %s", r.status_code, ticker)
            return []

        text = r.text.strip()

        if not text or "No data" in text or text.lower().startswith("<html"):
            return []

        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)

        return _normalize(rows, days)

    except Exception as exc:
        if LOG_PRICE_MISSES:
            log.info("Stooq error for %s: %s", ticker, exc)
        return []


def _from_yahoo(ticker: str, days: int) -> list[dict]:
    """
    Best-effort no-key fallback.

    Yahoo sometimes uses '-' instead of '.' for share classes.
    """
    frm, to = _window(days)

    start_dt = datetime.fromisoformat(frm).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(to).replace(tzinfo=timezone.utc) + timedelta(days=1)

    period1 = int(start_dt.timestamp())
    period2 = int(end_dt.timestamp())

    candidates = []

    clean = ticker.upper().strip()
    candidates.append(clean)

    if "." in clean:
        candidates.append(clean.replace(".", "-"))

    if "-" in clean:
        candidates.append(clean.replace("-", "."))

    seen = set()

    for symbol in candidates:
        if symbol in seen:
            continue

        seen.add(symbol)

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "period1": period1,
            "period2": period2,
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "false",
        }

        headers = {
            "User-Agent": "Mozilla/5.0 QuietMoneyEngine/1.0",
        }

        try:
            r = _polite_get(url, params=params, headers=headers)

            if r.status_code != 200:
                continue

            data = r.json()

            chart = data.get("chart") or {}
            result = chart.get("result") or []

            if not result:
                continue

            payload = result[0]
            timestamps = payload.get("timestamp") or []
            indicators = payload.get("indicators") or {}
            quote_list = indicators.get("quote") or []

            if not quote_list:
                continue

            quote = quote_list[0]
            rows = []

            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            for i, ts in enumerate(timestamps):
                # The quote close is from the SAME price basis as the quote
                # open/high/low. Dividend-adjusted adjclose is deliberately
                # ignored to avoid false OHLC ranges and stop-price triggers.
                if not all(i < len(v) and v[i] is not None
                           for v in (opens, highs, lows, closes)):
                    continue
                try:
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                except (OverflowError, ValueError, TypeError, OSError):
                    continue
                rows.append({
                    "date":d,
                    "open":opens[i],"high":highs[i],
                    "low":lows[i],"close":closes[i],
                    "volume":volumes[i] if i<len(volumes) and volumes[i] is not None else 0,
                })

            bars = _normalize(rows, days)

            if bars:
                return bars

        except Exception as exc:
            if LOG_PRICE_MISSES:
                log.info("Yahoo error for %s: %s", ticker, exc)
            continue

    return []


def _from_fmp(ticker: str, days: int) -> list[dict]:
    if not FMP_API_KEY:
        return []

    frm, to = _window(days)
    url = "https://financialmodelingprep.com/stable/historical-price-eod/dividend-adjusted"
    params = {"symbol": ticker, "from": frm, "to": to, "apikey": FMP_API_KEY}

    try:
        r = _polite_get(url, params)

        if r.status_code in (401, 402, 403):
            if LOG_PRICE_MISSES:
                masked = re.sub(r"apikey=[^&]+", "apikey=***", r.url)
                log.info("FMP %s on %s -> %s", r.status_code, masked, r.text[:120])
            return []

        if r.status_code != 200:
            if LOG_PRICE_MISSES:
                log.info("FMP HTTP %s for %s", r.status_code, ticker)
            return []

        data = r.json()
        rows = data.get("historical") if isinstance(data, dict) else data

        return _normalize(rows or [], days)

    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        if LOG_PRICE_MISSES:
            log.info("FMP error for %s: %s", ticker, exc)
        return []


def _from_tiingo(ticker: str, days: int) -> list[dict]:
    if not TIINGO_API_KEY:
        return []

    frm, _ = _window(days)
    url = f"https://api.tiingo.com/tiingo/daily/{ticker}/prices"
    params = {"startDate": frm, "token": TIINGO_API_KEY}

    try:
        r = _polite_get(url, params, headers={"Content-Type": "application/json"})

        if r.status_code != 200:
            if LOG_PRICE_MISSES:
                log.info("Tiingo HTTP %s for %s", r.status_code, ticker)
            return []

        data = r.json()

        if not isinstance(data, list):
            if LOG_PRICE_MISSES:
                log.info("Tiingo non-list response for %s", ticker)
            return []

        return _normalize(data, days)

    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        if LOG_PRICE_MISSES:
            log.info("Tiingo error for %s: %s", ticker, exc)
        return []


def get_price_history(ticker: str, days: int = 400) -> list[dict]:
    providers = {
        "stooq": _from_stooq,
        "yahoo": _from_yahoo,
        "fmp": _from_fmp,
        "tiingo": _from_tiingo,
    }

    tried = []

    for provider_name in PRICE_PROVIDER_ORDER:
        fn = providers.get(provider_name)

        if not fn:
            continue

        tried.append(provider_name)

        bars = fn(ticker, days)

        if bars:
            if provider_name != "fmp":
                log.info("Used %s price history for %s", provider_name, ticker)
            return bars

    if LOG_PRICE_MISSES:
        log.info("No price history for %s after providers=%s", ticker, tried)

    return []
