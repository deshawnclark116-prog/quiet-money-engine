#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Alpha Signals.

These signals are built for the real mission:

Find stocks before the major repricing move.

They are not hot-stock signals.
They try to detect:
- quiet base
- compression
- controlled volume wake-up
- early relative strength
- base breakout proximity
- pre-pop timing quality
- late/chase penalty
"""

from typing import Any, Optional


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def clamp(value: float, low: float = -5.0, high: float = 5.0) -> float:
    return max(low, min(high, value))


def get_bars(data: dict) -> list[dict]:
    return data.get("bars") or []


def closes(bars: list[dict]) -> list[float]:
    out = []

    for b in bars:
        c = safe_float(b.get("close"), 0.0)
        if c > 0:
            out.append(c)

    return out


def highs(bars: list[dict]) -> list[float]:
    out = []

    for b in bars:
        h = safe_float(b.get("high"), safe_float(b.get("close"), 0.0))
        if h > 0:
            out.append(h)

    return out


def lows(bars: list[dict]) -> list[float]:
    out = []

    for b in bars:
        low = safe_float(b.get("low"), safe_float(b.get("close"), 0.0))
        if low > 0:
            out.append(low)

    return out


def volumes(bars: list[dict]) -> list[float]:
    return [safe_float(b.get("volume"), 0.0) for b in bars]


def pct(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None:
        return None

    try:
        now = float(now)
        then = float(then)
    except Exception:
        return None

    if then <= 0:
        return None

    return (now / then - 1.0) * 100.0


def sma(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v and v > 0]

    if not vals:
        return None

    return sum(vals) / len(vals)


def range_pct(high_value: Optional[float], low_value: Optional[float]) -> Optional[float]:
    if high_value is None or low_value is None or low_value <= 0:
        return None

    return (high_value / low_value - 1.0) * 100.0


def latest_price(data: dict) -> float:
    price = safe_float(data.get("price"), 0.0)

    if price > 0:
        return price

    c = closes(get_bars(data))
    return c[-1] if c else 0.0


def avg_volume(vals: list[float]) -> Optional[float]:
    vals = [v for v in vals if v and v > 0]

    if not vals:
        return None

    return sum(vals) / len(vals)


def return_context(data: dict) -> dict:
    bars = get_bars(data)
    c = closes(bars)
    price = latest_price(data)

    if price <= 0 or len(c) < 21:
        return {
            "price": price,
            "r1": None,
            "r3": None,
            "r5": None,
            "r10": None,
            "r20": None,
            "vs_sma20": None,
        }

    sma20 = sma(c[-20:])

    return {
        "price": price,
        "r1": pct(price, c[-2]) if len(c) >= 2 else None,
        "r3": pct(price, c[-4]) if len(c) >= 4 else None,
        "r5": pct(price, c[-6]) if len(c) >= 6 else None,
        "r10": pct(price, c[-11]) if len(c) >= 11 else None,
        "r20": pct(price, c[-21]) if len(c) >= 21 else None,
        "vs_sma20": pct(price, sma20),
    }


def quiet_base_score(data: dict) -> float:
    """
    Rewards stocks still sitting in a controlled base/range.

    We want:
    - tight 20-day range
    - not far above 20-day average
    - not already up huge
    - price moving toward upper half of base, not vertical
    """
    bars = get_bars(data)
    c = closes(bars)
    h = highs(bars)
    l = lows(bars)
    ctx = return_context(data)
    price = ctx["price"]

    if price <= 0 or len(c) < 30 or len(h) < 30 or len(l) < 30:
        return 0.0

    high20 = max(h[-20:])
    low20 = min(l[-20:])
    high40 = max(h[-40:]) if len(h) >= 40 else max(h[-30:])
    low40 = min(l[-40:]) if len(l) >= 40 else min(l[-30:])

    range20 = range_pct(high20, low20)
    range40 = range_pct(high40, low40)

    r10 = ctx["r10"]
    vs20 = ctx["vs_sma20"]

    score = 0.0

    # Controlled base range.
    if range20 is not None:
        if range20 <= 18:
            score += 1.20
        elif range20 <= 25:
            score += 0.80
        elif range20 <= 35:
            score += 0.25
        elif range20 > 50:
            score -= 1.25

    if range40 is not None:
        if range40 <= 30:
            score += 0.55
        elif range40 > 75:
            score -= 1.00

    # Not already extended.
    if r10 is not None:
        if -12 <= r10 <= 20:
            score += 0.75
        elif 20 < r10 <= 35:
            score += 0.10
        elif r10 > 35:
            score -= 1.50

    if vs20 is not None:
        if -8 <= vs20 <= 12:
            score += 0.75
        elif 12 < vs20 <= 20:
            score += 0.10
        elif vs20 > 20:
            score -= 1.35
        elif vs20 < -20:
            score -= 0.50

    # Price near the upper half of base is useful,
    # but not if it is already stretched far above the base.
    if high20 > low20:
        position = (price - low20) / (high20 - low20)

        if 0.50 <= position <= 0.90:
            score += 0.75
        elif 0.90 < position <= 1.03 and (ctx["r5"] is None or ctx["r5"] <= 18):
            score += 0.35
        elif position > 1.05:
            score -= 0.60
        elif position < 0.25:
            score -= 0.25

    return clamp(score, -4.0, 4.0)


def compression_score(data: dict) -> float:
    """
    Rewards range compression before a possible expansion.

    We want pressure building, not a candle that already exploded.
    """
    bars = get_bars(data)
    c = closes(bars)

    if len(bars) < 30 or len(c) < 30:
        return 0.0

    ranges = []

    for b in bars:
        close = safe_float(b.get("close"), 0.0)
        high = safe_float(b.get("high"), close)
        low = safe_float(b.get("low"), close)

        if close > 0 and high > 0 and low > 0:
            ranges.append((high - low) / close * 100.0)

    if len(ranges) < 30:
        return 0.0

    recent5 = sma(ranges[-5:])
    recent10 = sma(ranges[-10:])
    prior20 = sma(ranges[-30:-10])

    ctx = return_context(data)
    r5 = ctx["r5"]

    if recent5 is None or recent10 is None or prior20 is None or prior20 <= 0:
        return 0.0

    score = 0.0

    # Range shrinking is pressure.
    if recent5 <= prior20 * 0.65:
        score += 1.30
    elif recent5 <= prior20 * 0.80:
        score += 0.90
    elif recent5 <= prior20:
        score += 0.35
    elif recent5 > prior20 * 1.40:
        score -= 1.00

    if recent10 <= prior20:
        score += 0.45

    # But if price already ripped, compression signal is no longer clean.
    if r5 is not None and r5 > 25:
        score -= 1.25

    return clamp(score, -3.0, 3.0)


def controlled_volume_wake_score(data: dict) -> float:
    """
    Rewards volume waking up BEFORE price has already expanded.

    This is one of the most important pre-pop distinctions:
    - volume up + price controlled = interesting
    - volume up + price already vertical = chase
    """
    bars = get_bars(data)
    v = volumes(bars)
    ctx = return_context(data)

    if len(v) < 30:
        return 0.0

    recent3 = avg_volume(v[-3:])
    recent5 = avg_volume(v[-5:])
    prior20 = avg_volume(v[-25:-5])

    if recent3 is None or recent5 is None or prior20 is None or prior20 <= 0:
        return 0.0

    ratio3 = recent3 / prior20
    ratio5 = recent5 / prior20

    r1 = ctx["r1"]
    r5 = ctx["r5"]
    r10 = ctx["r10"]

    score = 0.0

    # Controlled volume wake-up.
    if 1.20 <= ratio3 <= 2.50:
        score += 1.10
    elif 2.50 < ratio3 <= 4.00:
        score += 0.65
    elif ratio3 > 5.00:
        score -= 0.75

    if 1.10 <= ratio5 <= 2.50:
        score += 0.75
    elif 2.50 < ratio5 <= 4.00:
        score += 0.30

    # Price still controlled = volume is useful.
    if r5 is not None:
        if -8 <= r5 <= 15:
            score += 0.85
        elif 15 < r5 <= 25:
            score += 0.20
        elif r5 > 25:
            score -= 1.40

    if r10 is not None and r10 > 40:
        score -= 1.25

    if r1 is not None and r1 > 18:
        score -= 1.50

    return clamp(score, -4.0, 4.0)


def pre_pop_timing_score(data: dict) -> float:
    """
    Measures whether the alert is still early enough.

    This should be high when price has started waking up
    but has not already made the major run.
    """
    ctx = return_context(data)

    if ctx["price"] <= 0:
        return 0.0

    r1 = ctx["r1"]
    r3 = ctx["r3"]
    r5 = ctx["r5"]
    r10 = ctx["r10"]
    vs20 = ctx["vs_sma20"]

    score = 0.0

    if r1 is not None:
        if -5 <= r1 <= 8:
            score += 0.55
        elif 8 < r1 <= 15:
            score += 0.10
        elif r1 > 15:
            score -= 1.50

    if r3 is not None:
        if -8 <= r3 <= 12:
            score += 0.65
        elif 12 < r3 <= 20:
            score += 0.10
        elif r3 > 20:
            score -= 1.30

    if r5 is not None:
        if -10 <= r5 <= 18:
            score += 0.85
        elif 18 < r5 <= 25:
            score += 0.10
        elif r5 > 25:
            score -= 1.60

    if r10 is not None:
        if -15 <= r10 <= 28:
            score += 0.85
        elif 28 < r10 <= 38:
            score += 0.10
        elif r10 > 38:
            score -= 1.80

    if vs20 is not None:
        if -12 <= vs20 <= 15:
            score += 0.75
        elif 15 < vs20 <= 22:
            score += 0.10
        elif vs20 > 22:
            score -= 1.50

    return clamp(score, -6.0, 4.0)


def base_breakout_proximity_score(data: dict) -> float:
    """
    Rewards a stock sitting close to a base breakout zone,
    before the move is already vertical.
    """
    bars = get_bars(data)
    c = closes(bars)
    h = highs(bars)
    l = lows(bars)
    ctx = return_context(data)
    price = ctx["price"]

    if price <= 0 or len(c) < 30 or len(h) < 30 or len(l) < 30:
        return 0.0

    high20 = max(h[-20:])
    low20 = min(l[-20:])
    high40 = max(h[-40:]) if len(h) >= 40 else max(h[-30:])

    dist_high20 = pct(price, high20)
    dist_high40 = pct(price, high40)
    r5 = ctx["r5"]
    r10 = ctx["r10"]

    score = 0.0

    # Near the 20-day high but not already massively extended.
    if dist_high20 is not None:
        if -8 <= dist_high20 <= 2:
            score += 1.10
        elif -15 <= dist_high20 < -8:
            score += 0.40
        elif dist_high20 > 5:
            score -= 0.70

    # Near bigger base high.
    if dist_high40 is not None:
        if -10 <= dist_high40 <= 3:
            score += 0.70
        elif dist_high40 < -25:
            score -= 0.30

    # Base location.
    if high20 > low20:
        position = (price - low20) / (high20 - low20)

        if 0.55 <= position <= 0.95:
            score += 0.55
        elif position > 1.08:
            score -= 0.75

    # Already running too hard invalidates the breakout-proximity idea.
    if r5 is not None and r5 > 25:
        score -= 1.20

    if r10 is not None and r10 > 40:
        score -= 1.10

    return clamp(score, -4.0, 4.0)


def early_relative_strength_score(data: dict) -> float:
    """
    Rewards controlled relative strength versus benchmarks.

    The stock should be quietly outperforming,
    not already going vertical.
    """
    bars = get_bars(data)
    c = closes(bars)
    ctx = return_context(data)

    if len(c) < 21:
        return 0.0

    own5 = ctx["r5"]
    own10 = ctx["r10"]

    if own5 is None or own10 is None:
        return 0.0

    benchmark_bars = data.get("benchmark_bars") or {}

    bench_returns_5 = []
    bench_returns_10 = []

    for _, bbars in benchmark_bars.items():
        bc = closes(bbars)

        if len(bc) >= 11:
            bprice = bc[-1]
            bench_returns_5.append(pct(bprice, bc[-6]))
            bench_returns_10.append(pct(bprice, bc[-11]))

    if not bench_returns_5 or not bench_returns_10:
        return 0.0

    bench5 = sma([x for x in bench_returns_5 if x is not None])
    bench10 = sma([x for x in bench_returns_10 if x is not None])

    if bench5 is None or bench10 is None:
        return 0.0

    rel5 = own5 - bench5
    rel10 = own10 - bench10

    score = 0.0

    # Controlled outperformance is good.
    if 2 <= rel5 <= 15 and own5 <= 22:
        score += 0.90
    elif 15 < rel5 <= 25 and own5 <= 28:
        score += 0.30
    elif rel5 > 25 or own5 > 30:
        score -= 1.00

    if 3 <= rel10 <= 22 and own10 <= 35:
        score += 0.85
    elif 22 < rel10 <= 35 and own10 <= 42:
        score += 0.25
    elif rel10 > 35 or own10 > 45:
        score -= 1.25

    return clamp(score, -3.0, 3.0)


def late_chase_penalty(data: dict) -> float:
    """
    Negative signal for late alerts.

    Positive weight on this signal penalizes:
    - 1-day vertical moves
    - 3/5/10-day overextension
    - being too far above 20-day average
    """
    ctx = return_context(data)

    if ctx["price"] <= 0:
        return -1.0

    r1 = ctx["r1"]
    r3 = ctx["r3"]
    r5 = ctx["r5"]
    r10 = ctx["r10"]
    vs20 = ctx["vs_sma20"]

    penalty = 0.0

    if r1 is not None:
        if r1 > 30:
            penalty -= 2.50
        elif r1 > 18:
            penalty -= 1.50
        elif r1 > 12:
            penalty -= 0.60

    if r3 is not None:
        if r3 > 35:
            penalty -= 2.00
        elif r3 > 25:
            penalty -= 1.10
        elif r3 > 18:
            penalty -= 0.45

    if r5 is not None:
        if r5 > 60:
            penalty -= 2.75
        elif r5 > 40:
            penalty -= 1.75
        elif r5 > 25:
            penalty -= 0.85

    if r10 is not None:
        if r10 > 80:
            penalty -= 2.75
        elif r10 > 55:
            penalty -= 1.75
        elif r10 > 38:
            penalty -= 0.75

    if vs20 is not None:
        if vs20 > 45:
            penalty -= 2.00
        elif vs20 > 30:
            penalty -= 1.25
        elif vs20 > 20:
            penalty -= 0.50

    return clamp(penalty, -8.0, 0.0)


PREPOP_ALPHA_SIGNALS = {
    "quiet_base_score": quiet_base_score,
    "compression_score": compression_score,
    "controlled_volume_wake_score": controlled_volume_wake_score,
    "pre_pop_timing_score": pre_pop_timing_score,
    "base_breakout_proximity_score": base_breakout_proximity_score,
    "early_relative_strength_score": early_relative_strength_score,
    "late_chase_penalty": late_chase_penalty,
  }
