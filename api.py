import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

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


# -----------------------------
# Helpers
# -----------------------------

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def qcol(column_name: str) -> str:
    """
    Safely quote a column name that came from information_schema.
    """
    return '"' + column_name.replace('"', '""') + '"'


def clean_value(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

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


def get_table_columns(table_name: str) -> List[str]:
    sql = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [table_name])
            rows = cur.fetchall()

    return [row["column_name"] for row in rows]


def first_existing(columns: List[str], candidates: List[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return None


def require_table(table_name: str) -> List[str]:
    columns = get_table_columns(table_name)

    if not columns:
        raise HTTPException(
            status_code=500,
            detail=f"Table not found or has no visible columns: {table_name}",
        )

    return columns


# -----------------------------
# Basic routes
# -----------------------------

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
            "/api/debug/schema",
        ],
    }


# -----------------------------
# Debug schema route
# -----------------------------

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


# -----------------------------
# Insider buys
# -----------------------------

@app.get("/api/insider-buys")
def insider_buys(
    limit: int = Query(default=100, ge=1, le=500),
    days: int = Query(default=30, ge=1, le=365),
    ticker: Optional[str] = None,
):
    columns = require_table("insider_buys")

    ticker_col = first_existing(columns, ["ticker", "symbol"])
    date_col = first_existing(
        columns,
        [
            "filed_at",
            "filing_date",
            "transaction_date",
            "created_at",
            "saved_at",
            "inserted_at",
            "updated_at",
        ],
    )

    where_parts = []
    params = []

    if date_col:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        where_parts.append(f"{qcol(date_col)} >= %s")
        params.append(cutoff)

    if ticker and ticker_col:
        where_parts.append(f"UPPER({qcol(ticker_col)}) = UPPER(%s)")
        params.append(ticker)

    where_sql = ""

    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    order_col = date_col or first_existing(columns, ["id", "accession"]) or columns[0]

    sql = f"""
        SELECT *
        FROM insider_buys
        {where_sql}
        ORDER BY {qcol(order_col)} DESC
        LIMIT %s
    """

    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return {
        "count": len(rows),
        "items": clean_rows(rows),
    }


# -----------------------------
# Insider clusters
# -----------------------------

@app.get("/api/clusters")
def clusters(
    days: int = Query(default=14, ge=1, le=365),
    min_insiders: int = Query(default=2, ge=2, le=50),
    limit: int = Query(default=50, ge=1, le=200),
):
    columns = require_table("insider_buys")

    ticker_col = first_existing(columns, ["ticker", "symbol"])
    date_col = first_existing(
        columns,
        [
            "filed_at",
            "filing_date",
            "transaction_date",
            "created_at",
            "saved_at",
            "inserted_at",
            "updated_at",
        ],
    )
    insider_col = first_existing(
        columns,
        [
            "insider_name",
            "insider",
            "filer",
            "reporting_owner",
            "owner_name",
            "name",
        ],
    )
    value_col = first_existing(
        columns,
        [
            "total_value",
            "value",
            "dollar_value",
            "transaction_value",
            "amount",
        ],
    )
    shares_col = first_existing(
        columns,
        [
            "total_shares",
            "shares",
            "share_count",
            "transaction_shares",
        ],
    )

    if not ticker_col:
        raise HTTPException(
            status_code=500,
            detail="No ticker/symbol column found in insider_buys",
        )

    if not date_col:
        raise HTTPException(
            status_code=500,
            detail="No usable date column found in insider_buys",
        )

    insider_expr = qcol(insider_col) if insider_col else "'unknown_insider'"
    value_expr = qcol(value_col) if value_col else "0"
    shares_expr = qcol(shares_col) if shares_col else "0"

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sql = f"""
        SELECT
            {qcol(ticker_col)} AS ticker,
            COUNT(*) AS buy_count,
            COUNT(DISTINCT {insider_expr}) AS insider_count,
            SUM(COALESCE({value_expr}, 0)) AS total_value,
            SUM(COALESCE({shares_expr}, 0)) AS total_shares,
            MIN({qcol(date_col)}) AS first_seen,
            MAX({qcol(date_col)}) AS last_seen
        FROM insider_buys
        WHERE {qcol(date_col)} >= %s
        GROUP BY {qcol(ticker_col)}
        HAVING COUNT(DISTINCT {insider_expr}) >= %s
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


# -----------------------------
# Latest watchlist
# -----------------------------

@app.get("/api/watchlist/latest")
def watchlist_latest(
    limit: int = Query(default=50, ge=1, le=500),
):
    columns = require_table("watchlist_scores")

    run_date_col = first_existing(
        columns,
        [
            "run_date",
            "date",
            "scored_at",
            "created_at",
            "saved_at",
            "inserted_at",
        ],
    )
    composite_col = first_existing(
        columns,
        [
            "composite",
            "score",
            "composite_score",
            "rank_score",
        ],
    )

    if not run_date_col:
        raise HTTPException(
            status_code=500,
            detail="No usable run/date column found in watchlist_scores",
        )

    order_col = composite_col or first_existing(columns, ["rank", "ticker"]) or columns[0]
    order_dir = "DESC" if composite_col else "ASC"

    sql = f"""
        SELECT *
        FROM watchlist_scores
        WHERE {qcol(run_date_col)} = (
            SELECT MAX({qcol(run_date_col)})
            FROM watchlist_scores
        )
        ORDER BY {qcol(order_col)} {order_dir}
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


# -----------------------------
# Watchlist run history
# -----------------------------

@app.get("/api/watchlist/runs")
def watchlist_runs(
    limit: int = Query(default=20, ge=1, le=100),
):
    columns = require_table("watchlist_scores")

    run_date_col = first_existing(
        columns,
        [
            "run_date",
            "date",
            "scored_at",
            "created_at",
            "saved_at",
            "inserted_at",
        ],
    )

    saved_col = first_existing(
        columns,
        [
            "created_at",
            "saved_at",
            "inserted_at",
            "updated_at",
            "scored_at",
        ],
    )

    if not run_date_col:
        raise HTTPException(
            status_code=500,
            detail="No usable run/date column found in watchlist_scores",
        )

    saved_expr = f"MAX({qcol(saved_col)}) AS saved_at" if saved_col else "NULL AS saved_at"

    sql = f"""
        SELECT
            {qcol(run_date_col)} AS run_date,
            COUNT(*) AS ticker_count,
            {saved_expr}
        FROM watchlist_scores
        GROUP BY {qcol(run_date_col)}
        ORDER BY {qcol(run_date_col)} DESC
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
