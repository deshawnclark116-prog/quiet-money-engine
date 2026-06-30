#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Bucket Performance Report.

Purpose:
Grade model performance by bucket instead of blending everything together.

Buckets:
- show_on_main true vs false
- PRE-POP BUY CANDIDATE
- WATCH FOR ENTRY
- HIDDEN / ALREADY POPPED
- HIDDEN / LATE / HIDE
- HIDDEN / HIGH RISK
- HIDDEN / NO PRICE CONTEXT
- pre_pop_status groups

This report does not change the database.
"""

import os
import statistics
from collections import defaultdict
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.getenv("DATABASE_URL")
REPORT_SOURCE = os.getenv("REPORT_SOURCE", os.getenv("QME_MODEL_VERSION", "quality_heavy_v2"))
MIN_SAMPLE_TO_SHOW = int(os.getenv("MIN_SAMPLE_TO_SHOW", "1"))


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100.0:+.2f}%"


def pct_plain(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


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


def fetch_rows():
    conn = get_conn()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        ps.id AS snapshot_id,
                        ps.run_date,
                        ps.source,
                        ps.ticker,
                        ps.rank,
                        ps.composite,
                        ps.price_at_signal,
                        ps.entry_status,
                        ps.pre_pop_status,
                        ps.show_on_main,
                        ps.pre_alert_return_1d,
                        ps.pre_alert_return_3d,
                        ps.pre_alert_return_5d,
                        ps.pre_alert_return_10d,
                        ps.distance_from_sma20,

                        po.horizon_days,
                        po.outcome_date,
                        po.start_price,
                        po.end_price,
                        po.raw_return,
                        po.spy_return,
                        po.excess_return_vs_spy,
                        po.max_drawdown,
                        po.hit_5pct,
                        po.hit_10pct
                    FROM prediction_snapshots ps
                    JOIN prediction_outcomes po
                      ON po.snapshot_id = ps.id
                    WHERE ps.source = %s
                    ORDER BY po.horizon_days, ps.run_date, ps.rank
                    """,
                    [REPORT_SOURCE],
                )
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_snapshot_counts():
    conn = get_conn()

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        source,
                        COUNT(*) AS snapshots,
                        COUNT(DISTINCT run_date) AS runs,
                        MAX(run_date) AS latest_run,
                        SUM(CASE WHEN show_on_main = true THEN 1 ELSE 0 END) AS main_snapshots,
                        SUM(CASE WHEN show_on_main = false THEN 1 ELSE 0 END) AS hidden_snapshots,
                        SUM(CASE WHEN pre_pop_status IS NULL THEN 1 ELSE 0 END) AS missing_prepop_status
                    FROM prediction_snapshots
                    WHERE source = %s
                    GROUP BY source
                    """,
                    [REPORT_SOURCE],
                )
                return dict(cur.fetchone() or {})
    finally:
        conn.close()


def summarize(rows):
    if not rows:
        return None

    raw = [r["raw_return"] for r in rows]
    excess = [r["excess_return_vs_spy"] for r in rows]
    dd = [r["max_drawdown"] for r in rows]

    hit_5 = sum(1 for r in rows if r.get("hit_5pct"))
    hit_10 = sum(1 for r in rows if r.get("hit_10pct"))
    winners = sum(1 for r in rows if safe_float(r.get("raw_return"), 0.0) > 0)

    return {
        "n": len(rows),
        "avg_raw": mean(raw),
        "med_raw": median(raw),
        "avg_excess": mean(excess),
        "med_excess": median(excess),
        "avg_drawdown": mean(dd),
        "winners": winners,
        "win_rate": winners / len(rows) if rows else None,
        "hit_5": hit_5,
        "hit_5_rate": hit_5 / len(rows) if rows else None,
        "hit_10": hit_10,
        "hit_10_rate": hit_10 / len(rows) if rows else None,
        "best": max([safe_float(x, 0.0) for x in raw]) if raw else None,
        "worst": min([safe_float(x, 0.0) for x in raw]) if raw else None,
    }


def print_summary_line(label, rows):
    s = summarize(rows)

    if not s or s["n"] < MIN_SAMPLE_TO_SHOW:
        return

    print(
        f"{label:36s} | "
        f"n={s['n']:4d} | "
        f"avg={pct(s['avg_raw']):>8s} | "
        f"med={pct(s['med_raw']):>8s} | "
        f"excess={pct(s['avg_excess']):>8s} | "
        f"win={pct(s['win_rate']):>8s} | "
        f"hit5={pct(s['hit_5_rate']):>8s} | "
        f"hit10={pct(s['hit_10_rate']):>8s} | "
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


def bucket_name(row):
    if row.get("show_on_main") is True:
        return "MAIN / ACTIONABLE"
    return "HIDDEN / DIAGNOSTIC"


def entry_bucket(row):
    entry = str(row.get("entry_status") or "NULL")

    if entry.startswith("PRE-POP BUY CANDIDATE"):
        return "PRE-POP BUY CANDIDATE"

    if entry.startswith("WATCH FOR ENTRY"):
        return "WATCH FOR ENTRY"

    if entry.startswith("HIDDEN / ALREADY POPPED"):
        return "HIDDEN / ALREADY POPPED"

    if entry.startswith("HIDDEN / LATE"):
        return "HIDDEN / LATE"

    if entry.startswith("HIDDEN / HIGH RISK"):
        return "HIDDEN / HIGH RISK"

    if entry.startswith("HIDDEN / NO PRICE"):
        return "HIDDEN / NO PRICE"

    return entry


def add_derived_buckets(rows):
    for r in rows:
        r["main_bucket"] = bucket_name(r)
        r["entry_bucket"] = entry_bucket(r)
        r["rank_bucket"] = rank_bucket(r.get("rank"))


def rank_bucket(rank_value):
    rank = int(safe_float(rank_value, 9999))

    if rank <= 5:
        return "rank_01_05"
    if rank <= 10:
        return "rank_06_10"
    if rank <= 15:
        return "rank_11_15"
    if rank <= 20:
        return "rank_16_20"
    if rank <= 25:
        return "rank_21_25"
    return "rank_26_plus_hidden"


def print_section(title, rows, key):
    print()
    print(title)
    print("-" * 145)

    groups = group_by(rows, key)

    ordered = sorted(
        groups.items(),
        key=lambda kv: len(kv[1]),
        reverse=True,
    )

    for label, group_rows in ordered:
        print_summary_line(label, group_rows)


def print_horizon_report(rows):
    by_horizon = group_by(rows, "horizon_days")

    for horizon, horizon_rows in sorted(by_horizon.items(), key=lambda kv: int(kv[0])):
        print()
        print("=" * 145)
        print(f"HORIZON: {horizon} DAY")
        print("=" * 145)

        print_summary_line("ALL", horizon_rows)

        print_section("MAIN VS HIDDEN", horizon_rows, "main_bucket")
        print_section("ENTRY STATUS BUCKETS", horizon_rows, "entry_bucket")
        print_section("PRE-POP STATUS BUCKETS", horizon_rows, "pre_pop_status")
        print_section("RANK BUCKETS", horizon_rows, "rank_bucket")


def print_latest_graded_examples(rows):
    print()
    print("LATEST GRADED EXAMPLES")
    print("-" * 145)

    rows = sorted(rows, key=lambda r: (r["run_date"], r["horizon_days"], r["rank"]), reverse=True)

    print(
        "run_date   | h | rank | ticker | main | entry_status              | pre_pop_status    | raw_ret | excess | pre5 | pre10 | vs20"
    )
    print("-" * 145)

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
            f"{pct_plain(safe_float(r.get('pre_alert_return_5d'))):>7s} | "
            f"{pct_plain(safe_float(r.get('pre_alert_return_10d'))):>7s} | "
            f"{pct_plain(safe_float(r.get('distance_from_sma20'))):>7s}"
        )


def main():
    counts = fetch_snapshot_counts()

    print()
    print("PRE-POP BUCKET PERFORMANCE REPORT")
    print("=" * 145)
    print("REPORT_SOURCE:", REPORT_SOURCE)
    print("snapshot_info:", counts)

    rows = fetch_rows()
    add_derived_buckets(rows)

    print("graded_outcomes:", len(rows))

    if not rows:
        print("No graded outcomes yet.")
        return

    print_horizon_report(rows)
    print_latest_graded_examples(rows)

    print()
    print("DONE.")
    print("Use this report to decide whether main actionable picks beat hidden diagnostics and whether the gate is doing useful work.")


if __name__ == "__main__":
    main()
