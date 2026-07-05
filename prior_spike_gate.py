import os
import psycopg2
from psycopg2.extras import RealDictCursor
from data_layer import get_price_history

# Prior Spike / Overhead Supply Gate for Quiet Money Engine.
# Purpose:
# A stock is not a clean "pre-pop" candidate if it already had a major spike
# during the last ~2 years and is still trading far below that prior high.
#
# This catches post-spike rebuild charts like:
# old monster run -> crash/consolidation -> rebound attempt.
#
# That setup can be tradable, but it is not the same as quiet pre-pop discovery.

HISTORY_DAYS = int(os.getenv("QME_SPIKE_HISTORY_DAYS", "800"))
LOOKBACK_BARS = int(os.getenv("QME_SPIKE_LOOKBACK_BARS", "504"))  # about 2 trading years
RECENT_HIGH_IGNORE_BARS = int(os.getenv("QME_SPIKE_RECENT_IGNORE_BARS", "30"))

HARD_SPIKE_RANGE_PCT = float(os.getenv("QME_HARD_SPIKE_RANGE_PCT", "250"))
HARD_BELOW_HIGH_PCT = float(os.getenv("QME_HARD_BELOW_HIGH_PCT", "-40"))

WATCH_SPIKE_RANGE_PCT = float(os.getenv("QME_WATCH_SPIKE_RANGE_PCT", "150"))
WATCH_BELOW_HIGH_PCT = float(os.getenv("QME_WATCH_BELOW_HIGH_PCT", "-25"))

MAIN_ENTRY_STATUSES = {
    "PRE-POP BUY CANDIDATE",
    "WATCH FOR ENTRY",
}

MAIN_PREPOP_STATUSES = {
    "EARLY / CLEAN",
    "EARLY / WAKING",
}

def safe_float(x):
    try:
        return float(x)
    except Exception:
        return None

def pct(now, old):
    if now is None or old is None or old <= 0:
        return None
    return (now / old - 1.0) * 100.0

def is_candidate(row):
    return (
        str(row.get("entry_status") or "") in MAIN_ENTRY_STATUSES
        and str(row.get("pre_pop_status") or "") in MAIN_PREPOP_STATUSES
    )

def normalize_history(ticker):
    raw = get_price_history(ticker, days=HISTORY_DAYS)
    raw = raw.to_dict("records") if hasattr(raw, "to_dict") else raw

    rows = []
    for r in raw:
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

def classify(ticker):
    rows = normalize_history(ticker)

    if len(rows) < 180:
        return None, None, None, True

    window = rows[-LOOKBACK_BARS:] if len(rows) >= LOOKBACK_BARS else rows
    current_date, current_close, _, _ = rows[-1]

    high_idx, high_row = max(enumerate(window), key=lambda x: x[1][3])
    low_row = min(window, key=lambda x: x[2])

    two_year_high = high_row[3]
    two_year_high_date = high_row[0]
    two_year_low = low_row[2]
    two_year_low_date = low_row[0]

    bars_since_high = len(window) - 1 - high_idx

    spike_range_pct = pct(two_year_high, two_year_low)
    below_high_pct = pct(current_close, two_year_high)
    from_low_pct = pct(current_close, two_year_low)

    if (
        spike_range_pct is None
        or below_high_pct is None
        or from_low_pct is None
        or two_year_high <= 0
        or two_year_low <= 0
    ):
        return None, None, None, True

    # If the high is very recent, do not call it old overhead supply here.
    # That case should be handled by the long-range continuation/fatigue gate.
    if bars_since_high < RECENT_HIGH_IGNORE_BARS:
        return None, None, None, True

    reason = (
        f"Prior-spike gate: 2y high ${two_year_high:.2f} on {two_year_high_date}; "
        f"2y low ${two_year_low:.2f} on {two_year_low_date}; "
        f"current ${current_close:.2f}; spike_range={spike_range_pct:.1f}%; "
        f"below_high={below_high_pct:.1f}%; from_low={from_low_pct:.1f}%; "
        f"bars_since_high={bars_since_high}"
    )

    if spike_range_pct >= HARD_SPIKE_RANGE_PCT and below_high_pct <= HARD_BELOW_HIGH_PCT:
        return (
            "HIDDEN / PRIOR SPIKE DAMAGE",
            "PRIOR SPIKE DAMAGE / OVERHEAD SUPPLY",
            reason,
            False,
        )

    if spike_range_pct >= WATCH_SPIKE_RANGE_PCT and below_high_pct <= WATCH_BELOW_HIGH_PCT:
        return (
            "WATCH ONLY / POST-SPIKE REBUILD",
            "POST-SPIKE REBUILD / OVERHEAD SUPPLY",
            reason,
            False,
        )

    return None, None, None, True

def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT id, ticker, rank, entry_status, pre_pop_status, show_on_main
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = [dict(r) for r in cur.fetchall()]

            checked = 0
            changed = 0

            print(
                f"Prior-spike gate run_date={run_date} rows={len(rows)} "
                f"history_days={HISTORY_DAYS} lookback_bars={LOOKBACK_BARS}"
            )

            for row in rows:
                if not is_candidate(row):
                    continue

                checked += 1

                entry_status, pre_pop_status, reason, show_on_main = classify(row["ticker"])

                if not entry_status:
                    continue

                cur.execute(
                    """
                    UPDATE watchlist_scores
                    SET entry_status = %s,
                        pre_pop_status = %s,
                        pre_pop_reason = %s,
                        show_on_main = %s
                    WHERE id = %s
                    """,
                    [entry_status, pre_pop_status, reason, show_on_main, row["id"]],
                )

                print(
                    f"{row['ticker']} rank {row['rank']} -> "
                    f"{entry_status} | {pre_pop_status} | {reason}"
                )

                changed += 1

            print(f"Prior-spike gate checked={checked} changed={changed}")

    con.close()

if __name__ == "__main__":
    main()
