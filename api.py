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


def date_sql_expr(column_name: str) -> str:
    """
    filed_at is currently stored as text in the DB.
    Cast it inside API queries so date filters work.
    """
    return f"NULLIF({qcol(column_name)}::text, '')::timestamptz"


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


def safe_count_table(table_name: str) -> int:
    allowed = {
        "insider_buys",
        "watchlist_scores",
        "prediction_runs",
        "prediction_snapshots",
        "prediction_outcomes",
    }

    if table_name not in allowed:
        return 0

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table_name}")
                row = cur.fetchone()
                return int(row["n"])
    except Exception:
        return 0


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


def numeric_sum_expr(column_name: Optional[str]) -> str:
    if not column_name:
        return "0"

    return (
        "SUM(COALESCE("
        f"NULLIF(REGEXP_REPLACE({qcol(column_name)}::text, '[^0-9.-]', '', 'g'), '')::numeric"
        ", 0))"
    )


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
            "/api/predictions/latest",
            "/api/predictions/runs",
            "/api/predictions/by-ticker/{ticker}",
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
        "predictions": {},
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

    try:
        insider_columns = get_table_columns("insider_buys")
    except Exception as e:
        insider_columns = []
        status["warnings"].append(f"Could not inspect insider_buys: {str(e)}")

    try:
        watchlist_columns = get_table_columns("watchlist_scores")
    except Exception as e:
        watchlist_columns = []
        status["warnings"].append(f"Could not inspect watchlist_scores: {str(e)}")

    try:
        prediction_run_columns = get_table_columns("prediction_runs")
        prediction_snapshot_columns = get_table_columns("prediction_snapshots")
        prediction_outcome_columns = get_table_columns("prediction_outcomes")
    except Exception as e:
        prediction_run_columns = []
        prediction_snapshot_columns = []
        prediction_outcome_columns = []
        status["warnings"].append(f"Could not inspect prediction tables: {str(e)}")

    status["tables"] = {
        "insider_buys_exists": bool(insider_columns),
        "watchlist_scores_exists": bool(watchlist_columns),
        "prediction_runs_exists": bool(prediction_run_columns),
        "prediction_snapshots_exists": bool(prediction_snapshot_columns),
        "prediction_outcomes_exists": bool(prediction_outcome_columns),
        "insider_buys_rows": safe_count_table("insider_buys") if insider_columns else 0,
        "watchlist_scores_rows": safe_count_table("watchlist_scores") if watchlist_columns else 0,
        "prediction_runs_rows": safe_count_table("prediction_runs") if prediction_run_columns else 0,
        "prediction_snapshots_rows": safe_count_table("prediction_snapshots") if prediction_snapshot_columns else 0,
        "prediction_outcomes_rows": safe_count_table("prediction_outcomes") if prediction_outcome_columns else 0,
    }

    # Watchlist status
    try:
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

                latest_run = latest_row["latest_run"]

                status["watchlist"] = {
                    "latest_run": clean_value(latest_run),
                    "latest_run_days_old": days_old(latest_run),
                    "latest_run_row_count": int(count_row["latest_count"]),
                    "date_column": run_date_col,
                }

                if latest_run is None:
                    status["warnings"].append("No watchlist run found.")
            else:
                status["watchlist"] = {
                    "latest_run": None,
                    "latest_run_row_count": 0,
                    "error": "No usable run/date column found.",
                }
                status["warnings"].append("watchlist_scores has no usable date column.")
        else:
            status["warnings"].append("watchlist_scores table missing.")
    except Exception as e:
        status["watchlist"] = {"error": str(e)}
        status["warnings"].append("Watchlist status check failed.")

    # Insider-buy status
    try:
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

            status["insider_buys"]["detected_columns"] = {
                "ticker": ticker_col,
                "date": date_col,
                "insider": insider_col,
            }

            if date_col:
                date_expr = date_sql_expr(date_col)
                now = datetime.now(timezone.utc)
                cutoff_24h = now - timedelta(hours=24)
                cutoff_7d = now - timedelta(days=7)
                cutoff_30d = now - timedelta(days=30)

                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            SELECT MAX({date_expr}) AS latest_buy
                            FROM insider_buys
                            WHERE {qcol(date_col)} IS NOT NULL
                              AND {qcol(date_col)}::text <> ''
                            """
                        )
                        latest_buy_row = cur.fetchone()

                        cur.execute(
                            f"""
                            SELECT COUNT(*) AS n
                            FROM insider_buys
                            WHERE {date_expr} >= %s
                            """,
                            [cutoff_24h],
                        )
                        count_24h = cur.fetchone()["n"]

                        cur.execute(
                            f"""
                            SELECT COUNT(*) AS n
                            FROM insider_buys
                            WHERE {date_expr} >= %s
                            """,
                            [cutoff_7d],
                        )
                        count_7d = cur.fetchone()["n"]

                        cur.execute(
                            f"""
                            SELECT COUNT(*) AS n
                            FROM insider_buys
                            WHERE {date_expr} >= %s
                            """,
                            [cutoff_30d],
                        )
                        count_30d = cur.fetchone()["n"]

                latest_buy = latest_buy_row["latest_buy"]

                status["insider_buys"].update(
                    {
                        "latest_buy": clean_value(latest_buy),
                        "latest_buy_days_old": days_old(latest_buy),
                        "count_24h": int(count_24h),
                        "count_7d": int(count_7d),
                        "count_30d": int(count_30d),
                    }
                )
            else:
                status["insider_buys"].update(
                    {
                        "latest_buy": None,
                        "error": "No usable date column found.",
                    }
                )
                status["warnings"].append("insider_buys has no usable date column.")

            if ticker_col and date_col:
                date_expr = date_sql_expr(date_col)
                cutoff_14d = datetime.now(timezone.utc) - timedelta(days=14)
                insider_expr = qcol(insider_col) if insider_col else qcol(ticker_col)

                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"""
                            SELECT COUNT(*) AS cluster_count
                            FROM (
                                SELECT
                                    {qcol(ticker_col)} AS ticker,
                                    COUNT(DISTINCT {insider_expr}) AS insider_count
                                FROM insider_buys
                                WHERE {date_expr} >= %s
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
            status["warnings"].append("insider_buys table missing.")
    except Exception as e:
        status["insider_buys"]["error"] = str(e)
        status["clusters"]["error"] = str(e)
        status["warnings"].append("Insider-buy status check failed.")

    # Prediction status
    try:
        if prediction_run_columns and prediction_snapshot_columns:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, run_date, model_version, universe, created_at
                        FROM prediction_runs
                        ORDER BY run_date DESC, id DESC
                        LIMIT 1
                        """
                    )
                    latest_prediction_run = cur.fetchone()

                    cur.execute(
                        """
                        SELECT COUNT(*) AS n
                        FROM prediction_snapshots
                        WHERE run_id = (
                            SELECT id
                            FROM prediction_runs
                            ORDER BY run_date DESC, id DESC
                            LIMIT 1
                        )
                        """
                    )
                    latest_snapshot_count = cur.fetchone()["n"]

            status["predictions"] = {
                "latest_run": clean_value(latest_prediction_run) if latest_prediction_run else None,
                "latest_snapshot_count": int(latest_snapshot_count),
                "outcome_count": safe_count_table("prediction_outcomes") if prediction_outcome_columns else 0,
            }

            if latest_prediction_run is None:
                status["warnings"].append("No prediction run found.")
        else:
            status["predictions"] = {
                "latest_run": None,
                "latest_snapshot_count": 0,
                "error": "Prediction tables missing.",
            }
            status["warnings"].append("Prediction tables missing.")
    except Exception as e:
        status["predictions"] = {"error": str(e)}
        status["warnings"].append("Prediction status check failed.")

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
        WHERE table_name IN (
            'insider_buys',
            'watchlist_scores',
            'prediction_runs',
            'prediction_snapshots',
            'prediction_outcomes'
        )
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
        where_parts.append(f"{date_sql_expr(date_col)} >= %s")
        params.append(cutoff)

    if ticker and ticker_col:
        where_parts.append(f"UPPER({qcol(ticker_col)}) = UPPER(%s)")
        params.append(ticker)

    where_sql = ""

    if where_parts:
        where_sql = "WHERE " + " AND ".join(where_parts)

    if date_col:
        order_expr = date_sql_expr(date_col)
    else:
        order_expr = qcol(first_existing(columns, ["id", "accession"]) or columns[0])

    sql = f"""
        SELECT *
        FROM insider_buys
        {where_sql}
        ORDER BY {order_expr} DESC
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

    insider_expr = qcol(insider_col) if insider_col else qcol(ticker_col)
    value_expr = numeric_sum_expr(value_col)
    shares_expr = numeric_sum_expr(shares_col)
    date_expr = date_sql_expr(date_col)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    sql = f"""
        SELECT
            {qcol(ticker_col)} AS ticker,
            COUNT(*) AS buy_count,
            COUNT(DISTINCT {insider_expr}) AS insider_count,
            {value_expr} AS total_value,
            {shares_expr} AS total_shares,
            MIN({date_expr}) AS first_seen,
            MAX({date_expr}) AS last_seen
        FROM insider_buys
        WHERE {date_expr} >= %s
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


# -----------------------------
# Prediction endpoints
# -----------------------------

@app.get("/api/predictions/latest")
def predictions_latest(
    limit: int = Query(default=50, ge=1, le=500),
):
    require_table("prediction_runs")
    require_table("prediction_snapshots")

    sql = """
        SELECT
            ps.id AS snapshot_id,
            ps.run_id,
            ps.run_date,
            ps.ticker,
            ps.rank,
            ps.composite,
            ps.signals,
            ps.price_at_signal,
            ps.source,
            ps.created_at AS snapshot_created_at,
            pr.model_version,
            pr.universe,
            pr.signal_names,
            pr.notes,
            pr.created_at AS run_created_at
        FROM prediction_snapshots ps
        JOIN prediction_runs pr
            ON pr.id = ps.run_id
        WHERE ps.run_id = (
            SELECT id
            FROM prediction_runs
            ORDER BY run_date DESC, id DESC
            LIMIT 1
        )
        ORDER BY ps.rank ASC NULLS LAST, ps.composite DESC NULLS LAST
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


@app.get("/api/predictions/runs")
def predictions_runs(
    limit: int = Query(default=20, ge=1, le=100),
):
    require_table("prediction_runs")
    require_table("prediction_snapshots")

    sql = """
        SELECT
            pr.id,
            pr.run_date,
            pr.model_version,
            pr.universe,
            pr.signal_names,
            pr.notes,
            pr.created_at,
            COUNT(DISTINCT ps.id) AS snapshot_count,
            COUNT(DISTINCT po.id) AS outcome_count
        FROM prediction_runs pr
        LEFT JOIN prediction_snapshots ps
            ON ps.run_id = pr.id
        LEFT JOIN prediction_outcomes po
            ON po.snapshot_id = ps.id
        GROUP BY
            pr.id,
            pr.run_date,
            pr.model_version,
            pr.universe,
            pr.signal_names,
            pr.notes,
            pr.created_at
        ORDER BY pr.run_date DESC, pr.id DESC
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


@app.get("/api/predictions/by-ticker/{ticker}")
def predictions_by_ticker(
    ticker: str,
    limit: int = Query(default=20, ge=1, le=100),
):
    require_table("prediction_runs")
    require_table("prediction_snapshots")

    sql = """
        SELECT
            ps.id AS snapshot_id,
            ps.run_id,
            ps.run_date,
            ps.ticker,
            ps.rank,
            ps.composite,
            ps.signals,
            ps.price_at_signal,
            ps.source,
            ps.created_at AS snapshot_created_at,
            pr.model_version,
            pr.universe,
            pr.signal_names,
            COALESCE(
                JSON_AGG(
                    JSON_BUILD_OBJECT(
                        'horizon_days', po.horizon_days,
                        'outcome_date', po.outcome_date,
                        'start_price', po.start_price,
                        'end_price', po.end_price,
                        'raw_return', po.raw_return,
                        'spy_return', po.spy_return,
                        'excess_return_vs_spy', po.excess_return_vs_spy,
                        'max_drawdown', po.max_drawdown,
                        'hit_5pct', po.hit_5pct,
                        'hit_10pct', po.hit_10pct,
                        'graded_at', po.graded_at
                    )
                    ORDER BY po.horizon_days
                ) FILTER (WHERE po.id IS NOT NULL),
                '[]'::json
            ) AS outcomes
        FROM prediction_snapshots ps
        JOIN prediction_runs pr
            ON pr.id = ps.run_id
        LEFT JOIN prediction_outcomes po
            ON po.snapshot_id = ps.id
        WHERE UPPER(ps.ticker) = UPPER(%s)
        GROUP BY
            ps.id,
            ps.run_id,
            ps.run_date,
            ps.ticker,
            ps.rank,
            ps.composite,
            ps.signals,
            ps.price_at_signal,
            ps.source,
            ps.created_at,
            pr.model_version,
            pr.universe,
            pr.signal_names
        ORDER BY ps.run_date DESC, ps.id DESC
        LIMIT %s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [ticker, limit])
            rows = cur.fetchall()

    return {
        "ticker": ticker.upper(),
        "count": len(rows),
        "items": clean_rows(rows),
        }
