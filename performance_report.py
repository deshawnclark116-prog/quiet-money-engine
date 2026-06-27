#!/usr/bin/env python3
"""
Quiet Money Engine — performance report.

Reads saved prediction snapshots and graded outcomes from Postgres.

This is the honest forward-test report:
- It only evaluates predictions that were already saved in the DB.
- It does not simulate fake historical knowledge.
- It avoids look-ahead bias by using prediction_snapshots + prediction_outcomes.

Reports:
- Overall performance by horizon
- Rank bucket performance
- Best/worst outcomes
- Ticker summary
- Signal usefulness diagnostics
- Latest watchlist snapshot
"""

import os
import math
import json
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor


DATABASE_URL = os.getenv("DATABASE_URL")

MAX_ROWS = int(os.getenv("PERFORMANCE_REPORT_MAX_ROWS", "5000"))


RETURN_COL_CANDIDATES = [
    "return_pct",
    "actual_return_pct",
    "future_return_pct",
    "pct_return",
    "ret_pct",
    "return",
    "actual_return",
]

EXCESS_COL_CANDIDATES = [
    "excess_return_pct",
    "excess_vs_spy_pct",
    "excess_vs_benchmark_pct",
    "benchmark_excess_pct",
    "excess_return",
    "excess",
]

HORIZON_COL_CANDIDATES = [
    "horizon_days",
    "horizon",
    "prediction_horizon",
    "days",
]

TICKER_COL_CANDIDATES = [
    "ticker",
    "symbol",
]

RANK_COL_CANDIDATES = [
    "rank",
    "snapshot_rank",
    "watchlist_rank",
]

COMPOSITE_COL_CANDIDATES = [
    "composite",
    "snapshot_composite",
    "score",
]

SIGNALS_COL_CANDIDATES = [
    "signals",
    "snapshot_signals",
]


def connect():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is missing")

    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def table_columns(conn, table_name: str) -> List[str]:
    with conn.cursor() as cur:
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


def table_exists(conn, table_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = %s
            ) AS exists
            """,
            [table_name],
        )
        return bool(cur.fetchone()["exists"])


def first_existing(row: Dict[str, Any], candidates: List[str], default=None):
    for col in candidates:
        if col in row and row[col] is not None:
            return row[col]
    return default


def first_existing_col(columns: List[str], candidates: List[str]) -> Optional[str]:
    for col in candidates:
        if col in columns:
            return col
    return None


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def parse_signals(value: Any) -> Dict[str, float]:
    if value is None:
        return {}

    if isinstance(value, dict):
        raw = value
    elif isinstance(value, str):
        try:
            raw = json.loads(value)
        except Exception:
            return {}
    else:
        return {}

    out = {}

    for k, v in raw.items():
        f = safe_float(v, None)
        if f is not None:
            out[str(k)] = f

    return out


def normalize_pct_values(values: List[float]) -> Tuple[List[float], str]:
    """
    DB might store returns either as:
    - decimal returns: 0.0196
    - percentage points: 1.96

    This function normalizes to display percentage points.
    """
    clean = [v for v in values if v is not None and math.isfinite(v)]

    if not clean:
        return values, "unknown"

    abs_vals = sorted(abs(v) for v in clean if v != 0)

    if not abs_vals:
        return values, "unknown"

    median_abs = statistics.median(abs_vals)
    max_abs = max(abs_vals)

    # Most normal daily/swing returns stored as decimals will be below 1.0.
    # Percentage-point values will usually have median abs above 1.0.
    if median_abs <= 1.0 and max_abs <= 3.0:
        return [v * 100.0 for v in values], "decimal_to_percent"

    return values, "already_percent"


def pct_fmt(value: Optional[float]) -> str:
    if value is None:
        return "n/a"

    return f"{value:+.2f}%"


def num_fmt(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "n/a"

    return f"{value:.{digits}f}"


def median(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and math.isfinite(v)]

    if not clean:
        return None

    return statistics.median(clean)


def mean(values: List[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and math.isfinite(v)]

    if not clean:
        return None

    return sum(clean) / len(clean)


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    pairs = [
        (x, y)
        for x, y in zip(xs, ys)
        if x is not None and y is not None and math.isfinite(x) and math.isfinite(y)
    ]

    if len(pairs) < 5:
        return None

    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]

    mx = mean(x_vals)
    my = mean(y_vals)

    if mx is None or my is None:
        return None

    num = sum((x - mx) * (y - my) for x, y in pairs)
    den_x = math.sqrt(sum((x - mx) ** 2 for x in x_vals))
    den_y = math.sqrt(sum((y - my) ** 2 for y in y_vals))

    if den_x <= 0 or den_y <= 0:
        return None

    return num / (den_x * den_y)


def print_section(title: str):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def print_table(headers: List[str], rows: List[List[Any]], max_rows: Optional[int] = None):
    if max_rows is not None:
        rows = rows[:max_rows]

    str_rows = [[str(x) for x in row] for row in rows]
    str_headers = [str(h) for h in headers]

    widths = []

    for i, h in enumerate(str_headers):
        max_cell = len(h)

        for row in str_rows:
            if i < len(row):
                max_cell = max(max_cell, len(row[i]))

        widths.append(min(max_cell, 28))

    def trim(s, width):
        s = str(s)
        if len(s) <= width:
            return s
        return s[: width - 1] + "…"

    header_line = " | ".join(trim(h, widths[i]).ljust(widths[i]) for i, h in enumerate(str_headers))
    sep = "-+-".join("-" * w for w in widths)

    print(header_line)
    print(sep)

    for row in str_rows:
        print(" | ".join(trim(row[i] if i < len(row) else "", widths[i]).ljust(widths[i]) for i in range(len(widths))))


def build_join_query(conn) -> Tuple[str, Dict[str, str]]:
    outcome_cols = table_columns(conn, "prediction_outcomes")
    snapshot_cols = table_columns(conn, "prediction_snapshots") if table_exists(conn, "prediction_snapshots") else []

    if not outcome_cols:
        raise RuntimeError("prediction_outcomes table exists but has no visible columns")

    o_return_col = first_existing_col(outcome_cols, RETURN_COL_CANDIDATES)
    o_excess_col = first_existing_col(outcome_cols, EXCESS_COL_CANDIDATES)
    o_horizon_col = first_existing_col(outcome_cols, HORIZON_COL_CANDIDATES)
    o_ticker_col = first_existing_col(outcome_cols, TICKER_COL_CANDIDATES)

    if not o_return_col:
        raise RuntimeError(f"Could not identify return column in prediction_outcomes. Columns: {outcome_cols}")

    if not o_horizon_col:
        raise RuntimeError(f"Could not identify horizon column in prediction_outcomes. Columns: {outcome_cols}")

    if not o_ticker_col:
        raise RuntimeError(f"Could not identify ticker column in prediction_outcomes. Columns: {outcome_cols}")

    metadata = {
        "return_col": o_return_col,
        "excess_col": o_excess_col or "",
        "horizon_col": o_horizon_col,
        "ticker_col": o_ticker_col,
    }

    select_parts = ["o.*"]

    join_sql = ""

    if snapshot_cols:
        join_candidates = [
            ("snapshot_id", "id"),
            ("prediction_snapshot_id", "id"),
            ("prediction_id", "id"),
        ]

        joined = False

        for o_col, s_col in join_candidates:
            if o_col in outcome_cols and s_col in snapshot_cols:
                join_sql = f"LEFT JOIN prediction_snapshots s ON o.{o_col} = s.{s_col}"
                joined = True
                break

        if not joined and "ticker" in outcome_cols and "ticker" in snapshot_cols:
            if "run_date" in outcome_cols and "run_date" in snapshot_cols:
                join_sql = "LEFT JOIN prediction_snapshots s ON o.ticker = s.ticker AND o.run_date = s.run_date"
                joined = True
            elif "snapshot_date" in outcome_cols and "run_date" in snapshot_cols:
                join_sql = "LEFT JOIN prediction_snapshots s ON o.ticker = s.ticker AND o.snapshot_date = s.run_date"
                joined = True

        if joined:
            if "rank" in snapshot_cols:
                select_parts.append("s.rank AS snapshot_rank")
            if "composite" in snapshot_cols:
                select_parts.append("s.composite AS snapshot_composite")
            if "signals" in snapshot_cols:
                select_parts.append("s.signals AS snapshot_signals")
            if "run_date" in snapshot_cols:
                select_parts.append("s.run_date AS snapshot_run_date")
            if "price_at_signal" in snapshot_cols:
                select_parts.append("s.price_at_signal AS snapshot_price_at_signal")
            elif "price" in snapshot_cols:
                select_parts.append("s.price AS snapshot_price_at_signal")

    order_col = None

    for candidate in ["graded_at", "created_at", "id"]:
        if candidate in outcome_cols:
            order_col = candidate
            break

    order_sql = f"ORDER BY o.{order_col} DESC" if order_col else ""

    query = f"""
        SELECT {", ".join(select_parts)}
        FROM prediction_outcomes o
        {join_sql}
        {order_sql}
        LIMIT %s
    """

    return query, metadata


def load_rows(conn) -> List[Dict[str, Any]]:
    query, metadata = build_join_query(conn)

    with conn.cursor() as cur:
        cur.execute(query, [MAX_ROWS])
        raw_rows = [dict(row) for row in cur.fetchall()]

    if not raw_rows:
        return []

    returns_raw = []
    excess_raw = []

    for row in raw_rows:
        r = safe_float(row.get(metadata["return_col"]), None)
        if r is not None:
            returns_raw.append(r)

        if metadata["excess_col"]:
            e = safe_float(row.get(metadata["excess_col"]), None)
            if e is not None:
                excess_raw.append(e)

    returns_norm, return_mode = normalize_pct_values(returns_raw)
    excess_norm, excess_mode = normalize_pct_values(excess_raw)

    r_i = 0
    e_i = 0

    rows = []

    for row in raw_rows:
        ret_raw = safe_float(row.get(metadata["return_col"]), None)

        if ret_raw is None:
            continue

        ret_pct = returns_norm[r_i]
        r_i += 1

        excess_pct = None

        if metadata["excess_col"]:
            ex_raw = safe_float(row.get(metadata["excess_col"]), None)

            if ex_raw is not None:
                excess_pct = excess_norm[e_i]
                e_i += 1

        horizon = row.get(metadata["horizon_col"])
        ticker = str(row.get(metadata["ticker_col"]) or "").upper().strip()

        rank = safe_int(first_existing(row, RANK_COL_CANDIDATES), None)
        composite = safe_float(first_existing(row, COMPOSITE_COL_CANDIDATES), None)

        signals = parse_signals(first_existing(row, SIGNALS_COL_CANDIDATES))

        rows.append(
            {
                "ticker": ticker,
                "horizon": str(horizon),
                "horizon_num": safe_int(horizon, None),
                "return_pct": ret_pct,
                "excess_pct": excess_pct,
                "rank": rank,
                "composite": composite,
                "signals": signals,
                "raw": row,
            }
        )

    print_section("DATA LOAD")
    print(f"Loaded outcome rows: {len(rows)}")
    print(f"Return normalization: {return_mode}")
    if excess_raw:
        print(f"Excess-return normalization: {excess_mode}")
    else:
        print("Excess-return column: not found or empty")

    return rows


def summarize_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [r["return_pct"] for r in rows]
    excess = [r["excess_pct"] for r in rows if r.get("excess_pct") is not None]

    winners = [r for r in rows if r["return_pct"] > 0]
    hit5 = [r for r in rows if r["return_pct"] >= 5.0]
    hit10 = [r for r in rows if r["return_pct"] >= 10.0]

    ranks = [r["rank"] for r in rows if r.get("rank") is not None]
    comps = [r["composite"] for r in rows if r.get("composite") is not None]

    return {
        "n": len(rows),
        "avg_return": mean(returns),
        "median_return": median(returns),
        "avg_excess": mean(excess) if excess else None,
        "win_rate": len(winners) / len(rows) * 100 if rows else None,
        "hit5_rate": len(hit5) / len(rows) * 100 if rows else None,
        "hit10_rate": len(hit10) / len(rows) * 100 if rows else None,
        "worst": min(returns) if returns else None,
        "best": max(returns) if returns else None,
        "avg_rank": mean(ranks) if ranks else None,
        "avg_composite": mean(comps) if comps else None,
    }


def report_overall(rows: List[Dict[str, Any]]):
    print_section("OVERALL PERFORMANCE BY HORIZON")

    horizons = sorted(
        set(r["horizon"] for r in rows),
        key=lambda h: safe_int(h, 999999) if safe_int(h, None) is not None else 999999,
    )

    table = []

    for h in horizons:
        group = [r for r in rows if r["horizon"] == h]
        s = summarize_group(group)

        table.append(
            [
                h,
                s["n"],
                pct_fmt(s["avg_return"]),
                pct_fmt(s["median_return"]),
                pct_fmt(s["avg_excess"]),
                pct_fmt(s["win_rate"]),
                pct_fmt(s["hit5_rate"]),
                pct_fmt(s["hit10_rate"]),
                pct_fmt(s["worst"]),
                pct_fmt(s["best"]),
            ]
        )

    print_table(
        [
            "horizon",
            "n",
            "avg ret",
            "median",
            "avg excess",
            "win rate",
            "hit 5%",
            "hit 10%",
            "worst",
            "best",
        ],
        table,
    )

    total = len(rows)

    if total < 100:
        print("")
        print(f"NOTE: Only {total} graded outcomes loaded. Treat this as early signal checking, not proof.")


def rank_bucket(rank: Optional[int]) -> str:
    if rank is None:
        return "unknown"
    if rank <= 3:
        return "1-3"
    if rank <= 5:
        return "4-5"
    if rank <= 10:
        return "6-10"
    if rank <= 15:
        return "11-15"
    if rank <= 25:
        return "16-25"
    return "26+"


def report_rank_buckets(rows: List[Dict[str, Any]]):
    print_section("RANK BUCKET PERFORMANCE")

    horizons = sorted(
        set(r["horizon"] for r in rows),
        key=lambda h: safe_int(h, 999999) if safe_int(h, None) is not None else 999999,
    )

    table = []

    for h in horizons:
        h_rows = [r for r in rows if r["horizon"] == h]

        buckets = ["1-3", "4-5", "6-10", "11-15", "16-25", "26+", "unknown"]

        for b in buckets:
            group = [r for r in h_rows if rank_bucket(r.get("rank")) == b]

            if not group:
                continue

            s = summarize_group(group)

            table.append(
                [
                    h,
                    b,
                    s["n"],
                    pct_fmt(s["avg_return"]),
                    pct_fmt(s["median_return"]),
                    pct_fmt(s["avg_excess"]),
                    pct_fmt(s["win_rate"]),
                    pct_fmt(s["hit5_rate"]),
                    pct_fmt(s["best"]),
                    pct_fmt(s["worst"]),
                ]
            )

    print_table(
        [
            "horizon",
            "rank bucket",
            "n",
            "avg ret",
            "median",
            "avg excess",
            "win rate",
            "hit 5%",
            "best",
            "worst",
        ],
        table,
    )


def report_best_worst(rows: List[Dict[str, Any]]):
    print_section("BEST AND WORST OUTCOMES")

    horizons = sorted(
        set(r["horizon"] for r in rows),
        key=lambda h: safe_int(h, 999999) if safe_int(h, None) is not None else 999999,
    )

    for h in horizons:
        h_rows = [r for r in rows if r["horizon"] == h]

        if not h_rows:
            continue

        print("")
        print(f"Horizon {h}")

        worst = sorted(h_rows, key=lambda r: r["return_pct"])[:8]
        best = sorted(h_rows, key=lambda r: r["return_pct"], reverse=True)[:8]

        print("")
        print("Worst:")
        print_table(
            ["ticker", "rank", "return", "excess", "composite"],
            [
                [
                    r["ticker"],
                    r.get("rank"),
                    pct_fmt(r["return_pct"]),
                    pct_fmt(r.get("excess_pct")),
                    num_fmt(r.get("composite")),
                ]
                for r in worst
            ],
        )

        print("")
        print("Best:")
        print_table(
            ["ticker", "rank", "return", "excess", "composite"],
            [
                [
                    r["ticker"],
                    r.get("rank"),
                    pct_fmt(r["return_pct"]),
                    pct_fmt(r.get("excess_pct")),
                    num_fmt(r.get("composite")),
                ]
                for r in best
            ],
        )


def report_ticker_summary(rows: List[Dict[str, Any]]):
    print_section("TICKER SUMMARY")

    grouped = {}

    for r in rows:
        key = (r["ticker"], r["horizon"])
        grouped.setdefault(key, []).append(r)

    table = []

    for (ticker, horizon), group in grouped.items():
        if not ticker:
            continue

        s = summarize_group(group)

        table.append(
            [
                ticker,
                horizon,
                s["n"],
                pct_fmt(s["avg_return"]),
                pct_fmt(s["avg_excess"]),
                pct_fmt(s["win_rate"]),
                pct_fmt(s["best"]),
                pct_fmt(s["worst"]),
                num_fmt(s["avg_rank"], 1),
            ]
        )

    table.sort(
        key=lambda row: (
            safe_int(row[1], 999999) if safe_int(row[1], None) is not None else 999999,
            -safe_float(row[3].replace("%", ""), 0.0),
        )
    )

    print_table(
        ["ticker", "horizon", "n", "avg ret", "avg excess", "win rate", "best", "worst", "avg rank"],
        table,
        max_rows=80,
    )


def report_signal_diagnostics(rows: List[Dict[str, Any]]):
    print_section("SIGNAL DIAGNOSTICS")

    signal_names = sorted(
        set(
            name
            for r in rows
            for name in (r.get("signals") or {}).keys()
        )
    )

    if not signal_names:
        print("No signal JSON found in joined snapshots.")
        return

    horizons = sorted(
        set(r["horizon"] for r in rows),
        key=lambda h: safe_int(h, 999999) if safe_int(h, None) is not None else 999999,
    )

    for h in horizons:
        h_rows = [r for r in rows if r["horizon"] == h]

        diagnostics = []

        for sig in signal_names:
            pairs = []

            for r in h_rows:
                val = r.get("signals", {}).get(sig)

                if val is None:
                    continue

                pairs.append((float(val), r["return_pct"]))

            if len(pairs) < 8:
                continue

            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]

            corr = pearson(xs, ys)

            sorted_pairs = sorted(pairs, key=lambda p: p[0])
            q = max(1, len(sorted_pairs) // 4)

            bottom = sorted_pairs[:q]
            top = sorted_pairs[-q:]

            top_ret = mean([p[1] for p in top])
            bottom_ret = mean([p[1] for p in bottom])
            spread = None

            if top_ret is not None and bottom_ret is not None:
                spread = top_ret - bottom_ret

            win_top = len([p for p in top if p[1] > 0]) / len(top) * 100 if top else None
            win_bottom = len([p for p in bottom if p[1] > 0]) / len(bottom) * 100 if bottom else None

            diagnostics.append(
                {
                    "signal": sig,
                    "n": len(pairs),
                    "corr": corr,
                    "top_ret": top_ret,
                    "bottom_ret": bottom_ret,
                    "spread": spread,
                    "top_win": win_top,
                    "bottom_win": win_bottom,
                }
            )

        diagnostics.sort(key=lambda d: abs(d["spread"] or 0.0), reverse=True)

        print("")
        print(f"Horizon {h} — strongest signal separations")

        print_table(
            [
                "signal",
                "n",
                "corr",
                "top quartile ret",
                "bottom quartile ret",
                "spread",
                "top win",
                "bottom win",
            ],
            [
                [
                    d["signal"],
                    d["n"],
                    num_fmt(d["corr"], 3),
                    pct_fmt(d["top_ret"]),
                    pct_fmt(d["bottom_ret"]),
                    pct_fmt(d["spread"]),
                    pct_fmt(d["top_win"]),
                    pct_fmt(d["bottom_win"]),
                ]
                for d in diagnostics[:20]
            ],
        )


def report_latest_watchlist(conn):
    if not table_exists(conn, "watchlist_scores"):
        return

    cols = table_columns(conn, "watchlist_scores")

    required = {"run_date", "ticker", "rank", "composite", "signals"}

    if not required.issubset(set(cols)):
        return

    print_section("LATEST WATCHLIST")

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT run_date, rank, ticker, composite, signals
            FROM watchlist_scores
            WHERE run_date = (SELECT MAX(run_date) FROM watchlist_scores)
            ORDER BY rank ASC
            LIMIT 25
            """
        )

        rows = [dict(row) for row in cur.fetchall()]

    if not rows:
        print("No watchlist rows found.")
        return

    latest_date = rows[0]["run_date"]
    print(f"Latest watchlist date: {latest_date}")

    table = []

    for row in rows:
        sig = parse_signals(row.get("signals"))

        table.append(
            [
                row["rank"],
                row["ticker"],
                num_fmt(safe_float(row.get("composite")), 3),
                num_fmt(sig.get("momentum_12_1"), 2),
                num_fmt(sig.get("relative_strength_score"), 2),
                num_fmt(sig.get("accumulation_quality_score"), 2),
                num_fmt(sig.get("trend_quality_score"), 2),
                num_fmt(sig.get("breakout_setup_score"), 2),
                num_fmt(sig.get("liquidity_quality_score"), 2),
                num_fmt(sig.get("volatility_control_score"), 2),
                num_fmt(sig.get("dilution_risk_score"), 2),
                num_fmt(sig.get("news_catalyst_score"), 2),
            ]
        )

    print_table(
        [
            "rank",
            "ticker",
            "comp",
            "mom",
            "rel",
            "accum",
            "trend",
            "breakout",
            "liq",
            "vol ctrl",
            "dilution",
            "news",
        ],
        table,
    )


def report_schema(conn):
    print_section("DB TABLE CHECK")

    for table in ["prediction_snapshots", "prediction_outcomes", "watchlist_scores"]:
        if table_exists(conn, table):
            cols = table_columns(conn, table)
            print(f"{table}: {', '.join(cols)}")
        else:
            print(f"{table}: missing")


def main():
    print("")
    print("Quiet Money Engine Performance Report")
    print("Generated:", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    conn = connect()

    try:
        report_schema(conn)

        if not table_exists(conn, "prediction_outcomes"):
            print("")
            print("No prediction_outcomes table found. Run grade_predictions.py first.")
            return

        rows = load_rows(conn)

        if not rows:
            print("")
            print("No graded outcomes found yet.")
            print("That is normal until enough future bars exist for 1d / 5d / 20d grading.")
            return

        report_overall(rows)
        report_rank_buckets(rows)
        report_best_worst(rows)
        report_ticker_summary(rows)
        report_signal_diagnostics(rows)
        report_latest_watchlist(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
