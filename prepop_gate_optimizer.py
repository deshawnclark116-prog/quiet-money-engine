#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Gate Optimizer.

Purpose:
Find the right architecture for the actual mission:

1. First reject stocks that already moved too far before the alert.
2. Then rank the remaining early/setup names by predictive strength.

This does NOT change production.
It compares gate + weight combinations so we can decide what belongs in score_universe.py.
"""

from collections import defaultdict
from typing import Optional, Any
import statistics

import prepop_weight_lab as lab

from signals import SIGNALS as BASE_SIGNALS
from prepop_alpha_signals import PREPOP_ALPHA_SIGNALS


ALL_SIGNALS = dict(BASE_SIGNALS)
ALL_SIGNALS.update(PREPOP_ALPHA_SIGNALS)

lab.SIGNALS = ALL_SIGNALS


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def mean(values):
    vals = [safe_float(v, None) for v in values]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return statistics.mean(vals)


def median(values):
    vals = [safe_float(v, None) for v in values]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def fmt(x, digits=2):
    if x is None:
        return "n/a"
    return f"{x:.{digits}f}"


def composite(row: dict, weights: dict) -> float:
    total = 0.0
    sigs = row.get("signals") or {}

    for name, weight in weights.items():
        total += safe_float(sigs.get(name), 0.0) * safe_float(weight, 0.0)

    return total


def pass_threshold(value: Optional[float], max_value: float) -> bool:
    if value is None:
        return True
    return value <= max_value


def gate_none(row: dict) -> bool:
    return True


def gate_loose(row: dict) -> bool:
    """
    Loose pre-pop gate.

    Allows controlled early strength.
    Rejects only obvious chase/late conditions.
    """
    sigs = row.get("signals") or {}

    return (
        pass_threshold(row.get("pre_1d"), 18.0)
        and pass_threshold(row.get("pre_3d"), 30.0)
        and pass_threshold(row.get("pre_5d"), 40.0)
        and pass_threshold(row.get("pre_10d"), 55.0)
        and pass_threshold(row.get("vs_sma20"), 30.0)
        and safe_float(sigs.get("late_chase_penalty"), 0.0) >= -2.00
    )


def gate_core(row: dict) -> bool:
    """
    Core mission gate.

    This is the likely production candidate:
    not too strict, but rejects stocks that already made the easy move.
    """
    sigs = row.get("signals") or {}

    return (
        pass_threshold(row.get("pre_1d"), 15.0)
        and pass_threshold(row.get("pre_3d"), 25.0)
        and pass_threshold(row.get("pre_5d"), 35.0)
        and pass_threshold(row.get("pre_10d"), 45.0)
        and pass_threshold(row.get("vs_sma20"), 25.0)
        and safe_float(sigs.get("late_chase_penalty"), 0.0) >= -1.25
    )


def gate_strict(row: dict) -> bool:
    """
    Strict pre-pop gate.

    Used to see what happens if we demand very early setups only.
    """
    sigs = row.get("signals") or {}

    return (
        pass_threshold(row.get("pre_1d"), 12.0)
        and pass_threshold(row.get("pre_3d"), 20.0)
        and pass_threshold(row.get("pre_5d"), 25.0)
        and pass_threshold(row.get("pre_10d"), 35.0)
        and pass_threshold(row.get("vs_sma20"), 20.0)
        and safe_float(sigs.get("late_chase_penalty"), 0.0) >= -0.75
    )


GATES = {
    "none": gate_none,
    "loose": gate_loose,
    "core": gate_core,
    "strict": gate_strict,
}


LIVE_ACTIVE = lab.parse_signal_weights() if lab.parse_signal_weights else lab.QUALITY_HEAVY_V2


QUALITY_HEAVY_V2 = lab.QUALITY_HEAVY_V2


MISSION_HYBRID_V1 = {
    # Old signals allowed, but not allowed to dominate.
    "momentum_12_1": 0.10,
    "volume_pressure_score": 0.10,
    "relative_strength_score": 0.20,
    "trend_quality_score": 0.20,
    "breakout_setup_score": 0.45,
    "accumulation_quality_score": 0.25,

    # Quality and tradability still matter.
    "capital_efficiency_score": 0.60,
    "liquidity_quality_score": 0.60,
    "volatility_control_score": 0.50,
    "insider_buy_score": 0.35,

    # New pre-pop layer.
    "quiet_base_score": 0.20,
    "compression_score": 0.40,
    "controlled_volume_wake_score": 0.65,
    "pre_pop_timing_score": 0.45,
    "base_breakout_proximity_score": 0.70,
    "early_relative_strength_score": 0.55,
    "late_chase_penalty": 0.80,

    # Company/risk layer.
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,
    "dilution_risk_score": 0.95,
    "reverse_split_risk_score": 0.95,
    "company_insight_composite": 0.00,
}


MISSION_HYBRID_V2 = {
    # Slightly more aggressive after the gate.
    "momentum_12_1": 0.18,
    "volume_pressure_score": 0.16,
    "relative_strength_score": 0.28,
    "trend_quality_score": 0.28,
    "breakout_setup_score": 0.55,
    "accumulation_quality_score": 0.30,

    "capital_efficiency_score": 0.65,
    "liquidity_quality_score": 0.60,
    "volatility_control_score": 0.45,
    "insider_buy_score": 0.35,

    "quiet_base_score": 0.10,
    "compression_score": 0.30,
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


MISSION_HYBRID_V3_DEFENSIVE = {
    # Defensive version: stronger anti-chase, less hot-move recognition.
    "momentum_12_1": 0.05,
    "volume_pressure_score": 0.05,
    "relative_strength_score": 0.15,
    "trend_quality_score": 0.15,
    "breakout_setup_score": 0.35,
    "accumulation_quality_score": 0.20,

    "capital_efficiency_score": 0.55,
    "liquidity_quality_score": 0.65,
    "volatility_control_score": 0.60,
    "insider_buy_score": 0.35,

    "quiet_base_score": 0.35,
    "compression_score": 0.55,
    "controlled_volume_wake_score": 0.75,
    "pre_pop_timing_score": 0.70,
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


PROFILES = {
    "live_active": LIVE_ACTIVE,
    "quality_heavy_v2": QUALITY_HEAVY_V2,
    "mission_hybrid_v1": MISSION_HYBRID_V1,
    "mission_hybrid_v2": MISSION_HYBRID_V2,
    "mission_hybrid_v3_def": MISSION_HYBRID_V3_DEFENSIVE,
}


def label_counts(rows):
    counts = defaultdict(int)

    for r in rows:
        counts[r.get("label")] += 1

    return counts


def evaluate_combo(rows, gate_name, gate_fn, profile_name, weights, top_per_date):
    rows_by_date = defaultdict(list)

    for r in rows:
        rows_by_date[r["date"]].append(r)

    picks = []
    eligible_count = 0
    date_count = 0
    dates_with_pick = 0

    for d, day_rows in rows_by_date.items():
        date_count += 1

        eligible = [r for r in day_rows if gate_fn(r)]
        eligible_count += len(eligible)

        if not eligible:
            continue

        dates_with_pick += 1

        scored = []

        for r in eligible:
            rr = dict(r)
            rr["score"] = composite(r, weights)
            rr["gate"] = gate_name
            rr["profile"] = profile_name
            scored.append(rr)

        scored.sort(key=lambda x: x["score"], reverse=True)
        picks.extend(scored[:top_per_date])

    if not picks:
        return None

    n = len(picks)

    prepop = sum(1 for r in picks if r["label"] == "PREPOP_WIN")
    late = sum(1 for r in picks if r["label"] in {"LATE_POP", "LATE_NO_POP"})
    messy = sum(1 for r in picks if r["label"] == "MESSY_POP")
    nopop = sum(1 for r in picks if r["label"] == "NO_POP")

    fut = [safe_float(r.get("future_max_return"), 0.0) for r in picks]
    med_fut = median(fut)
    avg_fut = mean(fut)

    return {
        "gate": gate_name,
        "profile": profile_name,
        "picks": n,
        "dates": date_count,
        "dates_with_pick": dates_with_pick,
        "eligible": eligible_count,
        "prepop_pct": prepop / n * 100.0,
        "late_pct": late / n * 100.0,
        "messy_pct": messy / n * 100.0,
        "nopop_pct": nopop / n * 100.0,
        "avg_fut": avg_fut,
        "med_fut": med_fut,
        "avg_pre5": mean([safe_float(r.get("pre_5d"), 0.0) for r in picks]),
        "avg_pre10": mean([safe_float(r.get("pre_10d"), 0.0) for r in picks]),
        "picks_list": picks,
    }


def print_results(results):
    print()
    print("GATE + WEIGHT COMPARISON")
    print("Goal: keep late% low, improve prepop%, avoid boring no-pop selection.")
    print("-" * 150)
    print(
        "gate     | profile              | picks | dates | elig | prepop% | late% | messy% | nopop% | avg_fut | med_fut | avg_pre5 | avg_pre10"
    )
    print("-" * 150)

    for r in results:
        print(
            f"{r['gate']:8s} | "
            f"{r['profile']:20s} | "
            f"{r['picks']:5d} | "
            f"{r['dates_with_pick']:5d} | "
            f"{r['eligible']:5d} | "
            f"{r['prepop_pct']:7.2f} | "
            f"{r['late_pct']:5.2f} | "
            f"{r['messy_pct']:6.2f} | "
            f"{r['nopop_pct']:6.2f} | "
            f"{fmt(r['avg_fut']):>7s} | "
            f"{fmt(r['med_fut']):>7s} | "
            f"{fmt(r['avg_pre5']):>8s} | "
            f"{fmt(r['avg_pre10']):>9s}"
        )


def print_top_examples(result, limit=20):
    picks = list(result["picks_list"])
    picks.sort(key=lambda r: r["score"], reverse=True)

    print()
    print(f"TOP PICKS — gate={result['gate']} profile={result['profile']}")
    print("-" * 125)
    print("ticker | date       | score | label       | fut_max | pre1   | pre5   | pre10  | vs20")
    print("-" * 125)

    for r in picks[:limit]:
        print(
            f"{r['ticker']:6s} | "
            f"{r['date']} | "
            f"{r['score']:5.2f} | "
            f"{r['label']:11s} | "
            f"{lab.fmt_pct(r.get('future_max_return')):>7s} | "
            f"{lab.fmt_pct(r.get('pre_1d')):>6s} | "
            f"{lab.fmt_pct(r.get('pre_5d')):>6s} | "
            f"{lab.fmt_pct(r.get('pre_10d')):>6s} | "
            f"{lab.fmt_pct(r.get('vs_sma20')):>6s}"
        )


def print_best_results(results):
    ranked = sorted(
        results,
        key=lambda r: (
            r["prepop_pct"],
            -r["late_pct"],
            r["avg_fut"] if r["avg_fut"] is not None else -999,
        ),
        reverse=True,
    )

    print()
    print("BEST BY PREPOP RATE")
    print("-" * 120)

    for r in ranked[:8]:
        print(
            f"{r['gate']:8s} | {r['profile']:20s} | "
            f"prepop {r['prepop_pct']:.2f}% | "
            f"late {r['late_pct']:.2f}% | "
            f"avg_fut {fmt(r['avg_fut'])} | "
            f"med_fut {fmt(r['med_fut'])} | "
            f"avg_pre5 {fmt(r['avg_pre5'])} | "
            f"avg_pre10 {fmt(r['avg_pre10'])}"
        )

    print()
    print("BEST WITH LATE <= 2%")
    print("-" * 120)

    safe = [r for r in results if r["late_pct"] <= 2.0]
    safe.sort(
        key=lambda r: (
            r["prepop_pct"],
            r["avg_fut"] if r["avg_fut"] is not None else -999,
        ),
        reverse=True,
    )

    for r in safe[:8]:
        print(
            f"{r['gate']:8s} | {r['profile']:20s} | "
            f"prepop {r['prepop_pct']:.2f}% | "
            f"late {r['late_pct']:.2f}% | "
            f"avg_fut {fmt(r['avg_fut'])} | "
            f"med_fut {fmt(r['med_fut'])} | "
            f"avg_pre5 {fmt(r['avg_pre5'])} | "
            f"avg_pre10 {fmt(r['avg_pre10'])}"
        )


def main():
    rows = lab.build_rows()

    if not rows:
        print("No rows produced.")
        return

    print()
    print("LABEL COUNTS")
    print("-" * 60)
    counts = label_counts(rows)

    for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"{k:15s} {v}")

    results = []

    for gate_name, gate_fn in GATES.items():
        for profile_name, weights in PROFILES.items():
            result = evaluate_combo(
                rows=rows,
                gate_name=gate_name,
                gate_fn=gate_fn,
                profile_name=profile_name,
                weights=weights,
                top_per_date=lab.LAB_TOP_PER_DATE,
            )

            if result:
                results.append(result)

    print_results(results)
    print_best_results(results)

    # Show examples for likely candidates.
    interesting = []

    for r in results:
        if r["gate"] in {"core", "strict"} and r["profile"] in {
            "live_active",
            "quality_heavy_v2",
            "mission_hybrid_v1",
            "mission_hybrid_v2",
            "mission_hybrid_v3_def",
        }:
            interesting.append(r)

    interesting.sort(
        key=lambda r: (
            r["prepop_pct"],
            -r["late_pct"],
            r["avg_fut"] if r["avg_fut"] is not None else -999,
        ),
        reverse=True,
    )

    for r in interesting[:5]:
        print_top_examples(r, limit=15)

    print()
    print("DONE.")
    print("Decision rule:")
    print("Pick a production profile only if it reduces late picks without collapsing all future return.")
    print("If all gates kill performance, the signal stack needs more alpha, not stricter filtering.")


if __name__ == "__main__":
    main()
