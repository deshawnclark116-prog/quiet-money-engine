#!/usr/bin/env python3
"""
Quiet Money Engine - price/volume data layer.

Pulls daily EOD history for the cross-sectional signals. Returns bars
oldest -> newest as [{date, close, volume}, ...].

Provider chain (fails over automatically):
  1. FMP  /stable/historical-price-eod/dividend-adjusted   (primary)
     - adjusted closes so splits/dividends don't create fake momentum jumps
     - free tier serves years of history, ~250 calls/day
  2. Tiingo  /tiingo/daily/{ticker}/prices                 (fallback)
     - only used if FMP fails; needs TIINGO_API_KEY (optional)

The dead /api/v3/ legacy endpoint has been removed - it 403s on any FMP
account created after Aug 31, 2025.
"""
import os
import re
import time
import logging
from datetime import date, timedelta

import requests

log = logging.getLogger("data_layer")

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY", "")  # optional fallback
_MIN_INTERVAL = float(os.getenv("FMP_MIN_INTERVAL", "0.2"))
_last = 0.0
_HTTP_TIMEOUT = 30


def _polite_get(url: str, params: dict = None, headers: dict = None) -> requests.Response:
    global _last
    wait = _MIN_INTERVAL - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    return requests.get(url, params=params or {}, headers=headers or {}, timeout=_HTTP_TIMEOUT)


def _window(days: int) -> tuple[str, str]:
    """A from/to date window padded for weekends/holidays so we clear `days`
    trading days (need ~280 for 12-1 momentum; pad generously)."""
    to = date.today()
    frm = to - timedelta(days=int(days * 1.6) + 40)
    return frm.isoformat(), to.isoformat()


def _normalize(rows: list, days: int) -> list[dict]:
    bars = [
        {"date": r.get("date"),
         "close": float(r.get("adjClose") or r.get("close") or 0),
         "volume": float(r.get("adjVolume") or r.get("volume") or 0)}
        for r in rows if r.get("date")
    ]
    bars = [b for b in bars if b["close"] > 0]
    bars.sort(key=lambda b: b["date"])  # oldest -> newest
    return bars[-days:]


def _from_fmp(ticker: str, days: int) -> list[dict]:
    if not FMP_API_KEY:
        return []
    frm, to = _window(days)
    url = "https://financialmodelingprep.com/stable/historical-price-eod/dividend-adjusted"
    params = {"symbol": ticker, "from": frm, "to": to, "apikey": FMP_API_KEY}
    try:
        r = _polite_get(url, params)
        if r.status_code in (401, 403):
            masked = re.sub(r"apikey=[^&]+", "apikey=***", r.url)
            log.warning("FMP %s on %s -> %s", r.status_code, masked, r.text[:200])
            return []
        if r.status_code != 200:
            log.warning("FMP HTTP %s for %s", r.status_code, ticker)
            return []
        data = r.json()
        # stable returns a flat array; some versions wrap in {"historical":[...]}
        rows = data.get("historical") if isinstance(data, dict) else data
        return _normalize(rows or [], days)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        log.warning("FMP error for %s: %s", ticker, exc)
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
            log.warning("Tiingo HTTP %s for %s", r.status_code, ticker)
            return []
        data = r.json()
        if not isinstance(data, list):  # over-limit returns 200 + text
            log.warning("Tiingo non-list response for %s", ticker)
            return []
        return _normalize(data, days)
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        log.warning("Tiingo error for %s: %s", ticker, exc)
        return []


def get_price_history(ticker: str, days: int = 400) -> list[dict]:
    """Daily bars oldest -> newest, or [] if every provider failed."""
    bars = _from_fmp(ticker, days)
    if bars:
        return bars
    bars = _from_tiingo(ticker, days)  # only runs if TIINGO_API_KEY is set
    if bars:
        log.info("Used Tiingo fallback for %s", ticker)
    return bars
