#!/usr/bin/env python3
"""
Quiet Money Engine — signal library.

Signals return numeric scores. Higher = better.

Current signal stack:
- momentum_12_1
- volume_pressure_score
- insider_buy_score
- capital_efficiency_score
- relative_strength_score

relative_strength_score is comparative strength vs SPY/QQQ.
It is NOT RSI.
"""

import os
import math
from typing import Any, Dict, List, Optional


def _clamp(value: float, low: float = -3.0, high: float = 3.0) -> float:
    try:
        value = float(value)
    except Exception:
        return 0.0

    return max(low, min(high, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _bars(data: Dict[str, Any]) -> List[dict]:
    bars = data.get("bars") or data.get("price_history") or data.get("history") or []

    if not isinstance(bars, list):
        return []

    clean = []

    for bar in bars:
        if not isinstance(bar, dict):
            continue

        close = _safe_float(bar.get("close"), 0.0)
        volume = _safe_float(bar.get("volume"), 0.0)

        if close <= 0:
            continue

        clean.append(
            {
                "date": bar.get("date"),
                "open": _safe_float(bar.get("open"), close),
                "high": _safe_float(bar.get("high"), close),
                "low": _safe_float(bar.get("low"), close),
                "close": close,
                "volume": volume,
            }
        )

    return clean


def _ticker(data: Dict[str, Any]) -> str:
    return str(data.get("ticker") or data.get("symbol") or "").upper().strip()


def _last_close(data: Dict[str, Any]) -> float:
    bars = _bars(data)

    if not bars:
        return _safe_float(data.get("price") or data.get("last_price") or data.get("close"), 0.0)

    return _safe_float(bars[-1].get("close"), 0.0)


def _return_over_bars(bars: List[dict], lookback: int) -> Optional[float]:
    if len(bars) <= lookback:
        return None

    start = _safe_float(bars[-lookback - 1].get("close"), 0.0)
    end = _safe_float(bars[-1].get("close"), 0.0)

    if start <= 0 or end <= 0:
        return None

    return (end / start) - 1.0


def _benchmark_return(benchmark_bars: List[dict], lookback: int) -> Optional[float]:
    if not benchmark_bars or len(benchmark_bars) <= lookback:
        return None

    start = _safe_float(benchmark_bars[-lookback - 1].get("close"), 0.0)
    end = _safe_float(benchmark_bars[-1].get("close"), 0.0)

    if start <= 0 or end <= 0:
        return None

    return (end / start) - 1.0


def _avg_dollar_volume(bars: List[dict], window: int = 20) -> float:
    if not bars:
        return 0.0

    sample = bars[-window:]

    if not sample:
        return 0.0

    vals = []

    for bar in sample:
        close = _safe_float(bar.get("close"), 0.0)
        volume = _safe_float(bar.get("volume"), 0.0)

        if close > 0 and volume > 0:
            vals.append(close * volume)

    if not vals:
        return 0.0

    return sum(vals) / len(vals)


def _avg_volume(bars: List[dict], window: int = 20) -> float:
    if not bars:
        return 0.0

    sample = bars[-window:]

    vals = [
        _safe_float(bar.get("volume"), 0.0)
        for bar in sample
        if _safe_float(bar.get("volume"), 0.0) > 0
    ]

    if not vals:
        return 0.0

    return sum(vals) / len(vals)


def _split_csv_env(name: str, default: str) -> set:
    raw = os.getenv(name, default)

    return {
        x.strip().upper()
        for x in raw.split(",")
        if x.strip()
    }


OPTIONABLE_PROXY_TICKERS = _split_csv_env(
    "OPTIONABLE_PROXY_TICKERS",
    """
    AAPL,MSFT,NVDA,AMD,INTC,TSLA,META,AMZN,GOOGL,GOOG,
    PLTR,SOFI,RIOT,MARA,HOOD,AFRM,UPST,OPEN,LCID,RIVN,
    IONQ,SOUN,BBAI,ACHR,JOBY,ASTS,RKLB,ENVX,QS,PLUG,FCEL,
    F,GM,BAC,C,CCL,NCLH,UAL,DAL,AAL,RBLX,COIN,SNAP,PINS
    """,
)


def momentum_12_1(data: Dict[str, Any]) -> float:
    """
    Classic 12-minus-1 style momentum.

    Uses roughly 12-month return excluding the most recent month when enough bars exist.
    Falls back to shorter windows for newer/smaller names.
    """
    bars = _bars(data)

    if len(bars) < 45:
        return 0.0

    closes = [_safe_float(bar.get("close"), 0.0) for bar in bars]

    if len(closes) >= 253:
        start = closes[-253]
        end = closes[-22]
        recent = closes[-1]

        if start <= 0 or end <= 0 or recent <= 0:
            return 0.0

        twelve_minus_one = (end / start) - 1.0
        recent_month = (recent / end) - 1.0

        score = (twelve_minus_one * 3.0) + (recent_month * 0.75)
        return _clamp(score, -3.0, 3.0)

    if len(closes) >= 126:
        r = _return_over_bars(bars, 120)
        if r is None:
            return 0.0
        return _clamp(r * 3.0, -3.0, 3.0)

    if len(closes) >= 63:
        r = _return_over_bars(bars, 60)
        if r is None:
            return 0.0
        return _clamp(r * 2.5, -3.0, 3.0)

    r = _return_over_bars(bars, 20)

    if r is None:
        return 0.0

    return _clamp(r * 2.0, -3.0, 3.0)


def volume_pressure_score(data: Dict[str, Any]) -> float:
    """
    Looks for demand/accumulation pressure.

    Rewards:
    - recent volume above baseline
    - recent dollar volume expansion
    - up-volume beating down-volume
    - positive price follow-through

    Penalizes:
    - huge volume with weak/negative follow-through
    - thin or stale volume
    """
    bars = _bars(data)

    if len(bars) < 30:
        return 0.0

    recent = bars[-10:]
    base = bars[-60:-10] if len(bars) >= 70 else bars[:-10]

    if not base:
        return 0.0

    recent_vol = _avg_volume(recent, len(recent))
    base_vol = _avg_volume(base, len(base))

    recent_dv = _avg_dollar_volume(recent, len(recent))
    base_dv = _avg_dollar_volume(base, len(base))

    if base_vol <= 0 or base_dv <= 0:
        return 0.0

    vol_ratio = recent_vol / base_vol
    dollar_vol_ratio = recent_dv / base_dv

    up_volume = 0.0
    down_volume = 0.0

    for i in range(1, len(recent)):
        today = recent[i]
        yesterday = recent[i - 1]

        vol = _safe_float(today.get("volume"), 0.0)
        close_today = _safe_float(today.get("close"), 0.0)
        close_yesterday = _safe_float(yesterday.get("close"), 0.0)

        if close_today >= close_yesterday:
            up_volume += vol
        else:
            down_volume += vol

    up_down_ratio = up_volume / max(down_volume, 1.0)

    r_5 = _return_over_bars(bars, 5) or 0.0
    r_20 = _return_over_bars(bars, 20) or 0.0
    r_60 = _return_over_bars(bars, 60) or 0.0

    score = 0.0

    score += math.log(max(vol_ratio, 0.01)) * 0.65
    score += math.log(max(dollar_vol_ratio, 0.01)) * 0.55
    score += math.log(max(up_down_ratio, 0.01)) * 0.25
    score += r_20 * 1.25
    score += r_5 * 0.50

    if r_60 > 0:
        score += 0.25

    if vol_ratio > 2.5 and r_5 < 0:
        score -= 0.75

    if recent_dv < 250_000:
        score -= 0.75

    return _clamp(score, -3.0, 3.0)


def _extract_insider_count(data: Dict[str, Any]) -> int:
    ticker = _ticker(data)

    possible_keys = [
        "recent_insider_buy_count",
        "insider_buy_count",
        "insider_count",
        "form4_buy_count",
        "recent_form4_buy_count",
    ]

    for key in possible_keys:
        if key in data:
            try:
                return int(data.get(key) or 0)
            except Exception:
                pass

    recent_map = data.get("recent_insider_buys") or data.get("insider_buy_counts")

    if isinstance(recent_map, dict) and ticker:
        try:
            return int(recent_map.get(ticker) or 0)
        except Exception:
            return 0

    buys = data.get("insider_buys")

    if isinstance(buys, list):
        return len(buys)

    return 0


def insider_buy_score(data: Dict[str, Any]) -> float:
    """
    Lightweight insider-buy confirmation score.

    This stays conservative so one insider buy does not dominate the engine.
    """
    count = _extract_insider_count(data)

    if count <= 0:
        return 0.0

    score = 0.45

    if count >= 2:
        score += 0.35

    if count >= 3:
        score += 0.25

    avg_dv = _avg_dollar_volume(_bars(data), 20)

    if avg_dv >= 1_000_000:
        score += 0.10

    return _clamp(score, 0.0, 1.5)


def capital_efficiency_score(data: Dict[str, Any]) -> float:
    """
    Small-account opportunity score.

    Rewards:
    - cheap stocks that are still liquid enough to trade
    - $1-$5 sweet spot
    - $5-$10 secondary zone
    - some larger optionable-proxy names, so the engine does not ignore
      expensive stocks that may be playable through options later

    Penalizes:
    - ultra-cheap dead liquidity
    - under $0.25 names
    - expensive names with no optionability proxy
    """
    ticker = _ticker(data)
    bars = _bars(data)
    price = _last_close(data)
    avg_dv_20 = _avg_dollar_volume(bars, 20)

    if price <= 0:
        return 0.0

    score = 0.0

    if price < 0.25:
        score -= 1.50

    elif price < 1.00:
        score += 0.45

        if avg_dv_20 >= 2_000_000:
            score += 0.65
        elif avg_dv_20 >= 1_000_000:
            score += 0.40
        elif avg_dv_20 >= 500_000:
            score += 0.10
        else:
            score -= 0.85

    elif price <= 5.00:
        score += 1.35

        if avg_dv_20 >= 2_000_000:
            score += 0.35
        elif avg_dv_20 >= 500_000:
            score += 0.15
        else:
            score -= 0.40

    elif price <= 10.00:
        score += 0.95

        if avg_dv_20 >= 1_000_000:
            score += 0.25
        elif avg_dv_20 < 300_000:
            score -= 0.35

    elif price <= 25.00:
        score += 0.25

        if ticker in OPTIONABLE_PROXY_TICKERS:
            score += 0.45

    else:
        if ticker in OPTIONABLE_PROXY_TICKERS:
            score += 0.55
        else:
            score -= 0.25

    if avg_dv_20 >= 20_000_000:
        score += 0.25
    elif avg_dv_20 >= 5_000_000:
        score += 0.15
    elif avg_dv_20 < 250_000:
        score -= 0.75

    if price < 1.00 and avg_dv_20 < 500_000:
        score -= 0.50

    return _clamp(score, -2.0, 2.5)


def relative_strength_score(data: Dict[str, Any]) -> float:
    """
    Comparative relative strength.

    This is NOT RSI.

    It rewards stocks that outperform SPY/QQQ over multiple windows.

    Goal:
    - cheap stock rising faster than the market = good
    - cheap stock holding up while market is weak = good
    - stock rising only because everything is rising = less impressive
    """
    bars = _bars(data)

    if len(bars) < 35:
        return 0.0

    benchmark_bars = data.get("benchmark_bars") or {}
    spy_bars = benchmark_bars.get("SPY") or data.get("spy_bars") or []
    qqq_bars = benchmark_bars.get("QQQ") or data.get("qqq_bars") or []

    if not spy_bars and not qqq_bars:
        return 0.0

    windows = [
        (10, 0.40),
        (20, 0.80),
        (60, 1.10),
        (120, 0.70),
    ]

    score = 0.0
    used = 0.0

    for lookback, weight in windows:
        stock_r = _return_over_bars(bars, lookback)

        if stock_r is None:
            continue

        benchmark_returns = []

        spy_r = _benchmark_return(spy_bars, lookback)
        qqq_r = _benchmark_return(qqq_bars, lookback)

        if spy_r is not None:
            benchmark_returns.append(spy_r)

        if qqq_r is not None:
            benchmark_returns.append(qqq_r)

        if not benchmark_returns:
            continue

        hurdle = max(benchmark_returns)
        excess = stock_r - hurdle

        score += excess * weight * 4.0
        used += weight

    if used <= 0:
        return 0.0

    score = score / used

    r_20 = _return_over_bars(bars, 20) or 0.0

    spy_20 = _benchmark_return(spy_bars, 20)
    qqq_20 = _benchmark_return(qqq_bars, 20)

    benchmark_20s = [x for x in [spy_20, qqq_20] if x is not None]

    if benchmark_20s:
        hurdle_20 = max(benchmark_20s)

        if r_20 > 0 and hurdle_20 < 0:
            score += 0.35

        if r_20 < hurdle_20 and hurdle_20 > 0:
            score -= 0.25

    return _clamp(score, -2.5, 2.5)


SIGNALS = {
    "momentum_12_1": momentum_12_1,
    "insider_buy_score": insider_buy_score,
    "volume_pressure_score": volume_pressure_score,
    "capital_efficiency_score": capital_efficiency_score,
    "relative_strength_score": relative_strength_score,
        }
