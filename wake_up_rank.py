#!/usr/bin/env python3
"""
Quiet Money Engine — Wake-Up Ranker.

Runs after finalize_main_board.py and before rank_compactor.py.

The gates decide WHO is on the main board (clean, cheap, quiet setups).
This layer decides the ORDER: rank #1 must mean "closest to its move
right now", not "highest legacy composite".

Ranking score per main-board name:

  blended = wake_up_score            (0-100, from wake_up_audit.py)
          + TIER1_BONUS              if the chart shape is Tier 1
                                     (fresh base / controlled breakout)
          - COOLING_PENALTY          if price and 20d trend point down

Timing drives the order; chart quality breaks the close calls. A truly
FIRING rough chart can outrank a sleeping clean one, but near-ties go
to the better chart.

Both scores are imported from their audit modules, never copied, so the
audits and production always agree.

Writes, for show_on_main rows of the latest run only:
  rank          1..N in blended order (rank_compactor then renumbers
                the full list with main names first)
  entry_reason  live wake-up evidence, e.g.
                "Wake-up 67/100 WARMING (chart: CONTROLLED BREAKOUT
                 SETUP). 10d volume 0.95x its 60d norm; ..."

A name whose price data cannot be fetched keeps its position at the
bottom of the main board and its row text is left untouched — a
transient provider miss must never scramble the board.

Set QME_WAKE_RANK_DRY_RUN=true to log decisions without writing.
"""

import os

import psycopg2
from psycopg2.extras import RealDictCursor

from chart_shape_audit import (
    LABEL_CONTROLLED_BREAKOUT,
    LABEL_FRESH_BASE,
    audit_ticker as shape_audit_ticker,
)
from wake_up_audit import STATUS_COOLING, audit_ticker as wake_audit_ticker

DRY_RUN = os.getenv("QME_WAKE_RANK_DRY_RUN", "false").lower() in {"1", "true", "yes", "y"}

TIER1_BONUS = float(os.getenv("QME_RANK_TIER1_BONUS", "15"))
COOLING_PENALTY = float(os.getenv("QME_RANK_COOLING_PENALTY", "10"))

TIER1_LABELS = {LABEL_FRESH_BASE, LABEL_CONTROLLED_BREAKOUT}


def blend(wake_result, shape_result):
    """Pure ranking-score function so it can be tested without a DB."""
    score = wake_result["wake_up_score"]

    if shape_result and shape_result.get("label") in TIER1_LABELS:
        score += TIER1_BONUS

    if wake_result["status"] == STATUS_COOLING:
        score -= COOLING_PENALTY

    return score


def evaluate(ticker):
    """Return (blended_score, entry_reason) or (None, None) when the
    ticker cannot be scored."""
    wake = wake_audit_ticker(ticker)

    if not wake.get("ok"):
        return None, None

    try:
        shape = shape_audit_ticker(ticker)
    except Exception:
        shape = None

    blended = blend(wake, shape)

    shape_note = f" (chart: {shape['label']})" if shape else ""
    reason = (
        f"Wake-up {wake['wake_up_score']:.0f}/100 {wake['status']}"
        f"{shape_note}. {wake['reason']}"
    )

    return blended, reason


def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT id, ticker, rank
                FROM watchlist_scores
                WHERE run_date = %s
                  AND show_on_main = TRUE
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = [dict(r) for r in cur.fetchall()]

            print(f"Wake-up ranker run_date={run_date} main_rows={len(rows)} "
                  f"tier1_bonus={TIER1_BONUS} cooling_penalty={COOLING_PENALTY} "
                  f"dry_run={DRY_RUN}")

            if not rows:
                print("No main-board rows; nothing to rank.")
                return

            scored = []
            for row in rows:
                try:
                    blended, reason = evaluate(row["ticker"])
                except Exception as exc:
                    print(f"{row['ticker']} -> scoring error, keeping position: {exc}")
                    blended, reason = None, None

                scored.append({**row, "blended": blended, "reason": reason})

            # Blended score desc; unscorable names keep their old relative
            # order at the bottom.
            scored.sort(
                key=lambda r: (
                    0 if r["blended"] is not None else 1,
                    -(r["blended"] or 0),
                    r["rank"],
                )
            )

            for new_rank, row in enumerate(scored, 1):
                marker = (
                    f"{row['blended']:.1f}" if row["blended"] is not None else "unscored"
                )
                print(f"  #{new_rank} {row['ticker']:<6} blended={marker} "
                      f"(was rank {row['rank']})")

                if row["reason"]:
                    print(f"      {row['reason']}")

                if DRY_RUN:
                    continue

                if row["reason"] is not None:
                    cur.execute(
                        """
                        UPDATE watchlist_scores
                        SET rank = %s, entry_reason = %s
                        WHERE id = %s
                        """,
                        [new_rank, row["reason"], row["id"]],
                    )
                else:
                    cur.execute(
                        "UPDATE watchlist_scores SET rank = %s WHERE id = %s",
                        [new_rank, row["id"]],
                    )

            print(f"Wake-up ranker done dry_run={DRY_RUN}")

    con.close()


if __name__ == "__main__":
    main()
