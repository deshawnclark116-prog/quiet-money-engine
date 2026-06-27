#!/usr/bin/env python3
"""
Quiet Money Engine — weight comparison backtest.

Compares multiple technical-weight profiles side by side using the same
historical price/volume replay.

This does NOT change the live scorer.

It reuses the same honest limits as historical_technical_backtest.py:
- price/volume only
- no live insider replay
- no SEC/news/company replay
- no point-in-time historical universe rebuild yet

Goal:
Find whether adjusted technical weights improve 1d / 5d / 20d results before
we touch score_universe.py.
"""

import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from signals import SIGNALS

from historical_technical_backtest import (
    HORIZONS,
    BACKTEST_TOP_N,
    BACKTEST_MIN_HISTORY,
    get_universe,
    fetch_all_bars,
    fetch_benchmarks,
    choose_test_indices,
    benchmark_slice_map,
    forward_return_pct,
    bar_date,
    close_at,
)


WEIGHT_PROFILES = {
    "current_live_technical": {
        "momentum_12_1": 1.00,
        "volume_pressure_score": 0.60,
        "capital_efficiency_score": 0.55,
        "relative_strength_score": 0.50,
        "accumulation_quality_score": 0.70,
        "trend_quality_score": 0.50,
        "breakout_setup_score": 0.45,
        "liquidity_quality_score": 0.50,
        "volatility_control_score": 0.40,
        "insider_buy_score": 0.00,
    },

    "calibrated_v1": {
        "momentum_12_1": 0.45,
        "volume_pressure_score": 0.45,
        "capital_efficiency_score": 0.70,
        "relative_strength_score": 0.45,
        "accumulation_quality_score": 0.75,
        "trend_quality_score": 0.45,
        "breakout_setup_score": 0.70,
        "liquidity_quality_score": 0.75,
        "volatility_control_score": 0.55,
        "insider_buy_score": 0.00,
    },

    "quality_heavy_v2": {
        "momentum_12_1": 0.25,
        "volume_pressure_score": 0.35,
        "capital_efficiency_score": 0.80,
        "relative_strength_score": 0.40,
        "accumulation_quality_score": 0.85,
        "trend_quality_score": 0.45,
        "breakout_setup_score": 0.80,
        "liquidity_quality_score": 0.90,
        "volatility_control_score": 0.70,
        "insider_buy_score": 0.00,
    },

    "momentum_light_v3": {
        "momentum_12_1": 0.10,
        "volume_pressure_score": 0.40,
        "capital_efficiency_score": 0.75,
        "relative_strength_score": 0.55,
        "accumulation_quality_score": 0.75,
        "trend_quality_score": 0.50,
        "breakout_setup_score": 0.75,
        "liquidity_quality_score": 0.80,
        "volatility_control_score": 0.60,
        "insider_buy_score": 0.00,
    },

    "no_momentum_v4": {
        "momentum_12_1": 0.00,
        "volume_pressure_score": 0.35,
        "capital_efficiency_score": 0.80,
        "relative_strength_score": 0.50,
        "accumulation_quality_score": 0.80,
        "trend_quality_score": 0.45,
        "breakout_setup_score": 0.85,
        "liquidity_quality_score": 0.90,
        "volatility_control_score": 0.70,
        "insider_buy_score": 0.00,
    },
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def median(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return statistics.median(vals)


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def num(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def print_section(title: str):
    print("")
    print("=" * 110)
    print(title)
    print("=" * 110)


def print_table(headers: List[str], rows: List[List[Any]], max_rows: Optional[int] = None):
    if max_rows is not None:
        rows = rows[:max_rows]

    rows = [[str(x) for x in row] for row in rows]
    headers = [str(h) for h in headers]

    widths = []

    for i, h in enumerate(headers):
        width = len(h)

        for row in rows:
            if i < len(row):
                width = max(width, len(row[i]))

        widths.append(min(width, 26))

    def trim(s: str, width: int) -> str:
        if len(s) <= width:
            return s
        return s[: width - 1] + "…"

    print(" | ".join(trim(headers[i], widths[i]).ljust(widths[i]) for i in range(len(headers))))
    print("-+-".join("-" * w for w in widths))

    for row in rows:
        print(" | ".join(trim(row[i] if i < len(row) else "", widths[i]).ljust(widths[i]) for i in range(len(headers))))


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [r["return_pct"] for r in rows]
    winners = [r for r in rows if r["return_pct"] > 0]
    hit5 = [r for r in rows if r["return_pct"] >= 5.0]
    hit10 = [r for r in rows if r["return_pct"] >= 10.0]

    return {
        "n": len(rows),
        "avg": mean(returns),
        "median": median(returns),
        "win_rate": len(winners) / len(rows) * 100 if rows else None,
        "hit5": len(hit5) / len(rows) * 100 if rows else None,
        "hit10": len(hit10) / len(rows) * 100 if rows else None,
        "best": max(returns) if returns else None,
        "worst": min(returns) if returns else None,
    }


def slice_bars(bars: List[dict], end_idx: int) -> List[dict]:
    return bars[: end_idx + 1]


def score_signals_for_ticker_on_date(
    ticker: str,
    bars: List[dict],
    idx: int,
    benchmark_bars: Dict[str, List[dict]],
) -> Optional[Dict[str, Any]]:
    hist = slice_bars(bars, idx)

    if len(hist) < BACKTEST_MIN_HISTORY:
        return None

    price = close_at(bars, idx)

    if price <= 0:
        return None

    data = {
        "ticker": ticker,
        "bars": hist,
        "price": price,
        "benchmark_bars": benchmark_slice_map(benchmark_bars, idx),
        "insider_buys": [],
        "recent_insider_buy_count": 0,
    }

    signal_values = {}

    for name, fn in SIGNALS.items():
        try:
            signal_values[name] = float(fn(data))
        except Exception:
            signal_values[name] = 0.0

    return {
        "ticker": ticker,
        "date": bar_date(bars, idx),
        "idx": idx,
        "price": price,
        "signals": signal_values,
    }


def composite_from_profile(signal_values: Dict[str, float], weights: Dict[str, float]) -> float:
    composite = 0.0

    for name, value in signal_values.items():
        composite += float(value) * float(weights.get(name, 0.0))

    return composite


def run_comparison() -> Dict[str, List[Dict[str, Any]]]:
    universe = get_universe()

    print_section("CONFIG")
    print(f"Universe size: {len(universe)}")
    print(f"Universe: {', '.join(universe)}")
    print(f"Top N: {BACKTEST_TOP_N}")
    print(f"Horizons: {HORIZONS}")
    print(f"Profiles: {', '.join(WEIGHT_PROFILES.keys())}")
    print("")
    print("NOTE: price/volume-only replay. No historical SEC/news/insider replay.")

    all_bars = fetch_all_bars(universe)
    benchmark_bars = fetch_benchmarks()

    if not all_bars:
        raise SystemExit("No ticker bars loaded")

    reference_ticker = max(all_bars.keys(), key=lambda t: len(all_bars[t]))
    reference_bars = all_bars[reference_ticker]
    test_indices = choose_test_indices(reference_bars)

    print_section("TEST DATES")
    print(f"Reference ticker: {reference_ticker}")
    print(f"Test dates: {len(test_indices)}")

    if test_indices:
        print(f"First test date: {bar_date(reference_bars, test_indices[0])}")
        print(f"Last test date: {bar_date(reference_bars, test_indices[-1])}")

    outcomes_by_profile = {name: [] for name in WEIGHT_PROFILES}

    for idx in test_indices:
        base_rows = []

        for ticker, bars in all_bars.items():
            if idx >= len(bars) - max(HORIZONS):
                continue

            row = score_signals_for_ticker_on_date(
                ticker=ticker,
                bars=bars,
                idx=idx,
                benchmark_bars=benchmark_bars,
            )

            if row is not None:
                base_rows.append(row)

        if not base_rows:
            continue

        for profile_name, weights in WEIGHT_PROFILES.items():
            scored = []

            for row in base_rows:
                composite = composite_from_profile(row["signals"], weights)

                scored.append(
                    {
                        **row,
                        "composite": composite,
                    }
                )

            scored.sort(key=lambda r: r["composite"], reverse=True)
            picks = scored[:BACKTEST_TOP_N]

            for rank, pick in enumerate(picks, 1):
                bars = all_bars[pick["ticker"]]

                for horizon in HORIZONS:
                    ret = forward_return_pct(bars, idx, horizon)

                    if ret is None:
                        continue

                    outcomes_by_profile[profile_name].append(
                        {
                            "profile": profile_name,
                            "date": pick["date"],
                            "ticker": pick["ticker"],
                            "rank": rank,
                            "horizon": horizon,
                            "return_pct": ret,
                            "composite": pick["composite"],
                            "signals": pick["signals"],
                        }
                    )

    return outcomes_by_profile


def report_profile_comparison(outcomes_by_profile: Dict[str, List[Dict[str, Any]]]):
    print_section("PROFILE COMPARISON BY HORIZON")

    table = []

    for horizon in sorted(HORIZONS):
        for profile, outcomes in outcomes_by_profile.items():
            rows = [r for r in outcomes if r["horizon"] == horizon]
            s = summarize(rows)

            table.append(
                [
                    horizon,
                    profile,
                    s["n"],
                    pct(s["avg"]),
                    pct(s["median"]),
                    pct(s["win_rate"]),
                    pct(s["hit5"]),
                    pct(s["hit10"]),
                    pct(s["best"]),
                    pct(s["worst"]),
                ]
            )

    print_table(
        [
            "horizon",
            "profile",
            "n",
            "avg ret",
            "median",
            "win rate",
            "hit 5%",
            "hit 10%",
            "best",
            "worst",
        ],
        table,
    )


def report_delta_vs_current(outcomes_by_profile: Dict[str, List[Dict[str, Any]]):
    print_section("DELTA VS CURRENT LIVE TECHNICAL")

    baseline = "current_live_technical"

    table = []

    for horizon in sorted(HORIZONS):
        base_rows = [r for r in outcomes_by_profile[baseline] if r["horizon"] == horizon]
        base_s = summarize(base_rows)

        for profile, outcomes in outcomes_by_profile.items():
            if profile == baseline:
                continue

            rows = [r for r in outcomes if r["horizon"] == horizon]
            s = summarize(rows)

            avg_delta = None
            med_delta = None
            win_delta = None
            hit5_delta = None
            hit10_delta = None

            if s["avg"] is not None and base_s["avg"] is not None:
                avg_delta = s["avg"] - base_s["avg"]

            if s["median"] is not None and base_s["median"] is not None:
                med_delta = s["median"] - base_s["median"]

            if s["win_rate"] is not None and base_s["win_rate"] is not None:
                win_delta = s["win_rate"] - base_s["win_rate"]

            if s["hit5"] is not None and base_s["hit5"] is not None:
                hit5_delta = s["hit5"] - base_s["hit5"]

            if s["hit10"] is not None and base_s["hit10"] is not None:
                hit10_delta = s["hit10"] - base_s["hit10"]

            table.append(
                [
                    horizon,
                    profile,
                    pct(avg_delta),
                    pct(med_delta),
                    pct(win_delta),
                    pct(hit5_delta),
                    pct(hit10_delta),
                ]
            )

    print_table(
        [
            "horizon",
            "profile",
            "avg delta",
            "median delta",
            "win delta",
            "hit5 delta",
            "hit10 delta",
        ],
        table,
    )


def rank_bucket(rank: int) -> str:
    if rank <= 3:
        return "1-3"
    if rank <= 5:
        return "4-5"
    return "6-10"


def report_rank_buckets(outcomes_by_profile: Dict[str, List[Dict[str, Any]]]):
    print_section("RANK BUCKETS BY PROFILE")

    table = []

    for horizon in sorted(HORIZONS):
        for profile, outcomes in outcomes_by_profile.items():
            h_rows = [r for r in outcomes if r["horizon"] == horizon]

            for bucket in ["1-3", "4-5", "6-10"]:
                rows = [r for r in h_rows if rank_bucket(r["rank"]) == bucket]

                if not rows:
                    continue

                s = summarize(rows)

                table.append(
                    [
                        horizon,
                        profile,
                        bucket,
                        s["n"],
                        pct(s["avg"]),
                        pct(s["median"]),
                        pct(s["win_rate"]),
                        pct(s["best"]),
                        pct(s["worst"]),
                    ]
                )

    print_table(
        [
            "horizon",
            "profile",
            "bucket",
            "n",
            "avg ret",
            "median",
            "win rate",
            "best",
            "worst",
        ],
        table,
    )


def report_overlap(outcomes_by_profile: Dict[str, List[Dict[str, Any]]]):
    print_section("PICK OVERLAP VS CURRENT")

    baseline = "current_live_technical"

    table = []

    for horizon in sorted(HORIZONS):
        base_rows = [r for r in outcomes_by_profile[baseline] if r["horizon"] == horizon]
        base_keys = {(r["date"], r["ticker"], r["horizon"]) for r in base_rows}

        for profile, outcomes in outcomes_by_profile.items():
            if profile == baseline:
                continue

            rows = [r for r in outcomes if r["horizon"] == horizon]
            keys = {(r["date"], r["ticker"], r["horizon"]) for r in rows}

            overlap = len(base_keys & keys)
            total = len(keys)

            overlap_pct = overlap / total * 100 if total else None

            table.append(
                [
                    horizon,
                    profile,
                    overlap,
                    total,
                    pct(overlap_pct),
                ]
            )

    print_table(["horizon", "profile", "overlap picks", "total picks", "overlap %"], table)


def report_best_profile_by_horizon(outcomes_by_profile: Dict[str, List[Dict[str, Any]]]):
    print_section("BEST PROFILE SUMMARY")

    table = []

    for horizon in sorted(HORIZONS):
        scored = []

        for profile, outcomes in outcomes_by_profile.items():
            rows = [r for r in outcomes if r["horizon"] == horizon]
            s = summarize(rows)

            scored.append(
                {
                    "profile": profile,
                    "avg": s["avg"],
                    "median": s["median"],
                    "win_rate": s["win_rate"],
                    "hit5": s["hit5"],
                    "hit10": s["hit10"],
                }
            )

        best_avg = max(scored, key=lambda x: x["avg"] if x["avg"] is not None else -999)
        best_median = max(scored, key=lambda x: x["median"] if x["median"] is not None else -999)
        best_win = max(scored, key=lambda x: x["win_rate"] if x["win_rate"] is not None else -999)
        best_hit5 = max(scored, key=lambda x: x["hit5"] if x["hit5"] is not None else -999)
        best_hit10 = max(scored, key=lambda x: x["hit10"] if x["hit10"] is not None else -999)

        table.append(
            [
                horizon,
                best_avg["profile"],
                pct(best_avg["avg"]),
                best_median["profile"],
                pct(best_median["median"]),
                best_win["profile"],
                pct(best_win["win_rate"]),
                best_hit5["profile"],
                pct(best_hit5["hit5"]),
                best_hit10["profile"],
                pct(best_hit10["hit10"]),
            ]
        )

    print_table(
        [
            "horizon",
            "best avg",
            "avg",
            "best median",
            "median",
            "best win",
            "win",
            "best hit5",
            "hit5",
            "best hit10",
            "hit10",
        ],
        table,
    )


def main():
    print("")
    print("Quiet Money Engine Weight Comparison Backtest")
    print("Generated:", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    outcomes_by_profile = run_comparison()

    report_profile_comparison(outcomes_by_profile)
    report_delta_vs_current(outcomes_by_profile)
    report_best_profile_by_horizon(outcomes_by_profile)
    report_rank_buckets(outcomes_by_profile)
    report_overlap(outcomes_by_profile)


if __name__ == "__main__":
    main()
