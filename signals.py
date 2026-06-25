#!/usr/bin/env python3
"""
Quiet Money Engine — signal library.

Each signal is a pure function: data dict -> float (higher = more bullish) or
None when there is not enough data.

The scoring engine z-scores each signal across the universe, so raw units do
not need to match between signals.
"""
import math
from datetime import datetime, timezone


TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_datetime(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None

        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
        except Exception:
            return None

    return None


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]

    if not vals:
        return None

    return sum(vals) / len(vals)


def momentum_12_1(data: dict) -> float | None:
    """
    12-month price return skipping the most recent month.

    This is classic 12-1 momentum. Skipping the most recent month avoids some
    short-term reversal noise.
    """
    bars = data.get("bars") or []

    if len(bars) < TRADING_DAYS_YEAR + 1:
        return None

    closes = [b["close"] for b in bars]

    start = closes[-(TRADING_DAYS_YEAR + 1)]
    end = closes[-(TRADING_DAYS_MONTH + 1)]

    if start <= 0:
        return None

    return end / start - 1.0


def volume_pressure_score(data: dict) -> float | None:
    """
    Volume/accumulation pressure signal using close + volume.

    Rewards:
    - recent volume expansion vs 20d/60d baseline
    - recent dollar-volume expansion
    - price up while volume expands
    - short-term follow-through over 5d and 20d
    - quiet accumulation, not just one random volume spike

    This v1 only uses date/close/volume because that is what data_layer.py
    currently returns. Later v2 can use high/low/open/VWAP after we upgrade
    data_layer.py to the full EOD endpoint.
    """
    bars = data.get("bars") or []

    if len(bars) < 80:
        return None

    closes = [_safe_float(b.get("close")) for b in bars]
    volumes = [_safe_float(b.get("volume")) for b in bars]

    if not closes or not volumes:
        return None

    if closes[-1] <= 0:
        return None

    recent_5 = bars[-5:]
    recent_10 = bars[-10:]
    recent_20 = bars[-20:]
    base_20 = bars[-40:-20]
    base_60 = bars[-80:-20]

    recent_5_vol = _mean([_safe_float(b.get("volume")) for b in recent_5])
    recent_10_vol = _mean([_safe_float(b.get("volume")) for b in recent_10])
    recent_20_vol = _mean([_safe_float(b.get("volume")) for b in recent_20])
    base_20_vol = _mean([_safe_float(b.get("volume")) for b in base_20])
    base_60_vol = _mean([_safe_float(b.get("volume")) for b in base_60])

    if not recent_5_vol or not recent_10_vol or not recent_20_vol:
        return None

    if not base_20_vol or not base_60_vol:
        return None

    if base_20_vol <= 0 or base_60_vol <= 0:
        return None

    recent_5_dollar_vol = _mean(
        [
            _safe_float(b.get("close")) * _safe_float(b.get("volume"))
            for b in recent_5
        ]
    )

    base_20_dollar_vol = _mean(
        [
            _safe_float(b.get("close")) * _safe_float(b.get("volume"))
            for b in base_20
        ]
    )

    if not recent_5_dollar_vol or not base_20_dollar_vol or base_20_dollar_vol <= 0:
        dollar_volume_ratio = 1.0
    else:
        dollar_volume_ratio = recent_5_dollar_vol / base_20_dollar_vol

    vol_ratio_5_vs_20 = recent_5_vol / base_20_vol
    vol_ratio_10_vs_60 = recent_10_vol / base_60_vol
    vol_ratio_20_vs_60 = recent_20_vol / base_60_vol

    close_now = closes[-1]
    close_5 = closes[-6] if len(closes) >= 6 else closes[0]
    close_20 = closes[-21] if len(closes) >= 21 else closes[0]
    close_60 = closes[-61] if len(closes) >= 61 else closes[0]

    ret_5 = (close_now / close_5) - 1.0 if close_5 > 0 else 0.0
    ret_20 = (close_now / close_20) - 1.0 if close_20 > 0 else 0.0
    ret_60 = (close_now / close_60) - 1.0 if close_60 > 0 else 0.0

    up_volume = 0.0
    down_volume = 0.0

    for i in range(max(1, len(bars) - 20), len(bars)):
        today_close = closes[i]
        yesterday_close = closes[i - 1]
        today_volume = volumes[i]

        if today_close >= yesterday_close:
            up_volume += today_volume
        else:
            down_volume += today_volume

    up_down_ratio = up_volume / max(down_volume, 1.0)

    positive_volume_days = 0

    for i in range(max(1, len(bars) - 10), len(bars)):
        if closes[i] > closes[i - 1] and volumes[i] > base_20_vol:
            positive_volume_days += 1

    # Components are capped so one wild spike does not dominate.
    volume_expansion = min(math.log(max(vol_ratio_5_vs_20, 0.01), 2), 3.0)
    medium_volume_expansion = min(math.log(max(vol_ratio_10_vs_60, 0.01), 2), 3.0)
    sustained_volume = min(math.log(max(vol_ratio_20_vs_60, 0.01), 2), 2.0)
    dollar_volume_expansion = min(math.log(max(dollar_volume_ratio, 0.01), 2), 3.0)
    up_down_component = min(math.log(max(up_down_ratio, 0.01), 2), 2.0)

    price_confirm_5 = max(min(ret_5 * 10.0, 2.0), -2.0)
    price_confirm_20 = max(min(ret_20 * 5.0, 2.0), -2.0)

    trend_guard = 0.0
    if ret_60 > 0:
        trend_guard += min(ret_60 * 2.0, 1.0)
    else:
        trend_guard += max(ret_60 * 2.0, -1.0)

    accumulation_days_component = min(positive_volume_days * 0.25, 2.0)

    # Penalize blow-off style action: huge 5d volume but negative 5d return.
    blowoff_penalty = 0.0
    if vol_ratio_5_vs_20 > 2.5 and ret_5 < 0:
        blowoff_penalty = 1.5

    score = (
        volume_expansion
        + medium_volume_expansion
        + sustained_volume
        + dollar_volume_expansion
        + up_down_component
        + price_confirm_5
        + price_confirm_20
        + trend_guard
        + accumulation_days_component
        - blowoff_penalty
    )

    return round(score, 6)


def _role_weight(role_text: str | None) -> float:
    role = (role_text or "").lower()

    if any(x in role for x in ["chief executive", "ceo", "president"]):
        return 1.75

    if any(x in role for x in ["chief financial", "cfo"]):
        return 1.55

    if any(x in role for x in ["chief operating", "coo"]):
        return 1.45

    if any(x in role for x in ["chairman", "chair"]):
        return 1.40

    if any(x in role for x in ["officer", "evp", "svp", "vp", "chief"]):
        return 1.30

    if "director" in role:
        return 1.20

    if "10%" in role or "ten percent" in role or "beneficial owner" in role:
        return 1.10

    return 1.00


def insider_buy_score(data: dict) -> float:
    """
    Bullish insider-buy signal from existing Form 4 buy table.

    Assumes the worker already filtered to real transaction-code P buys.
    No recent insider buys = 0.0.

    Rewards:
    - more recent buys
    - more distinct insiders
    - officer/director role quality
    - larger dollar value
    - buy size relative to market cap
    - buy size relative to average dollar volume
    """
    buys = data.get("insider_buys") or []

    if not buys:
        return 0.0

    now = datetime.now(timezone.utc)

    total_value = 0.0
    total_role_weighted_value = 0.0
    best_market_cap = 0.0
    best_avg_dollar_vol = 0.0

    insiders = set()
    recent_7d_count = 0
    recent_14d_count = 0
    recent_30d_count = 0

    recency_value_score = 0.0
    role_score_sum = 0.0

    for buy in buys:
        insider = str(buy.get("insider") or "").strip().lower()

        if insider:
            insiders.add(insider)

        role = buy.get("role")
        role_w = _role_weight(role)
        role_score_sum += role_w

        value = _safe_float(buy.get("value"))

        if value <= 0:
            shares = _safe_float(buy.get("shares"))
            price = _safe_float(buy.get("price"))
            value = shares * price

        market_cap = _safe_float(buy.get("market_cap"))
        avg_dollar_vol = _safe_float(buy.get("avg_dollar_vol"))

        if market_cap > 0:
            best_market_cap = max(best_market_cap, market_cap)

        if avg_dollar_vol > 0:
            best_avg_dollar_vol = max(best_avg_dollar_vol, avg_dollar_vol)

        seen_at = _parse_datetime(buy.get("seen_at")) or _parse_datetime(buy.get("filed_at"))

        age_days = 60.0

        if seen_at:
            age_days = max(0.0, (now - seen_at).total_seconds() / 86400.0)

        if age_days <= 7:
            recent_7d_count += 1

        if age_days <= 14:
            recent_14d_count += 1

        if age_days <= 30:
            recent_30d_count += 1

        recency_w = max(0.05, 1.0 - min(age_days, 60.0) / 60.0)

        total_value += max(value, 0.0)
        total_role_weighted_value += max(value, 0.0) * role_w

        if value > 0:
            dollar_component = math.log10(1.0 + value) / 6.0
        else:
            dollar_component = 0.05

        recency_value_score += dollar_component * role_w * recency_w

    buy_count = len(buys)
    insider_count = len(insiders)

    cluster_bonus = 0.0

    if insider_count >= 2:
        cluster_bonus += 1.00

    if insider_count >= 3:
        cluster_bonus += 0.75

    if insider_count >= 5:
        cluster_bonus += 0.75

    recent_bonus = (
        min(recent_7d_count, 5) * 0.35
        + min(recent_14d_count, 5) * 0.20
        + min(recent_30d_count, 8) * 0.10
    )

    count_score = min(buy_count, 10) * 0.12
    insider_count_score = min(insider_count, 8) * 0.35

    avg_role_score = role_score_sum / buy_count if buy_count else 1.0
    role_quality_bonus = max(0.0, avg_role_score - 1.0) * 1.25

    market_cap_component = 0.0

    if total_value > 0 and best_market_cap > 0:
        market_cap_component = min((total_value / best_market_cap) * 10000.0, 3.0)

    liquidity_component = 0.0

    if total_value > 0 and best_avg_dollar_vol > 0:
        liquidity_component = min(total_value / best_avg_dollar_vol, 3.0)

    total_value_component = 0.0

    if total_value > 0:
        total_value_component = min(math.log10(1.0 + total_value) / 3.0, 3.0)

    role_weighted_value_component = 0.0

    if total_role_weighted_value > 0:
        role_weighted_value_component = min(math.log10(1.0 + total_role_weighted_value) / 4.0, 3.0)

    score = (
        recency_value_score
        + cluster_bonus
        + recent_bonus
        + count_score
        + insider_count_score
        + role_quality_bonus
        + market_cap_component
        + liquidity_component
        + total_value_component
        + role_weighted_value_component
    )

    return round(score, 6)


SIGNALS = {
    "momentum_12_1": momentum_12_1,
    "insider_buy_score": insider_buy_score,
    "volume_pressure_score": volume_pressure_score,
}
