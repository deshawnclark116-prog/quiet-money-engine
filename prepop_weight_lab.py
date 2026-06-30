#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Weight Lab.

Purpose:
Find which signal weights would have caught stocks BEFORE they ran.

This script does NOT change production tables.
It does NOT save picks.
It only studies historical price windows using your existing data_layer + signals.

Core question:
Before a stock popped, which signals were already showing up?

Outputs:
1. PREPOP_WIN examples
2. LATE_POP examples
3. Signal separation table
4. Weight-profile comparison
"""

import os
import math
import statistics
from collections import defaultdict
from datetime import datetime, date
from typing import Any, Optional

from data_layer import get_price_history
from signals import SIGNALS

try:
    from universe_builder import build_dynamic_universe
except Exception:
    build_dynamic_universe = None

try:
    from score_universe import parse_signal_weights
except Exception:
    parse_signal_weights = None


LAB_MAX_TICKERS = int(os.getenv("LAB_MAX_TICKERS", "60"))
LAB_HISTORY_DAYS = int(os.getenv("LAB_HISTORY_DAYS", "420"))
LAB_MIN_LOOKBACK = int(os.getenv("LAB_MIN_LOOKBACK", "80"))
LAB_FUTURE_WINDOW = int(os.getenv("LAB_FUTURE_WINDOW", "20"))
LAB_POP_THRESHOLD = float(os.getenv("LAB_POP_THRESHOLD", "30.0"))
LAB_TOP_PER_DATE = int(os.getenv("LAB_TOP_PER_DATE", "5"))

CORE_EXAMPLES = [
    "BOLD",
    "LILA",
    "GDC",
    "FTH",
    "TOI",
    "ARTV",
    "CGON",
    "IMRX",
    "MRBK",
    "IX",
    "AMD",
    "RIOT",
    "SOFI",
    "AFRM",
    "PLTR",
]


QUALITY_HEAVY_V2 = {
    "momentum_12_1": 0.25,
    "insider_buy_score": 0.35,
    "volume_pressure_score": 0.35,
    "capital_efficiency_score": 0.80,
    "relative_strength_score": 0.40,

    "accumulation_quality_score": 0.85,
    "trend_quality_score": 0.45,
    "breakout_setup_score": 0.80,
    "liquidity_quality_score": 0.90,
    "volatility_control_score": 0.70,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.25,

    "dilution_risk_score": 0.70,
    "reverse_split_risk_score": 0.65,

    "company_insight_composite": 0.00,
}


PREPOP_V1 = {
    # Raw hot-move signals are intentionally low.
    "momentum_12_1": 0.10,
    "volume_pressure_score": 0.20,
    "relative_strength_score": 0.20,

    # Quiet setup / quality signals carry the model.
    "accumulation_quality_score": 1.25,
    "liquidity_quality_score": 1.10,
    "volatility_control_score": 1.00,
    "capital_efficiency_score": 0.90,
    "breakout_setup_score": 0.80,
    "trend_quality_score": 0.55,

    # Catalyst/risk layer.
    "insider_buy_score": 0.35,
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.10,

    # Negative scores should hurt composite.
    "dilution_risk_score": 0.90,
    "reverse_split_risk_score": 0.85,

    "company_insight_composite": 0.00,
}


PREPOP_V2_STRICT = {
    # Even less chase/momentum.
    "momentum_12_1": 0.05,
    "volume_pressure_score": 0.15,
    "relative_strength_score": 0.15,

    "accumulation_quality_score": 1.40,
    "liquidity_quality_score": 1.20,
    "volatility_control_score": 1.10,
    "capital_efficiency_score": 1.00,
    "breakout_setup_score": 0.75,
    "trend_quality_score": 0.50,

    "insider_buy_score": 0.35,
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,

    "dilution_risk_score": 1.00,
    "reverse_split_risk_score": 0.95,

    "company_insight_composite": 0.00,
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def clean_ticker(ticker: str) -> str:
    return str(ticker or "").upper().strip()


def parse_bar_date(value: Any) -> Optional[date]:
    if value is None:
        return None

    if isinstance(value, date):
        return value

    s = str(value)

    if "T" in s:
        s = s.split("T")[0]

    s = s[:10]

    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def normalize_bars(raw_bars: list[dict]) -> list[dict]:
    out = []

    for bar in raw_bars or []:
        d = None

        for key in ["date", "datetime", "timestamp", "time"]:
            if key in bar:
                d = parse_bar_date(bar.get(key))
                if d:
                    break

        close = safe_float(bar.get("close"), 0.0)

        if not d or close <= 0:
            continue

        out.append(
            {
                "date": d.isoformat(),
                "open": safe_float(bar.get("open"), close),
                "high": safe_float(bar.get("high"), close),
                "low": safe_float(bar.get("low"), close),
                "close": close,
                "volume": safe_float(bar.get("volume"), 0.0),
            }
        )

    out.sort(key=lambda x: x["date"])
    return out


def pct(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1.0) * 100.0


def mean(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return statistics.mean(vals)


def median(values: list[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return statistics.median(vals)


def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.1f}%"


def fmt_num(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:+.2f}"


def get_lab_universe() -> list[str]:
    raw = os.getenv("LAB_UNIVERSE", "").strip()

    tickers = []

    if raw:
        for item in raw.split(","):
            t = clean_ticker(item)
            if t and t not in tickers:
                tickers.append(t)
        return tickers[:LAB_MAX_TICKERS]

    for t in CORE_EXAMPLES:
        t = clean_ticker(t)
        if t and t not in tickers:
            tickers.append(t)

    if build_dynamic_universe:
        try:
            for t in build_dynamic_universe():
                t = clean_ticker(t)
                if t and t not in tickers:
                    tickers.append(t)
                if len(tickers) >= LAB_MAX_TICKERS:
                    break
        except Exception as exc:
            print("Dynamic universe failed in lab:", exc)

    return tickers[:LAB_MAX_TICKERS]


def avg_dollar_volume(bars: list[dict], window: int = 20) -> float:
    sample = bars[-window:]
    values = []

    for b in sample:
        c = safe_float(b.get("close"), 0.0)
        v = safe_float(b.get("volume"), 0.0)
        if c > 0 and v > 0:
            values.append(c * v)

    if not values:
        return 0.0

    return sum(values) / len(values)


def future_max_return(closes: list[float], i: int, window: int) -> Optional[float]:
    start = closes[i]

    if start <= 0:
        return None

    future = closes[i + 1 : i + 1 + window]

    if not future:
        return None

    return (max(future) / start - 1.0) * 100.0


def future_end_return(closes: list[float], i: int, window: int) -> Optional[float]:
    start = closes[i]

    if start <= 0 or i + window >= len(closes):
        return None

    return (closes[i + window] / start - 1.0) * 100.0


def pre_alert_context(closes: list[float], i: int) -> dict:
    price = closes[i]

    c1 = closes[i - 1] if i >= 1 else None
    c3 = closes[i - 3] if i >= 3 else None
    c5 = closes[i - 5] if i >= 5 else None
    c10 = closes[i - 10] if i >= 10 else None

    sma20 = None
    if i >= 19:
        sma20 = sum(closes[i - 19 : i + 1]) / 20

    return {
        "pre_1d": pct(price, c1),
        "pre_3d": pct(price, c3),
        "pre_5d": pct(price, c5),
        "pre_10d": pct(price, c10),
        "vs_sma20": pct(price, sma20),
    }


def is_early_context(ctx: dict) -> bool:
    pre_1d = ctx.get("pre_1d")
    pre_3d = ctx.get("pre_3d")
    pre_5d = ctx.get("pre_5d")
    pre_10d = ctx.get("pre_10d")
    vs_sma20 = ctx.get("vs_sma20")

    checks = [
        pre_1d is None or pre_1d <= 15.0,
        pre_3d is None or pre_3d <= 20.0,
        pre_5d is None or pre_5d <= 25.0,
        pre_10d is None or pre_10d <= 35.0,
        vs_sma20 is None or vs_sma20 <= 20.0,
    ]

    return all(checks)


def is_late_context(ctx: dict) -> bool:
    pre_1d = ctx.get("pre_1d")
    pre_3d = ctx.get("pre_3d")
    pre_5d = ctx.get("pre_5d")
    pre_10d = ctx.get("pre_10d")
    vs_sma20 = ctx.get("vs_sma20")

    return (
        (pre_1d is not None and pre_1d > 20.0)
        or (pre_3d is not None and pre_3d > 30.0)
        or (pre_5d is not None and pre_5d > 40.0)
        or (pre_10d is not None and pre_10d > 55.0)
        or (vs_sma20 is not None and vs_sma20 > 30.0)
    )


def label_event(future_max: Optional[float], ctx: dict) -> str:
    if future_max is None:
        return "NO_FUTURE"

    popped = future_max >= LAB_POP_THRESHOLD
    early = is_early_context(ctx)
    late = is_late_context(ctx)

    if popped and early:
        return "PREPOP_WIN"

    if popped and late:
        return "LATE_POP"

    if popped:
        return "MESSY_POP"

    if late:
        return "LATE_NO_POP"

    return "NO_POP"


def score_signals(ticker: str, bars_until_now: list[dict], benchmark_bars_by_ticker: dict) -> dict:
    signal_values = {}

    current_date = parse_bar_date(bars_until_now[-1].get("date"))
    benchmark_context = {}

    for bench, bench_bars in benchmark_bars_by_ticker.items():
        usable = []

        for b in bench_bars:
            d = parse_bar_date(b.get("date"))
            if d and current_date and d <= current_date:
                usable.append(b)

        if usable:
            benchmark_context[bench] = usable

    ticker_data = {
        "ticker": ticker,
        "bars": bars_until_now,
        "price": safe_float(bars_until_now[-1].get("close"), 0.0),
        "avg_dollar_volume_20": avg_dollar_volume(bars_until_now, 20),
        "insider_buys": [],
        "recent_insider_buy_count": 0,
        "benchmark_bars": benchmark_context,
    }

    for name, fn in SIGNALS.items():
        try:
            signal_values[name] = float(fn(ticker_data))
        except Exception:
            signal_values[name] = 0.0

    return signal_values


def composite(signals: dict, weights: dict) -> float:
    total = 0.0

    for name, value in signals.items():
        total += safe_float(value, 0.0) * safe_float(weights.get(name), 0.0)

    return total


def build_rows() -> list[dict]:
    tickers = get_lab_universe()

    print()
    print("PRE-POP WEIGHT LAB")
    print("Universe size:", len(tickers))
    print("Tickers:", ", ".join(tickers))
    print("History days:", LAB_HISTORY_DAYS)
    print("Future window:", LAB_FUTURE_WINDOW)
    print("Pop threshold:", f"+{LAB_POP_THRESHOLD:.1f}%")
    print()

    print("Loading benchmark bars...")
    benchmark_bars_by_ticker = {}
    for bench in ["SPY", "QQQ"]:
        try:
            benchmark_bars_by_ticker[bench] = normalize_bars(
                get_price_history(bench, days=LAB_HISTORY_DAYS)
            )
            print(bench, "bars:", len(benchmark_bars_by_ticker[bench]))
        except Exception as exc:
            print(bench, "failed:", exc)

    rows = []

    for n, ticker in enumerate(tickers, 1):
        try:
            bars = normalize_bars(get_price_history(ticker, days=LAB_HISTORY_DAYS))
        except Exception as exc:
            print(ticker, "fetch failed:", exc)
            continue

        if len(bars) < LAB_MIN_LOOKBACK + LAB_FUTURE_WINDOW + 5:
            print(f"{n:>2}. {ticker:<6} skipped, bars={len(bars)}")
            continue

        closes = [safe_float(b.get("close"), 0.0) for b in bars]

        made = 0

        for i in range(LAB_MIN_LOOKBACK, len(bars) - LAB_FUTURE_WINDOW):
            price = closes[i]

            if price <= 0:
                continue

            ctx = pre_alert_context(closes, i)
            fut_max = future_max_return(closes, i, LAB_FUTURE_WINDOW)
            fut_end = future_end_return(closes, i, LAB_FUTURE_WINDOW)
            label = label_event(fut_max, ctx)

            # Keep all event types but avoid massive output later.
            bars_until_now = bars[: i + 1]
            signals = score_signals(ticker, bars_until_now, benchmark_bars_by_ticker)

            rows.append(
                {
                    "ticker": ticker,
                    "date": bars[i]["date"],
                    "price": price,
                    "future_max_return": fut_max,
                    "future_end_return": fut_end,
                    "label": label,
                    "signals": signals,
                    **ctx,
                }
            )

            made += 1

        print(f"{n:>2}. {ticker:<6} bars={len(bars)} event_rows={made}")

    return rows


def summarize_labels(rows: list[dict]) -> None:
    counts = defaultdict(int)

    for r in rows:
        counts[r["label"]] += 1

    print()
    print("LABEL COUNTS")
    print("-" * 50)
    for label, n in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"{label:15s} {n}")


def show_examples(rows: list[dict], label: str, limit: int = 15) -> None:
    sample = [r for r in rows if r["label"] == label]
    sample.sort(key=lambda r: safe_float(r.get("future_max_return"), 0.0), reverse=True)

    print()
    print(label, "EXAMPLES")
    print("-" * 110)
    print("ticker | date       | price   | fut_max | pre1   | pre3   | pre5   | pre10  | vs20")
    print("-" * 110)

    for r in sample[:limit]:
        print(
            f"{r['ticker']:6s} | "
            f"{r['date']} | "
            f"{r['price']:7.2f} | "
            f"{fmt_pct(r.get('future_max_return')):>7s} | "
            f"{fmt_pct(r.get('pre_1d')):>6s} | "
            f"{fmt_pct(r.get('pre_3d')):>6s} | "
            f"{fmt_pct(r.get('pre_5d')):>6s} | "
            f"{fmt_pct(r.get('pre_10d')):>6s} | "
            f"{fmt_pct(r.get('vs_sma20')):>6s}"
        )


def signal_separation(rows: list[dict]) -> None:
    pre = [r for r in rows if r["label"] == "PREPOP_WIN"]
    late = [r for r in rows if r["label"] in {"LATE_POP", "LATE_NO_POP"}]
    nopop = [r for r in rows if r["label"] == "NO_POP"]

    print()
    print("SIGNAL SEPARATION")
    print("Positive means the signal was stronger in PREPOP_WIN than late/no-pop rows.")
    print("-" * 115)
    print("signal                           | prepop_avg | late_avg | nopop_avg | pre-late | pre-nopop")
    print("-" * 115)

    signal_names = sorted(SIGNALS.keys())

    lines = []

    for name in signal_names:
        pre_avg = mean([safe_float(r["signals"].get(name), 0.0) for r in pre])
        late_avg = mean([safe_float(r["signals"].get(name), 0.0) for r in late])
        nopop_avg = mean([safe_float(r["signals"].get(name), 0.0) for r in nopop])

        pre_late = None if pre_avg is None or late_avg is None else pre_avg - late_avg
        pre_nopop = None if pre_avg is None or nopop_avg is None else pre_avg - nopop_avg

        sort_key = -999 if pre_late is None else pre_late
        lines.append((sort_key, name, pre_avg, late_avg, nopop_avg, pre_late, pre_nopop))

    lines.sort(key=lambda x: x[0], reverse=True)

    for _, name, pre_avg, late_avg, nopop_avg, pre_late, pre_nopop in lines:
        print(
            f"{name:32s} | "
            f"{fmt_num(pre_avg):>10s} | "
            f"{fmt_num(late_avg):>8s} | "
            f"{fmt_num(nopop_avg):>9s} | "
            f"{fmt_num(pre_late):>8s} | "
            f"{fmt_num(pre_nopop):>10s}"
        )


def get_profiles() -> dict:
    profiles = {
        "quality_heavy_v2": QUALITY_HEAVY_V2,
        "prepop_v1": PREPOP_V1,
        "prepop_v2_strict": PREPOP_V2_STRICT,
    }

    if parse_signal_weights:
        try:
            profiles["live_active"] = parse_signal_weights()
        except Exception:
            pass

    return profiles


def profile_backtest(rows: list[dict]) -> None:
    profiles = get_profiles()

    rows_by_date = defaultdict(list)

    for r in rows:
        rows_by_date[r["date"]].append(r)

    print()
    print("WEIGHT PROFILE COMPARISON")
    print("For each date, pick top LAB_TOP_PER_DATE by composite.")
    print("-" * 120)
    print("profile             | picks | prepop% | late% | avg_fut_max | med_fut_max | avg_pre5 | avg_pre10")
    print("-" * 120)

    for name, weights in profiles.items():
        picks = []

        for d, day_rows in rows_by_date.items():
            scored = []

            for r in day_rows:
                score = composite(r["signals"], weights)
                scored.append((score, r))

            scored.sort(key=lambda x: x[0], reverse=True)

            for score, r in scored[:LAB_TOP_PER_DATE]:
                rr = dict(r)
                rr["profile_score"] = score
                picks.append(rr)

        if not picks:
            continue

        prepop_n = sum(1 for r in picks if r["label"] == "PREPOP_WIN")
        late_n = sum(1 for r in picks if r["label"] in {"LATE_POP", "LATE_NO_POP"})

        prepop_rate = prepop_n / len(picks) * 100.0
        late_rate = late_n / len(picks) * 100.0

        futs = [safe_float(r.get("future_max_return"), 0.0) for r in picks]
        pre5s = [safe_float(r.get("pre_5d"), 0.0) for r in picks]
        pre10s = [safe_float(r.get("pre_10d"), 0.0) for r in picks]

        print(
            f"{name:19s} | "
            f"{len(picks):5d} | "
            f"{prepop_rate:7.2f} | "
            f"{late_rate:5.2f} | "
            f"{mean(futs):11.2f} | "
            f"{median(futs):11.2f} | "
            f"{mean(pre5s):8.2f} | "
            f"{mean(pre10s):9.2f}"
        )


def show_profile_top_examples(rows: list[dict], profile_name: str, limit: int = 20) -> None:
    profiles = get_profiles()

    if profile_name not in profiles:
        return

    weights = profiles[profile_name]

    scored = []

    for r in rows:
        rr = dict(r)
        rr["score"] = composite(r["signals"], weights)
        scored.append(rr)

    scored.sort(key=lambda r: r["score"], reverse=True)

    print()
    print(f"TOP RAW SCORES — {profile_name}")
    print("This shows what that profile naturally wants to select.")
    print("-" * 120)
    print("ticker | date       | score | label       | fut_max | pre5   | pre10  | vs20")
    print("-" * 120)

    for r in scored[:limit]:
        print(
            f"{r['ticker']:6s} | "
            f"{r['date']} | "
            f"{r['score']:5.2f} | "
            f"{r['label']:11s} | "
            f"{fmt_pct(r.get('future_max_return')):>7s} | "
            f"{fmt_pct(r.get('pre_5d')):>6s} | "
            f"{fmt_pct(r.get('pre_10d')):>6s} | "
            f"{fmt_pct(r.get('vs_sma20')):>6s}"
        )


def print_weight_profiles() -> None:
    print()
    print("WEIGHT PROFILES BEING TESTED")
    print("-" * 80)

    profiles = get_profiles()

    for profile, weights in profiles.items():
        print()
        print(profile)
        for k, v in sorted(weights.items()):
            print(f"  {k:32s} {v}")


def main() -> None:
    rows = build_rows()

    if not rows:
        print("No rows produced.")
        return

    summarize_labels(rows)
    show_examples(rows, "PREPOP_WIN", 15)
    show_examples(rows, "LATE_POP", 15)
    signal_separation(rows)
    profile_backtest(rows)
    show_profile_top_examples(rows, "live_active", 15)
    show_profile_top_examples(rows, "prepop_v1", 15)
    print_weight_profiles()

    print()
    print("DONE.")
    print("Next move: use the separation table + profile comparison to lock the first real pre-pop weight profile.")


if __name__ == "__main__":
    main()
