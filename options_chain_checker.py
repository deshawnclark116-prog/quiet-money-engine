#!/usr/bin/env python3
"""
Quiet Money Engine — options chain checker.

Purpose:
Identify whether a bigger/more expensive stock is still small-account playable
through affordable, liquid options.

This file does NOT change rankings yet.
It is a standalone checker for Step 3.8B.

Example:
python options_chain_checker.py INTC AMD RIOT OPEN SOFI PLTR F GM

Environment knobs:
OPTIONS_DTE_MIN=21
OPTIONS_DTE_MAX=75
OPTIONS_DTE_IDEAL_MIN=30
OPTIONS_DTE_IDEAL_MAX=60
MAX_OPTION_PREMIUM=1.50
MIN_OPTION_OPEN_INTEREST=100
MIN_OPTION_VOLUME=10
MAX_BID_ASK_SPREAD_PCT=0.25
MIN_OPTION_DELTA=0.25
MAX_OPTION_DELTA=0.70
"""

import os
import sys
import math
import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

YAHOO_OPTIONS_URL = "https://query2.finance.yahoo.com/v7/finance/options/{ticker}"

OPTIONS_DTE_MIN = int(os.getenv("OPTIONS_DTE_MIN", "21"))
OPTIONS_DTE_MAX = int(os.getenv("OPTIONS_DTE_MAX", "75"))
OPTIONS_DTE_IDEAL_MIN = int(os.getenv("OPTIONS_DTE_IDEAL_MIN", "30"))
OPTIONS_DTE_IDEAL_MAX = int(os.getenv("OPTIONS_DTE_IDEAL_MAX", "60"))

MAX_OPTION_PREMIUM = float(os.getenv("MAX_OPTION_PREMIUM", "1.50"))
MIN_OPTION_OPEN_INTEREST = int(os.getenv("MIN_OPTION_OPEN_INTEREST", "100"))
MIN_OPTION_VOLUME = int(os.getenv("MIN_OPTION_VOLUME", "10"))
MAX_BID_ASK_SPREAD_PCT = float(os.getenv("MAX_BID_ASK_SPREAD_PCT", "0.25"))

MIN_OPTION_DELTA = float(os.getenv("MIN_OPTION_DELTA", "0.25"))
MAX_OPTION_DELTA = float(os.getenv("MAX_OPTION_DELTA", "0.70"))

RISK_FREE_RATE = float(os.getenv("OPTIONS_RISK_FREE_RATE", "0.04"))
DEFAULT_IV = float(os.getenv("OPTIONS_DEFAULT_IV", "0.80"))

REQUEST_SLEEP_SECONDS = float(os.getenv("OPTIONS_REQUEST_SLEEP_SECONDS", "0.25"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 QuietMoneyEngine/1.0",
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


def today_utc_date():
    return datetime.now(timezone.utc).date()


def unix_to_date(ts: int):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date()


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def approximate_call_delta(
    stock_price: float,
    strike: float,
    dte: int,
    implied_volatility: float,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """
    Black-Scholes style approximate call delta.

    This is only a filter estimate, not a trading model.
    """
    if stock_price <= 0 or strike <= 0 or dte <= 0:
        return 0.0

    sigma = implied_volatility if implied_volatility and implied_volatility > 0 else DEFAULT_IV

    # Yahoo IV is usually already decimal form, e.g. 0.85 = 85%.
    if sigma > 5:
        sigma = sigma / 100.0

    sigma = max(0.05, min(sigma, 5.0))
    t = max(dte / 365.0, 1.0 / 365.0)

    try:
        d1 = (
            math.log(stock_price / strike)
            + (risk_free_rate + 0.5 * sigma * sigma) * t
        ) / (sigma * math.sqrt(t))

        return norm_cdf(d1)
    except Exception:
        return 0.0


def fetch_options_payload(ticker: str, expiration_ts: Optional[int] = None) -> Optional[dict]:
    ticker = ticker.upper().strip()
    url = YAHOO_OPTIONS_URL.format(ticker=ticker)

    params = {}

    if expiration_ts:
        params["date"] = int(expiration_ts)

    try:
        time.sleep(REQUEST_SLEEP_SECONDS)
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)

        if resp.status_code != 200:
            logging.warning(
                "%s options fetch failed HTTP %s",
                ticker,
                resp.status_code,
            )
            return None

        data = resp.json()

        chart = data.get("optionChain") or {}
        result = chart.get("result") or []

        if not result:
            return None

        return result[0]

    except Exception as exc:
        logging.warning("%s options fetch error: %s", ticker, exc)
        return None


def get_underlying_price(payload: dict) -> float:
    quote = payload.get("quote") or {}

    for key in [
        "regularMarketPrice",
        "postMarketPrice",
        "preMarketPrice",
        "bid",
        "ask",
        "previousClose",
    ]:
        value = safe_float(quote.get(key), 0.0)
        if value > 0:
            return value

    return 0.0


def get_expiration_timestamps(payload: dict) -> List[int]:
    expirations = payload.get("expirationDates") or []

    clean = []

    for ts in expirations:
        try:
            clean.append(int(ts))
        except Exception:
            pass

    return sorted(set(clean))


def option_mid(bid: float, ask: float, last: float) -> float:
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0

    if ask > 0:
        return ask

    if last > 0:
        return last

    if bid > 0:
        return bid

    return 0.0


def spread_pct(bid: float, ask: float, mid: float) -> float:
    if bid <= 0 or ask <= 0 or mid <= 0:
        return 9.99

    return (ask - bid) / mid


def dte_score(dte: int) -> float:
    if dte < OPTIONS_DTE_MIN or dte > OPTIONS_DTE_MAX:
        return -2.0

    if OPTIONS_DTE_IDEAL_MIN <= dte <= OPTIONS_DTE_IDEAL_MAX:
        return 1.0

    if dte < OPTIONS_DTE_IDEAL_MIN:
        return 0.45

    return 0.65


def premium_score(ask: float, mid: float) -> float:
    premium = ask if ask > 0 else mid

    if premium <= 0:
        return -2.0

    if premium <= MAX_OPTION_PREMIUM:
        if premium < 0.10:
            return 0.25
        if premium <= 0.75:
            return 1.0
        return 0.85

    if premium <= MAX_OPTION_PREMIUM * 1.5:
        return 0.20

    return -1.0


def liquidity_score(open_interest: int, volume: int, spct: float) -> float:
    score = 0.0

    if open_interest >= 1000:
        score += 1.0
    elif open_interest >= 500:
        score += 0.75
    elif open_interest >= MIN_OPTION_OPEN_INTEREST:
        score += 0.40
    else:
        score -= 0.75

    if volume >= 500:
        score += 0.75
    elif volume >= 100:
        score += 0.50
    elif volume >= MIN_OPTION_VOLUME:
        score += 0.25
    else:
        score -= 0.35

    if spct <= 0.10:
        score += 0.75
    elif spct <= MAX_BID_ASK_SPREAD_PCT:
        score += 0.35
    else:
        score -= 1.0

    return score


def moneyness_score(stock_price: float, strike: float) -> float:
    if stock_price <= 0 or strike <= 0:
        return -2.0

    ratio = strike / stock_price

    # For bullish calls, prefer ATM to moderately OTM.
    if 0.95 <= ratio <= 1.08:
        return 1.0

    if 0.90 <= ratio < 0.95:
        return 0.55

    if 1.08 < ratio <= 1.15:
        return 0.45

    if 1.15 < ratio <= 1.25:
        return -0.25

    return -1.0


def delta_score(delta: float) -> float:
    if MIN_OPTION_DELTA <= delta <= MAX_OPTION_DELTA:
        if 0.35 <= delta <= 0.60:
            return 1.0
        return 0.55

    if 0.15 <= delta < MIN_OPTION_DELTA:
        return -0.25

    if MAX_OPTION_DELTA < delta <= 0.85:
        return 0.10

    return -0.75


def score_call_contract(
    ticker: str,
    stock_price: float,
    expiration_ts: int,
    contract: dict,
) -> Optional[dict]:
    exp_date = unix_to_date(expiration_ts)
    dte = (exp_date - today_utc_date()).days

    if dte < OPTIONS_DTE_MIN or dte > OPTIONS_DTE_MAX:
        return None

    strike = safe_float(contract.get("strike"), 0.0)
    bid = safe_float(contract.get("bid"), 0.0)
    ask = safe_float(contract.get("ask"), 0.0)
    last = safe_float(contract.get("lastPrice"), 0.0)
    volume = safe_int(contract.get("volume"), 0)
    open_interest = safe_int(contract.get("openInterest"), 0)
    iv = safe_float(contract.get("impliedVolatility"), 0.0)

    if strike <= 0 or stock_price <= 0:
        return None

    mid = option_mid(bid, ask, last)
    spct = spread_pct(bid, ask, mid)
    premium = ask if ask > 0 else mid

    if premium <= 0:
        return None

    delta = approximate_call_delta(
        stock_price=stock_price,
        strike=strike,
        dte=dte,
        implied_volatility=iv,
    )

    score = 0.0
    score += dte_score(dte) * 0.75
    score += premium_score(ask, mid) * 0.90
    score += liquidity_score(open_interest, volume, spct) * 1.00
    score += moneyness_score(stock_price, strike) * 0.80
    score += delta_score(delta) * 0.75

    hard_pass = (
        premium <= MAX_OPTION_PREMIUM
        and open_interest >= MIN_OPTION_OPEN_INTEREST
        and volume >= MIN_OPTION_VOLUME
        and spct <= MAX_BID_ASK_SPREAD_PCT
        and MIN_OPTION_DELTA <= delta <= MAX_OPTION_DELTA
        and OPTIONS_DTE_MIN <= dte <= OPTIONS_DTE_MAX
    )

    soft_pass = (
        premium <= MAX_OPTION_PREMIUM * 1.25
        and open_interest >= MIN_OPTION_OPEN_INTEREST
        and spct <= MAX_BID_ASK_SPREAD_PCT
        and MIN_OPTION_DELTA <= delta <= MAX_OPTION_DELTA
        and OPTIONS_DTE_MIN <= dte <= OPTIONS_DTE_MAX
        and score >= 2.5
    )

    playable = hard_pass or soft_pass

    return {
        "ticker": ticker,
        "contract_symbol": contract.get("contractSymbol") or "",
        "expiration": exp_date.isoformat(),
        "dte": dte,
        "stock_price": stock_price,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "premium": premium,
        "estimated_cost": premium * 100.0,
        "spread_pct": spct,
        "volume": volume,
        "open_interest": open_interest,
        "implied_volatility": iv,
        "approx_delta": delta,
        "score": score,
        "playable": playable,
    }


def check_options_playability(ticker: str, max_contracts_to_return: int = 5) -> dict:
    ticker = ticker.upper().strip()

    base_payload = fetch_options_payload(ticker)

    if not base_payload:
        return {
            "ticker": ticker,
            "has_options": False,
            "playable": False,
            "reason": "no options payload",
            "best": None,
            "candidates": [],
        }

    stock_price = get_underlying_price(base_payload)

    if stock_price <= 0:
        return {
            "ticker": ticker,
            "has_options": True,
            "playable": False,
            "reason": "missing underlying price",
            "best": None,
            "candidates": [],
        }

    expirations = get_expiration_timestamps(base_payload)

    if not expirations:
        return {
            "ticker": ticker,
            "has_options": True,
            "playable": False,
            "reason": "no expirations",
            "best": None,
            "candidates": [],
        }

    today = today_utc_date()

    target_expirations = []

    for ts in expirations:
        exp = unix_to_date(ts)
        dte = (exp - today).days

        if OPTIONS_DTE_MIN <= dte <= OPTIONS_DTE_MAX:
            target_expirations.append(ts)

    if not target_expirations:
        return {
            "ticker": ticker,
            "has_options": True,
            "playable": False,
            "reason": "no expirations in target DTE window",
            "best": None,
            "candidates": [],
        }

    all_candidates = []

    for ts in target_expirations:
        payload = fetch_options_payload(ticker, ts)

        if not payload:
            continue

        options = payload.get("options") or []

        if not options:
            continue

        calls = options[0].get("calls") or []

        for contract in calls:
            scored = score_call_contract(
                ticker=ticker,
                stock_price=stock_price,
                expiration_ts=ts,
                contract=contract,
            )

            if scored:
                all_candidates.append(scored)

    all_candidates.sort(
        key=lambda x: (
            1 if x["playable"] else 0,
            x["score"],
            -x["spread_pct"],
            x["open_interest"],
        ),
        reverse=True,
    )

    best = all_candidates[0] if all_candidates else None

    if not best:
        return {
            "ticker": ticker,
            "has_options": True,
            "playable": False,
            "reason": "no call contracts scored",
            "best": None,
            "candidates": [],
        }

    playable = bool(best["playable"])

    return {
        "ticker": ticker,
        "has_options": True,
        "playable": playable,
        "reason": "playable contract found" if playable else "options exist but no contract passed guardrails",
        "best": best,
        "candidates": all_candidates[:max_contracts_to_return],
    }


def format_money(value: float) -> str:
    try:
        return f"${float(value):.2f}"
    except Exception:
        return "$0.00"


def format_pct(value: float) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "0.0%"


def print_result(result: dict):
    ticker = result["ticker"]

    print("")
    print("=" * 100)
    print(f"{ticker} OPTIONS PLAYABILITY")
    print("=" * 100)

    print(f"has_options: {result.get('has_options')}")
    print(f"playable:    {result.get('playable')}")
    print(f"reason:      {result.get('reason')}")

    best = result.get("best")

    if not best:
        return

    print("")
    print("Best contract:")
    print(f"contract:    {best['contract_symbol']}")
    print(f"expiration:  {best['expiration']} ({best['dte']} DTE)")
    print(f"stock price: {format_money(best['stock_price'])}")
    print(f"strike:      {format_money(best['strike'])}")
    print(f"bid/ask:     {format_money(best['bid'])} / {format_money(best['ask'])}")
    print(f"premium:     {format_money(best['premium'])}")
    print(f"est. cost:   {format_money(best['estimated_cost'])}")
    print(f"spread:      {format_pct(best['spread_pct'])}")
    print(f"volume:      {best['volume']}")
    print(f"open int:    {best['open_interest']}")
    print(f"approx delta:{best['approx_delta']:.2f}")
    print(f"score:       {best['score']:.2f}")

    candidates = result.get("candidates") or []

    if len(candidates) > 1:
        print("")
        print("Top candidates:")
        print("exp        dte  strike  premium  cost    spread  vol   oi     delta  score  playable")
        print("-" * 100)

        for c in candidates:
            print(
                f"{c['expiration']} "
                f"{str(c['dte']).rjust(3)} "
                f"{format_money(c['strike']).rjust(7)} "
                f"{format_money(c['premium']).rjust(8)} "
                f"{format_money(c['estimated_cost']).rjust(7)} "
                f"{format_pct(c['spread_pct']).rjust(7)} "
                f"{str(c['volume']).rjust(5)} "
                f"{str(c['open_interest']).rjust(6)} "
                f"{c['approx_delta']:.2f} "
                f"{c['score']:.2f} "
                f"{c['playable']}"
            )


def main():
    tickers = [x.upper().strip() for x in sys.argv[1:] if x.strip()]

    if not tickers:
        tickers = ["INTC", "AMD", "RIOT", "OPEN", "SOFI", "PLTR", "F", "GM"]

    print("Options checker settings:")
    print(f"DTE window:       {OPTIONS_DTE_MIN}-{OPTIONS_DTE_MAX}")
    print(f"Ideal DTE:        {OPTIONS_DTE_IDEAL_MIN}-{OPTIONS_DTE_IDEAL_MAX}")
    print(f"Max premium:      {format_money(MAX_OPTION_PREMIUM)}")
    print(f"Min OI:           {MIN_OPTION_OPEN_INTEREST}")
    print(f"Min volume:       {MIN_OPTION_VOLUME}")
    print(f"Max spread pct:   {format_pct(MAX_BID_ASK_SPREAD_PCT)}")
    print(f"Delta window:     {MIN_OPTION_DELTA}-{MAX_OPTION_DELTA}")

    for ticker in tickers:
        result = check_options_playability(ticker)
        print_result(result)


if __name__ == "__main__":
    main()
