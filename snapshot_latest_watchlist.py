#!/usr/bin/env python3
"""
Quiet Money Engine — snapshot latest watchlist.

Purpose:
Copy the latest watchlist_scores run into prediction_snapshots so future
grading can evaluate the model.

This version preserves the new pre-pop gate fields:

- pre_alert_return_1d
- pre_alert_return_3d
- pre_alert_return_5d
- pre_alert_return_10d
- distance_from_sma20
- pre_pop_status
- pre_pop_reason
- show_on_main

That lets future reports separate:
- clean pre-pop candidates
- late/chase rejects
- already-popped rejects
- high-risk rejects
- no-price-context rejects
"""

import os
from datetime import datetime
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor, Json


DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_VERSION = os.getenv("QME_MODEL_VERSION", "quality_heavy_v2").strip() or "quality_heavy_v2"


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")

    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def table_columns(cur, table_name: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        """,
        [table_name],
    )
    return {r["column_name"] for r in cur.fetchall()}


def add_if_available(cols: list[str], vals: list[Any], available: set[str], name: str, value: Any) -> None:
    if name in available:
        cols.append(name)
        vals.append(value)


def latest_watchlist_run(cur):
    cur.execute(
        """
        SELECT MAX(run_date) AS latest_run
        FROM watchlist_scores
        """
    )
    row = cur.fetchone()
    return row["latest_run"] if row else None


def fetch_watchlist_rows(cur, run_date):
    cur.execute(
        """
        SELECT *
        FROM watchlist_scores
        WHERE run_date = %s
        ORDER BY rank ASC
        """,
        [run_date],
    )
    return [dict(r) for r in cur.fetchall()]


def create_prediction_run(cur, run_date, snapshot_count: int):
    run_cols = table_columns(cur, "prediction_runs")

    cols = []
    vals = []

    add_if_available(cols, vals, run_cols, "run_date", run_date)
    add_if_available(cols, vals, run_cols, "source", MODEL_VERSION)
    add_if_available(cols, vals, run_cols, "model_version", MODEL_VERSION)
    add_if_available(cols, vals, run_cols, "snapshot_count", snapshot_count)
    add_if_available(cols, vals, run_cols, "created_at", datetime.utcnow())

    if not cols:
        raise RuntimeError("prediction_runs table has no usable insert columns")

    placeholders = ", ".join(["%s"] * len(vals))
    col_sql = ", ".join(cols)

    if "id" in run_cols:
        cur.execute(
            f"""
            INSERT INTO prediction_runs ({col_sql})
            VALUES ({placeholders})
            RETURNING id
            """,
            vals,
        )
        return cur.fetchone()["id"]

    cur.execute(
        f"""
        INSERT INTO prediction_runs ({col_sql})
        VALUES ({placeholders})
        """,
        vals,
    )
    return None


def delete_existing_snapshots(cur, run_date):
    snap_cols = table_columns(cur, "prediction_snapshots")

    if "source" in snap_cols:
        cur.execute(
            """
            DELETE FROM prediction_snapshots
            WHERE run_date = %s
              AND source = %s
            """,
            [run_date, MODEL_VERSION],
        )
    elif "model_version" in snap_cols:
        cur.execute(
            """
            DELETE FROM prediction_snapshots
            WHERE run_date = %s
              AND model_version = %s
            """,
            [run_date, MODEL_VERSION],
        )
    else:
        cur.execute(
            """
            DELETE FROM prediction_snapshots
            WHERE run_date = %s
            """,
            [run_date],
        )

    return cur.rowcount


def insert_snapshot(cur, snap_cols: set[str], run_id, row: dict):
    cols = []
    vals = []

    add_if_available(cols, vals, snap_cols, "run_id", run_id)
    add_if_available(cols, vals, snap_cols, "run_date", row.get("run_date"))
    add_if_available(cols, vals, snap_cols, "source", MODEL_VERSION)
    add_if_available(cols, vals, snap_cols, "model_version", MODEL_VERSION)

    add_if_available(cols, vals, snap_cols, "ticker", row.get("ticker"))
    add_if_available(cols, vals, snap_cols, "rank", row.get("rank"))
    add_if_available(cols, vals, snap_cols, "composite", row.get("composite"))

    signals = row.get("signals")
    add_if_available(cols, vals, snap_cols, "signals", Json(signals) if isinstance(signals, dict) else signals)

    add_if_available(cols, vals, snap_cols, "price_at_signal", row.get("price_at_signal"))

    # Paper-trade fields.
    add_if_available(cols, vals, snap_cols, "entry_status", row.get("entry_status"))
    add_if_available(cols, vals, snap_cols, "entry_reason", row.get("entry_reason"))
    add_if_available(cols, vals, snap_cols, "stop_loss_price", row.get("stop_loss_price"))
    add_if_available(cols, vals, snap_cols, "first_trim_price", row.get("first_trim_price"))
    add_if_available(cols, vals, snap_cols, "max_hold_days", row.get("max_hold_days"))
    add_if_available(cols, vals, snap_cols, "trade_rule_version", row.get("trade_rule_version"))

    # Pre-pop gate fields.
    add_if_available(cols, vals, snap_cols, "pre_alert_return_1d", row.get("pre_alert_return_1d"))
    add_if_available(cols, vals, snap_cols, "pre_alert_return_3d", row.get("pre_alert_return_3d"))
    add_if_available(cols, vals, snap_cols, "pre_alert_return_5d", row.get("pre_alert_return_5d"))
    add_if_available(cols, vals, snap_cols, "pre_alert_return_10d", row.get("pre_alert_return_10d"))
    add_if_available(cols, vals, snap_cols, "distance_from_sma20", row.get("distance_from_sma20"))
    add_if_available(cols, vals, snap_cols, "pre_pop_status", row.get("pre_pop_status"))
    add_if_available(cols, vals, snap_cols, "pre_pop_reason", row.get("pre_pop_reason"))
    add_if_available(cols, vals, snap_cols, "show_on_main", row.get("show_on_main"))

    add_if_available(cols, vals, snap_cols, "created_at", datetime.utcnow())

    if not cols:
        raise RuntimeError("prediction_snapshots table has no usable insert columns")

    placeholders = ", ".join(["%s"] * len(vals))
    col_sql = ", ".join(cols)

    cur.execute(
        f"""
        INSERT INTO prediction_snapshots ({col_sql})
        VALUES ({placeholders})
        """,
        vals,
    )


def main():
    print("MODEL_VERSION:", MODEL_VERSION)

    conn = get_conn()

    try:
        with conn:
            with conn.cursor() as cur:
                run_date = latest_watchlist_run(cur)

                if not run_date:
                    raise RuntimeError("No watchlist_scores rows found")

                rows = fetch_watchlist_rows(cur, run_date)

                if not rows:
                    raise RuntimeError(f"No watchlist rows found for run_date={run_date}")

                deleted = delete_existing_snapshots(cur, run_date)
                print(f"Deleted {deleted} existing prediction snapshots for {run_date} / {MODEL_VERSION}")

                run_id = create_prediction_run(cur, run_date, len(rows))
                snap_cols = table_columns(cur, "prediction_snapshots")

                saved = 0

                for row in rows:
                    insert_snapshot(cur, snap_cols, run_id, row)
                    saved += 1

                shown = sum(1 for r in rows if r.get("show_on_main") is True)
                hidden = sum(1 for r in rows if r.get("show_on_main") is False)

                print(f"Saved {saved} prediction snapshots for {run_date} as {MODEL_VERSION}")
                print(f"Copied main actionable snapshots: {shown}")
                print(f"Copied hidden diagnostic snapshots: {hidden}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
