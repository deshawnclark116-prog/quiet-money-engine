import os
import json
import logging
from datetime import date, datetime

import psycopg2
from psycopg2.extras import RealDictCursor, Json


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_VERSION = os.getenv("MODEL_VERSION", "quiet-money-v0.1")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")


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


def first_existing(columns, candidates):
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def qcol(column_name):
    return '"' + column_name.replace('"', '""') + '"'


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
            return {"raw": value}

    return {"raw": str(value)}


def main():
    logging.info("Connecting to Postgres...")
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    try:
        with conn:
            with conn.cursor() as cur:
                watchlist_cols = get_columns(cur, "watchlist_scores")

                if not watchlist_cols:
                    raise RuntimeError("watchlist_scores table not found or has no columns")

                run_date_col = first_existing(
                    watchlist_cols,
                    ["run_date", "date", "scored_at", "created_at", "saved_at", "inserted_at"],
                )
                ticker_col = first_existing(watchlist_cols, ["ticker", "symbol"])
                composite_col = first_existing(
                    watchlist_cols,
                    ["composite", "score", "composite_score", "rank_score"],
                )
                signals_col = first_existing(watchlist_cols, ["signals", "signal_values", "features"])
                price_col = first_existing(
                    watchlist_cols,
                    ["price_at_signal", "price", "close", "close_price", "last_price"],
                )

                if not run_date_col:
                    raise RuntimeError("No usable run/date column found in watchlist_scores")

                if not ticker_col:
                    raise RuntimeError("No ticker/symbol column found in watchlist_scores")

                if not composite_col:
                    raise RuntimeError("No composite/score column found in watchlist_scores")

                cur.execute(
                    f"""
                    SELECT MAX({qcol(run_date_col)}) AS latest_run
                    FROM watchlist_scores
                    """
                )
                latest_run = cur.fetchone()["latest_run"]

                if latest_run is None:
                    raise RuntimeError("No watchlist rows found")

                if isinstance(latest_run, datetime):
                    run_date = latest_run.date()
                elif isinstance(latest_run, date):
                    run_date = latest_run
                else:
                    run_date = str(latest_run)

                logging.info("Latest watchlist run_date: %s", run_date)

                cur.execute(
                    f"""
                    SELECT *
                    FROM watchlist_scores
                    WHERE {qcol(run_date_col)} = %s
                    ORDER BY {qcol(composite_col)} DESC
                    """,
                    [latest_run],
                )
                watchlist_rows = cur.fetchall()

                if not watchlist_rows:
                    raise RuntimeError("No rows found for latest watchlist run")

                signal_name_set = set()

                for row in watchlist_rows:
                    signals = normalize_signals(row.get(signals_col)) if signals_col else {}
                    for name in signals:
                        signal_name_set.add(name)

                signal_names = sorted(signal_name_set) if signal_name_set else ["unknown"]

                cur.execute(
                    """
                    INSERT INTO prediction_runs (
                        run_date,
                        model_version,
                        universe,
                        signal_names,
                        notes
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (run_date, model_version)
                    DO UPDATE SET
                        signal_names = EXCLUDED.signal_names,
                        notes = EXCLUDED.notes
                    RETURNING id
                    """,
                    [
                        run_date,
                        MODEL_VERSION,
                        os.getenv("UNIVERSE", "dynamic"),
                        Json(signal_names),
                        "Snapshot copied from latest watchlist_scores run.",
                    ],
                )

                run_id = cur.fetchone()["id"]
                logging.info("prediction_runs id: %s", run_id)

                # Replace current run snapshots exactly, so stale same-day tickers disappear.
                cur.execute(
                    "DELETE FROM prediction_snapshots WHERE run_id = %s",
                    [run_id],
                )

                inserted = 0

                for idx, row in enumerate(watchlist_rows, start=1):
                    ticker = row[ticker_col]
                    composite = row[composite_col]
                    signals = normalize_signals(row.get(signals_col)) if signals_col else {}
                    price_at_signal = row.get(price_col) if price_col else None

                    cur.execute(
                        """
                        INSERT INTO prediction_snapshots (
                            run_id,
                            run_date,
                            ticker,
                            rank,
                            composite,
                            signals,
                            price_at_signal,
                            source
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        [
                            run_id,
                            run_date,
                            ticker,
                            idx,
                            composite,
                            Json(signals),
                            price_at_signal,
                            "watchlist_scores",
                        ],
                    )

                    inserted += 1

                logging.info("Saved %s prediction snapshots for %s", inserted, run_date)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
