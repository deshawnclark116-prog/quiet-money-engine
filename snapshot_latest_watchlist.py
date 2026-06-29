#!/usr/bin/env python3
"""
Quiet Money Engine — snapshot latest watchlist.

Copies the latest rows from watchlist_scores into prediction_snapshots so the
grader can later score 1d / 5d / 20d outcomes.

Important:
- source/model version is controlled by QME_MODEL_VERSION
- default model version is quality_heavy_v2
"""

import os
import json
import logging
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor, Json


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_VERSION = os.getenv("QME_MODEL_VERSION", "quality_heavy_v2").strip() or "quality_heavy_v2"


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is required")

    logging.info("Connecting to Postgres.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def get_columns(cur, table_name):
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
        """,
        [table_name],
    )
    return [row["column_name"] for row in cur.fetchall()]


def pick_col(columns, candidates):
    for c in candidates:
        if c in columns:
            return c
    return None


def normalize_signals(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}

    return {}


def create_prediction_run(cur, latest_run_date):
    """
    Dynamically inserts into prediction_runs based on columns that exist.
    Expected common columns:
    - id
    - run_date
    - source
    - notes
    - created_at
    """
    run_cols = get_columns(cur, "prediction_runs")

    if not run_cols:
        raise RuntimeError("prediction_runs table not found or has no columns")

    insert_cols = []
    values = []

    if "run_date" in run_cols:
        insert_cols.append("run_date")
        values.append(latest_run_date)

    if "source" in run_cols:
        insert_cols.append("source")
        values.append(MODEL_VERSION)

    if "model_version" in run_cols:
        insert_cols.append("model_version")
        values.append(MODEL_VERSION)

    if "notes" in run_cols:
        insert_cols.append("notes")
        values.append(f"Snapshot copied from latest watchlist_scores run using {MODEL_VERSION}.")

    if "description" in run_cols:
        insert_cols.append("description")
        values.append(f"Snapshot copied from latest watchlist_scores run using {MODEL_VERSION}.")

    if insert_cols:
        placeholders = ", ".join(["%s"] * len(insert_cols))
        col_sql = ", ".join(insert_cols)

        cur.execute(
            f"""
            INSERT INTO prediction_runs ({col_sql})
            VALUES ({placeholders})
            RETURNING id
            """,
            values,
        )
    else:
        cur.execute(
            """
            INSERT INTO prediction_runs DEFAULT VALUES
            RETURNING id
            """
        )

    row = cur.fetchone()
    run_id = row["id"]

    logging.info("prediction_runs id: %s", run_id)
    return run_id


def load_latest_watchlist(cur):
    watchlist_cols = get_columns(cur, "watchlist_scores")

    if not watchlist_cols:
        raise RuntimeError("watchlist_scores table not found or has no columns")

    run_col = pick_col(watchlist_cols, ["run_date", "date", "as_of_date"])
    ticker_col = pick_col(watchlist_cols, ["ticker", "symbol"])
    rank_col = pick_col(watchlist_cols, ["rank", "watchlist_rank"])
    composite_col = pick_col(watchlist_cols, ["composite", "score", "composite_score"])
    signals_col = pick_col(watchlist_cols, ["signals", "signal_values", "signal_json"])
    price_col = pick_col(watchlist_cols, ["price_at_signal", "price", "last_price", "close"])

    if not run_col:
        raise RuntimeError("No usable run/date column found in watchlist_scores")

    if not ticker_col:
        raise RuntimeError("No ticker/symbol column found in watchlist_scores")

    if not composite_col:
        raise RuntimeError("No composite/score column found in watchlist_scores")

    cur.execute(
        f"""
        SELECT MAX({run_col}) AS latest_run_date
        FROM watchlist_scores
        """
    )

    latest = cur.fetchone()
    latest_run_date = latest["latest_run_date"] if latest else None

    if not latest_run_date:
        raise RuntimeError("No latest run_date found in watchlist_scores")

    logging.info("Latest watchlist run_date: %s", latest_run_date)

    order_sql = f"ORDER BY {rank_col} ASC" if rank_col else f"ORDER BY {composite_col} DESC"

    cur.execute(
        f"""
        SELECT *
        FROM watchlist_scores
        WHERE {run_col} = %s
        {order_sql}
        """,
        [latest_run_date],
    )

    rows = [dict(r) for r in cur.fetchall()]

    if not rows:
        raise RuntimeError(f"No watchlist rows found for latest run_date {latest_run_date}")

    return {
        "latest_run_date": latest_run_date,
        "rows": rows,
        "cols": {
            "run": run_col,
            "ticker": ticker_col,
            "rank": rank_col,
            "composite": composite_col,
            "signals": signals_col,
            "price": price_col,
        },
    }


def delete_existing_snapshots(cur, latest_run_date):
    """
    Keep reruns clean for the same model version and run date.
    """
    cur.execute(
        """
        DELETE FROM prediction_snapshots
        WHERE run_date = %s
          AND source = %s
        """,
        [latest_run_date, MODEL_VERSION],
    )

    if cur.rowcount:
        logging.info(
            "Deleted %s existing prediction snapshots for %s / %s",
            cur.rowcount,
            latest_run_date,
            MODEL_VERSION,
        )


def insert_snapshots(cur, run_id, latest_run_date, rows, cols):
    snapshot_cols = get_columns(cur, "prediction_snapshots")

    if not snapshot_cols:
        raise RuntimeError("prediction_snapshots table not found or has no columns")

    inserted = 0

    for i, row in enumerate(rows, 1):
        ticker = str(row.get(cols["ticker"]) or "").upper().strip()

        if not ticker:
            continue

        rank_value = row.get(cols["rank"]) if cols["rank"] else i
        composite_value = row.get(cols["composite"])
        signals_value = normalize_signals(row.get(cols["signals"])) if cols["signals"] else {}
        price_value = row.get(cols["price"]) if cols["price"] else None

        insert_cols = []
        values = []

        if "run_id" in snapshot_cols:
            insert_cols.append("run_id")
            values.append(run_id)

        if "run_date" in snapshot_cols:
            insert_cols.append("run_date")
            values.append(latest_run_date)

        if "ticker" in snapshot_cols:
            insert_cols.append("ticker")
            values.append(ticker)

        if "rank" in snapshot_cols:
            insert_cols.append("rank")
            values.append(rank_value)

        if "composite" in snapshot_cols:
            insert_cols.append("composite")
            values.append(composite_value)

        if "signals" in snapshot_cols:
            insert_cols.append("signals")
            values.append(Json(signals_value))

        if "price_at_signal" in snapshot_cols:
            insert_cols.append("price_at_signal")
            values.append(price_value)

        if "source" in snapshot_cols:
            insert_cols.append("source")
            values.append(MODEL_VERSION)

        if not insert_cols:
            raise RuntimeError("No usable insert columns found for prediction_snapshots")

        placeholders = ", ".join(["%s"] * len(insert_cols))
        col_sql = ", ".join(insert_cols)

        cur.execute(
            f"""
            INSERT INTO prediction_snapshots ({col_sql})
            VALUES ({placeholders})
            """,
            values,
        )

        inserted += 1

    logging.info("Saved %s prediction snapshots for %s as %s", inserted, latest_run_date, MODEL_VERSION)
    return inserted


def main():
    logging.info("MODEL_VERSION: %s", MODEL_VERSION)

    conn = connect()

    try:
        with conn:
            with conn.cursor() as cur:
                latest = load_latest_watchlist(cur)
                latest_run_date = latest["latest_run_date"]

                run_id = create_prediction_run(cur, latest_run_date)

                delete_existing_snapshots(cur, latest_run_date)

                inserted = insert_snapshots(
                    cur=cur,
                    run_id=run_id,
                    latest_run_date=latest_run_date,
                    rows=latest["rows"],
                    cols=latest["cols"],
                )

                if inserted <= 0:
                    raise RuntimeError("No prediction snapshots were inserted")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
