#!/usr/bin/env python3
"""
Quiet Money Engine — Discovery Brain Audit (non-mutating).

The question this answers:
If the NEW brain (chart-shape classifier + wake-up score + mission price
band) scanned the SAME daily universe as production, who would it pick —
and how does that compare to what the old composite actually saved?

Today the funnel is:
  universe_builder (~225 rotating candidates/day)
    -> legacy composite picks the 75 saved rows
    -> gates + wake-up ranker only ever re-judge those 75

So a perfect Tier 1 / waking chart that the legacy composite ranks #80
is invisible to everything built since. This audit measures how much
that costs by scanning the full candidate slice with the new brain.

For each candidate (one price fetch, ~2 years):
  1. mission eligibility: price band, dollar volume, enough history
  2. chart shape label (chart_shape_audit)  -> must be Tier 1 or Tier 2
  3. wake-up score (wake_up_audit)
  4. blended = wake + Tier 1 bonus - cooling penalty (same as production)

Output: the new brain's top picks, each cross-referenced against what
the old system did with that ticker today (main board / hidden / never
saved at all).

READ-ONLY. No database writes. Universe rotation is date-seeded, so a
same-day run sees the same slice production scanned (minus DB-only
recent-insider additions).
"""

import os
import sys

os.environ.setdefault("MAX_UNIVERSE_SIZE", "75")  # match production before import

from data_layer import get_price_history
from universe_builder import build_dynamic_universe
from chart_shape_audit import (
    LABEL_BASE_BUILDING,
    LABEL_CONTROLLED_BREAKOUT,
    LABEL_FRESH_BASE,
    MIN_BARS as SHAPE_MIN_BARS,
    classify,
    compute_features,
)
from wake_up_audit import STATUS_COOLING, score_bars

HISTORY_DAYS = int(os.getenv("QME_DISCOVERY_HISTORY_DAYS", "800"))

MIN_MAIN_PRICE = float(os.getenv("QME_MIN_MAIN_PRICE", "0.25"))
MAX_MAIN_PRICE = float(os.getenv("QME_MAX_MAIN_PRICE", "15.00"))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "250000"))

TIER1_BONUS = float(os.getenv("QME_RANK_TIER1_BONUS", "15"))
COOLING_PENALTY = float(os.getenv("QME_RANK_COOLING_PENALTY", "10"))

TOP_N = int(os.getenv("QME_DISCOVERY_TOP_N", "25"))

TIER1_LABELS = {LABEL_FRESH_BASE, LABEL_CONTROLLED_BREAKOUT}
ELIGIBLE_LABELS = TIER1_LABELS | {LABEL_BASE_BUILDING}

API_URL = os.getenv(
    "QME_WATCHLIST_API",
    "https://quiet-money-api.onrender.com/api/watchlist/latest?limit=75",
)


def safe_float(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def fetch_old_system_state():
    """ticker -> short verdict string for what production did today."""
    import requests

    try:
        data = requests.get(API_URL, timeout=60).json()
    except Exception as exc:
        print(f"WARNING: could not load old-system state ({exc})")
        return {}

    rows = data if isinstance(data, list) else (
        data.get("items") or data.get("watchlist") or data.get("rows") or []
    )

    out = {}
    for r in rows:
        t = str(r.get("ticker") or "").upper()
        if not t:
            continue
        if r.get("show_on_main") is True:
            out[t] = f"ON MAIN BOARD (rank {r.get('rank')})"
        else:
            out[t] = f"saved but hidden: {r.get('entry_status')}"

    return out


def normalize_bars(ticker):
    raw = get_price_history(ticker, days=HISTORY_DAYS)
    raw = raw.to_dict("records") if hasattr(raw, "to_dict") else raw

    rows = []
    for r in raw or []:
        try:
            d = str(r.get("date") or r.get("Date") or r.get("datetime") or "")[:10]
            c = safe_float(r.get("close") or r.get("Close"))
            lo = safe_float(r.get("low") or r.get("Low") or c)
            hi = safe_float(r.get("high") or r.get("High") or c)
            v = safe_float(r.get("volume") or r.get("Volume"), 0.0) or 0.0
            if d and c is not None and c > 0:
                rows.append((d, c, lo or c, hi or c, v))
        except Exception:
            pass

    return sorted({x[0]: x for x in rows}.values())


def evaluate(ticker):
    """Return a result dict; 'skip' key set when ineligible."""
    bars = normalize_bars(ticker)

    if len(bars) < SHAPE_MIN_BARS:
        return {"ticker": ticker, "skip": f"history: {len(bars)} bars"}

    closes = [b[1] for b in bars]
    highs = [b[3] for b in bars]
    vols = [b[4] for b in bars]
    price = closes[-1]

    if not (MIN_MAIN_PRICE <= price <= MAX_MAIN_PRICE):
        return {"ticker": ticker, "skip": f"price ${price:.2f} outside main band"}

    recent_vols = vols[-20:]
    avg_dollar_vol = price * (sum(recent_vols) / len(recent_vols)) if recent_vols else 0

    if avg_dollar_vol < MIN_DOLLAR_VOLUME:
        return {"ticker": ticker, "skip": f"dollar volume ${avg_dollar_vol:,.0f} too thin"}

    shape_rows = [(b[0], b[1], b[2], b[3]) for b in bars]
    features = compute_features(shape_rows)
    label, confidence, shape_reason = classify(features)

    if label not in ELIGIBLE_LABELS:
        return {"ticker": ticker, "skip": f"shape: {label}"}

    wake = score_bars(closes, highs, vols)

    blended = wake["wake_up_score"]
    if label in TIER1_LABELS:
        blended += TIER1_BONUS
    if wake["status"] == STATUS_COOLING:
        blended -= COOLING_PENALTY

    return {
        "ticker": ticker,
        "price": price,
        "dollar_vol": avg_dollar_vol,
        "shape": label,
        "shape_confidence": confidence,
        "wake": wake["wake_up_score"],
        "status": wake["status"],
        "blended": round(blended, 1),
        "wake_reason": wake["reason"],
        "shape_reason": shape_reason,
    }


def main():
    cli = [t.upper() for t in sys.argv[1:] if t.strip()]

    if cli:
        universe = cli
        print(f"Scanning {len(universe)} tickers from CLI args")
    else:
        universe = build_dynamic_universe()
        print(f"Scanning today's dynamic universe: {len(universe)} candidates")

    old_state = fetch_old_system_state()

    picks = []
    skipped = {}

    for i, t in enumerate(universe, 1):
        try:
            r = evaluate(t)
        except Exception as exc:
            r = {"ticker": t, "skip": f"error: {exc}"}

        if "skip" in r:
            key = r["skip"].split(":")[0]
            skipped[key] = skipped.get(key, 0) + 1
        else:
            picks.append(r)

        if i % 25 == 0:
            print(f"  ...{i}/{len(universe)} scanned, {len(picks)} eligible so far")

    picks.sort(key=lambda r: -r["blended"])

    print()
    print("NEW BRAIN — TOP PICKS FROM THE FULL UNIVERSE")
    print("=" * 118)
    print(f"{'#':<3} {'ticker':<7} {'price':>7} {'score':>6} {'wake':>5} "
          f"{'status':<8} {'shape':<34} | old system verdict")
    print("-" * 118)

    for i, r in enumerate(picks[:TOP_N], 1):
        verdict = old_state.get(r["ticker"], "NOT EVEN SAVED — invisible to old system")
        print(f"{i:<3} {r['ticker']:<7} {r['price']:>7.2f} {r['blended']:>6.1f} "
              f"{r['wake']:>5.1f} {r['status']:<8} {r['shape']:<34} | {verdict}")

    print()
    print("DETAIL ON TOP 10")
    print("=" * 118)
    for r in picks[:10]:
        print(f"\n{r['ticker']}  ${r['price']:.2f}  blended {r['blended']}  "
              f"({r['status']}, {r['shape']} {r['shape_confidence']})")
        print(f"  wake:  {r['wake_reason']}")
        print(f"  shape: {r['shape_reason']}")
        print(f"  old system: {old_state.get(r['ticker'], 'NOT SAVED')}")

    print()
    print("SCAN SUMMARY")
    print("-" * 60)
    print(f"universe scanned: {len(universe)}")
    print(f"eligible after all checks: {len(picks)}")
    for k, v in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  skipped {k}: {v}")

    in_old = sum(1 for r in picks[:TOP_N] if r["ticker"] in old_state)
    print()
    print(f"Of the new brain's top {min(TOP_N, len(picks))}: "
          f"{in_old} were saved by the old system, "
          f"{min(TOP_N, len(picks)) - in_old} were invisible to it.")
    print()
    print("This audit made zero database writes.")


if __name__ == "__main__":
    main()
