#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Research Lab v2.

Mission:
Find which signal profiles catch stocks BEFORE the major repricing move.

This script does NOT touch production tables.
This script does NOT save picks.
This script is the research wind tunnel before production changes.

What it tests:
1. Larger ticker universe
2. Historical pre-pop windows
3. Late/chase windows
4. No-pop windows
5. Time split validation
6. Ticker holdout validation
7. Base-rate comparison
8. Gate + weight-profile comparisons
9. Case-study sanity checks

Core rule:
A model is not good because it finds stocks that already moved.
A model is good if it finds candidates before the move better than the base rate,
without simply selecting dead/no-pop bases.
"""

import os
import math
import statistics
from collections import defaultdict
from datetime import datetime, date
from typing import Any, Optional

from data_layer import get_price_history
from signals import SIGNALS as BASE_SIGNALS

try:
    from universe_builder import build_dynamic_universe
except Exception:
    build_dynamic_universe = None

try:
    from score_universe import parse_signal_weights
except Exception:
    parse_signal_weights = None

try:
    from prepop_alpha_signals import PREPOP_ALPHA_SIGNALS
except Exception:
    PREPOP_ALPHA_SIGNALS = {}


LAB_MAX_TICKERS = int(os.getenv("LAB_MAX_TICKERS", "100"))
LAB_HISTORY_DAYS = int(os.getenv("LAB_HISTORY_DAYS", "420"))
LAB_MIN_LOOKBACK = int(os.getenv("LAB_MIN_LOOKBACK", "80"))
LAB_FUTURE_WINDOW = int(os.getenv("LAB_FUTURE_WINDOW", "20"))
LAB_POP_THRESHOLD = float(os.getenv("LAB_POP_THRESHOLD", "30.0"))
LAB_TOP_PER_DATE = int(os.getenv("LAB_TOP_PER_DATE", "5"))
LAB_TRAIN_SPLIT = float(os.getenv("LAB_TRAIN_SPLIT", "0.60"))
LAB_HOLDOUT_MOD = int(os.getenv("LAB_HOLDOUT_MOD", "5"))

ALL_SIGNALS = dict(BASE_SIGNALS)
ALL_SIGNALS.update(PREPOP_ALPHA_SIGNALS)


CORE_CASE_STUDY_TICKERS = [
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
]


BROAD_SEED_UNIVERSE = [
    # Current / recent model names.
    "BOLD", "LILA", "GDC", "FTH", "TOI", "ARTV", "CGON", "IMRX", "MRBK", "IX",
    "AMD", "RIOT", "SOFI", "AFRM", "PLTR", "INTC", "F", "GM", "NVDA", "AAPL",

    # Small / mid / active speculative universe.
    "MARA", "CLSK", "HOOD", "UPST", "OPEN", "LCID", "RIVN", "CHPT", "IONQ", "SOUN",
    "BBAI", "ACHR", "JOBY", "ASTS", "RKLB", "ENVX", "QS", "PLUG", "FCEL", "DNA",
    "WULF", "BITF", "HUT", "IREN", "BTBT", "HIVE", "CIFR", "CORZ", "CAN", "HIMS",
    "RKLB", "SPCE", "LAZR", "OUST", "MVIS", "AEHR", "SMR", "OKLO", "QBTS", "RGTI",
    "SERV", "SAVA", "SOUN", "AI", "PATH", "U", "RUN", "ENPH", "SEDG", "BE",
    "BLNK", "EVGO", "NIO", "XPEV", "LI", "FSR", "NKLA", "TMC", "MP", "UUUU",

    # Biotech / event-driven names.
    "ALT", "VKTX", "IOVA", "RXRX", "CRSP", "EDIT", "NTLA", "BEAM", "VERV", "PRME",
    "BLUE", "GERN", "TGTX", "SWTX", "ARWR", "IONS", "MNKD", "OCUL", "EYPT", "KURA",

    # Higher-liquidity swing universe.
    "KMX", "BCDA", "ENR", "AAL", "UAL", "CCL", "RCL", "NCLH", "SAVE", "JBLU",
    "RDFN", "ZG", "CVNA", "CAR", "W", "ETSY", "ROKU", "SHOP", "COIN", "DKNG",
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


LIVE_FALLBACK = {
    "momentum_12_1": 1.00,
    "insider_buy_score": 0.35,
    "volume_pressure_score": 0.60,
    "capital_efficiency_score": 0.55,
    "relative_strength_score": 0.50,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.25,

    "dilution_risk_score": 0.90,
    "reverse_split_risk_score": 0.70,
    "company_insight_composite": 0.00,
}


MISSION_HYBRID_CORE = {
    # Existing signals, controlled.
    "momentum_12_1": 0.12,
    "volume_pressure_score": 0.12,
    "relative_strength_score": 0.22,
    "trend_quality_score": 0.25,
    "breakout_setup_score": 0.55,
    "accumulation_quality_score": 0.30,

    # Quality/tradability.
    "capital_efficiency_score": 0.65,
    "liquidity_quality_score": 0.65,
    "volatility_control_score": 0.55,
    "insider_buy_score": 0.35,

    # New pre-pop signals if available.
    "quiet_base_score": 0.20,
    "compression_score": 0.35,
    "controlled_volume_wake_score": 0.65,
    "pre_pop_timing_score": 0.45,
    "base_breakout_proximity_score": 0.70,
    "early_relative_strength_score": 0.55,
    "late_chase_penalty": 0.85,

    # Company/risk.
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,
    "dilution_risk_score": 0.95,
    "reverse_split_risk_score": 0.95,
    "company_insight_composite": 0.00,
}


MISSION_HYBRID_DEFENSIVE = {
    "momentum_12_1": 0.05,
    "volume_pressure_score": 0.05,
    "relative_strength_score": 0.15,
    "trend_quality_score": 0.15,
    "breakout_setup_score": 0.35,
    "accumulation_quality_score": 0.20,

    "capital_efficiency_score": 0.55,
    "liquidity_quality_score": 0.70,
    "volatility_control_score": 0.65,
    "insider_buy_score": 0.35,

    "quiet_base_score": 0.35,
    "compression_score": 0.55,
    "controlled_volume_wake_score": 0.80,
    "pre_pop_timing_score": 0.75,
    "base_breakout_proximity_score": 0.75,
    "early_relative_strength_score": 0.50,
    "late_chase_penalty": 1.10,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.00,
    "dilution_risk_score": 1.00,
    "reverse_split_risk_score": 1.00,
    "company_insight_composite": 0.00,
}


MISSION_HYBRID_AGGRESSIVE_AFTER_GATE = {
    # More aggressive, but only after a gate removes the late garbage.
    "momentum_12_1": 0.22,
    "volume_pressure_score": 0.18,
    "relative_strength_score": 0.32,
    "trend_quality_score": 0.30,
    "breakout_setup_score": 0.60,
    "accumulation_quality_score": 0.35,

    "capital_efficiency_score": 0.65,
    "liquidity_quality_score": 0.60,
    "volatility_control_score": 0.45,
    "insider_buy_score": 0.35,

    "quiet_base_score": 0.10,
    "compression_score": 0.25,
    "controlled_volume_wake_score": 0.55,
    "pre_pop_timing_score": 0.35,
    "base_breakout_proximity_score": 0.65,
    "early_relative_strength_score": 0.55,
    "late_chase_penalty": 0.70,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,
    "dilution_risk_score": 0.95,
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


def mean(values: list[float]) -> Optional[float]:
    vals = [safe_float(v, None) for v in values]
    vals = [v for v in vals if v is not None and not math.isnan(v)]

    if not vals:
        return None

    return statistics.mean(vals)


def median(values: list[float]) -> Optional[float]:
    vals = [safe_float(v, None) for v in values]
    vals = [v for v in vals if v is not None and not math.isnan(v)]

    if not vals:
        return None

    return statistics.median(vals)


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.1f}%"


def fmt_num(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def get_lab_universe() -> list[str]:
    raw = os.getenv("LAB_UNIVERSE", "").strip()
    tickers = []

    if raw:
        for item in raw.split(","):
            t = clean_ticker(item)
            if t and t not in tickers:
                tickers.append(t)
        return tickers[:LAB_MAX_TICKERS]

    for t in BROAD_SEED_UNIVERSE:
        t = clean_ticker(t)
        if t and t not in tickers:
            tickers.append(t)

    if build_dynamic_universe:
        try:
            dyn = build_dynamic_universe()
            for t in dyn:
                t = clean_ticker(t)
                if t and t not in tickers:
                    tickers.append(t)
                if len(tickers) >= LAB_MAX_TICKERS:
                    break
        except Exception as exc:
            print("Dynamic universe failed in research lab:", exc)

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


def is_core_early(ctx: dict) -> bool:
    return (
        (ctx.get("pre_1d") is None or ctx["pre_1d"] <= 15.0)
        and (ctx.get("pre_3d") is None or ctx["pre_3d"] <= 25.0)
        and (ctx.get("pre_5d") is None or ctx["pre_5d"] <= 35.0)
        and (ctx.get("pre_10d") is None or ctx["pre_10d"] <= 45.0)
        and (ctx.get("vs_sma20") is None or ctx["vs_sma20"] <= 25.0)
    )


def is_late(ctx: dict) -> bool:
    return (
        (ctx.get("pre_1d") is not None and ctx["pre_1d"] > 20.0)
        or (ctx.get("pre_3d") is not None and ctx["pre_3d"] > 30.0)
        or (ctx.get("pre_5d") is not None and ctx["pre_5d"] > 40.0)
        or (ctx.get("pre_10d") is not None and ctx["pre_10d"] > 55.0)
        or (ctx.get("vs_sma20") is not None and ctx["vs_sma20"] > 30.0)
    )


def label_event(future_max: Optional[float], ctx: dict) -> str:
    if future_max is None:
        return "NO_FUTURE"

    popped = future_max >= LAB_POP_THRESHOLD
    early = is_core_early(ctx)
    late = is_late(ctx)

    if popped and early:
        return "PREPOP_WIN"

    if popped and late:
        return "LATE_POP"

    if popped:
        return "MESSY_POP"

    if late:
        return "LATE_NO_POP"

    return "NO_POP"


def load_benchmark_bars() -> dict:
    out = {}

    for ticker in ["SPY", "QQQ"]:
        try:
            out[ticker] = normalize_bars(get_price_history(ticker, days=LAB_HISTORY_DAYS))
            print(f"{ticker} benchmark bars: {len(out[ticker])}")
        except Exception as exc:
            print(f"{ticker} benchmark failed: {exc}")

    return out


def align_benchmarks(benchmark_bars: dict, current_date: date) -> dict:
    out = {}

    for ticker, bars in benchmark_bars.items():
        usable = []

        for b in bars:
            d = parse_bar_date(b.get("date"))

            if d and d <= current_date:
                usable.append(b)

        if usable:
            out[ticker] = usable

    return out


def score_signals(ticker: str, bars_until_now: list[dict], benchmark_bars: dict) -> dict:
    current_date = parse_bar_date(bars_until_now[-1].get("date"))
    benchmark_context = align_benchmarks(benchmark_bars, current_date) if current_date else {}

    ticker_data = {
        "ticker": ticker,
        "bars": bars_until_now,
        "price": safe_float(bars_until_now[-1].get("close"), 0.0),
        "avg_dollar_volume_20": avg_dollar_volume(bars_until_now, 20),
        "insider_buys": [],
        "recent_insider_buy_count": 0,
        "benchmark_bars": benchmark_context,
    }

    signal_values = {}

    for name, fn in ALL_SIGNALS.items():
        try:
            signal_values[name] = float(fn(ticker_data))
        except Exception:
            signal_values[name] = 0.0

    return signal_values


def composite(row: dict, weights: dict) -> float:
    sigs = row.get("signals") or {}
    total = 0.0

    for name, weight in weights.items():
        total += safe_float(sigs.get(name), 0.0) * safe_float(weight, 0.0)

    return total


def pass_max(value: Optional[float], max_allowed: float) -> bool:
    if value is None:
        return True

    return value <= max_allowed


def gate_none(row: dict) -> bool:
    return True


def gate_loose(row: dict) -> bool:
    return (
        pass_max(row.get("pre_1d"), 18.0)
        and pass_max(row.get("pre_3d"), 30.0)
        and pass_max(row.get("pre_5d"), 40.0)
        and pass_max(row.get("pre_10d"), 55.0)
        and pass_max(row.get("vs_sma20"), 30.0)
    )


def gate_core(row: dict) -> bool:
    return (
        pass_max(row.get("pre_1d"), 15.0)
        and pass_max(row.get("pre_3d"), 25.0)
        and pass_max(row.get("pre_5d"), 35.0)
        and pass_max(row.get("pre_10d"), 45.0)
        and pass_max(row.get("vs_sma20"), 25.0)
    )


def gate_strict(row: dict) -> bool:
    return (
        pass_max(row.get("pre_1d"), 12.0)
        and pass_max(row.get("pre_3d"), 20.0)
        and pass_max(row.get("pre_5d"), 25.0)
        and pass_max(row.get("pre_10d"), 35.0)
        and pass_max(row.get("vs_sma20"), 20.0)
    )


GATES = {
    "none": gate_none,
    "loose": gate_loose,
    "core": gate_core,
    "strict": gate_strict,
}


def get_profiles() -> dict:
    profiles = {
        "live_active": LIVE_FALLBACK,
        "quality_heavy_v2": QUALITY_HEAVY_V2,
        "mission_hybrid_core": MISSION_HYBRID_CORE,
        "mission_hybrid_def": MISSION_HYBRID_DEFENSIVE,
        "mission_hybrid_aggr": MISSION_HYBRID_AGGRESSIVE_AFTER_GATE,
    }

    if parse_signal_weights:
        try:
            profiles["render_active"] = parse_signal_weights()
        except Exception:
            pass

    return profiles


def build_research_rows() -> list[dict]:
    tickers = get_lab_universe()

    print()
    print("PRE-POP RESEARCH LAB V2")
    print("=" * 80)
    print("Tickers:", len(tickers))
    print("History days:", LAB_HISTORY_DAYS)
    print("Future window:", LAB_FUTURE_WINDOW)
    print("Pop threshold:", f"+{LAB_POP_THRESHOLD:.1f}%")
    print("Top per date:", LAB_TOP_PER_DATE)
    print("Signals:", len(ALL_SIGNALS))
    print("New alpha signals loaded:", bool(PREPOP_ALPHA_SIGNALS))
    print()

    benchmark_bars = load_benchmark_bars()

    rows = []

    for idx, ticker in enumerate(tickers, 1):
        try:
            bars = normalize_bars(get_price_history(ticker, days=LAB_HISTORY_DAYS))
        except Exception as exc:
            print(f"{idx:>3}. {ticker:<7} fetch failed: {exc}")
            continue

        needed = LAB_MIN_LOOKBACK + LAB_FUTURE_WINDOW + 5

        if len(bars) < needed:
            print(f"{idx:>3}. {ticker:<7} skipped bars={len(bars)} need={needed}")
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

            bars_until_now = bars[: i + 1]
            sigs = score_signals(ticker, bars_until_now, benchmark_bars)

            rows.append(
                {
                    "ticker": ticker,
                    "date": bars[i]["date"],
                    "price": price,
                    "future_max_return": fut_max,
                    "future_end_return": fut_end,
                    "label": label,
                    "signals": sigs,
                    **ctx,
                }
            )

            made += 1

        print(f"{idx:>3}. {ticker:<7} bars={len(bars)} rows={made}")

    return rows


def split_rows(rows: list[dict]) -> dict:
    dates = sorted({r["date"] for r in rows})
    tickers = sorted({r["ticker"] for r in rows})

    if not dates:
        return {"all": rows}

    split_idx = max(1, int(len(dates) * LAB_TRAIN_SPLIT))
    split_date = dates[split_idx - 1]

    holdout_tickers = set()
    for i, ticker in enumerate(tickers):
        if LAB_HOLDOUT_MOD > 0 and i % LAB_HOLDOUT_MOD == 0:
            holdout_tickers.add(ticker)

    splits = {
        "all": rows,
        "train_time": [r for r in rows if r["date"] <= split_date],
        "test_time": [r for r in rows if r["date"] > split_date],
        "holdout_tickers": [r for r in rows if r["ticker"] in holdout_tickers],
        "non_holdout_tickers": [r for r in rows if r["ticker"] not in holdout_tickers],
    }

    print()
    print("SPLIT INFO")
    print("-" * 80)
    print("date_count:", len(dates))
    print("ticker_count:", len(tickers))
    print("time_split_date:", split_date)
    print("holdout_tickers:", ", ".join(sorted(holdout_tickers)[:40]))
    print("holdout_count:", len(holdout_tickers))

    for name, subset in splits.items():
        print(f"{name:20s} rows={len(subset)}")

    return splits


def label_counts(rows: list[dict]) -> dict:
    counts = defaultdict(int)

    for r in rows:
        counts[r["label"]] += 1

    return dict(counts)


def print_label_counts(name: str, rows: list[dict]) -> None:
    counts = label_counts(rows)
    total = len(rows)
    pre = counts.get("PREPOP_WIN", 0)
    late = counts.get("LATE_POP", 0) + counts.get("LATE_NO_POP", 0)

    print()
    print(f"LABEL COUNTS — {name}")
    print("-" * 80)
    print("total_rows:", total)
    print("base_prepop_rate:", f"{(pre / total * 100.0) if total else 0:.2f}%")
    print("base_late_rate:", f"{(late / total * 100.0) if total else 0:.2f}%")

    for label, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        pct_val = count / total * 100.0 if total else 0.0
        print(f"{label:15s} {count:6d} {pct_val:7.2f}%")


def evaluate_selection(rows: list[dict], gate_name: str, gate_fn, profile_name: str, weights: dict) -> Optional[dict]:
    by_date = defaultdict(list)

    for r in rows:
        by_date[r["date"]].append(r)

    picks = []
    eligible_total = 0
    dates_with_pick = 0

    for d, day_rows in by_date.items():
        eligible = [r for r in day_rows if gate_fn(r)]
        eligible_total += len(eligible)

        if not eligible:
            continue

        scored = []

        for r in eligible:
            rr = dict(r)
            rr["score"] = composite(r, weights)
            rr["gate"] = gate_name
            rr["profile"] = profile_name
            scored.append(rr)

        scored.sort(key=lambda x: x["score"], reverse=True)

        selected = scored[:LAB_TOP_PER_DATE]

        if selected:
            dates_with_pick += 1
            picks.extend(selected)

    if not picks:
        return None

    n = len(picks)
    pre = sum(1 for r in picks if r["label"] == "PREPOP_WIN")
    late = sum(1 for r in picks if r["label"] in {"LATE_POP", "LATE_NO_POP"})
    messy = sum(1 for r in picks if r["label"] == "MESSY_POP")
    nopop = sum(1 for r in picks if r["label"] == "NO_POP")

    futs = [safe_float(r.get("future_max_return"), 0.0) for r in picks]

    return {
        "gate": gate_name,
        "profile": profile_name,
        "picks": n,
        "eligible": eligible_total,
        "dates_with_pick": dates_with_pick,
        "prepop_pct": pre / n * 100.0,
        "late_pct": late / n * 100.0,
        "messy_pct": messy / n * 100.0,
        "nopop_pct": nopop / n * 100.0,
        "avg_fut": mean(futs),
        "med_fut": median(futs),
        "avg_pre1": mean([safe_float(r.get("pre_1d"), 0.0) for r in picks]),
        "avg_pre5": mean([safe_float(r.get("pre_5d"), 0.0) for r in picks]),
        "avg_pre10": mean([safe_float(r.get("pre_10d"), 0.0) for r in picks]),
        "avg_vs20": mean([safe_float(r.get("vs_sma20"), 0.0) for r in picks]),
        "picks_list": picks,
    }


def evaluate_split(split_name: str, rows: list[dict]) -> list[dict]:
    profiles = get_profiles()
    results = []

    for gate_name, gate_fn in GATES.items():
        for profile_name, weights in profiles.items():
            result = evaluate_selection(rows, gate_name, gate_fn, profile_name, weights)

            if result:
                result["split"] = split_name
                results.append(result)

    return results


def print_results_table(title: str, results: list[dict]) -> None:
    print()
    print(title)
    print("-" * 170)
    print(
        "split           | gate   | profile              | picks | elig  | prepop% | late% | messy% | nopop% | avg_fut | med_fut | pre1 | pre5 | pre10 | vs20"
    )
    print("-" * 170)

    ordered = sorted(
        results,
        key=lambda r: (
            r["split"],
            r["gate"],
            r["profile"],
        ),
    )

    for r in ordered:
        print(
            f"{r['split']:15s} | "
            f"{r['gate']:6s} | "
            f"{r['profile']:20s} | "
            f"{r['picks']:5d} | "
            f"{r['eligible']:5d} | "
            f"{r['prepop_pct']:7.2f} | "
            f"{r['late_pct']:5.2f} | "
            f"{r['messy_pct']:6.2f} | "
            f"{r['nopop_pct']:6.2f} | "
            f"{fmt_num(r['avg_fut']):>7s} | "
            f"{fmt_num(r['med_fut']):>7s} | "
            f"{fmt_num(r['avg_pre1']):>5s} | "
            f"{fmt_num(r['avg_pre5']):>5s} | "
            f"{fmt_num(r['avg_pre10']):>6s} | "
            f"{fmt_num(r['avg_vs20']):>5s}"
        )


def print_best_by_split(results: list[dict]) -> None:
    print()
    print("BEST CANDIDATES BY SPLIT")
    print("-" * 120)

    by_split = defaultdict(list)

    for r in results:
        by_split[r["split"]].append(r)

    for split, items in by_split.items():
        print()
        print(split)
        print("-" * 120)

        ranked = sorted(
            items,
            key=lambda r: (
                r["prepop_pct"],
                -r["late_pct"],
                r["avg_fut"] if r["avg_fut"] is not None else -999,
                r["med_fut"] if r["med_fut"] is not None else -999,
            ),
            reverse=True,
        )

        for r in ranked[:8]:
            print(
                f"{r['gate']:6s} | {r['profile']:20s} | "
                f"prepop={r['prepop_pct']:.2f}% late={r['late_pct']:.2f}% "
                f"avg_fut={fmt_num(r['avg_fut'])} med_fut={fmt_num(r['med_fut'])} "
                f"pre5={fmt_num(r['avg_pre5'])} pre10={fmt_num(r['avg_pre10'])}"
            )


def score_stability(results: list[dict]) -> None:
    """
    Looks for combinations that are strong across all/test/holdout.
    """
    wanted_splits = {"all", "test_time", "holdout_tickers"}
    grouped = defaultdict(dict)

    for r in results:
        key = (r["gate"], r["profile"])
        grouped[key][r["split"]] = r

    candidates = []

    for (gate, profile), by_split in grouped.items():
        if not wanted_splits.issubset(set(by_split.keys())):
            continue

        all_r = by_split["all"]
        test_r = by_split["test_time"]
        hold_r = by_split["holdout_tickers"]

        score = (
            test_r["prepop_pct"] * 2.0
            + hold_r["prepop_pct"] * 2.0
            + all_r["prepop_pct"]
            - test_r["late_pct"] * 1.5
            - hold_r["late_pct"] * 1.5
            + (test_r["avg_fut"] or 0) * 0.15
            + (hold_r["avg_fut"] or 0) * 0.15
        )

        candidates.append((score, gate, profile, all_r, test_r, hold_r))

    candidates.sort(key=lambda x: x[0], reverse=True)

    print()
    print("STABILITY SCOREBOARD")
    print("Ranks gate/profile combos across all + time holdout + ticker holdout.")
    print("-" * 150)

    for score, gate, profile, all_r, test_r, hold_r in candidates[:12]:
        print(
            f"{score:7.2f} | {gate:6s} | {profile:20s} | "
            f"ALL pre={all_r['prepop_pct']:.2f} late={all_r['late_pct']:.2f} fut={fmt_num(all_r['avg_fut'])} | "
            f"TEST pre={test_r['prepop_pct']:.2f} late={test_r['late_pct']:.2f} fut={fmt_num(test_r['avg_fut'])} | "
            f"HOLD pre={hold_r['prepop_pct']:.2f} late={hold_r['late_pct']:.2f} fut={fmt_num(hold_r['avg_fut'])}"
        )


def print_signal_separation(rows: list[dict]) -> None:
    pre = [r for r in rows if r["label"] == "PREPOP_WIN"]
    late = [r for r in rows if r["label"] in {"LATE_POP", "LATE_NO_POP"}]
    nopop = [r for r in rows if r["label"] == "NO_POP"]

    print()
    print("SIGNAL SEPARATION — all rows")
    print("Positive pre-late means stronger before true pre-pop wins than late/chase rows.")
    print("-" * 125)
    print("signal                           | pre_avg | late_avg | nopop_avg | pre-late | pre-nopop")
    print("-" * 125)

    lines = []

    for name in sorted(ALL_SIGNALS.keys()):
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
            f"{fmt_num(pre_avg):>7s} | "
            f"{fmt_num(late_avg):>8s} | "
            f"{fmt_num(nopop_avg):>9s} | "
            f"{fmt_num(pre_late):>8s} | "
            f"{fmt_num(pre_nopop):>10s}"
        )


def print_top_picks_for_best(results: list[dict], limit: int = 20) -> None:
    by_split = [r for r in results if r["split"] == "test_time"]

    if not by_split:
        return

    best = sorted(
        by_split,
        key=lambda r: (
            r["prepop_pct"],
            -r["late_pct"],
            r["avg_fut"] if r["avg_fut"] is not None else -999,
        ),
        reverse=True,
    )[:3]

    for r in best:
        picks = list(r["picks_list"])
        picks.sort(key=lambda x: x["score"], reverse=True)

        print()
        print(f"TOP PICKS — test_time gate={r['gate']} profile={r['profile']}")
        print("-" * 130)
        print("ticker | date       | score | label       | fut_max | pre1   | pre5   | pre10  | vs20")
        print("-" * 130)

        for p in picks[:limit]:
            print(
                f"{p['ticker']:6s} | "
                f"{p['date']} | "
                f"{p['score']:5.2f} | "
                f"{p['label']:11s} | "
                f"{fmt_pct(p.get('future_max_return')):>7s} | "
                f"{fmt_pct(p.get('pre_1d')):>6s} | "
                f"{fmt_pct(p.get('pre_5d')):>6s} | "
                f"{fmt_pct(p.get('pre_10d')):>6s} | "
                f"{fmt_pct(p.get('vs_sma20')):>6s}"
            )


def case_study_sanity(rows: list[dict]) -> None:
    print()
    print("CASE-STUDY SANITY CHECK")
    print("These names do not set the weights. They only verify expected behavior.")
    print("-" * 120)

    by_ticker = defaultdict(list)

    for r in rows:
        if r["ticker"] in CORE_CASE_STUDY_TICKERS:
            by_ticker[r["ticker"]].append(r)

    for ticker in CORE_CASE_STUDY_TICKERS:
        sample = by_ticker.get(ticker, [])

        if not sample:
            print(f"{ticker:6s} no rows")
            continue

        sample.sort(key=lambda r: r["date"])

        late = sum(1 for r in sample if r["label"] in {"LATE_POP", "LATE_NO_POP"})
        pre = sum(1 for r in sample if r["label"] == "PREPOP_WIN")
        pop = sum(1 for r in sample if r["label"] in {"PREPOP_WIN", "LATE_POP", "MESSY_POP"})
        total = len(sample)

        latest = sample[-1]

        print(
            f"{ticker:6s} rows={total:4d} prepop={pre:4d} late={late:4d} pop_any={pop:4d} | "
            f"latest {latest['date']} price={latest['price']:.2f} "
            f"pre1={fmt_pct(latest.get('pre_1d'))} pre5={fmt_pct(latest.get('pre_5d'))} "
            f"pre10={fmt_pct(latest.get('pre_10d'))} vs20={fmt_pct(latest.get('vs_sma20'))} "
            f"label={latest['label']}"
        )


def print_recommendation(results: list[dict]) -> None:
    print()
    print("RESEARCH DECISION")
    print("-" * 120)

    stable = []

    grouped = defaultdict(dict)

    for r in results:
        grouped[(r["gate"], r["profile"])][r["split"]] = r

    for key, by_split in grouped.items():
        if "test_time" not in by_split or "holdout_tickers" not in by_split:
            continue

        test = by_split["test_time"]
        hold = by_split["holdout_tickers"]

        # Hard requirements for a production candidate.
        if test["late_pct"] > 3.0:
            continue

        if hold["late_pct"] > 3.0:
            continue

        if test["prepop_pct"] < 6.0:
            continue

        if hold["prepop_pct"] < 6.0:
            continue

        if test["avg_fut"] is None or test["avg_fut"] < 10.0:
            continue

        score = (
            test["prepop_pct"] * 2
            + hold["prepop_pct"] * 2
            + (test["avg_fut"] or 0) * 0.25
            + (hold["avg_fut"] or 0) * 0.25
            - test["late_pct"] * 2
            - hold["late_pct"] * 2
        )

        stable.append((score, key, test, hold))

    stable.sort(key=lambda x: x[0], reverse=True)

    if not stable:
        print("No production candidate met the hard requirements yet.")
        print("That means we continue research instead of patching live scoring.")
        return

    score, (gate, profile), test, hold = stable[0]

    print("Best production candidate from this run:")
    print(f"gate={gate}")
    print(f"profile={profile}")
    print(f"stability_score={score:.2f}")
    print(
        f"test_time: prepop={test['prepop_pct']:.2f}% late={test['late_pct']:.2f}% "
        f"avg_fut={fmt_num(test['avg_fut'])} med_fut={fmt_num(test['med_fut'])}"
    )
    print(
        f"holdout_tickers: prepop={hold['prepop_pct']:.2f}% late={hold['late_pct']:.2f}% "
        f"avg_fut={fmt_num(hold['avg_fut'])} med_fut={fmt_num(hold['med_fut'])}"
    )
    print()
    print("This is not automatically deployed.")
    print("Next step would be a production patch only after reviewing top picks and case-study sanity.")


def main() -> None:
    rows = build_research_rows()

    if not rows:
        print("No rows produced.")
        return

    splits = split_rows(rows)

    all_results = []

    for split_name, subset in splits.items():
        print_label_counts(split_name, subset)
        all_results.extend(evaluate_split(split_name, subset))

    print_results_table("FULL GATE + PROFILE RESULTS", all_results)
    print_best_by_split(all_results)
    score_stability(all_results)
    print_signal_separation(rows)
    print_top_picks_for_best(all_results, limit=20)
    case_study_sanity(rows)
    print_recommendation(all_results)

    print()
    print("DONE.")
    print("No production code was changed.")
    print("This lab decides whether the model is ready for a live scoring patch.")


if __name__ == "__main__":
    main()
