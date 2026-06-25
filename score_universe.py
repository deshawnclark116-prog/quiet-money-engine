#!/usr/bin/env python3
"""
Quiet Money Engine — daily cross-sectional scorer.

Fetches price history for the universe, attaches recent insider-buy data,
computes every signal, z-scores and ranks them into a watchlist, then saves
the ranking to Postgres.

Important:
New signals can be run at conservative weights until the grader proves they
add value. This prevents one fresh unvalidated signal from hijacking the ranker.
"""
import os
import logging
from datetime import date

import psycopg2
from psycopg2.extras import RealDictCursor

from data_layer import get_price_history
from signals import SIGNALS
from scoring import score_universe
from db import init_db, save_watchlist


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scorer")


DATABASE_URL = os.getenv("DATABASE_URL", "")

UNIVERSE = [
    t.strip().upper()
    for t in os.getenv(
        "UNIVERSE",
        "AAPL,MSFT,NVDA,AMD,INTC,F,GM,RIOT,SOFI,PLTR",
    ).split(",")
    if t.strip()
]

INSIDER_LOOKBACK_DAYS = int(os.getenv("INSIDER_LOOKBACK_DAYS", "60"))


DEFAULT_SIGNAL_WEIGHTS = {
    "momentum_12_1": 1.0,
    "insider_buy_score": 0.35,
    "volume_pressure_score": 0.50,
}


def parse_signal_weights() -> dict:
    """
    Optional env override:
        SIGNAL_WEIGHTS=momentum_12_1:1.0,insider_buy_score:0.35,volume_pressure_score:0.50

    If unset, use conservative defaults.
    """
    raw = os.getenv("SIGNAL_WEIGHTS", "").strip()

    if not raw:
        return DEFAULT_SIGNAL_WEIGHTS

    weights = {}

    for part in raw.split(","):
        part = part.strip()

        if not part or ":" not in part:
            continue

        name, value = part.split(":", 1)
        name = name.strip()
        value = value.strip()

        try:
            weights[name] = float(value)
        except Exception:
            log.warning("Bad SIGNAL_WEIGHTS entry ignored: %s", part)

    if not weights:
        return DEFAULT_SIGNAL_WEIGHTS

    return weights


def load_recent_insider_buys(tickers: list[str], days: int = 60) -> dict[str, list[dict]]:
    """
    Load recent insider buys for the current universe.

    Uses seen_at because it is a real TIMESTAMPTZ column. filed_at is currently
    stored as text in the DB, so seen_at is safer for the daily scoring feature.
    """
    result = {ticker: [] for ticker in tickers}

    if not DATABASE_URL:
        log.warning("DATABASE_URL missing; insider_buy_score will be zero")
        return result

    if not tickers:
        return result

    sql = """
        SELECT
            ticker,
            insider,
            role,
            shares,
            price,
            value,
            market_cap,
            avg_dollar_vol,
            filed_at,
            seen_at
        FROM insider_buys
        WHERE UPPER(ticker) = ANY(%s)
          AND seen_at >= NOW() - (%s || ' days')::interval
        ORDER BY seen_at DESC
    """

    try:
        with psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [tickers, str(days)])
                rows = cur.fetchall()

        for row in rows:
            ticker = str(row["ticker"]).upper()

            if ticker in result:
                result[ticker].append(dict(row))

        active = {t: len(v) for t, v in result.items() if v}

        if active:
            log.info("Loaded recent insider buys: %s", active)
        else:
            log.info("No recent insider buys found for current universe")

    except Exception as e:
        log.warning("Failed loading insider buys; insider_buy_score will be zero: %s", e)

    return result


def build_universe_data(tickers: list[str]) -> dict:
    insider_buys_by_ticker = load_recent_insider_buys(
        tickers,
        days=INSIDER_LOOKBACK_DAYS,
    )

    data = {}

    for ticker in tickers:
        bars = get_price_history(ticker, days=400)

        if bars:
            data[ticker] = {
                "bars": bars,
                "insider_buys": insider_buys_by_ticker.get(ticker, []),
            }
        else:
            log.warning("No price history for %s; skipping", ticker)

    return data


def main() -> None:
    init_db()

    weights = parse_signal_weights()

    log.info(
        "Scoring %d tickers on signals: %s",
        len(UNIVERSE),
        ", ".join(SIGNALS),
    )

    log.info("Signal weights: %s", weights)

    data = build_universe_data(UNIVERSE)

    if not data:
        log.error("No data fetched — check FMP_API_KEY")
        return

    ranked = score_universe(data, SIGNALS, weights=weights)

    rows = []

    for i, row in enumerate(ranked, 1):
        row["rank"] = i
        rows.append(row)

        sig_str = " ".join(
            f"{name}={z:+.2f}"
            for name, z in row["signals"].items()
        )

        raw_insider_count = len(data.get(row["ticker"], {}).get("insider_buys", []))

        log.info(
            "%2d. %-6s composite %+.2f | insider_buys=%d | %s",
            i,
            row["ticker"],
            row["composite"],
            raw_insider_count,
            sig_str,
        )

    save_watchlist(date.today(), rows)

    log.info("Saved %d ranked names to DB for %s", len(rows), date.today())


if __name__ == "__main__":
    main()
