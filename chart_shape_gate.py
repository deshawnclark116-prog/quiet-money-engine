#!/usr/bin/env python3
"""
Quiet Money Engine — Chart Shape Gate.

Runs after prior_spike_gate.py and before finalize_main_board.py.

This gate applies the chart-shape classifier (chart_shape_audit.py) to the
current main-board candidates. It exists because three different shape
failures kept slipping past the numeric gates one at a time:

  TOI  — medium-term already repriced
  IMRX — post-spike rebuild under overhead supply
  SGHC — multi-year extended staircase near highs

Behavior by tier:
  Tier 1 (FRESH BASE, CONTROLLED BREAKOUT)  -> untouched
  Tier 2 (BASE-BUILDING)                    -> untouched (early watch is
                                               still main-board material)
  Tier 3 (MULTI-YEAR EXTENDED, CONTINUATION,
          POST-SPIKE REBUILD)               -> WATCH ONLY, off main
  Severe (PRIOR SPIKE DAMAGE, FALLING KNIFE)-> HIDDEN, off main
  NO PRICE CONTEXT (incl. fetch failure)    -> untouched — a transient
                                               price-provider miss must
                                               never hide a valid name

The classification logic lives in chart_shape_audit.py and is imported,
never copied, so the non-mutating audit and this gate always agree.

Set QME_SHAPE_GATE_DRY_RUN=true to log decisions without writing.
"""

import os

import psycopg2
from psycopg2.extras import RealDictCursor

from chart_shape_audit import (
    LABEL_BASE_BUILDING,
    LABEL_CONTINUATION,
    LABEL_CONTROLLED_BREAKOUT,
    LABEL_FALLING_KNIFE,
    LABEL_FRESH_BASE,
    LABEL_MULTI_YEAR_EXTENDED,
    LABEL_NO_CONTEXT,
    LABEL_POST_SPIKE_REBUILD,
    LABEL_PRIOR_SPIKE_DAMAGE,
    TIER_BY_LABEL,
    audit_ticker,
)

DRY_RUN = os.getenv("QME_SHAPE_GATE_DRY_RUN", "false").lower() in {"1", "true", "yes", "y"}

# Same candidate definition as prior_spike_gate.py: only names still in
# contention for the main board get shape-checked.
MAIN_ENTRY_STATUSES = {
    "PRE-POP BUY CANDIDATE",
    "WATCH FOR ENTRY",
}

MAIN_PREPOP_STATUSES = {
    "EARLY / CLEAN",
    "EARLY / WAKING",
}

# label -> (entry_status, pre_pop_status, show_on_main)
# Labels not in this map (Tier 1, Tier 2, NO PRICE CONTEXT) pass untouched.
DEMOTIONS = {
    LABEL_MULTI_YEAR_EXTENDED: (
        "WATCH ONLY / MULTI-YEAR EXTENDED",
        "MULTI-YEAR EXTENDED",
        False,
    ),
    LABEL_CONTINUATION: (
        "WATCH ONLY / CONTINUATION",
        "CONTINUATION / REPRICED",
        False,
    ),
    LABEL_POST_SPIKE_REBUILD: (
        "WATCH ONLY / POST-SPIKE REBUILD",
        "POST-SPIKE REBUILD / OVERHEAD SUPPLY",
        False,
    ),
    LABEL_PRIOR_SPIKE_DAMAGE: (
        "HIDDEN / PRIOR SPIKE DAMAGE",
        "PRIOR SPIKE DAMAGE / OVERHEAD SUPPLY",
        False,
    ),
    LABEL_FALLING_KNIFE: (
        "HIDDEN / FALLING KNIFE",
        "FALLING KNIFE / WEAK TREND",
        False,
    ),
}

KEEP_LABELS = {
    LABEL_FRESH_BASE,
    LABEL_CONTROLLED_BREAKOUT,
    LABEL_BASE_BUILDING,
    LABEL_NO_CONTEXT,
}


def is_candidate(row):
    return (
        str(row.get("entry_status") or "") in MAIN_ENTRY_STATUSES
        and str(row.get("pre_pop_status") or "") in MAIN_PREPOP_STATUSES
    )


def decide(result):
    """Map a classifier result to a DB update, or None to leave the row
    untouched. Pure function so it can be tested without a database."""
    label = result["label"]

    if label in KEEP_LABELS or label not in DEMOTIONS:
        return None

    entry_status, pre_pop_status, show_on_main = DEMOTIONS[label]

    reason = (
        f"Chart-shape gate: {label} "
        f"({result['confidence']} confidence): {result['reason']}"
    )

    return entry_status, pre_pop_status, reason, show_on_main


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
            demoted = 0
            kept = 0

            print(
                f"Chart-shape gate run_date={run_date} rows={len(rows)} "
                f"dry_run={DRY_RUN}"
            )

            for row in rows:
                if not is_candidate(row):
                    continue

                checked += 1
                ticker = row["ticker"]

                try:
                    result = audit_ticker(ticker)
                except Exception as exc:
                    print(f"{ticker} rank {row['rank']} -> kept (classifier error: {exc})")
                    kept += 1
                    continue

                label = result["label"]
                tier = TIER_BY_LABEL.get(label, "?")
                decision = decide(result)

                if decision is None:
                    print(f"{ticker} rank {row['rank']} -> kept | {label} | {tier}")
                    kept += 1
                    continue

                entry_status, pre_pop_status, reason, show_on_main = decision

                if not DRY_RUN:
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
                    f"{ticker} rank {row['rank']} -> "
                    f"{entry_status} | {pre_pop_status} | {reason}"
                )

                demoted += 1

            print(
                f"Chart-shape gate checked={checked} kept={kept} "
                f"demoted={demoted} dry_run={DRY_RUN}"
            )

    con.close()


if __name__ == "__main__":
    main()
