#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Alpha Lab.

Runs the existing pre-pop lab again, but injects the new Pre-Pop Alpha Signals.

This does NOT change production picks.
It tells us whether the new signals/weights actually find earlier winners.
"""

import prepop_weight_lab as lab

from signals import SIGNALS as BASE_SIGNALS
from prepop_alpha_signals import PREPOP_ALPHA_SIGNALS


ALL_SIGNALS = dict(BASE_SIGNALS)
ALL_SIGNALS.update(PREPOP_ALPHA_SIGNALS)

lab.SIGNALS = ALL_SIGNALS


PREPOP_ALPHA_V1 = {
    # Old late-recognition signals reduced.
    "momentum_12_1": 0.00,
    "volume_pressure_score": 0.05,
    "relative_strength_score": 0.10,
    "trend_quality_score": 0.10,
    "breakout_setup_score": 0.20,
    "accumulation_quality_score": 0.10,

    # Keep useful quality/risk pieces.
    "capital_efficiency_score": 0.40,
    "liquidity_quality_score": 0.45,
    "volatility_control_score": 0.45,
    "insider_buy_score": 0.35,

    # New pre-pop alpha layer.
    "quiet_base_score": 1.25,
    "compression_score": 1.00,
    "controlled_volume_wake_score": 1.35,
    "pre_pop_timing_score": 1.50,
    "base_breakout_proximity_score": 1.10,
    "early_relative_strength_score": 0.85,
    "late_chase_penalty": 1.50,

    # Company layer if present.
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,
    "dilution_risk_score": 0.95,
    "reverse_split_risk_score": 0.95,
    "company_insight_composite": 0.00,
}


PREPOP_ALPHA_V2_STRICT = {
    # Almost no raw momentum chasing.
    "momentum_12_1": 0.00,
    "volume_pressure_score": 0.00,
    "relative_strength_score": 0.05,
    "trend_quality_score": 0.05,
    "breakout_setup_score": 0.10,
    "accumulation_quality_score": 0.05,

    "capital_efficiency_score": 0.35,
    "liquidity_quality_score": 0.50,
    "volatility_control_score": 0.55,
    "insider_buy_score": 0.35,

    # Pre-pop layer dominates.
    "quiet_base_score": 1.40,
    "compression_score": 1.15,
    "controlled_volume_wake_score": 1.50,
    "pre_pop_timing_score": 1.75,
    "base_breakout_proximity_score": 1.20,
    "early_relative_strength_score": 0.90,
    "late_chase_penalty": 1.90,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.00,
    "dilution_risk_score": 1.05,
    "reverse_split_risk_score": 1.05,
    "company_insight_composite": 0.00,
}


PREPOP_ALPHA_V3_BALANCED = {
    # Some trend allowed, but only secondary.
    "momentum_12_1": 0.05,
    "volume_pressure_score": 0.05,
    "relative_strength_score": 0.15,
    "trend_quality_score": 0.20,
    "breakout_setup_score": 0.30,
    "accumulation_quality_score": 0.15,

    "capital_efficiency_score": 0.50,
    "liquidity_quality_score": 0.55,
    "volatility_control_score": 0.55,
    "insider_buy_score": 0.35,

    "quiet_base_score": 1.15,
    "compression_score": 0.95,
    "controlled_volume_wake_score": 1.25,
    "pre_pop_timing_score": 1.45,
    "base_breakout_proximity_score": 1.05,
    "early_relative_strength_score": 0.80,
    "late_chase_penalty": 1.60,

    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.05,
    "dilution_risk_score": 1.00,
    "reverse_split_risk_score": 1.00,
    "company_insight_composite": 0.00,
}


def get_profiles():
    profiles = {
        "live_active": lab.parse_signal_weights() if lab.parse_signal_weights else lab.QUALITY_HEAVY_V2,
        "quality_heavy_v2": lab.QUALITY_HEAVY_V2,
        "old_prepop_v1": lab.PREPOP_V1,
        "old_prepop_v2_strict": lab.PREPOP_V2_STRICT,
        "prepop_alpha_v1": PREPOP_ALPHA_V1,
        "prepop_alpha_v2_strict": PREPOP_ALPHA_V2_STRICT,
        "prepop_alpha_v3_balanced": PREPOP_ALPHA_V3_BALANCED,
    }

    return profiles


lab.get_profiles = get_profiles


def main():
    rows = lab.build_rows()

    if not rows:
        print("No rows produced.")
        return

    lab.summarize_labels(rows)

    lab.show_examples(rows, "PREPOP_WIN", 15)
    lab.show_examples(rows, "LATE_POP", 15)

    lab.signal_separation(rows)

    lab.profile_backtest(rows)

    lab.show_profile_top_examples(rows, "live_active", 15)
    lab.show_profile_top_examples(rows, "quality_heavy_v2", 15)
    lab.show_profile_top_examples(rows, "prepop_alpha_v1", 15)
    lab.show_profile_top_examples(rows, "prepop_alpha_v2_strict", 15)
    lab.show_profile_top_examples(rows, "prepop_alpha_v3_balanced", 15)

    print()
    print("NEW PRE-POP ALPHA SIGNALS")
    print("-" * 80)
    for name in PREPOP_ALPHA_SIGNALS.keys():
        print("-", name)

    print()
    print("DONE.")
    print("Next move: pick the first production alpha profile based on prepop%, late%, avg_pre5, and top raw-score examples.")


if __name__ == "__main__":
    main()
