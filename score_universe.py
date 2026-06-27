#!/usr/bin/env python3
"""
Quiet Money Engine — universe scorer.

Builds a ticker universe, fetches price history, applies tradeability gates,
scores each ticker, adds company/news insight, and saves the ranked watchlist.

Current stock-side engine:
- momentum_12_1
- insider_buy_score
- volume_pressure_score
- capital_efficiency_score
- relative_strength_score
- filing_catalyst_score
- company_quality_score
- news_catalyst_score
- dilution_risk_score
- reverse_split_risk_score

Options are parked for later.
"""

import os
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor, Json

from db import init_db
from data_layer import get_price_history
from signals import SIGNALS

try:
    from universe_builder import build_dynamic_universe
except Exception:
    build_dynamic_universe = None

try:
    from company_insights import analyze_ticker
except Exception:
    analyze_ticker = None


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

log = logging.getLogger("score_universe")


DATABASE_URL = os.getenv("DATABASE_URL")

MAX_UNIVERSE_SIZE = int(os.getenv("MAX_UNIVERSE_SIZE", "25"))
MIN_RANKED_TO_SAVE = int(os.getenv("MIN_RANKED_TO_SAVE", "8"))

MIN_PRICE = float(os.getenv("MIN_PRICE", "0.10"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "1000000"))
MIN_DOLLAR_VOLUME = float(os.getenv("MIN_DOLLAR_VOLUME", "250000"))

INSIDER_LOOKBACK_DAYS = int(os.getenv("INSIDER_LOOKBACK_DAYS", "30"))
PRICE_HISTORY_DAYS = int(os.getenv("PRICE_HISTORY_DAYS", "400"))

ENABLE_COMPANY_INSIGHTS = os.getenv("ENABLE_COMPANY_INSIGHTS", "true").lower() in {
    "1",
    "true",
    "yes",
    "y",
}

COMPANY_INSIGHT_TOP_N = int(
    os.getenv(
        "COMPANY_INSIGHT_TOP_N",
        str(max(MAX_UNIVERSE_SIZE * 2, 35)),
    )
)

BENCHMARK_TICKERS = [
    x.strip().upper()
    for x in os.getenv("BENCHMARK_TICKERS", "SPY,QQQ").split(",")
    if x.strip()
]

DEFAULT_UNIVERSE = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMD",
    "INTC",
    "F",
    "GM",
    "RIOT",
    "SOFI",
    "PLTR",
    "MARA",
    "CLSK",
    "HOOD",
    "AFRM",
    "UPST",
    "OPEN",
    "LCID",
    "RIVN",
    "CHPT",
    "IONQ",
    "SOUN",
    "BBAI",
    "ACHR",
    "JOBY",
    "ASTS",
    "RKLB",
    "ENVX",
    "QS",
    "PLUG",
    "FCEL",
]


DEFAULT_SIGNAL_WEIGHTS = {
    "momentum_12_1": 1.00,
    "insider_buy_score": 0.35,
    "volume_pressure_score": 0.60,
    "capital_efficiency_score": 0.55,
    "relative_strength_score": 0.50,

    # Company / filing / news insight layer.
    "filing_catalyst_score": 0.35,
    "company_quality_score": 0.30,
    "news_catalyst_score": 0.25,

    # These scores are already negative when risky, so use positive weights.
    "dilution_risk_score": 0.90,
    "reverse_split_risk_score": 0.70,

    # Display-only. Do not double-count this because it already combines pieces.
    "company_insight_composite": 0.00,
}


COMPANY_INSIGHT_SCORE_KEYS = [
    "filing_catalyst_score",
    "dilution_risk_score",
    "reverse_split_risk_score",
    "company_quality_score",
    "news_catalyst_score",
    "company_insight_composite",
]


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def clean_ticker(ticker: str) -> str:
    return str(ticker or "").upper().strip()


def parse_signal_weights() -> dict:
    """
    Allows optional env override:

    SIGNAL_WEIGHTS_JSON='{"momentum_12_1":1.0,"relative_strength_score":0.5}'
    """
    raw = os.getenv("SIGNAL_WEIGHTS_JSON", "").strip()

    if not raw:
        return DEFAULT_SIGNAL_WEIGHTS

    try:
        parsed = json.loads(raw)

        if not isinstance(parsed, dict):
            log.warning("SIGNAL_WEIGHTS_JSON was not a dict; using defaults")
            return DEFAULT_SIGNAL_WEIGHTS

        weights = {}

        for key, value in parsed.items():
            weights[str(key)] = float(value)

        return weights or DEFAULT_SIGNAL_WEIGHTS

    except Exception as exc:
        log.warning("Failed parsing SIGNAL_WEIGHTS_JSON; using defaults: %s", exc)
        return DEFAULT_SIGNAL_WEIGHTS


def get_universe() -> list[str]:
    """
    If UNIVERSE env var is set, use it exactly.
    Otherwise use dynamic universe builder when available.
    Otherwise fall back to DEFAULT_UNIVERSE.
    """
    raw = os.getenv("UNIVERSE", "").strip()

    if raw:
        tickers = []

        for item in raw.split(","):
            t = clean_ticker(item)

            if t and t not in tickers:
                tickers.append(t)

        log.info("Using UNIVERSE env var with %d tickers", len(tickers))
        log.info("Universe candidates: %s", ", ".join(tickers))
        return tickers

    if build_dynamic_universe:
        try:
            tickers = build_dynamic_universe()

            cleaned = []

            for t in tickers:
                t = clean_ticker(t)

                if t and t not in cleaned:
                    cleaned.append(t)

            if cleaned:
                log.info("Using dynamic universe with %d candidates", len(cleaned))
                log.info("Universe candidates: %s", ", ".join(cleaned))
                return cleaned

        except Exception as exc:
            log.warning("Dynamic universe builder failed; using fallback universe: %s", exc)

    log.info("Using fallback universe with %d tickers", len(DEFAULT_UNIVERSE))
    log.info("Universe candidates: %s", ", ".join(DEFAULT_UNIVERSE))
    return DEFAULT_UNIVERSE[:]


def avg_dollar_volume(bars: list[dict], window: int = 20) -> float:
    if not bars:
        return 0.0

    sample = bars[-window:]

    values = []

    for bar in sample:
        close = safe_float(bar.get("close"), 0.0)
        volume = safe_float(bar.get("volume"), 0.0)

        if close > 0 and volume > 0:
            values.append(close * volume)

    if not values:
        return 0.0

    return sum(values) / len(values)


def last_close(bars: list[dict]) -> float:
    if not bars:
        return 0.0

    return safe_float(bars[-1].get("close"), 0.0)


def passes_tradeability_price_gate(ticker: str, bars: list[dict]) -> bool:
    price = last_close(bars)

    if price <= 0:
        log.info("%s failed price gate: missing/invalid price", ticker)
        return False

    if price < MIN_PRICE:
        log.info("%s failed min-price gate: %.4f < %.4f", ticker, price, MIN_PRICE)
        return False

    if price > MAX_PRICE:
        log.info("%s failed max-price gate: %.4f > %.4f", ticker, price, MAX_PRICE)
        return False

    adv = avg_dollar_volume(bars, 20)

    if adv < MIN_DOLLAR_VOLUME:
        log.info(
            "%s failed dollar-volume gate: %.0f < %.0f",
            ticker,
            adv,
            MIN_DOLLAR_VOLUME,
        )
        return False

    return True


def load_recent_insider_buys(tickers: list[str], days: int = 30) -> dict:
    """
    Loads recent insider buys from DB.

    The actual insider_buys table uses seen_at, not created_at.
    """
    result = {clean_ticker(t): [] for t in tickers}

    if not DATABASE_URL:
        log.warning("DATABASE_URL missing; insider_buy_score will be zero")
        return result

    if not tickers:
        return result

    cutoff = datetime.utcnow() - timedelta(days=days)

    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT *
                        FROM insider_buys
                        WHERE UPPER(ticker) = ANY(%s)
                          AND seen_at >= %s
                        ORDER BY seen_at DESC NULLS LAST, filed_at DESC NULLS LAST
                        """,
                        [[clean_ticker(t) for t in tickers], cutoff],
                    )

                    for row in cur.fetchall():
                        t = clean_ticker(row.get("ticker"))

                        if t in result:
                            result[t].append(dict(row))

        finally:
            conn.close()

    except Exception as exc:
        log.warning("Failed loading insider buys; insider_buy_score will be zero: %s", exc)

    counts = {ticker: len(rows) for ticker, rows in result.items() if rows}

    if counts:
        log.info("Loaded recent insider buys: %s", counts)

    return result


def load_benchmark_bars() -> dict:
    """
    Fetches benchmark bars once per run.

    Passed into every ticker so relative_strength_score can compare against
    SPY/QQQ without refetching benchmarks for every ticker.
    """
    benchmarks = {}

    for ticker in BENCHMARK_TICKERS:
        try:
            bars = get_price_history(ticker, days=PRICE_HISTORY_DAYS)

            if bars:
                benchmarks[ticker] = bars
                log.info("Loaded benchmark %s bars: %d", ticker, len(bars))
            else:
                log.warning("No benchmark bars for %s", ticker)

        except Exception as exc:
            log.warning("Benchmark fetch failed for %s: %s", ticker, exc)

    if not benchmarks:
        log.warning("No benchmark bars loaded; relative_strength_score will be zero")

    return benchmarks


def build_universe_data(tickers: list[str]) -> dict:
    insider_buys_by_ticker = load_recent_insider_buys(
        tickers,
        days=INSIDER_LOOKBACK_DAYS,
    )

    benchmark_bars = load_benchmark_bars()

    data = {}

    for ticker in tickers:
        ticker = clean_ticker(ticker)

        if not ticker:
            continue

        if ticker in benchmark_bars:
            continue

        log.info("Fetching price history for %s", ticker)

        bars = get_price_history(ticker, days=PRICE_HISTORY_DAYS)

        if not bars:
            log.warning("No price history for %s; skipping", ticker)
            continue

        if not passes_tradeability_price_gate(ticker, bars):
            continue

        data[ticker] = {
            "ticker": ticker,
            "bars": bars,
            "price": last_close(bars),
            "avg_dollar_volume_20": avg_dollar_volume(bars, 20),
            "insider_buys": insider_buys_by_ticker.get(ticker, []),
            "recent_insider_buy_count": len(insider_buys_by_ticker.get(ticker, [])),
            "benchmark_bars": benchmark_bars,
        }

    return data


def get_company_insight_scores(ticker: str, price: Optional[float] = None) -> dict:
    """
    Returns numeric company/news insight scores.

    Fails open/neutral so SEC/Finnhub problems do not break the scorer.
    """
    neutral = {key: 0.0 for key in COMPANY_INSIGHT_SCORE_KEYS}

    if not ENABLE_COMPANY_INSIGHTS:
        return neutral

    if analyze_ticker is None:
        log.warning("company_insights module unavailable; company insight scores neutral")
        return neutral

    try:
        result = analyze_ticker(ticker, price=price)
        scores = result.get("scores") or {}

        out = {}

        for key in COMPANY_INSIGHT_SCORE_KEYS:
            out[key] = safe_float(scores.get(key), 0.0)

        log.info(
            "%s company insight: filing %+0.2f | dilution %+0.2f | split %+0.2f | quality %+0.2f | news %+0.2f | composite %+0.2f",
            ticker,
            out["filing_catalyst_score"],
            out["dilution_risk_score"],
            out["reverse_split_risk_score"],
            out["company_quality_score"],
            out["news_catalyst_score"],
            out["company_insight_composite"],
        )

        return out

    except Exception as exc:
        log.warning("%s company insight failed; using neutral scores: %s", ticker, exc)
        return neutral


def score_universe(
    data: dict,
    signals: dict,
    weights: Optional[dict] = None,
) -> list[dict]:
    weights = weights or DEFAULT_SIGNAL_WEIGHTS
    ranked = []

    # First pass: technical / market / insider signals.
    for ticker, ticker_data in data.items():
        signal_values = {}
        composite = 0.0

        for name, fn in signals.items():
            try:
                value = float(fn(ticker_data))
            except Exception as exc:
                log.warning("%s %s failed: %s", ticker, name, exc)
                value = 0.0

            signal_values[name] = value
            composite += value * float(weights.get(name, 0.0))

        ranked.append(
            {
                "ticker": ticker,
                "composite": composite,
                "signals": signal_values,
                "price_at_signal": ticker_data.get("price"),
                "avg_dollar_volume_20": ticker_data.get("avg_dollar_volume_20"),
            }
        )

    ranked.sort(key=lambda row: row["composite"], reverse=True)

    # Second pass: company/news insight layer.
    # Analyze top candidates only to control SEC/Finnhub API usage.
    if ENABLE_COMPANY_INSIGHTS and ranked:
        n = min(COMPANY_INSIGHT_TOP_N, len(ranked))
        log.info("Applying company/news insight layer to top %d candidates", n)

        for row in ranked[:n]:
            ticker = row["ticker"]
            price = safe_float(row.get("price_at_signal"), 0.0)

            insight_scores = get_company_insight_scores(ticker, price=price)

            for name, value in insight_scores.items():
                value = safe_float(value, 0.0)
                row["signals"][name] = value
                row["composite"] += value * float(weights.get(name, 0.0))

        ranked.sort(key=lambda row: row["composite"], reverse=True)

    return ranked


def save_watchlist_rows(rows: list[dict], run_date: Optional[str] = None) -> None:
    """
    Saves ranked rows directly to watchlist_scores.

    Same-day reruns are clean because this deletes the run_date first.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL env var is required")

    run_date = run_date or date.today().isoformat()

    conn = psycopg2.connect(DATABASE_URL)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM watchlist_scores
                    WHERE run_date = %s
                    """,
                    [run_date],
                )

                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO watchlist_scores (
                            run_date,
                            ticker,
                            rank,
                            composite,
                            signals
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            run_date,
                            row["ticker"],
                            row["rank"],
                            float(row["composite"]),
                            Json(row["signals"]),
                        ],
                    )

    finally:
        conn.close()

    log.info("Saved %d ranked names to DB for %s", len(rows), run_date)


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
    log.info(
        "Company/news insights enabled=%s top_n=%d",
        ENABLE_COMPANY_INSIGHTS,
        COMPANY_INSIGHT_TOP_N,
    )

    data = build_universe_data(universe)

    if not data:
        log.error("No usable data fetched - refusing to save empty watchlist")
        raise SystemExit(1)

    ranked = score_universe(data, SIGNALS, weights=weights)

    rows = []

    for i, row in enumerate(ranked, 1):
        if i > MAX_UNIVERSE_SIZE:
            break

        row["rank"] = i
        rows.append(row)

    if len(rows) < MIN_RANKED_TO_SAVE:
        log.error(
            "Only %d ranked names produced; refusing to save because MIN_RANKED_TO_SAVE=%d",
            len(rows),
            MIN_RANKED_TO_SAVE,
        )
        raise SystemExit(1)

    for row in rows:
        signal_text = ", ".join(
            f"{name}={value:+.2f}"
            for name, value in row["signals"].items()
        )

        log.info(
            "%d. %-7s composite %+0.2f | price %.4f | %s",
            row["rank"],
            row["ticker"],
            row["composite"],
            safe_float(row.get("price_at_signal"), 0.0),
            signal_text,
        )

    save_watchlist_rows(rows)


if __name__ == "__main__":
    main()
