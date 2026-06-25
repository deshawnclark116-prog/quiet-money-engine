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

                signal_names = [signals_col] if signals_col else ["momentum_12_1"]

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
                        os.getenv("UNIVERSE", "default"),
                        Json(signal_names),
                        "Snapshot copied from latest watchlist_scores run.",
                    ],
                )

                run_id = cur.fetchone()["id"]
                logging.info("prediction_runs id: %s", run_id)

                select_fields = [
                    f"{qcol(ticker_col)} AS ticker",
                    f"{qcol(composite_col)} AS composite",
                ]

                if signals_col:
                    select_fields.append(f"{qcol(signals_col)} AS signals")
                else:
                    select_fields.append("NULL AS signals")

                if price_col:
                    select_fields.append(f"{qcol(price_col)} AS price_at_signal")
                else:
                    select_fields.append("NULL AS price_at_signal")

                sql = f"""
                    SELECT
                        {", ".join(select_fields)}
                    FROM watchlist_scores
                    WHERE {qcol(run_date_col)} = %s
                    ORDER BY {qcol(composite_col)} DESC
                """

                cur.execute(sql, [latest_run])
                rows = cur.fetchall()

                if not rows:
                    raise RuntimeError("No rows found for latest watchlist run")

                inserted = 0

                for idx, row in enumerate(rows, start=1):
                    ticker = row["ticker"]
                    composite = row["composite"]
                    signals = normalize_signals(row.get("signals"))
                    price_at_signal = row.get("price_at_signal")

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
                        ON CONFLICT (run_id, ticker)
                        DO UPDATE SET
                            rank = EXCLUDED.rank,
                            composite = EXCLUDED.composite,
                            signals = EXCLUDED.signals,
                            price_at_signal = EXCLUDED.price_at_signal
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
