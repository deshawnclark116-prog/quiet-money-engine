#!/usr/bin/env python3
"""
Quiet Money Engine — database layer.
"""
import os
import json
import logging

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

log = logging.getLogger("db")

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


SCHEMA = """
CREATE TABLE IF NOT EXISTS insider_buys (
    id            BIGSERIAL PRIMARY KEY,
    accession     TEXT UNIQUE,
    ticker        TEXT NOT NULL,
    exchange      TEXT,
    insider       TEXT,
    role          TEXT,
    shares        DOUBLE PRECISION,
    price         DOUBLE PRECISION,
    value         DOUBLE PRECISION,
    market_cap    DOUBLE PRECISION,
    avg_dollar_vol DOUBLE PRECISION,
    filed_at      TEXT,
    seen_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_buys_ticker ON insider_buys(ticker);
CREATE INDEX IF NOT EXISTS idx_buys_seen   ON insider_buys(seen_at DESC);

CREATE TABLE IF NOT EXISTS watchlist_scores (
    id          BIGSERIAL PRIMARY KEY,
    run_date    DATE NOT NULL,
    ticker      TEXT NOT NULL,
    rank        INTEGER,
    composite   DOUBLE PRECISION,
    signals     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_scores_date ON watchlist_scores(run_date DESC);
"""


def init_db() -> None:
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set — persistence disabled")
        return

    with _conn() as c, c.cursor() as cur:
        cur.execute(SCHEMA)
        c.commit()

    log.info("DB ready")


def save_insider_buy(b: dict) -> None:
    if not DATABASE_URL:
        return

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO insider_buys
            (accession, ticker, exchange, insider, role, shares, price, value,
             market_cap, avg_dollar_vol, filed_at)
            VALUES (%(accession)s, %(ticker)s, %(exchange)s, %(insider)s, %(role)s,
                    %(shares)s, %(price)s, %(value)s, %(market_cap)s,
                    %(avg_dollar_vol)s, %(filed_at)s)
            ON CONFLICT (accession) DO NOTHING
            """,
            b,
        )
        c.commit()


def save_watchlist(run_date, rows: list) -> None:
    """
    Save one exact daily ranking.

    Important:
    We delete that run_date first so old tickers do not linger when the universe
    changes size or when a data provider fails.
    """
    if not DATABASE_URL or not rows:
        return

    values = [
        (
            run_date,
            r["ticker"],
            r["rank"],
            r["composite"],
            json.dumps(r["signals"]),
        )
        for r in rows
    ]

    with _conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM watchlist_scores WHERE run_date = %s", [run_date])

        execute_values(
            cur,
            """
            INSERT INTO watchlist_scores
            (run_date, ticker, rank, composite, signals)
            VALUES %s
            """,
            values,
        )

        c.commit()
