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
    bars = []

    for r in rows or []:
        if not isinstance(r, dict):
            continue

        d = r.get("date") or r.get("Date")
        if not d:
            continue

        close = _safe_float(
            r.get("adjClose")
            or r.get("adjustedClose")
            or r.get("dividendAdjustedClose")
            or r.get("Close")
            or r.get("close")
        )

        if close <= 0:
            continue

        open_price = _safe_float(r.get("Open") or r.get("open"), close)
        high = _safe_float(r.get("High") or r.get("high"), close)
        low = _safe_float(r.get("Low") or r.get("low"), close)
        volume = _safe_float(r.get("adjVolume") or r.get("Volume") or r.get("volume"), 0)

        bars.append(
            {
                "date": str(d)[:10],
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )

    bars.sort(key=lambda b: b["date"])
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
            "includeAdjustedClose": "true",
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
            adjclose_list = indicators.get("adjclose") or []
            adjclose = adjclose_list[0].get("adjclose") if adjclose_list else []

            rows = []

            opens = quote.get("open") or []
            highs = quote.get("high") or []
            lows = quote.get("low") or []
            closes = quote.get("close") or []
            volumes = quote.get("volume") or []

            for i, ts in enumerate(timestamps):
                close = None

                if i < len(adjclose):
                    close = adjclose[i]

                if close is None and i < len(closes):
                    close = closes[i]

                if close is None:
                    continue

                d = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()

                row = {
                    "date": d,
                    "open": opens[i] if i < len(opens) and opens[i] is not None else close,
                    "high": highs[i] if i < len(highs) and highs[i] is not None else close,
                    "low": lows[i] if i < len(lows) and lows[i] is not None else close,
                    "close": close,
                    "volume": volumes[i] if i < len(volumes) and volumes[i] is not None else 0,
                }

                rows.append(row)

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
