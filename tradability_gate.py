#!/usr/bin/env python3
"""
Quiet Money Engine — Stage 3: tradability gate.

For each buy signal's ticker, enrich it with exchange + market cap + price +
average dollar volume, then ALLOW only Nasdaq / NYSE / NYSE American names that
clear the floors, and BLOCK everything else — OTC, pink, grey, expert market,
delisted, or anything no source can confidently place.

Provider-agnostic with a fallback chain:
  - FMP is the primary (one call returns every field the gate needs).
  - Finnhub is the standby; set FALLBACK_PROVIDERS=finnhub to chain it in.

HARD RULE: if NO source can confidently classify the exchange, the gate BLOCKS.
Better to miss a marginal alert than leak a pink-sheet name.

Env vars (only the two keys are required; everything else has sane defaults):
  FMP_API_KEY               required for the FMP provider
  FINNHUB_API_KEY           required only when finnhub is in the chain
  PRIMARY_PROVIDER=fmp
  FALLBACK_PROVIDERS=       comma list, e.g. "finnhub"; empty = none (dormant)
  TARGET_EXCHANGES=NASDAQ,NYSE,NYSE AMERICAN
  MIN_PRICE=0.10
  MIN_AVG_DOLLAR_VOLUME=250000
  MIN_MARKET_CAP=0
  GATE_CACHE_TTL=3600       seconds to remember a verdict (saves API calls)
"""
import os
import time
import logging

import requests

log = logging.getLogger("tradability_gate")

FMP_API_KEY = os.getenv("FMP_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

PRIMARY_PROVIDER = os.getenv("PRIMARY_PROVIDER", "fmp").strip().lower()
FALLBACK_PROVIDERS = [p.strip().lower() for p in os.getenv("FALLBACK_PROVIDERS", "").split(",") if p.strip()]

TARGET_EXCHANGES = {e.strip().upper() for e in os.getenv("TARGET_EXCHANGES", "NASDAQ,NYSE,NYSE AMERICAN").split(",") if e.strip()}

MIN_PRICE = float(os.getenv("MIN_PRICE", "0.10"))
MIN_AVG_DOLLAR_VOLUME = float(os.getenv("MIN_AVG_DOLLAR_VOLUME", "250000"))
MIN_MARKET_CAP = float(os.getenv("MIN_MARKET_CAP", "0"))
_CACHE_TTL = float(os.getenv("GATE_CACHE_TTL", "3600"))

_HTTP_TIMEOUT = 15
_cache: dict[str, tuple[float, dict]] = {}


# --- Exchange normalization --------------------------------------------------
# Each provider names exchanges differently. Collapse raw strings into ONE
# taxonomy: NASDAQ, NYSE, NYSE AMERICAN, or "" (unknown -> blocked).

def _classify_exchange(raw: str) -> str:
    if not raw:
        return ""
    s = raw.upper()
    # NYSE American (formerly AMEX) FIRST, since "NYSE AMERICAN" contains "NYSE".
    if "AMERICAN" in s or "NYSE AMER" in s or "NYSE MKT" in s or s in ("AMEX", "ASE"):
        return "NYSE AMERICAN"
    if "NASDAQ" in s or s in ("NMS", "NCM", "NGM", "NGS", "XNAS"):
        return "NASDAQ"
    if "NYSE" in s or s in ("NYQ", "XNYS") or "NEW YORK STOCK EXCHANGE" in s:
        return "NYSE"
    return ""  # OTC, OTCQX/QB, pink, grey, expert market, foreign, or blank


# --- Providers: each returns a normalized dict or None -----------------------

def _provider_fmp(ticker: str) -> dict | None:
    if not FMP_API_KEY:
        return None
    # FMP migrated endpoints; try newer 'stable' path, then legacy v3. Both carry
    # the same fields, so whichever the key is provisioned for will answer.
    urls = [
        f"https://financialmodelingprep.com/stable/profile?symbol={ticker}&apikey={FMP_API_KEY}",
        f"https://financialmodelingprep.com/api/v3/profile/{ticker}?apikey={FMP_API_KEY}",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=_HTTP_TIMEOUT)
            if resp.status_code == 401 or resp.status_code == 403:
                log.warning("FMP rejected the key (HTTP %s) — check FMP_API_KEY", resp.status_code)
                return None
            if resp.status_code != 200:
                continue
            data = resp.json()
            if not data:
                continue
            row = data[0] if isinstance(data, list) else data
            return {
                "exchange_raw": row.get("exchangeShortName") or row.get("exchange") or "",
                "market_cap": float(row.get("mktCap") or row.get("marketCap") or 0),
                "price": float(row.get("price") or 0),
                "avg_volume": float(row.get("volAvg") or row.get("averageVolume") or 0),
                "active": row.get("isActivelyTrading", True),
                "source": "fmp",
            }
        except (requests.RequestException, ValueError, KeyError, IndexError):
            continue
    return None


def _provider_finnhub(ticker: str) -> dict | None:
    if not FINNHUB_API_KEY:
        return None
    base = "https://finnhub.io/api/v1"
    try:
        prof = requests.get(f"{base}/stock/profile2", params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=_HTTP_TIMEOUT)
        if prof.status_code in (401, 403):
            log.warning("Finnhub rejected the key (HTTP %s)", prof.status_code)
            return None
        if prof.status_code != 200:
            return None
        p = prof.json() or {}
        if not p.get("exchange") and not p.get("ticker"):
            return None
        quote = requests.get(f"{base}/quote", params={"symbol": ticker, "token": FINNHUB_API_KEY}, timeout=_HTTP_TIMEOUT)
        q = quote.json() if quote.status_code == 200 else {}
        return {
            "exchange_raw": p.get("exchange", ""),
            "market_cap": float(p.get("marketCapitalization") or 0) * 1_000_000,  # Finnhub reports millions
            "price": float(q.get("c") or 0),
            "avg_volume": 0.0,  # not on Finnhub free; gate skips the $-volume floor when unknown
            "active": True,
            "source": "finnhub",
        }
    except (requests.RequestException, ValueError, KeyError):
        return None


_PROVIDERS = {"fmp": _provider_fmp, "finnhub": _provider_finnhub}


def _enrich(ticker: str) -> dict | None:
    """Walk the provider chain; return the first that places it on a real
    exchange, else the last data seen (so the gate can report the raw value)."""
    last = None
    for name in [PRIMARY_PROVIDER, *FALLBACK_PROVIDERS]:
        fn = _PROVIDERS.get(name)
        if not fn:
            log.warning("Unknown provider '%s' in chain; skipping", name)
            continue
        data = fn(ticker)
        if not data:
            continue
        last = data
        if _classify_exchange(data.get("exchange_raw", "")):
            return data
    return last


# --- The gate ----------------------------------------------------------------

def _evaluate_uncached(ticker: str) -> dict:
    result = {"ticker": ticker, "allowed": False, "reason": "", "exchange": "",
              "market_cap": 0.0, "price": 0.0, "avg_dollar_volume": 0.0, "source": ""}
    if not ticker:
        result["reason"] = "no ticker on filing"
        return result

    data = _enrich(ticker)
    if not data:
        result["reason"] = "no source returned data (unknown ticker / bad key / provider down)"
        return result  # fail closed

    result["source"] = data.get("source", "")
    result["price"] = data.get("price", 0.0)
    result["market_cap"] = data.get("market_cap", 0.0)
    result["avg_dollar_volume"] = data.get("price", 0.0) * data.get("avg_volume", 0.0)
    exch = _classify_exchange(data.get("exchange_raw", ""))
    result["exchange"] = exch or (data.get("exchange_raw") or "unknown")

    # 1) Exchange must be one we target. This is the hard anti-junk rule.
    if exch not in TARGET_EXCHANGES:
        result["reason"] = f"exchange not allowed ({result['exchange']})"
        return result
    # 2) Delisted-leftover guard.
    if data.get("active") is False:
        result["reason"] = "not actively trading"
        return result
    # 3) Price floor.
    if result["price"] < MIN_PRICE:
        result["reason"] = f"price ${result['price']:.4f} below floor ${MIN_PRICE:.2f}"
        return result
    # 4) Market-cap floor (only if you set one).
    if MIN_MARKET_CAP > 0 and result["market_cap"] < MIN_MARKET_CAP:
        result["reason"] = f"market cap ${result['market_cap']:,.0f} below floor ${MIN_MARKET_CAP:,.0f}"
        return result
    # 5) Dollar-volume floor — enforced only when we actually have a volume.
    if data.get("avg_volume", 0) > 0 and result["avg_dollar_volume"] < MIN_AVG_DOLLAR_VOLUME:
        result["reason"] = f"avg $ volume ${result['avg_dollar_volume']:,.0f} below floor ${MIN_AVG_DOLLAR_VOLUME:,.0f}"
        return result

    result["allowed"] = True
    note = "" if data.get("avg_volume", 0) > 0 else " ($-volume unmeasured by this source)"
    result["reason"] = f"OK on {exch}{note}"
    return result


def evaluate(ticker: str) -> dict:
    """Public entry: allow/block verdict for a ticker, with a TTL cache so the
    same name in one cycle (or nearby cycles) doesn't burn repeated API calls."""
    now = time.monotonic()
    hit = _cache.get(ticker)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    verdict = _evaluate_uncached(ticker)
    _cache[ticker] = (now, verdict)
    return verdict
