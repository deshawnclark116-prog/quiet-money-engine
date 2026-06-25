import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional

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


def clean_one(row):
    if not row:
        return None

    item = {}
    for key, value in dict(row).items():
        item[key] = clean_value(value)
    return item


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


def safe_count_table(table_name: str) -> int:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table_name}")
                row = cur.fetchone()
                return int(row["n"])
    except Exception:
        return 0


def is_weekend_utc() -> bool:
    return datetime.now(timezone.utc).weekday() >= 5


def days_old(value) -> Optional[float]:
    if not value:
        return None

    now = datetime.now(timezone.utc)

    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return round((now - value).total_seconds() / 86400, 2)

    return None


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
            "/api/status",
            "/api/insider-buys",
            "/api/clusters",
            "/api/watchlist/latest",
            "/api/watchlist/runs",
            "/api/debug/schema",
        ],
    }


# -----------------------------
# Engine status
# -----------------------------

@app.get("/api/status")
def api_status():
    status = {
        "database_connected": False,
        "engine_status": "unknown",
        "warnings": [],
        "tables": {},
        "watchlist": {},
        "insider_buys": {},
        "clusters": {},
    }

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT NOW() AS db_time")
                row = cur.fetchone()
                status["database_connected"] = True
                status["db_time"] = clean_value(row["db_time"])
    except Exception as e:
        status["engine_status"] = "down"
        status["warnings"].append(f"Database connection failed: {str(e)}")
        return status

    insider_columns = get_table_columns("insider_buys")
    watchlist_columns = get_table_columns("watchlist_scores")

    status["tables"] = {
        "insider_buys_exists": bool(insider_columns),
        "watchlist_scores_exists": bool(watchlist_columns),
        "insider_buys_rows": safe_count_table("insider_buys") if insider_columns else 0,
        "watchlist_scores_rows": safe_count_table("watchlist_scores") if watchlist_columns else 0,
    }

    # Watchlist status
    if watchlist_columns:
        run_date_col = first_existing(
            watchlist_columns,
            [
                "run_date",
                "date",
                "scored_at",
                "created_at",
                "saved_at",
                "inserted_at",
            ],
        )

        if run_date_col:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT MAX({qcol(run_date_col)}) AS latest_run
                        FROM watchlist_scores
                        """
                    )
                    latest_row = cur.fetchone()

            latest_run = latest_row["latest_run"]

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS latest_count
                        FROM watchlist_scores
                        WHERE {qcol(run_date_col)} = (
                            SELECT MAX({qcol(run_date_col)})
                            FROM watchlist_scores
                        )
                        """
                    )
                    count_row = cur.fetchone()

            watchlist_days_old = days_old(latest_run)

            status["watchlist"] = {
                "latest_run": clean_value(latest_run),
                "latest_run_days_old": watchlist_days_old,
                "latest_run_row_count": int(count_row["latest_count"]),
            }

            if latest_run is None:
                status["warnings"].append("No watchlist run found.")
            elif watchlist_days_old is not None and watchlist_days_old > 3:
                status["warnings"].append("Watchlist appears stale: latest run is older than 3 days.")
            elif (
                watchlist_days_old is not None
                and watchlist_days_old > 1.5
                and not is_weekend_utc()
            ):
                status["warnings"].append("Watchlist may be stale for a weekday.")
        else:
            status["watchlist"] = {
                "latest_run": None,
                "latest_run_row_count": 0,
                "error": "No usable run/date column found.",
            }
            status["warnings"].append("watchlist_scores has no usable date column.")
    else:
        status["warnings"].append("watchlist_scores table missing.")

    # Insider-buy status
    if insider_columns:
        ticker_col = first_existing(insider_columns, ["ticker", "symbol"])
        date_col = first_existing(
            insider_columns,
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
            insider_columns,
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
            insider_columns,
            [
                "total_value",
                "value",
                "dollar_value",
                "transaction_value",
                "amount",
            ],
        )

        if date_col:
            now = datetime.now(timezone.utc)
            cutoff_24h = now - timedelta(hours=24)
            cutoff_7d = now - timedelta(days=7)
            cutoff_30d = now - timedelta(days=30)
            cutoff_14d = now - timedelta(days=14)

            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"""
                        SELECT MAX({qcol(date_col)}) AS latest_buy
                        FROM insider_buys
                        """
                    )
                    latest_buy_row = cur.fetchone()

                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS n
                        FROM insider_buys
                        WHERE {qcol(date_col)} >= %s
                        """,
                        [cutoff_24h],
                    )
                    count_24h = cur.fetchone()["n"]

                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS n
                        FROM insider_buys
                        WHERE {qcol(date_col)} >= %s
                        """,
                        [cutoff_7d],
                    )
                    count_7d = cur.fetchone()["n"]

                    cur.execute(
                        f"""
                        SELECT COUNT(*) AS n
                        FROM insider_buys
                        WHERE {qcol(date_col)} >= %s
                        """,
                        [cutoff_30d],
                    )
                    count_30d = cur.fetchone()["n"]

            latest_buy = latest_buy_row["latest_buy"]
            insider_days_old = days_old(latest_buy)

            status["insider_buys"] = {
                "latest_buy": clean_value(latest_buy),
                "latest_buy_days_old": insider_days_old,
                "count_24h": int(count_24h),
                "count_7d": int(count_7d),
                "count_30d": int(count_30d),
                "detected_columns": {
                    "ticker": ticker_col,
                    "date": date_col,
                    "insider": insider_col,
                    "value": value_col,
                },
            }

            if ticker_col and date_col:
                insider_expr = qcol(insider_col) if insider_col else "'unknown_insider'"
                value_expr = qcol(value_col) if value_col else "0"

                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            SELECT COUNT(*) AS cluster_count
                            FROM (
                                SELECT
                                    {qcol(ticker_col)} AS ticker,
                                    COUNT(DISTINCT {insider_expr}) AS insider_count,
                                    SUM(COALESCE({value_expr}, 0)) AS total_value
                                FROM insider_buys
                                WHERE {qcol(date_col)} >= %s
                                GROUP BY {qcol(ticker_col)}
                                HAVING COUNT(DISTINCT {insider_expr}) >= 2
                            ) x
                            """,
                            [cutoff_14d],
                        )
                        cluster_row = cur.fetchone()

                status["clusters"] = {
                    "cluster_count_14d": int(cluster_row["cluster_count"]),
                    "min_insiders": 2,
                }
            else:
                status["clusters"] = {
                    "cluster_count_14d": None,
                    "error": "No ticker/date columns available for cluster count.",
                }

        else:
            status["insider_buys"] = {
                "latest_buy": None,
                "error": "No usable date column found.",
            }
            status["warnings"].append("insider_buys has no usable date column.")
    else:
        status["warnings"].append("insider_buys table missing.")

    if status["database_connected"] and not status["warnings"]:
        status["engine_status"] = "healthy"
    elif status["database_connected"]:
        status["engine_status"] = "warning"
    else:
        status["engine_status"] = "down"

    return status


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
