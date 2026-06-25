#!/usr/bin/env python3
"""
Quiet Money Engine — prediction grader.

Grades saved prediction snapshots at 1d / 5d / 20d horizons.

Uses data_layer.get_price_history(), so grading follows the same provider chain
as the scorer.

Recommended:
PRICE_PROVIDER_ORDER=stooq,yahoo
"""
import os
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from data_layer import get_price_history


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

DATABASE_URL = os.getenv("DATABASE_URL")

HORIZONS = [
    int(x.strip())
    for x in os.getenv("GRADE_HORIZONS", "1,5,20").split(",")
    if x.strip()
]

MAX_SNAPSHOTS = int(os.getenv("GRADE_MAX_SNAPSHOTS", "200"))

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL env var is required")


_price_cache: Dict[str, List[dict]] = {}


def parse_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value

    if isinstance(value, datetime):
        return value.date()

    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def as_float(value) -> Optional[float]:
    if value is None:
        return None

    try:
        return float(value)
    except Exception:
        return None


def fetch_bars(symbol: str) -> List[dict]:
    symbol = symbol.upper().strip()

    if symbol in _price_cache:
        return _price_cache[symbol]

    logging.info("Fetching price history for %s", symbol)

    bars = get_price_history(symbol, days=1400)

    if not bars:
        logging.warning("No usable bars for %s", symbol)
        _price_cache[symbol] = []
        return []

    clean = []

    for bar in bars:
        try:
            d = parse_date(bar.get("date"))
            close = as_float(bar.get("close"))
            high = as_float(bar.get("high")) or close

            if close is None or close <= 0:
                continue

            clean.append(
                {
                    "date": d,
                    "close": close,
                    "high": high if high and high > 0 else close,
                }
            )
        except Exception:
            continue

    clean.sort(key=lambda x: x["date"])

    _price_cache[symbol] = clean
    return clean


def find_bar_index_on_or_before(bars: List[dict], target_date: date) -> Optional[int]:
    idx = None

    for i, bar in enumerate(bars):
        if bar["date"] <= target_date:
            idx = i
        else:
            break

    return idx


def grade_snapshot(snapshot: dict, horizon_days: int) -> Optional[dict]:
    ticker = snapshot["ticker"].upper()
    run_date = parse_date(snapshot["run_date"])

    bars = fetch_bars(ticker)

    if not bars:
        return None

    start_idx = find_bar_index_on_or_before(bars, run_date)

    if start_idx is None:
        logging.info("%s has no start bar on/before %s", ticker, run_date)
        return None

    end_idx = start_idx + horizon_days

    if end_idx >= len(bars):
        logging.info(
            "%s not ready for %sd grade yet. Need index %s, have %s bars.",
            ticker,
            horizon_days,
            end_idx,
            len(bars),
        )
        return None

    start_bar = bars[start_idx]
    end_bar = bars[end_idx]

    start_price = as_float(snapshot.get("price_at_signal")) or start_bar["close"]
    end_price = end_bar["close"]

    if not start_price or start_price <= 0:
        return None

    raw_return = (end_price / start_price) - 1.0

    window = bars[start_idx + 1 : end_idx + 1]

    if window:
        lowest_close = min(bar["close"] for bar in window)
        highest_high = max(bar["high"] for bar in window)
    else:
        lowest_close = end_price
        highest_high = end_price

    max_drawdown = (lowest_close / start_price) - 1.0
    hit_5pct = highest_high >= start_price * 1.05
    hit_10pct = highest_high >= start_price * 1.10

    spy_return = None
    excess_return_vs_spy = None

    spy_bars = fetch_bars("SPY")

    if spy_bars:
        spy_start_idx = find_bar_index_on_or_before(spy_bars, run_date)
        spy_end_idx = find_bar_index_on_or_before(spy_bars, end_bar["date"])

        if spy_start_idx is not None and spy_end_idx is not None:
            spy_start = spy_bars[spy_start_idx]["close"]
            spy_end = spy_bars[spy_end_idx]["close"]

            if spy_start and spy_start > 0:
                spy_return = (spy_end / spy_start) - 1.0
                excess_return_vs_spy = raw_return - spy_return

    return {
        "snapshot_id": snapshot["snapshot_id"],
        "ticker": ticker,
        "run_date": run_date,
        "horizon_days": horizon_days,
        "outcome_date": end_bar["date"],
        "start_price": start_price,
        "end_price": end_price,
        "raw_return": raw_return,
        "spy_return": spy_return,
        "excess_return_vs_spy": excess_return_vs_spy,
        "max_drawdown": max_drawdown,
        "hit_5pct": hit_5pct,
        "hit_10pct": hit_10pct,
    }


def get_ungraded_snapshots(cur, horizon_days: int) -> List[dict]:
    cur.execute(
        """
        SELECT
            ps.id AS snapshot_id,
            ps.run_id,
            ps.run_date,
            ps.ticker,
            ps.rank,
            ps.composite,
            ps.price_at_signal
        FROM prediction_snapshots ps
        WHERE NOT EXISTS (
            SELECT 1
            FROM prediction_outcomes po
            WHERE po.snapshot_id = ps.id
              AND po.horizon_days = %s
        )
        ORDER BY ps.run_date ASC, ps.rank ASC NULLS LAST, ps.id ASC
        LIMIT %s
        """,
        [horizon_days, MAX_SNAPSHOTS],
    )

    return list(cur.fetchall())


def save_outcome(cur, outcome: dict):
    cur.execute(
        """
        INSERT INTO prediction_outcomes (
            snapshot_id,
            ticker,
            run_date,
            horizon_days,
            outcome_date,
            start_price,
            end_price,
            raw_return,
            spy_return,
            excess_return_vs_spy,
            max_drawdown,
            hit_5pct,
            hit_10pct
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s
        )
        ON CONFLICT (snapshot_id, horizon_days)
        DO UPDATE SET
            outcome_date = EXCLUDED.outcome_date,
            start_price = EXCLUDED.start_price,
            end_price = EXCLUDED.end_price,
            raw_return = EXCLUDED.raw_return,
            spy_return = EXCLUDED.spy_return,
            excess_return_vs_spy = EXCLUDED.excess_return_vs_spy,
            max_drawdown = EXCLUDED.max_drawdown,
            hit_5pct = EXCLUDED.hit_5pct,
            hit_10pct = EXCLUDED.hit_10pct,
            graded_at = NOW()
        """,
        [
            outcome["snapshot_id"],
            outcome["ticker"],
            outcome["run_date"],
            outcome["horizon_days"],
            outcome["outcome_date"],
            outcome["start_price"],
            outcome["end_price"],
            outcome["raw_return"],
            outcome["spy_return"],
            outcome["excess_return_vs_spy"],
            outcome["max_drawdown"],
            outcome["hit_5pct"],
            outcome["hit_10pct"],
        ],
    )


def main():
    logging.info("Starting prediction grader")
    logging.info("HORIZONS=%s", HORIZONS)

    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    total_saved = 0
    total_checked = 0

    try:
        with conn:
            with conn.cursor() as cur:
                for horizon in HORIZONS:
                    snapshots = get_ungraded_snapshots(cur, horizon)

                    logging.info(
                        "Found %s ungraded snapshots for %sd horizon",
                        len(snapshots),
                        horizon,
                    )

                    saved_for_horizon = 0

                    for snapshot in snapshots:
                        total_checked += 1

                        outcome = grade_snapshot(snapshot, horizon)

                        if not outcome:
                            continue

                        save_outcome(cur, outcome)
                        saved_for_horizon += 1
                        total_saved += 1

                        logging.info(
                            "Graded %s %sd: raw_return=%+.2f%% excess_vs_spy=%s",
                            outcome["ticker"],
                            horizon,
                            outcome["raw_return"] * 100,
                            (
                                f"{outcome['excess_return_vs_spy'] * 100:+.2f}%"
                                if outcome["excess_return_vs_spy"] is not None
                                else "NA"
                            ),
                        )

                    logging.info(
                        "Saved %s outcomes for %sd horizon",
                        saved_for_horizon,
                        horizon,
                    )

        logging.info("Prediction grading finished")
        logging.info("Checked %s snapshots", total_checked)
        logging.info("Saved %s outcomes", total_saved)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
