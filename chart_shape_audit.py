#!/usr/bin/env python3
"""
Quiet Money Engine — Step 3.15: Chart Shape Audit (non-mutating).

Purpose:
The numeric gates catch short-term pops, 6-12 month repricing, price band,
and prior-spike overhead supply — but they still cannot see chart SHAPE the
way a human does. TOI (medium-term already repriced), IMRX (post-spike
rebuild under overhead supply), and SGHC (multi-year extended staircase near
highs) are three different shape failures that pure thresholds keep missing
one at a time.

This script is a CLASSIFIER AUDIT, not a gate. It:
  1. pulls ~2 years of daily bars per ticker
  2. computes shape/sequence features (drawdown geometry, slopes, advances,
     time-at-highs, range compression, spike geometry)
  3. proposes one chart-shape label + tier + confidence + reason
  4. validates the proposed labels against the known case-study tickers

It NEVER writes to the database. SELECT-only when DATABASE_URL is present
(to also audit the current main board and a hidden sample); with no
DATABASE_URL it audits the case-study tickers alone.

Only after these labels are manually reviewed should chart-shape logic be
allowed to influence production ranking or board visibility.
"""

import os
import math
import sys

from data_layer import get_price_history

HISTORY_DAYS = int(os.getenv("QME_SHAPE_HISTORY_DAYS", "800"))
LOOKBACK_BARS = int(os.getenv("QME_SHAPE_LOOKBACK_BARS", "504"))  # ~2 trading years
MIN_BARS = int(os.getenv("QME_SHAPE_MIN_BARS", "180"))

# Prior-spike geometry thresholds — intentionally identical to
# prior_spike_gate.py so the classifier and the deployed gate agree.
HARD_SPIKE_RANGE_PCT = float(os.getenv("QME_HARD_SPIKE_RANGE_PCT", "250"))
HARD_BELOW_HIGH_PCT = float(os.getenv("QME_HARD_BELOW_HIGH_PCT", "-40"))
WATCH_SPIKE_RANGE_PCT = float(os.getenv("QME_WATCH_SPIKE_RANGE_PCT", "150"))
WATCH_BELOW_HIGH_PCT = float(os.getenv("QME_WATCH_BELOW_HIGH_PCT", "-25"))
RECENT_HIGH_IGNORE_BARS = int(os.getenv("QME_SPIKE_RECENT_IGNORE_BARS", "30"))

# Near-high / extension thresholds.
NEAR_HIGH_PCT = float(os.getenv("QME_SHAPE_NEAR_HIGH_PCT", "-15"))
STAIRCASE_FROM_LOW_PCT = float(os.getenv("QME_SHAPE_STAIRCASE_FROM_LOW_PCT", "300"))
EXTENDED_FROM_LOW_PCT = float(os.getenv("QME_SHAPE_EXTENDED_FROM_LOW_PCT", "150"))
SLOW_GRIND_MIN_BARS = int(os.getenv("QME_SHAPE_SLOW_GRIND_MIN_BARS", "250"))
REPRICED_FROM_LOW_PCT = float(os.getenv("QME_SHAPE_REPRICED_FROM_LOW_PCT", "80"))
REPRICED_R60_PCT = float(os.getenv("QME_SHAPE_REPRICED_R60_PCT", "45"))
REPRICED_R90_PCT = float(os.getenv("QME_SHAPE_REPRICED_R90_PCT", "65"))

# Base-bucket thresholds.
WAKEUP_R20_MIN_PCT = float(os.getenv("QME_SHAPE_WAKEUP_R20_MIN_PCT", "3"))
WAKEUP_R20_MAX_PCT = float(os.getenv("QME_SHAPE_WAKEUP_R20_MAX_PCT", "20"))
COMPRESSION_RATIO_MAX = float(os.getenv("QME_SHAPE_COMPRESSION_RATIO_MAX", "0.45"))
KNIFE_FROM_LOW_MAX_PCT = float(os.getenv("QME_SHAPE_KNIFE_FROM_LOW_MAX_PCT", "20"))

LABEL_FRESH_BASE = "FRESH BASE / EARLY WAKE-UP"
LABEL_CONTROLLED_BREAKOUT = "CONTROLLED BREAKOUT SETUP"
LABEL_BASE_BUILDING = "BASE-BUILDING / WEAK CONFIRMATION"
LABEL_MULTI_YEAR_EXTENDED = "MULTI-YEAR EXTENDED"
LABEL_POST_SPIKE_REBUILD = "POST-SPIKE REBUILD"
LABEL_PRIOR_SPIKE_DAMAGE = "PRIOR SPIKE DAMAGE"
LABEL_FALLING_KNIFE = "FALLING KNIFE / WEAK TREND"
LABEL_CONTINUATION = "CONTINUATION / REPRICED"
LABEL_NO_CONTEXT = "NO PRICE CONTEXT"

TIER_BY_LABEL = {
    LABEL_FRESH_BASE: "TIER 1 / CLEAN PRE-POP",
    LABEL_CONTROLLED_BREAKOUT: "TIER 1 / CLEAN PRE-POP",
    LABEL_BASE_BUILDING: "TIER 2 / EARLY WATCH",
    LABEL_POST_SPIKE_REBUILD: "TIER 3 / CONTINUATION-REBUILD",
    LABEL_CONTINUATION: "TIER 3 / CONTINUATION-REBUILD",
    LABEL_MULTI_YEAR_EXTENDED: "TIER 3 / CONTINUATION-REBUILD",
    LABEL_FALLING_KNIFE: "HIDDEN / SEVERE RISK",
    LABEL_PRIOR_SPIKE_DAMAGE: "HIDDEN / SEVERE RISK",
    LABEL_NO_CONTEXT: "HIDDEN / NO CONTEXT",
}

# Section 30 of the handoff: labels each case ticker is EXPECTED to receive.
# The audit fails loudly if the classifier disagrees with the human read.
CASE_EXPECTATIONS = {
    "TOI": {LABEL_CONTINUATION, LABEL_MULTI_YEAR_EXTENDED},
    "IMRX": {LABEL_POST_SPIKE_REBUILD, LABEL_PRIOR_SPIKE_DAMAGE},
    "SGHC": {LABEL_MULTI_YEAR_EXTENDED},
    "BBD": {LABEL_BASE_BUILDING, LABEL_FALLING_KNIFE},
    "BZUN": {LABEL_CONTROLLED_BREAKOUT, LABEL_FRESH_BASE, LABEL_BASE_BUILDING},
    "CLNE": {LABEL_POST_SPIKE_REBUILD, LABEL_FALLING_KNIFE},
}

CASE_TICKERS = [
    t.strip().upper()
    for t in os.getenv("QME_SHAPE_CASE_TICKERS", "TOI,IMRX,SGHC,BBD,BZUN,CLNE").split(",")
    if t.strip()
]

HIDDEN_SAMPLE_SIZE = int(os.getenv("QME_SHAPE_HIDDEN_SAMPLE", "10"))


def safe_float(x, default=None):
    try:
        if x is None or x == "":
            return default
        return float(x)
    except Exception:
        return default


def pct(now, old):
    if now is None or old is None or old <= 0:
        return None
    return (now / old - 1.0) * 100.0


def fmt(value, digits=1):
    if value is None:
        return "n/a"
    return f"{value:+.{digits}f}%"


def normalize_history(ticker):
    raw = get_price_history(ticker, days=HISTORY_DAYS)
    raw = raw.to_dict("records") if hasattr(raw, "to_dict") else raw

    rows = []
    for r in raw or []:
        try:
            d = str(r.get("date") or r.get("Date") or r.get("datetime") or "")[:10]
            c = safe_float(r.get("close") or r.get("Close"))
            lo = safe_float(r.get("low") or r.get("Low") or c)
            hi = safe_float(r.get("high") or r.get("High") or c)
            if d and c is not None and c > 0:
                rows.append((d, c, lo or c, hi or c))
        except Exception:
            pass

    return sorted({x[0]: x for x in rows}.values())


def slope_pct(closes, n):
    """Least-squares slope of log(close) over the last n bars,
    reported as the fitted total % change across that window."""
    if len(closes) < n or n < 2:
        return None

    ys = [math.log(c) for c in closes[-n:] if c > 0]
    if len(ys) < n:
        return None

    xs = list(range(n))
    mean_x = (n - 1) / 2.0
    mean_y = sum(ys) / n

    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None

    m = num / den
    return (math.exp(m * (n - 1)) - 1.0) * 100.0


def range_width_pct(rows, n):
    """(window high - window low) as % of window low, over the last n bars."""
    if not rows:
        return None
    w = rows[-n:] if len(rows) >= n else rows
    lo = min(x[2] for x in w)
    hi = max(x[3] for x in w)
    if lo <= 0:
        return None
    return (hi / lo - 1.0) * 100.0


def count_advances(closes, threshold_pct, reset_pullback_pct=20.0):
    """Count distinct advances of >= threshold_pct measured trough-to-peak.
    A new advance is only counted after a >= reset_pullback_pct pullback."""
    if len(closes) < 2:
        return 0

    count = 0
    trough = closes[0]
    peak = closes[0]
    fired = False

    for c in closes[1:]:
        if c < trough:
            trough = c
            peak = c
            fired = False
            continue

        if c > peak:
            peak = c

        if not fired and trough > 0 and (peak / trough - 1.0) * 100.0 >= threshold_pct:
            count += 1
            fired = True

        if fired and peak > 0 and (c / peak - 1.0) * 100.0 <= -reset_pullback_pct:
            trough = c
            peak = c
            fired = False

    return count


def largest_move(closes, n):
    """Largest positive n-bar % move anywhere in the window."""
    best = None
    for i in range(n, len(closes)):
        move = pct(closes[i], closes[i - n])
        if move is not None and (best is None or move > best):
            best = move
    return best


def max_drawdown_pct(closes):
    peak = None
    worst = 0.0
    for c in closes:
        if peak is None or c > peak:
            peak = c
        dd = (c / peak - 1.0) * 100.0
        if dd < worst:
            worst = dd
    return worst


def compute_features(rows):
    window = rows[-LOOKBACK_BARS:] if len(rows) >= LOOKBACK_BARS else rows
    closes = [x[1] for x in window]
    n = len(window)

    current_date, current, _, _ = window[-1]

    high_idx, high_row = max(enumerate(window), key=lambda x: x[1][3])
    low_idx, low_row = min(enumerate(window), key=lambda x: x[1][2])

    two_year_high = high_row[3]
    two_year_low = low_row[2]

    def ret(k):
        if n <= k:
            return None
        return pct(current, closes[-1 - k])

    near_high_band = two_year_high * 0.90
    bottom_band = two_year_low + 0.25 * (two_year_high - two_year_low)

    return {
        "date": current_date,
        "price": current,
        "bars": n,
        "two_year_high": two_year_high,
        "two_year_high_date": high_row[0],
        "two_year_low": two_year_low,
        "two_year_low_date": low_row[0],
        "below_high_pct": pct(current, two_year_high),
        "from_low_pct": pct(current, two_year_low),
        "bars_since_high": n - 1 - high_idx,
        "bars_since_low": n - 1 - low_idx,
        "spike_range_pct": pct(two_year_high, two_year_low),
        "r20": ret(20),
        "r60": ret(60),
        "r90": ret(90),
        "largest_1d": largest_move(closes, 1),
        "largest_5d": largest_move(closes, 5),
        "largest_20d": largest_move(closes, 20),
        "max_drawdown": max_drawdown_pct(closes),
        "slope_20": slope_pct(closes, 20),
        "slope_60": slope_pct(closes, 60),
        "slope_120": slope_pct(closes, 120),
        "slope_252": slope_pct(closes, 252),
        "range_20": range_width_pct(window, 20),
        "range_60": range_width_pct(window, 60),
        "advances_50": count_advances(closes, 50.0),
        "advances_100": count_advances(closes, 100.0),
        "pct_time_near_high": 100.0 * sum(1 for c in closes if c >= near_high_band) / n,
        "pct_time_bottom_25": 100.0 * sum(1 for c in closes if c <= bottom_band) / n,
    }


def classify(features):
    """Return (label, confidence, reason). Order matters: severe shape
    damage first, extension second, base shapes last."""
    f = features

    below_high = f["below_high_pct"]
    from_low = f["from_low_pct"]
    spike_range = f["spike_range_pct"]
    bars_since_high = f["bars_since_high"]
    bars_since_low = f["bars_since_low"]
    r20, r60, r90 = f["r20"], f["r60"], f["r90"]
    slope_60, slope_120 = f["slope_60"], f["slope_120"]

    if below_high is None or from_low is None or spike_range is None:
        return LABEL_NO_CONTEXT, "HIGH", "missing range context"

    # 1. Old spike + still far below it = overhead supply shapes.
    if bars_since_high >= RECENT_HIGH_IGNORE_BARS:
        if spike_range >= HARD_SPIKE_RANGE_PCT and below_high <= HARD_BELOW_HIGH_PCT:
            return (
                LABEL_PRIOR_SPIKE_DAMAGE,
                "HIGH",
                f"2y range {spike_range:.0f}% with price {below_high:.0f}% below the "
                f"old high from {f['two_year_high_date']} "
                f"({bars_since_high} bars ago); heavy overhead supply",
            )

        if spike_range >= WATCH_SPIKE_RANGE_PCT and below_high <= WATCH_BELOW_HIGH_PCT:
            bleeding = (
                (slope_60 is not None and slope_60 < 0)
                and (r20 is not None and r20 < 0)
            )
            if bleeding:
                return (
                    LABEL_FALLING_KNIFE,
                    "MEDIUM",
                    f"post-spike chart ({spike_range:.0f}% 2y range, "
                    f"{below_high:.0f}% below high) still bleeding: "
                    f"60d slope {fmt(slope_60)}, 20d return {fmt(r20)}",
                )
            return (
                LABEL_POST_SPIKE_REBUILD,
                "MEDIUM",
                f"rebuilding {from_low:.0f}% off the 2y low but still "
                f"{below_high:.0f}% below the {f['two_year_high_date']} high "
                f"({bars_since_high} bars ago); overhead supply above",
            )

    # 2. Near the 2y high = extension shapes. The question is how the price
    # got there: slow multi-year staircase vs fast recent repricing.
    if below_high >= NEAR_HIGH_PCT:
        slow_grind = bars_since_low >= SLOW_GRIND_MIN_BARS
        fast_recent = (
            (r60 is not None and r60 >= REPRICED_R60_PCT)
            or (r90 is not None and r90 >= REPRICED_R90_PCT)
        )

        if from_low >= STAIRCASE_FROM_LOW_PCT and slow_grind:
            return (
                LABEL_MULTI_YEAR_EXTENDED,
                "HIGH",
                f"{from_low:.0f}% above the 2y low set {bars_since_low} bars ago, "
                f"now within {abs(below_high):.0f}% of the 2y high with "
                f"{f['advances_50']} distinct 50%+ advances; mature staircase",
            )

        if from_low >= EXTENDED_FROM_LOW_PCT and slow_grind and not fast_recent:
            return (
                LABEL_MULTI_YEAR_EXTENDED,
                "MEDIUM",
                f"{from_low:.0f}% above the 2y low over {bars_since_low} bars "
                f"and near the 2y high without a fast recent leg; extended grind",
            )

        if fast_recent or from_low >= REPRICED_FROM_LOW_PCT:
            return (
                LABEL_CONTINUATION,
                "HIGH" if fast_recent else "MEDIUM",
                f"near the 2y high after repricing: 60d {fmt(r60)}, 90d {fmt(r90)}, "
                f"{from_low:.0f}% above the 2y low; the move already happened",
            )

    # 3. Persistent downtrend with no meaningful lift off the low.
    if (
        slope_60 is not None
        and slope_120 is not None
        and slope_60 < 0
        and slope_120 < 0
        and from_low < KNIFE_FROM_LOW_MAX_PCT
    ):
        return (
            LABEL_FALLING_KNIFE,
            "MEDIUM",
            f"60d slope {fmt(slope_60)}, 120d slope {fmt(slope_120)}, only "
            f"{from_low:.0f}% above the 2y low; no evidence of a turn",
        )

    # 4. Base shapes: neither spiked, nor extended, nor collapsing.
    compression = None
    if f["range_20"] is not None and f["range_60"] is not None and f["range_60"] > 0:
        compression = f["range_20"] / f["range_60"]

    waking = r20 is not None and WAKEUP_R20_MIN_PCT <= r20 <= WAKEUP_R20_MAX_PCT
    uptrend_start = slope_60 is not None and slope_60 > 0

    if waking and uptrend_start and compression is not None and compression <= COMPRESSION_RATIO_MAX:
        return (
            LABEL_CONTROLLED_BREAKOUT,
            "MEDIUM",
            f"20d range is {compression:.2f}x the 60d range (compressing), "
            f"20d return {fmt(r20)}, 60d slope {fmt(slope_60)}; "
            f"controlled setup near a base",
        )

    if waking and uptrend_start:
        return (
            LABEL_FRESH_BASE,
            "MEDIUM",
            f"first lift off a base: 20d return {fmt(r20)}, 60d slope "
            f"{fmt(slope_60)}, only {from_low:.0f}% above the 2y low "
            f"and {abs(below_high):.0f}% below the 2y high",
        )

    return (
        LABEL_BASE_BUILDING,
        "LOW",
        f"in a base but unconfirmed: 20d return {fmt(r20)}, 60d slope "
        f"{fmt(slope_60)}, {from_low:.0f}% above the 2y low, "
        f"{abs(below_high):.0f}% below the 2y high",
    )


def audit_ticker(ticker):
    rows = normalize_history(ticker)

    if len(rows) < MIN_BARS:
        return {
            "ticker": ticker,
            "features": None,
            "label": LABEL_NO_CONTEXT,
            "confidence": "HIGH",
            "reason": f"only {len(rows)} usable bars (< {MIN_BARS})",
        }

    features = compute_features(rows)
    label, confidence, reason = classify(features)

    return {
        "ticker": ticker,
        "features": features,
        "label": label,
        "confidence": confidence,
        "reason": reason,
    }


def print_detail(result):
    f = result["features"]

    print()
    print(result["ticker"])
    print("-" * 110)

    if not f:
        print(f"  label: {result['label']} ({result['confidence']}) — {result['reason']}")
        return

    print(
        f"  as of {f['date']}  price ${f['price']:.2f}  bars={f['bars']}\n"
        f"  2y high ${f['two_year_high']:.2f} on {f['two_year_high_date']} "
        f"({f['bars_since_high']} bars ago)   "
        f"2y low ${f['two_year_low']:.2f} on {f['two_year_low_date']} "
        f"({f['bars_since_low']} bars ago)\n"
        f"  below 2y high {fmt(f['below_high_pct'])}   "
        f"above 2y low {fmt(f['from_low_pct'])}   "
        f"2y range {fmt(f['spike_range_pct'])}   "
        f"max drawdown {fmt(f['max_drawdown'])}\n"
        f"  returns: 20d {fmt(f['r20'])}  60d {fmt(f['r60'])}  90d {fmt(f['r90'])}\n"
        f"  slopes:  20d {fmt(f['slope_20'])}  60d {fmt(f['slope_60'])}  "
        f"120d {fmt(f['slope_120'])}  252d {fmt(f['slope_252'])}\n"
        f"  largest moves: 1d {fmt(f['largest_1d'])}  5d {fmt(f['largest_5d'])}  "
        f"20d {fmt(f['largest_20d'])}\n"
        f"  range width: 20d {fmt(f['range_20'])}  60d {fmt(f['range_60'])}   "
        f"advances: 50%+ x{f['advances_50']}  100%+ x{f['advances_100']}\n"
        f"  time within 10% of high {f['pct_time_near_high']:.0f}%   "
        f"time in bottom 25% of range {f['pct_time_bottom_25']:.0f}%"
    )
    print(
        f"  label: {result['label']}  "
        f"tier: {TIER_BY_LABEL.get(result['label'], '?')}  "
        f"confidence: {result['confidence']}"
    )
    print(f"  reason: {result['reason']}")


def fetch_db_tickers():
    """SELECT-only: latest run's main board plus a sample of hidden names.
    Returns (main, hidden_sample) ticker lists; empty if no DATABASE_URL."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return [], []

    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
    except Exception:
        return [], []

    try:
        con = psycopg2.connect(dsn, cursor_factory=RealDictCursor)
    except Exception as exc:
        print(f"DB unavailable ({exc}); auditing case tickers only.")
        return [], []

    try:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT ticker, show_on_main
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        con.close()

    main = [r["ticker"] for r in rows if r.get("show_on_main") is True]
    hidden = [r["ticker"] for r in rows if r.get("show_on_main") is False]

    step = max(1, len(hidden) // HIDDEN_SAMPLE_SIZE) if hidden else 1
    hidden_sample = hidden[::step][:HIDDEN_SAMPLE_SIZE]

    print(f"DB run_date={run_date}: main={len(main)} hidden={len(hidden)} "
          f"(sampling {len(hidden_sample)} hidden)")

    return main, hidden_sample


def main():
    cli_tickers = [t.upper() for t in sys.argv[1:] if t.strip()]

    db_main, db_hidden = ([], []) if cli_tickers else fetch_db_tickers()

    ordered = []
    seen = set()
    for t in (cli_tickers or CASE_TICKERS) + db_main + db_hidden:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    print()
    print("CHART SHAPE AUDIT (non-mutating)")
    print("=" * 110)
    print(f"lookback: {LOOKBACK_BARS} bars (~2 trading years), "
          f"history request: {HISTORY_DAYS} calendar days")
    print(f"tickers: {', '.join(ordered)}")

    results = {}
    for ticker in ordered:
        try:
            results[ticker] = audit_ticker(ticker)
        except Exception as exc:
            results[ticker] = {
                "ticker": ticker,
                "features": None,
                "label": LABEL_NO_CONTEXT,
                "confidence": "LOW",
                "reason": f"audit error: {exc}",
            }
        print_detail(results[ticker])

    print()
    print("SUMMARY")
    print("=" * 110)
    print(f"{'ticker':<7} | {'price':>8} | {'below hi':>8} | {'from lo':>8} | "
          f"{'label':<34} | {'tier':<30} | conf")
    print("-" * 110)
    for ticker in ordered:
        r = results[ticker]
        f = r["features"]
        price = f"{f['price']:.2f}" if f else "n/a"
        bh = fmt(f["below_high_pct"], 0) if f else "n/a"
        fl = fmt(f["from_low_pct"], 0) if f else "n/a"
        print(f"{ticker:<7} | {price:>8} | {bh:>8} | {fl:>8} | "
              f"{r['label']:<34} | {TIER_BY_LABEL.get(r['label'], '?'):<30} | "
              f"{r['confidence']}")

    case_in_run = [t for t in CASE_TICKERS if t in results]
    if case_in_run:
        print()
        print("CASE-STUDY VALIDATION (expected labels from handoff section 30)")
        print("=" * 110)

        passed = 0
        checked = 0
        for ticker in case_in_run:
            expected = CASE_EXPECTATIONS.get(ticker)
            got = results[ticker]["label"]

            if not expected:
                continue

            if got == LABEL_NO_CONTEXT:
                print(f"{ticker:<7} SKIP    no usable price history "
                      f"({results[ticker]['reason']})")
                continue

            checked += 1
            if got in expected:
                passed += 1
                print(f"{ticker:<7} PASS    {got}")
            else:
                print(f"{ticker:<7} REVIEW  got '{got}', expected one of: "
                      f"{sorted(expected)}")

        print()
        print(f"validation: {passed}/{checked} case tickers matched the human read")
        if checked and passed == checked:
            print("Classifier agrees with the manual chart reads. "
                  "Next step: review these labels before wiring any of this "
                  "into production ranking or visibility.")
        else:
            print("Classifier disagrees with at least one manual chart read. "
                  "Do NOT let chart-shape labels touch production until the "
                  "disagreements above are resolved.")

    print()
    print("This audit made zero database writes.")


if __name__ == "__main__":
    main()
