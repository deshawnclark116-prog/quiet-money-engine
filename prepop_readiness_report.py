#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Readiness Report.

Purpose:
Separate old pre-gate snapshots from new post-gate snapshots.

This answers:
1. Which snapshot runs have pre_pop_status?
2. Which runs contain hidden diagnostics?
3. Which post-gate snapshots have been graded?
4. Which post-gate snapshots are still pending future bars?
5. Are we accidentally judging the new system using old pre-gate rows?

This script does not modify the database.
"""

import os
import statistics
from collections import defaultdict
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.getenv("DATABASE_URL")
REPORT_SOURCE = os.getenv("REPORT_SOURCE", os.getenv("QME_MODEL_VERSION", "quality_heavy_v2"))
HORIZONS = [1, 5, 20]


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def pct(value: Optional[float]) -> str:
    value = safe_float(value)
    if value is None:
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def mean(values):
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return statistics.mean(vals)


def median(values):
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def fetch_snapshot_coverage(cur):
    cur.execute(
        """
        SELECT
            run_date,
            source,
            COUNT(*) AS snapshots,
            SUM(CASE WHEN show_on_main = true THEN 1 ELSE 0 END) AS main_snapshots,
            SUM(CASE WHEN show_on_main = false THEN 1 ELSE 0 END) AS hidden_snapshots,
            SUM(CASE WHEN pre_pop_status IS NULL THEN 1 ELSE 0 END) AS missing_prepop_status,
            SUM(CASE WHEN pre_pop_status IS NOT NULL THEN 1 ELSE 0 END) AS post_gate_snapshots
        FROM prediction_snapshots
        WHERE source = %s
        GROUP BY run_date, source
        ORDER BY run_date
        """,
        [REPORT_SOURCE],
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_graded_coverage(cur):
    cur.execute(
        """
        SELECT
            ps.run_date,
            po.horizon_days,
            COUNT(*) AS graded_rows,
            SUM(CASE WHEN ps.pre_pop_status IS NULL THEN 1 ELSE 0 END) AS pre_gate_graded,
            SUM(CASE WHEN ps.pre_pop_status IS NOT NULL THEN 1 ELSE 0 END) AS post_gate_graded,
            SUM(CASE WHEN ps.show_on_main = true THEN 1 ELSE 0 END) AS graded_main,
            SUM(CASE WHEN ps.show_on_main = false THEN 1 ELSE 0 END) AS graded_hidden
        FROM prediction_snapshots ps
        JOIN prediction_outcomes po
          ON po.snapshot_id = ps.id
        WHERE ps.source = %s
        GROUP BY ps.run_date, po.horizon_days
        ORDER BY ps.run_date, po.horizon_days
        """,
        [REPORT_SOURCE],
    )
    return [dict(r) for r in cur.fetchall()]


def fetch_pending_by_run(cur):
    rows = []

    for horizon in HORIZONS:
        cur.execute(
            """
            SELECT
                ps.run_date,
                %s AS horizon_days,
                COUNT(*) AS total_snapshots,
                SUM(CASE WHEN ps.pre_pop_status IS NOT NULL THEN 1 ELSE 0 END) AS post_gate_snapshots,
                SUM(CASE WHEN ps.show_on_main = true THEN 1 ELSE 0 END) AS main_snapshots,
                SUM(CASE WHEN ps.show_on_main = false THEN 1 ELSE 0 END) AS hidden_snapshots,
                SUM(CASE WHEN po.id IS NULL THEN 1 ELSE 0 END) AS pending_rows,
                SUM(CASE WHEN po.id IS NOT NULL THEN 1 ELSE 0 END) AS graded_rows
            FROM prediction_snapshots ps
            LEFT JOIN prediction_outcomes po
              ON po.snapshot_id = ps.id
             AND po.horizon_days = %s
            WHERE ps.source = %s
            GROUP BY ps.run_date
            ORDER BY ps.run_date
            """,
            [horizon, horizon, REPORT_SOURCE],
        )
        rows.extend([dict(r) for r in cur.fetchall()])

    return rows


def fetch_post_gate_outcomes(cur):
    cur.execute(
        """
        SELECT
            ps.run_date,
            ps.ticker,
            ps.rank,
            ps.entry_status,
            ps.pre_pop_status,
            ps.show_on_main,
            ps.pre_alert_return_1d,
            ps.pre_alert_return_5d,
            ps.pre_alert_return_10d,
            ps.distance_from_sma20,
            po.horizon_days,
            po.raw_return,
            po.excess_return_vs_spy,
            po.hit_5pct,
            po.hit_10pct,
            po.max_drawdown
        FROM prediction_snapshots ps
        JOIN prediction_outcomes po
          ON po.snapshot_id = ps.id
        WHERE ps.source = %s
          AND ps.pre_pop_status IS NOT NULL
        ORDER BY po.horizon_days, ps.run_date, ps.rank
        """,
        [REPORT_SOURCE],
    )
    return [dict(r) for r in cur.fetchall()]


def summarize(rows):
    if not rows:
        return None

    raw = [r.get("raw_return") for r in rows]
    excess = [r.get("excess_return_vs_spy") for r in rows]

    winners = sum(1 for r in rows if safe_float(r.get("raw_return"), 0.0) > 0)
    hit5 = sum(1 for r in rows if r.get("hit_5pct"))
    hit10 = sum(1 for r in rows if r.get("hit_10pct"))

    return {
        "n": len(rows),
        "avg_raw": mean(raw),
        "med_raw": median(raw),
        "avg_excess": mean(excess),
        "win_rate": winners / len(rows),
        "hit5": hit5 / len(rows),
        "hit10": hit10 / len(rows),
        "best": max([safe_float(x, 0.0) for x in raw]),
        "worst": min([safe_float(x, 0.0) for x in raw]),
    }


def print_summary(label, rows):
    s = summarize(rows)
    if not s:
        print(f"{label:35s} | no graded rows yet")
        return

    print(
        f"{label:35s} | "
        f"n={s['n']:4d} | "
        f"avg={pct(s['avg_raw']):>8s} | "
        f"med={pct(s['med_raw']):>8s} | "
        f"excess={pct(s['avg_excess']):>8s} | "
        f"win={pct(s['win_rate']):>8s} | "
        f"hit5={pct(s['hit5']):>8s} | "
        f"hit10={pct(s['hit10']):>8s} | "
        f"best={pct(s['best']):>8s} | "
        f"worst={pct(s['worst']):>8s}"
    )


def group_by(rows, key):
    out = defaultdict(list)
    for r in rows:
        value = r.get(key)
        if value is None:
            value = "NULL"
        out[str(value)].append(r)
    return dict(out)


def print_snapshot_coverage(rows):
    print()
    print("SNAPSHOT COVERAGE BY RUN")
    print("-" * 120)

    for r in rows:
        print(dict(r))


def print_graded_coverage(rows):
    print()
    print("GRADED COVERAGE BY RUN")
    print("-" * 120)

    if not rows:
        print("No graded outcomes yet.")
        return

    for r in rows:
        print(dict(r))


def print_pending(rows):
    print()
    print("PENDING / GRADED BY HORIZON")
    print("-" * 140)
    print("run_date   | h  | total | post_gate | main | hidden | graded | pending")
    print("-" * 140)

    for r in rows:
        print(
            f"{str(r['run_date']):<10} | "
            f"{int(r['horizon_days']):>2d} | "
            f"{int(r['total_snapshots']):>5d} | "
            f"{int(r['post_gate_snapshots'] or 0):>9d} | "
            f"{int(r['main_snapshots'] or 0):>4d} | "
            f"{int(r['hidden_snapshots'] or 0):>6d} | "
            f"{int(r['graded_rows'] or 0):>6d} | "
            f"{int(r['pending_rows'] or 0):>7d}"
        )


def print_post_gate_performance(rows):
    print()
    print("POST-GATE PERFORMANCE ONLY")
    print("-" * 140)

    if not rows:
        print("No post-gate outcomes have matured yet.")
        print("This is expected immediately after creating the new 36-row snapshots.")
        return

    by_horizon = group_by(rows, "horizon_days")

    for horizon, hrows in sorted(by_horizon.items(), key=lambda kv: int(kv[0])):
        print()
        print(f"HORIZON {horizon} DAY")
        print("-" * 140)
        print_summary("ALL POST-GATE", hrows)

        by_main = group_by(hrows, "show_on_main")
        print_summary("MAIN / ACTIONABLE", by_main.get("True", []))
        print_summary("HIDDEN / DIAGNOSTIC", by_main.get("False", []))

        print()
        print("BY PRE_POP_STATUS")
        for label, group in sorted(group_by(hrows, "pre_pop_status").items()):
            print_summary(label, group)

        print()
        print("BY ENTRY_STATUS")
        for label, group in sorted(group_by(hrows, "entry_status").items()):
            print_summary(label[:35], group)


def print_latest_post_gate_examples(rows):
    print()
    print("LATEST POST-GATE GRADED EXAMPLES")
    print("-" * 150)

    if not rows:
        print("No post-gate graded examples yet.")
        return

    rows = sorted(rows, key=lambda r: (r["run_date"], r["horizon_days"], r["rank"]), reverse=True)

    print("run_date   | h | rank | ticker | main  | entry_status              | pre_pop_status    | raw_ret | excess | pre5 | pre10 | vs20")
    print("-" * 150)

    for r in rows[:40]:
        print(
            f"{str(r.get('run_date')):<10} | "
            f"{int(r.get('horizon_days')):>1d} | "
            f"{int(r.get('rank')):>4d} | "
            f"{str(r.get('ticker')):<6} | "
            f"{str(r.get('show_on_main')):<5} | "
            f"{str(r.get('entry_status') or '')[:25]:<25} | "
            f"{str(r.get('pre_pop_status') or '')[:17]:<17} | "
            f"{pct(r.get('raw_return')):>7s} | "
            f"{pct(r.get('excess_return_vs_spy')):>7s} | "
            f"{safe_float(r.get('pre_alert_return_5d'), None)} | "
            f"{safe_float(r.get('pre_alert_return_10d'), None)} | "
            f"{safe_float(r.get('distance_from_sma20'), None)}"
        )


def main():
    print()
    print("PRE-POP READINESS REPORT")
    print("=" * 140)
    print("REPORT_SOURCE:", REPORT_SOURCE)

    conn = get_conn()

    try:
        with conn:
            with conn.cursor() as cur:
                snapshot_rows = fetch_snapshot_coverage(cur)
                graded_rows = fetch_graded_coverage(cur)
                pending_rows = fetch_pending_by_run(cur)
                post_gate_outcomes = fetch_post_gate_outcomes(cur)

    finally:
        conn.close()

    print_snapshot_coverage(snapshot_rows)
    print_graded_coverage(graded_rows)
    print_pending(pending_rows)
    print_post_gate_performance(post_gate_outcomes)
    print_latest_post_gate_examples(post_gate_outcomes)

    print()
    print("DECISION")
    print("-" * 140)

    if not post_gate_outcomes:
        print("Do not judge the new pre-pop gate yet. No post-gate outcomes have matured.")
        print("Backend plumbing is installed; performance proof starts when the new snapshots receive future bars.")
    else:
        print("Post-gate outcomes exist. Use the post-gate section above to judge whether main picks beat hidden diagnostics.")

    print()
    print("DONE.")


if __name__ == "__main__":
    main()
