import os
import json
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")


app = FastAPI(title="Quiet Money Engine API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def clean_value(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {k: clean_value(v) for k, v in value.items()}

    if isinstance(value, list):
        return [clean_value(v) for v in value]

    return value


def clean_rows(rows):
    cleaned = []

    for row in rows:
        item = {}
        for key, value in dict(row).items():
            item[key] = clean_value(value)
        cleaned.append(item)

    return cleaned


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "name": "Quiet Money Engine API",
        "status": "online",
        "endpoints": [
            "/health",
            "/api/insider-buys",
            "/api/clusters",
            "/api/watchlist/latest",
            "/api/watchlist/runs",
        ],
    }


@app.get("/api/insider-buys")
def insider_buys(
    limit: int = Query(default=100, ge=1, le=500),
    days: int = Query(default=30, ge=1, le=365),
    ticker: str | None = None,
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sql = """
        SELECT *
        FROM insider_buys
        WHERE filed_at >= %s
    """

    params = [cutoff]

    if ticker:
        sql += " AND UPPER(ticker) = UPPER(%s)"
        params.append(ticker)

    sql += " ORDER BY filed_at DESC LIMIT %s"
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }


@app.get("/api/clusters")
def clusters(
    days: int = Query(default=14, ge=1, le=365),
    min_insiders: int = Query(default=2, ge=2, le=50),
    limit: int = Query(default=50, ge=1, le=200),
):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sql = """
        SELECT
            ticker,
            COUNT(*) AS buy_count,
            COUNT(DISTINCT insider_name) AS insider_count,
            SUM(COALESCE(total_value, 0)) AS total_value,
            SUM(COALESCE(total_shares, 0)) AS total_shares,
            MIN(filed_at) AS first_seen,
            MAX(filed_at) AS last_seen
        FROM insider_buys
        WHERE filed_at >= %s
        GROUP BY ticker
        HAVING COUNT(DISTINCT insider_name) >= %s
        ORDER BY insider_count DESC, total_value DESC, last_seen DESC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [cutoff, min_insiders, limit])
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }


@app.get("/api/watchlist/latest")
def watchlist_latest(
    limit: int = Query(default=50, ge=1, le=500),
):
    sql = """
        SELECT *
        FROM watchlist_scores
        WHERE run_date = (
            SELECT MAX(run_date)
            FROM watchlist_scores
        )
        ORDER BY composite DESC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [limit])
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }


@app.get("/api/watchlist/runs")
def watchlist_runs(
    limit: int = Query(default=20, ge=1, le=100),
):
    sql = """
        SELECT
            run_date,
            COUNT(*) AS ticker_count,
            MAX(created_at) AS saved_at
        FROM watchlist_scores
        GROUP BY run_date
        ORDER BY run_date DESC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [limit])
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }
    @app.get("/api/debug/schema")
    def debug_schema():
    sql = """
        SELECT
            table_name,
            column_name,
            data_type
        FROM information_schema.columns
        WHERE table_name IN ('insider_buys', 'watchlist_scores')
        ORDER BY table_name, ordinal_position
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }
