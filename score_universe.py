#!/usr/bin/env python3
"""
Quiet Money Engine — daily cross-sectional scorer.
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
from universe_builder import build_dynamic_universe


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scorer")


DATABASE_URL = os.getenv("DATABASE_URL", "")

MANUAL_UNIVERSE = os.getenv("UNIVERSE", "").strip()
MAX_UNIVERSE_SIZE = int(os.getenv("MAX_UNIVERSE_SIZE", "25"))
MIN_RANKED_TO_SAVE = int(os.getenv("MIN_RANKED_TO_SAVE", "5"))

MIN_PRICE = float(os.getenv("MIN_PRICE", "0.10"))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "250000"))

INSIDER_LOOKBACK_DAYS = int(os.getenv("INSIDER_LOOKBACK_DAYS", "60"))


DEFAULT_SIGNAL_WEIGHTS = {
    "momentum_12_1": 1.0,
    "insider_buy_score": 0.35,
    "volume_pressure_score": 0.50,
}


def get_universe() -> list[str]:
    if MANUAL_UNIVERSE:
        tickers = [
            t.strip().upper()
            for t in MANUAL_UNIVERSE.split(",")
            if t.strip()
        ]

        log.info("Using manual UNIVERSE env var with %s tickers", len(tickers))
        return tickers

    tickers = build_dynamic_universe(max_size=MAX_UNIVERSE_SIZE)

    log.info("Using dynamic universe with %s candidates", len(tickers))
    log.info("Universe candidates: %s", ",".join(tickers))

    return tickers


def parse_signal_weights() -> dict:
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

    return weights or DEFAULT_SIGNAL_WEIGHTS


def load_recent_insider_buys(tickers: list[str], days: int = 60) -> dict[str, list[dict]]:
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


def avg_dollar_volume(bars: list[dict], n: int = 20) -> float:
    recent = bars[-n:]

    if not recent:
        return 0.0

    values = []

    for b in recent:
        close = float(b.get("close") or 0)
        volume = float(b.get("volume") or 0)

        if close > 0 and volume > 0:
            values.append(close * volume)

    if not values:
        return 0.0

    return sum(values) / len(values)


def passes_tradeability_price_gate(ticker: str, bars: list[dict]) -> bool:
    if not bars:
        return False

    last_close = float(bars[-1].get("close") or 0)

    if last_close < MIN_PRICE:
        log.info("%s failed price gate: %.4f < %.4f", ticker, last_close, MIN_PRICE)
        return False

    adv = avg_dollar_volume(bars, 20)

    if adv < MIN_DOLLAR_VOLUME:
        log.info("%s failed dollar-volume gate: %.0f < %.0f", ticker, adv, MIN_DOLLAR_VOLUME)
        return False

    return True


def build_universe_data(tickers: list[str]) -> dict:
    insider_buys_by_ticker = load_recent_insider_buys(
        tickers,
        days=INSIDER_LOOKBACK_DAYS,
    )

    data = {}

    for ticker in tickers:
        bars = get_price_history(ticker, days=400)

        if not bars:
            log.warning("No price history for %s; skipping", ticker)
            continue

        if not passes_tradeability_price_gate(ticker, bars):
            continue

        data[ticker] = {
            "bars": bars,
            "insider_buys": insider_buys_by_ticker.get(ticker, []),
        }

    return data


def main() -> None:
    init_db()

    universe = get_universe()
    weights = parse_signal_weights()

    log.info(
        "Scoring up to %d candidates on signals: %s",
        len(universe),
        ", ".join(SIGNALS),
    )

    log.info("Signal weights: %s", weights)

    data = build_universe_data(universe)

    if not data:
        log.error("No usable data fetched — refusing to save empty watchlist")
        raise SystemExit(1)

    ranked = score_universe(data, SIGNALS, weights=weights)

    rows = []

    for i, row in enumerate(ranked, 1):
        if i > MAX_UNIVERSE_SIZE:
            break

        row["rank"] = i
        rows.append(row)

        sig_str = " ".join(
            f"{name}={z:+.2f}"
            for name, z in row["signals"].items()
        )

        raw_insider_count = len(data.get(row["ticker"], {}).get("insider_buys", []))

        log.info(
            "%2d. %-8s composite %+.2f | insider_buys=%d | %s",
            i,
            row["ticker"],
            row["composite"],
            raw_insider_count,
            sig_str,
        )

    if len(rows) < MIN_RANKED_TO_SAVE:
        log.error(
            "Only %d ranked rows. Minimum required is %d. Refusing to overwrite DB.",
            len(rows),
            MIN_RANKED_TO_SAVE,
        )
        raise SystemExit(1)

    save_watchlist(date.today(), rows)

    log.info("Saved %d ranked names to DB for %s", len(rows), date.today())


if __name__ == "__main__":
    main()
