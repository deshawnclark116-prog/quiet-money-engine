#!/usr/bin/env python3
"""
Quiet Money Engine — signal library.

Signals return numeric scores. Higher = better.

Market / behavior signals:
- momentum_12_1
- insider_buy_score
- volume_pressure_score
- capital_efficiency_score
- relative_strength_score

Technical quality stack:
- accumulation_quality_score
- trend_quality_score
- breakout_setup_score
- liquidity_quality_score
- volatility_control_score

relative_strength_score compares against SPY/QQQ.
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


def _moving_average(values: List[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None

    sample = values[-window:]

    if not sample:
        return None

    return sum(sample) / len(sample)


def _stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0

    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)

    return math.sqrt(max(var, 0.0))


def _daily_returns(bars: List[dict], window: int = 20) -> List[float]:
    if len(bars) < 2:
        return []

    sample = bars[-(window + 1):]

    returns = []

    for i in range(1, len(sample)):
        prev = _safe_float(sample[i - 1].get("close"), 0.0)
        cur = _safe_float(sample[i].get("close"), 0.0)

        if prev > 0 and cur > 0:
            returns.append((cur / prev) - 1.0)

    return returns


def _close_location_value(bar: dict) -> float:
    high = _safe_float(bar.get("high"), 0.0)
    low = _safe_float(bar.get("low"), 0.0)
    close = _safe_float(bar.get("close"), 0.0)

    if high <= low or close <= 0:
        return 0.5

    return max(0.0, min(1.0, (close - low) / (high - low)))


def _true_range_pct(bars: List[dict], window: int = 20) -> float:
    if len(bars) < 2:
        return 0.0

    sample = bars[-window:]
    vals = []

    for i, bar in enumerate(sample):
        high = _safe_float(bar.get("high"), 0.0)
        low = _safe_float(bar.get("low"), 0.0)
        close = _safe_float(bar.get("close"), 0.0)

        if high <= 0 or low <= 0 or close <= 0:
            continue

        vals.append((high - low) / close)

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
    Comparative relative strength vs SPY/QQQ.
    This is NOT RSI.
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


def accumulation_quality_score(data: Dict[str, Any]) -> float:
    """
    Detects clean accumulation.

    Rewards:
    - up days on stronger volume
    - down days on lighter volume
    - closes near the high of the daily range
    - steady recent demand

    Penalizes:
    - heavy-volume down days
    - ugly closing location
    - blowoff-style volume without follow-through
    """
    bars = _bars(data)

    if len(bars) < 30:
        return 0.0

    recent = bars[-20:]
    base = bars[-80:-20] if len(bars) >= 90 else bars[:-20]

    if len(recent) < 10 or not base:
        return 0.0

    up_volume = 0.0
    down_volume = 0.0
    up_days = 0
    down_days = 0
    clv_values = []
    heavy_red_days = 0

    avg_recent_vol = _avg_volume(recent, len(recent))

    for i in range(1, len(recent)):
        today = recent[i]
        yesterday = recent[i - 1]

        close_today = _safe_float(today.get("close"), 0.0)
        close_yesterday = _safe_float(yesterday.get("close"), 0.0)
        volume = _safe_float(today.get("volume"), 0.0)
        clv = _close_location_value(today)

        clv_values.append(clv)

        if close_today >= close_yesterday:
            up_volume += volume
            up_days += 1
        else:
            down_volume += volume
            down_days += 1

            if avg_recent_vol > 0 and volume > avg_recent_vol * 1.5 and clv < 0.35:
                heavy_red_days += 1

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else 0.5

    up_down_volume_ratio = up_volume / max(down_volume, 1.0)
    up_day_ratio = up_days / max(up_days + down_days, 1)

    recent_dv = _avg_dollar_volume(recent, len(recent))
    base_dv = _avg_dollar_volume(base, len(base))
    dollar_volume_ratio = recent_dv / max(base_dv, 1.0)

    r_20 = _return_over_bars(bars, 20) or 0.0
    r_5 = _return_over_bars(bars, 5) or 0.0

    score = 0.0

    score += math.log(max(up_down_volume_ratio, 0.01)) * 0.70
    score += (avg_clv - 0.50) * 1.75
    score += (up_day_ratio - 0.50) * 1.25
    score += math.log(max(dollar_volume_ratio, 0.01)) * 0.35
    score += r_20 * 0.80
    score += r_5 * 0.35

    if heavy_red_days >= 2:
        score -= 0.85
    elif heavy_red_days == 1:
        score -= 0.35

    if avg_clv < 0.35 and r_20 > 0:
        score -= 0.55

    if dollar_volume_ratio > 2.5 and r_5 < 0:
        score -= 0.65

    return _clamp(score, -3.0, 3.0)


def trend_quality_score(data: Dict[str, Any]) -> float:
    """
    Rewards clean trend structure instead of random spikes.
    """
    bars = _bars(data)

    if len(bars) < 60:
        return 0.0

    closes = [_safe_float(b.get("close"), 0.0) for b in bars if _safe_float(b.get("close"), 0.0) > 0]

    if len(closes) < 60:
        return 0.0

    price = closes[-1]
    ma10 = _moving_average(closes, 10)
    ma20 = _moving_average(closes, 20)
    ma50 = _moving_average(closes, 50)

    if not ma10 or not ma20 or not ma50:
        return 0.0

    r_20 = _return_over_bars(bars, 20) or 0.0
    r_60 = _return_over_bars(bars, 60) or 0.0

    recent_lows = [_safe_float(b.get("low"), 0.0) for b in bars[-20:] if _safe_float(b.get("low"), 0.0) > 0]
    prior_lows = [_safe_float(b.get("low"), 0.0) for b in bars[-40:-20] if _safe_float(b.get("low"), 0.0) > 0]

    recent_highs = [_safe_float(b.get("high"), 0.0) for b in bars[-20:] if _safe_float(b.get("high"), 0.0) > 0]
    prior_highs = [_safe_float(b.get("high"), 0.0) for b in bars[-40:-20] if _safe_float(b.get("high"), 0.0) > 0]

    score = 0.0

    if price > ma10 > ma20 > ma50:
        score += 1.00
    elif price > ma20 > ma50:
        score += 0.55
    elif price > ma50:
        score += 0.25
    else:
        score -= 0.60

    if r_20 > 0:
        score += min(r_20 * 1.2, 0.80)

    if r_60 > 0:
        score += min(r_60 * 0.8, 0.75)

    if recent_lows and prior_lows and min(recent_lows) > min(prior_lows):
        score += 0.35

    if recent_highs and prior_highs and max(recent_highs) > max(prior_highs):
        score += 0.35

    recent_returns = _daily_returns(bars, 20)
    vol = _stddev(recent_returns)

    if vol > 0.18:
        score -= 0.75
    elif vol > 0.12:
        score -= 0.35
    elif 0.025 <= vol <= 0.10:
        score += 0.20

    high_60 = max(closes[-60:])
    drawdown_from_60_high = (price / high_60) - 1.0 if high_60 > 0 else 0.0

    if drawdown_from_60_high < -0.35:
        score -= 0.80
    elif drawdown_from_60_high > -0.10:
        score += 0.25

    return _clamp(score, -3.0, 3.0)


def breakout_setup_score(data: Dict[str, Any]) -> float:
    """
    Scores whether a stock is close to a useful breakout zone without being too extended.
    """
    bars = _bars(data)

    if len(bars) < 60:
        return 0.0

    closes = [_safe_float(b.get("close"), 0.0) for b in bars]
    highs = [_safe_float(b.get("high"), 0.0) for b in bars]
    volumes = [_safe_float(b.get("volume"), 0.0) for b in bars]

    price = closes[-1]

    if price <= 0:
        return 0.0

    high_20 = max(highs[-20:])
    high_60 = max(highs[-60:])
    prior_high_20 = max(highs[-21:-1]) if len(highs) >= 21 else high_20

    ma20 = _moving_average(closes, 20)
    avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0.0
    avg_vol_60 = sum(volumes[-60:]) / 60 if len(volumes) >= 60 else avg_vol_20

    r_5 = _return_over_bars(bars, 5) or 0.0
    r_20 = _return_over_bars(bars, 20) or 0.0

    distance_to_20_high = (price / high_20) - 1.0 if high_20 > 0 else 0.0
    distance_to_60_high = (price / high_60) - 1.0 if high_60 > 0 else 0.0
    extension_from_ma20 = (price / ma20) - 1.0 if ma20 and ma20 > 0 else 0.0
    volume_expansion = avg_vol_20 / max(avg_vol_60, 1.0)

    score = 0.0

    if distance_to_20_high >= -0.03:
        score += 0.75
    elif distance_to_20_high >= -0.08:
        score += 0.35

    if distance_to_60_high >= -0.05:
        score += 0.55
    elif distance_to_60_high >= -0.12:
        score += 0.20

    if price > prior_high_20:
        score += 0.45

    if 1.15 <= volume_expansion <= 3.5:
        score += 0.35
    elif volume_expansion > 4.5 and r_5 < 0:
        score -= 0.60

    if 0.02 <= r_20 <= 0.60:
        score += 0.30
    elif r_20 > 1.25:
        score -= 0.90

    if extension_from_ma20 > 0.75:
        score -= 1.00
    elif extension_from_ma20 > 0.45:
        score -= 0.45
    elif 0.00 <= extension_from_ma20 <= 0.25:
        score += 0.25

    if r_5 > 0.50:
        score -= 0.45

    return _clamp(score, -3.0, 3.0)


def liquidity_quality_score(data: Dict[str, Any]) -> float:
    """
    Measures whether liquidity is strong enough and stable enough to trade.

    This is especially important for cheaper stocks.
    """
    bars = _bars(data)

    if len(bars) < 30:
        return 0.0

    price = _last_close(data)
    adv20 = _avg_dollar_volume(bars, 20)
    adv60 = _avg_dollar_volume(bars, 60)
    avg_vol20 = _avg_volume(bars, 20)

    recent = bars[-20:]

    zero_or_tiny_volume_days = 0
    dollar_vols = []

    for bar in recent:
        close = _safe_float(bar.get("close"), 0.0)
        volume = _safe_float(bar.get("volume"), 0.0)

        if volume <= 0:
            zero_or_tiny_volume_days += 1

        if close > 0 and volume > 0:
            dollar_vols.append(close * volume)

    score = 0.0

    if adv20 >= 50_000_000:
        score += 1.30
    elif adv20 >= 20_000_000:
        score += 1.05
    elif adv20 >= 5_000_000:
        score += 0.80
    elif adv20 >= 1_000_000:
        score += 0.45
    elif adv20 >= 500_000:
        score += 0.15
    else:
        score -= 0.75

    if price < 1.00:
        if adv20 >= 2_000_000:
            score += 0.30
        elif adv20 < 1_000_000:
            score -= 0.75

    if price < 0.50 and adv20 < 2_000_000:
        score -= 0.45

    if adv60 > 0:
        dv_ratio = adv20 / adv60

        if 0.75 <= dv_ratio <= 2.5:
            score += 0.25
        elif dv_ratio > 5.0:
            score -= 0.35
        elif dv_ratio < 0.40:
            score -= 0.35

    if len(dollar_vols) >= 5:
        mean_dv = sum(dollar_vols) / len(dollar_vols)
        sd_dv = _stddev(dollar_vols)
        cv = sd_dv / max(mean_dv, 1.0)

        if cv <= 1.0:
            score += 0.25
        elif cv > 2.5:
            score -= 0.45

    if zero_or_tiny_volume_days >= 2:
        score -= 0.50

    if avg_vol20 <= 0:
        score -= 1.00

    return _clamp(score, -3.0, 3.0)


def volatility_control_score(data: Dict[str, Any]) -> float:
    """
    Rewards tradable volatility and penalizes chaotic volatility.

    We do NOT want to remove volatility entirely because cheap-stock winners move.
    We want to penalize unstable dump/reversal behavior.
    """
    bars = _bars(data)

    if len(bars) < 30:
        return 0.0

    returns_20 = _daily_returns(bars, 20)
    returns_60 = _daily_returns(bars, 60)

    if not returns_20:
        return 0.0

    vol20 = _stddev(returns_20)
    vol60 = _stddev(returns_60) if returns_60 else vol20
    atr_pct = _true_range_pct(bars, 20)

    worst_day = min(returns_20) if returns_20 else 0.0
    best_day = max(returns_20) if returns_20 else 0.0

    closes = [_safe_float(b.get("close"), 0.0) for b in bars]
    price = closes[-1]

    high_20 = max(closes[-20:])
    low_20 = min(closes[-20:])

    drawdown_from_20_high = (price / high_20) - 1.0 if high_20 > 0 else 0.0
    run_from_20_low = (price / low_20) - 1.0 if low_20 > 0 else 0.0

    score = 0.0

    # Sweet spot: enough volatility to move, not so much that it is chaos.
    if 0.025 <= vol20 <= 0.09:
        score += 0.70
    elif 0.09 < vol20 <= 0.14:
        score += 0.25
    elif vol20 > 0.18:
        score -= 1.00
    elif vol20 < 0.01:
        score -= 0.25

    if 0.03 <= atr_pct <= 0.16:
        score += 0.35
    elif atr_pct > 0.25:
        score -= 0.85

    if worst_day < -0.20:
        score -= 1.00
    elif worst_day < -0.12:
        score -= 0.45

    if best_day > 0.45 and drawdown_from_20_high < -0.15:
        score -= 0.75

    if run_from_20_low > 1.50:
        score -= 0.90
    elif run_from_20_low > 0.85:
        score -= 0.35

    if drawdown_from_20_high > -0.08 and worst_day > -0.12:
        score += 0.25

    if vol60 > 0 and vol20 / max(vol60, 0.0001) > 2.5:
        score -= 0.45

    return _clamp(score, -3.0, 3.0)


SIGNALS = {
    "momentum_12_1": momentum_12_1,
    "insider_buy_score": insider_buy_score,
    "volume_pressure_score": volume_pressure_score,
    "capital_efficiency_score": capital_efficiency_score,
    "relative_strength_score": relative_strength_score,
    "accumulation_quality_score": accumulation_quality_score,
    "trend_quality_score": trend_quality_score,
    "breakout_setup_score": breakout_setup_score,
    "liquidity_quality_score": liquidity_quality_score,
    "volatility_control_score": volatility_control_score,
    }
