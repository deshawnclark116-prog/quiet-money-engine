#!/usr/bin/env python3
"""
Quiet Money Engine — Wake-Up Score Audit (non-mutating).

Purpose:
The gates answer "which stocks are clean, cheap, and quiet?" — the SETUP
question. This layer answers the TRIGGER question: "which of those is
showing evidence of waking up RIGHT NOW?"

A perfect base that stays asleep for a year is a parked dollar. The board
should be ordered so rank #1 always means "closest to its move", not
"highest legacy composite".

Score = 4 components, 25 points each, all from price/volume bars:

  volume_awakening   recent volume swelling vs the stock's own 60d norm
  smart_accumulation volume landing on up-days vs down-days (20d)
  trend_turn         20d trend flipped up, price above a rising 20d average
  at_the_door        price near / just through the top of its recent base

Status bands:
  70-100  FIRING    evidence is live, closest to the move
  50-69   WARMING   pressure clearly building
  30-49   COILED    setup intact, early signs only
   0-29   SLEEPING  clean but no evidence yet
  COOLING (override) price and 20d trend both pointing down right now

This script is a CLASSIFIER AUDIT, not a ranker. It never writes to the
database. Run it on the main-board survivors (pass tickers as CLI args,
or it reads the current board from the public API) and compare its
ordering against the human read before wiring anything into production.
"""

import os
import sys

from data_layer import get_price_history

HISTORY_DAYS = int(os.getenv("QME_WAKE_HISTORY_DAYS", "260"))
MIN_BARS = int(os.getenv("QME_WAKE_MIN_BARS", "80"))

VOL_RECENT_BARS = int(os.getenv("QME_WAKE_VOL_RECENT_BARS", "10"))
VOL_BASE_BARS = int(os.getenv("QME_WAKE_VOL_BASE_BARS", "60"))
VOL_RATIO_FULL = float(os.getenv("QME_WAKE_VOL_RATIO_FULL", "2.5"))

ACCUM_BARS = int(os.getenv("QME_WAKE_ACCUM_BARS", "20"))
ACCUM_RATIO_FULL = float(os.getenv("QME_WAKE_ACCUM_RATIO_FULL", "3.0"))


API_URL = os.getenv(
    "QME_WATCHLIST_API",
    "https://quiet-money-api.onrender.com/api/watchlist/latest?limit=75",
)

STATUS_FIRING = "FIRING"
STATUS_WARMING = "WARMING"
STATUS_COILED = "COILED"
STATUS_SLEEPING = "SLEEPING"
STATUS_COOLING = "COOLING"


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


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def normalize_history(ticker):
    raw = get_price_history(ticker, days=HISTORY_DAYS)
    raw = raw.to_dict("records") if hasattr(raw, "to_dict") else raw

    rows = []
    for r in raw or []:
        try:
            d = str(r.get("date") or r.get("Date") or r.get("datetime") or "")[:10]
            c = safe_float(r.get("close") or r.get("Close"))
            v = safe_float(r.get("volume") or r.get("Volume"), 0.0) or 0.0
            hi = safe_float(r.get("high") or r.get("High") or c)
            if d and c is not None and c > 0:
                rows.append((d, c, hi or c, v))
        except Exception:
            pass

    return sorted({x[0]: x for x in rows}.values())


def sma(closes, n, offset=0):
    """Simple moving average of the n closes ending `offset` bars back."""
    end = len(closes) - offset
    start = end - n
    if start < 0:
        return None
    w = closes[start:end]
    return sum(w) / len(w) if w else None


def score_volume_awakening(vols):
    recent = vols[-VOL_RECENT_BARS:]
    base = vols[-VOL_BASE_BARS:]

    if not recent or not base:
        return 0.0, None

    avg_recent = sum(recent) / len(recent)
    avg_base = sum(base) / len(base)

    if avg_base <= 0:
        return 0.0, None

    ratio = avg_recent / avg_base
    # ratio 1.0 -> 0 pts, VOL_RATIO_FULL -> 25 pts
    points = clamp((ratio - 1.0) / (VOL_RATIO_FULL - 1.0), 0.0, 1.0) * 25.0
    return points, ratio


def score_smart_accumulation(closes, vols):
    n = min(ACCUM_BARS, len(closes) - 1)
    if n < 5:
        return 0.0, None

    up_vol = 0.0
    down_vol = 0.0

    for i in range(len(closes) - n, len(closes)):
        if closes[i] > closes[i - 1]:
            up_vol += vols[i]
        elif closes[i] < closes[i - 1]:
            down_vol += vols[i]

    if up_vol <= 0 and down_vol <= 0:
        return 0.0, None

    if down_vol <= 0:
        ratio = ACCUM_RATIO_FULL
    else:
        ratio = up_vol / down_vol

    # ratio 1.0 -> 0 pts (balanced), ACCUM_RATIO_FULL -> 25 pts
    points = clamp((ratio - 1.0) / (ACCUM_RATIO_FULL - 1.0), 0.0, 1.0) * 25.0
    return points, ratio


def score_trend_turn(closes):
    price = closes[-1]

    sma20_now = sma(closes, 20)
    sma20_prev = sma(closes, 20, offset=5)
    r20 = pct(price, closes[-21]) if len(closes) > 21 else None

    if sma20_now is None or sma20_prev is None or r20 is None:
        return 0.0, {"r20": r20, "above_sma20": None, "sma20_rising": None}

    above = price > sma20_now
    rising = sma20_now > sma20_prev

    points = 0.0
    if above:
        points += 8.0
    if rising:
        points += 8.0
    # moving, but not vertical: +2%..+25% over 20 bars earns up to 9 pts
    if 2.0 <= r20 <= 25.0:
        points += 9.0 * clamp((r20 - 2.0) / 10.0, 0.0, 1.0)

    detail = {"r20": r20, "above_sma20": above, "sma20_rising": rising}
    return points, detail


def score_still_early(closes):
    """The mission is to enter near the bottom of the base and ride the
    whole move — NOT to buy at the breakout door. Full credit for price
    still within ~15% of a CONFIRMED low, fading to zero as the name gets
    60-70% away. A very fresh low earns reduced credit: the literal bottom
    is only knowable in hindsight, so we require the low to have held for
    a while (falling-knife protection) before calling it a bottom."""
    window = closes[-252:] if len(closes) >= 252 else closes

    if not window:
        return 0.0, None

    low = min(window)
    if low <= 0:
        return 0.0, None

    low_pos = max(i for i, c in enumerate(window) if c == low)
    bars_since_low = len(window) - 1 - low_pos

    from_low = pct(closes[-1], low)
    if from_low is None:
        return 0.0, None

    if from_low <= 15.0:
        points = 25.0
    elif from_low <= 40.0:
        points = 25.0 - (from_low - 15.0) / 25.0 * 13.0   # 25 -> 12
    elif from_low <= 70.0:
        points = 12.0 - (from_low - 40.0) / 30.0 * 8.0    # 12 -> 4
    else:
        points = 0.0

    # Bottom-confirmation structure: a low set only days ago may not be
    # the bottom at all.
    if bars_since_low < 20:
        points *= 0.3
    elif bars_since_low < 40:
        points *= 0.6

    return points, {"from_low": from_low, "bars_since_low": bars_since_low}


def already_moved_penalty(closes):
    """Up more than 40% in 90 bars means the pop partially happened.
    Deduct up to 10 points — the mission does not chase recent runners."""
    if len(closes) <= 90:
        return 0.0, None

    r90 = pct(closes[-1], closes[-91])
    if r90 is None or r90 <= 40.0:
        return 0.0, r90

    return clamp((r90 - 40.0) / 20.0, 0.0, 1.0) * 10.0, r90


def score_bars(closes, highs, vols):
    """Score already-fetched bars. Used by audit_ticker and by callers
    that fetch price history once and feed multiple scorers."""
    vol_pts, vol_ratio = score_volume_awakening(vols)
    acc_pts, acc_ratio = score_smart_accumulation(closes, vols)
    trend_pts, trend_detail = score_trend_turn(closes)
    early_pts, early_detail = score_still_early(closes)
    moved_penalty, r90 = already_moved_penalty(closes)

    total = max(0.0, vol_pts + acc_pts + trend_pts + early_pts - moved_penalty)

    r20 = trend_detail.get("r20")
    sma20_rising = trend_detail.get("sma20_rising")

    if r20 is not None and r20 < 0 and sma20_rising is False:
        status = STATUS_COOLING
    elif total >= 70:
        status = STATUS_FIRING
    elif total >= 50:
        status = STATUS_WARMING
    elif total >= 30:
        status = STATUS_COILED
    else:
        status = STATUS_SLEEPING

    parts = []
    if vol_ratio is not None:
        parts.append(f"10d volume {vol_ratio:.2f}x its 60d norm")
    if acc_ratio is not None:
        parts.append(f"up-day/down-day volume {acc_ratio:.2f}x (20d)")
    if r20 is not None:
        parts.append(
            f"20d {r20:+.1f}%, "
            f"{'above' if trend_detail.get('above_sma20') else 'below'} 20d avg "
            f"({'rising' if sma20_rising else 'flat/falling'})"
        )
    if early_detail is not None:
        parts.append(
            f"{early_detail['from_low']:+.1f}% off its 12m low "
            f"(set {early_detail['bars_since_low']} bars ago)"
        )
    if moved_penalty > 0 and r90 is not None:
        parts.append(f"already ran {r90:+.1f}% in 90d (-{moved_penalty:.0f} pts)")

    return {
        "ok": True,
        "price": closes[-1],
        "components": {
            "volume_awakening": round(vol_pts, 1),
            "smart_accumulation": round(acc_pts, 1),
            "trend_turn": round(trend_pts, 1),
            "still_early": round(early_pts, 1),
            "already_moved_penalty": round(-moved_penalty, 1),
        },
        "wake_up_score": round(total, 1),
        "status": status,
        "reason": "; ".join(parts),
    }


def audit_ticker(ticker):
    rows = normalize_history(ticker)

    if len(rows) < MIN_BARS:
        return {
            "ticker": ticker,
            "ok": False,
            "reason": f"only {len(rows)} usable bars (< {MIN_BARS})",
        }

    closes = [x[1] for x in rows]
    highs = [x[2] for x in rows]
    vols = [x[3] for x in rows]

    result = score_bars(closes, highs, vols)
    result["ticker"] = ticker
    result["date"] = rows[-1][0]
    return result


def fetch_board_tickers():
    """Read the current main board from the public API (read-only)."""
    import requests

    data = requests.get(API_URL, timeout=60).json()
    rows = data if isinstance(data, list) else (
        data.get("items") or data.get("watchlist") or data.get("rows") or []
    )

    main = [r for r in rows if r.get("show_on_main") is True]
    main.sort(key=lambda r: int(r.get("rank") or 0))

    return [str(r.get("ticker")).upper() for r in main if r.get("ticker")]


def main():
    tickers = [t.upper() for t in sys.argv[1:] if t.strip()]

    if not tickers:
        try:
            tickers = fetch_board_tickers()
            print(f"Loaded current main board from API: {', '.join(tickers)}")
        except Exception as exc:
            print(f"Could not load board from API ({exc}); pass tickers as arguments.")
            return

    print()
    print("WAKE-UP SCORE AUDIT (non-mutating)")
    print("=" * 108)

    results = []
    for t in tickers:
        try:
            results.append(audit_ticker(t))
        except Exception as exc:
            results.append({"ticker": t, "ok": False, "reason": f"audit error: {exc}"})

    scored = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]

    scored.sort(key=lambda r: -r["wake_up_score"])

    print(f"{'#':<3} {'ticker':<7} {'price':>8} {'score':>6} {'status':<9} "
          f"{'vol':>5} {'accum':>5} {'trend':>5} {'early':>5} {'moved':>6}")
    print("-" * 108)

    for i, r in enumerate(scored, 1):
        c = r["components"]
        print(f"{i:<3} {r['ticker']:<7} {r['price']:>8.2f} "
              f"{r['wake_up_score']:>6.1f} {r['status']:<9} "
              f"{c['volume_awakening']:>5.1f} {c['smart_accumulation']:>5.1f} "
              f"{c['trend_turn']:>5.1f} {c['still_early']:>5.1f} "
              f"{c['already_moved_penalty']:>6.1f}")

    print()
    for r in scored:
        print(f"{r['ticker']:<7} {r['status']:<9} {r['reason']}")

    for r in failed:
        print(f"{r['ticker']:<7} SKIPPED   {r['reason']}")

    print()
    print("Proposed board order (most awake first):",
          " > ".join(r["ticker"] for r in scored))
    print()
    print("This audit made zero database writes.")


if __name__ == "__main__":
    main()
