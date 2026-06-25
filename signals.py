#!/usr/bin/env python3
"""
Quiet Money Engine — signal library.

Each signal is a pure function: data dict -> float (higher = more bullish) or
None when there isn't enough data.

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
        # Example: $100k buy on $1B cap = 0.0001. Multiply to make it scoreable.
        market_cap_component = min((total_value / best_market_cap) * 10000.0, 3.0)

    liquidity_component = 0.0
    if total_value > 0 and best_avg_dollar_vol > 0:
        # Insider buy that is large vs normal dollar volume is more meaningful.
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
}
