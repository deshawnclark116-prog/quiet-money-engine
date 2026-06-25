import os
import logging
import psycopg2


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")


SQL = """
CREATE TABLE IF NOT EXISTS prediction_runs (
    id SERIAL PRIMARY KEY,
    run_date DATE NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'quiet-money-v0.1',
    universe TEXT,
    signal_names JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_date, model_version)
);

CREATE TABLE IF NOT EXISTS prediction_snapshots (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES prediction_runs(id) ON DELETE CASCADE,
    run_date DATE NOT NULL,
    ticker TEXT NOT NULL,
    rank INTEGER,
    composite NUMERIC,
    signals JSONB,
    price_at_signal NUMERIC,
    source TEXT NOT NULL DEFAULT 'daily_score_universe',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_run_date
ON prediction_snapshots (run_date DESC);

CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_ticker
ON prediction_snapshots (ticker);

CREATE INDEX IF NOT EXISTS idx_prediction_snapshots_rank
ON prediction_snapshots (rank);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES prediction_snapshots(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    run_date DATE NOT NULL,
    horizon_days INTEGER NOT NULL,
    outcome_date DATE,
    start_price NUMERIC,
    end_price NUMERIC,
    raw_return NUMERIC,
    spy_return NUMERIC,
    excess_return_vs_spy NUMERIC,
    max_drawdown NUMERIC,
    hit_5pct BOOLEAN,
    hit_10pct BOOLEAN,
    graded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (snapshot_id, horizon_days)
);

CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_run_date
ON prediction_outcomes (run_date DESC);

CREATE INDEX IF NOT EXISTS idx_prediction_outcomes_horizon
ON prediction_outcomes (horizon_days);
"""


def main():
    logging.info("Connecting to Postgres...")
    conn = psycopg2.connect(DATABASE_URL)

    try:
        with conn:
            with conn.cursor() as cur:
                logging.info("Creating prediction logging tables...")
                cur.execute(SQL)

        logging.info("Prediction tables ready.")
        logging.info("Created/verified: prediction_runs")
        logging.info("Created/verified: prediction_snapshots")
        logging.info("Created/verified: prediction_outcomes")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
