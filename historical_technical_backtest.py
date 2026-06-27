#!/usr/bin/env python3
"""
Quiet Money Engine — historical technical backtest.

This is a price/volume-only historical replay.

It tests:
- momentum_12_1
- volume_pressure_score
- capital_efficiency_score
- relative_strength_score
- accumulation_quality_score
- trend_quality_score
- breakout_setup_score
- liquidity_quality_score
- volatility_control_score

It intentionally does NOT replay:
- live insider_buy_score
- SEC/company filing scores
- Finnhub news scores

Why:
Those require point-in-time historical filing/news/insider availability. Using today's
filings/news to score past dates would create look-ahead bias.

Default behavior:
- Uses latest watchlist tickers from DB if available.
- Falls back to BACKTEST_UNIVERSE env var if set.
- Falls back to a default small universe if DB unavailable.
- Scores every Nth trading day over a lookback window.
- Measures 1d / 5d / 20d forward returns.
"""

import os
import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from data_layer import get_price_history
from signals import SIGNALS


DATABASE_URL = os.getenv("DATABASE_URL")

BACKTEST_DAYS = int(os.getenv("BACKTEST_DAYS", "252"))
BACKTEST_STEP_DAYS = int(os.getenv("BACKTEST_STEP_DAYS", "5"))
BACKTEST_MIN_HISTORY = int(os.getenv("BACKTEST_MIN_HISTORY", "80"))
BACKTEST_TOP_N = int(os.getenv("BACKTEST_TOP_N", "10"))
BACKTEST_MAX_TICKERS = int(os.getenv("BACKTEST_MAX_TICKERS", "25"))

HORIZONS = [
    int(x.strip())
    for x in os.getenv("BACKTEST_HORIZONS", "1,5,20").split(",")
    if x.strip()
]

BENCHMARK_TICKERS = [
    x.strip().upper()
    for x in os.getenv("BENCHMARK_TICKERS", "SPY,QQQ").split(",")
    if x.strip()
]

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMD", "INTC",
    "RIOT", "MARA", "CLSK", "SOFI", "PLTR",
    "OPEN", "HOOD", "AFRM", "UPST", "F",
    "GM", "JOBY", "ACHR", "SOUN", "BBAI",
    "OESX", "TOI", "LILA", "YYGH", "FTH",
]


TECHNICAL_WEIGHTS = {
    "momentum_12_1": 1.00,
    "volume_pressure_score": 0.60,
    "capital_efficiency_score": 0.55,
    "relative_strength_score": 0.50,
    "accumulation_quality_score": 0.70,
    "trend_quality_score": 0.50,
    "breakout_setup_score": 0.45,
    "liquidity_quality_score": 0.50,
    "volatility_control_score": 0.40,

    # Explicitly neutral for historical technical replay.
    "insider_buy_score": 0.00,
}


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def mean(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def median(values: List[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return statistics.median(vals)


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def num(value: Optional[float], digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def print_section(title: str):
    print("")
    print("=" * 100)
    print(title)
    print("=" * 100)


def print_table(headers: List[str], rows: List[List[Any]], max_rows: Optional[int] = None):
    if max_rows is not None:
        rows = rows[:max_rows]

    rows = [[str(x) for x in row] for row in rows]
    headers = [str(h) for h in headers]

    widths = []
    for i, h in enumerate(headers):
        width = len(h)
        for row in rows:
            if i < len(row):
                width = max(width, len(row[i]))
        widths.append(min(width, 24))

    def trim(s: str, width: int) -> str:
        if len(s) <= width:
            return s
        return s[: width - 1] + "…"

    print(" | ".join(trim(headers[i], widths[i]).ljust(widths[i]) for i in range(len(headers))))
    print("-+-".join("-" * w for w in widths))

    for row in rows:
        print(" | ".join(trim(row[i] if i < len(row) else "", widths[i]).ljust(widths[i]) for i in range(len(headers))))


def latest_watchlist_tickers() -> List[str]:
    if not DATABASE_URL:
        return []

    try:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ticker
                        FROM watchlist_scores
                        WHERE run_date = (SELECT MAX(run_date) FROM watchlist_scores)
                        ORDER BY rank ASC
                        LIMIT %s
                        """,
                        [BACKTEST_MAX_TICKERS],
                    )

                    return [
                        str(row["ticker"]).upper().strip()
                        for row in cur.fetchall()
                        if row.get("ticker")
                    ]
        finally:
            conn.close()

    except Exception as exc:
        print(f"Could not load latest watchlist from DB: {exc}")
        return []


def get_universe() -> List[str]:
    raw = os.getenv("BACKTEST_UNIVERSE", "").strip()

    if raw:
        tickers = []
        for item in raw.split(","):
            t = item.strip().upper()
            if t and t not in tickers:
                tickers.append(t)
        return tickers[:BACKTEST_MAX_TICKERS]

    tickers = latest_watchlist_tickers()

    if tickers:
        return tickers[:BACKTEST_MAX_TICKERS]

    return DEFAULT_UNIVERSE[:BACKTEST_MAX_TICKERS]


def close_at(bars: List[dict], idx: int) -> float:
    return safe_float(bars[idx].get("close"), 0.0)


def bar_date(bars: List[dict], idx: int) -> str:
    return str(bars[idx].get("date") or idx)


def forward_return_pct(bars: List[dict], idx: int, horizon: int) -> Optional[float]:
    future_idx = idx + horizon

    if future_idx >= len(bars):
        return None

    start = close_at(bars, idx)
    end = close_at(bars, future_idx)

    if start <= 0 or end <= 0:
        return None

    return ((end / start) - 1.0) * 100.0


def slice_bars(bars: List[dict], end_idx: int) -> List[dict]:
    return bars[: end_idx + 1]


def fetch_all_bars(tickers: List[str]) -> Dict[str, List[dict]]:
    out = {}

    needed_days = BACKTEST_DAYS + BACKTEST_MIN_HISTORY + max(HORIZONS) + 60

    for t in tickers:
        print(f"Fetching {t}...")
        try:
            bars = get_price_history(t, days=needed_days)
            if bars and len(bars) >= BACKTEST_MIN_HISTORY + max(HORIZONS) + 5:
                out[t] = bars
            else:
                print(f"  skipping {t}: not enough bars")
        except Exception as exc:
            print(f"  skipping {t}: {exc}")

    return out


def fetch_benchmarks() -> Dict[str, List[dict]]:
    out = {}

    needed_days = BACKTEST_DAYS + BACKTEST_MIN_HISTORY + max(HORIZONS) + 60

    for t in BENCHMARK_TICKERS:
        print(f"Fetching benchmark {t}...")
        try:
            bars = get_price_history(t, days=needed_days)
            if bars:
                out[t] = bars
        except Exception as exc:
            print(f"  benchmark {t} failed: {exc}")

    return out


def benchmark_slice_map(benchmark_bars: Dict[str, List[dict]], end_idx: int) -> Dict[str, List[dict]]:
    sliced = {}

    for t, bars in benchmark_bars.items():
        if len(bars) > end_idx:
            sliced[t] = bars[: end_idx + 1]
        else:
            sliced[t] = bars[:]

    return sliced


def score_ticker_on_date(
    ticker: str,
    bars: List[dict],
    idx: int,
    benchmark_bars: Dict[str, List[dict]],
) -> Optional[Dict[str, Any]]:
    hist = slice_bars(bars, idx)

    if len(hist) < BACKTEST_MIN_HISTORY:
        return None

    price = close_at(bars, idx)

    if price <= 0:
        return None

    data = {
        "ticker": ticker,
        "bars": hist,
        "price": price,
        "benchmark_bars": benchmark_slice_map(benchmark_bars, idx),
        "insider_buys": [],
        "recent_insider_buy_count": 0,
    }

    signals = {}
    composite = 0.0

    for name, fn in SIGNALS.items():
        try:
            value = float(fn(data))
        except Exception:
            value = 0.0

        signals[name] = value
        composite += value * float(TECHNICAL_WEIGHTS.get(name, 0.0))

    return {
        "ticker": ticker,
        "date": bar_date(bars, idx),
        "idx": idx,
        "price": price,
        "composite": composite,
        "signals": signals,
    }


def choose_test_indices(reference_bars: List[dict]) -> List[int]:
    last_usable = len(reference_bars) - max(HORIZONS) - 1
    first_usable = max(BACKTEST_MIN_HISTORY, last_usable - BACKTEST_DAYS)

    if first_usable >= last_usable:
        return []

    return list(range(first_usable, last_usable + 1, BACKTEST_STEP_DAYS))


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    returns = [r["return_pct"] for r in rows]
    winners = [r for r in rows if r["return_pct"] > 0]
    hit5 = [r for r in rows if r["return_pct"] >= 5.0]
    hit10 = [r for r in rows if r["return_pct"] >= 10.0]

    return {
        "n": len(rows),
        "avg": mean(returns),
        "median": median(returns),
        "win_rate": len(winners) / len(rows) * 100 if rows else None,
        "hit5": len(hit5) / len(rows) * 100 if rows else None,
        "hit10": len(hit10) / len(rows) * 100 if rows else None,
        "best": max(returns) if returns else None,
        "worst": min(returns) if returns else None,
    }


def run_backtest() -> List[Dict[str, Any]]:
    universe = get_universe()

    print_section("CONFIG")
    print(f"Universe size: {len(universe)}")
    print(f"Universe: {', '.join(universe)}")
    print(f"BACKTEST_DAYS: {BACKTEST_DAYS}")
    print(f"BACKTEST_STEP_DAYS: {BACKTEST_STEP_DAYS}")
    print(f"BACKTEST_MIN_HISTORY: {BACKTEST_MIN_HISTORY}")
    print(f"BACKTEST_TOP_N: {BACKTEST_TOP_N}")
    print(f"Horizons: {HORIZONS}")
    print("")
    print("NOTE: This is a historical technical replay, not full SEC/news/insider point-in-time replay.")

    all_bars = fetch_all_bars(universe)
    benchmark_bars = fetch_benchmarks()

    if not all_bars:
        raise SystemExit("No ticker bars loaded")

    reference_ticker = max(all_bars.keys(), key=lambda t: len(all_bars[t]))
    reference_bars = all_bars[reference_ticker]
    test_indices = choose_test_indices(reference_bars)

    print_section("TEST DATES")
    print(f"Reference ticker: {reference_ticker}")
    print(f"Test dates: {len(test_indices)}")

    if test_indices:
        print(f"First test date: {bar_date(reference_bars, test_indices[0])}")
        print(f"Last test date: {bar_date(reference_bars, test_indices[-1])}")

    outcomes = []

    for idx in test_indices:
        scored = []

        for ticker, bars in all_bars.items():
            if idx >= len(bars) - max(HORIZONS):
                continue

            row = score_ticker_on_date(ticker, bars, idx, benchmark_bars)

            if row is not None:
                scored.append(row)

        if not scored:
            continue

        scored.sort(key=lambda r: r["composite"], reverse=True)
        picks = scored[:BACKTEST_TOP_N]

        for rank, pick in enumerate(picks, 1):
            bars = all_bars[pick["ticker"]]

            for horizon in HORIZONS:
                ret = forward_return_pct(bars, idx, horizon)

                if ret is None:
                    continue

                outcomes.append(
                    {
                        "date": pick["date"],
                        "ticker": pick["ticker"],
                        "rank": rank,
                        "horizon": horizon,
                        "return_pct": ret,
                        "composite": pick["composite"],
                        "signals": pick["signals"],
                    }
                )

    return outcomes


def report_overall(outcomes: List[Dict[str, Any]]):
    print_section("OVERALL HISTORICAL TECHNICAL BACKTEST")

    table = []

    for horizon in sorted(set(r["horizon"] for r in outcomes)):
        rows = [r for r in outcomes if r["horizon"] == horizon]
        s = summarize(rows)

        table.append(
            [
                horizon,
                s["n"],
                pct(s["avg"]),
                pct(s["median"]),
                pct(s["win_rate"]),
                pct(s["hit5"]),
                pct(s["hit10"]),
                pct(s["best"]),
                pct(s["worst"]),
            ]
        )

    print_table(
        ["horizon", "n", "avg ret", "median", "win rate", "hit 5%", "hit 10%", "best", "worst"],
        table,
    )


def report_rank_buckets(outcomes: List[Dict[str, Any]]):
    print_section("RANK BUCKETS")

    def bucket(rank: int) -> str:
        if rank <= 3:
            return "1-3"
        if rank <= 5:
            return "4-5"
        return "6-10"

    table = []

    for horizon in sorted(set(r["horizon"] for r in outcomes)):
        h_rows = [r for r in outcomes if r["horizon"] == horizon]

        for b in ["1-3", "4-5", "6-10"]:
            rows = [r for r in h_rows if bucket(r["rank"]) == b]

            if not rows:
                continue

            s = summarize(rows)

            table.append(
                [
                    horizon,
                    b,
                    s["n"],
                    pct(s["avg"]),
                    pct(s["median"]),
                    pct(s["win_rate"]),
                    pct(s["best"]),
                    pct(s["worst"]),
                ]
            )

    print_table(
        ["horizon", "rank bucket", "n", "avg ret", "median", "win rate", "best", "worst"],
        table,
    )


def report_best_worst(outcomes: List[Dict[str, Any]]):
    print_section("BEST / WORST")

    for horizon in sorted(set(r["horizon"] for r in outcomes)):
        rows = [r for r in outcomes if r["horizon"] == horizon]

        worst = sorted(rows, key=lambda r: r["return_pct"])[:10]
        best = sorted(rows, key=lambda r: r["return_pct"], reverse=True)[:10]

        print("")
        print(f"Horizon {horizon} — worst")
        print_table(
            ["date", "ticker", "rank", "return", "comp"],
            [[r["date"], r["ticker"], r["rank"], pct(r["return_pct"]), num(r["composite"])] for r in worst],
        )

        print("")
        print(f"Horizon {horizon} — best")
        print_table(
            ["date", "ticker", "rank", "return", "comp"],
            [[r["date"], r["ticker"], r["rank"], pct(r["return_pct"]), num(r["composite"])] for r in best],
        )


def report_signal_spread(outcomes: List[Dict[str, Any]]):
    print_section("SIGNAL SPREAD DIAGNOSTICS")

    signal_names = sorted(set(k for r in outcomes for k in r.get("signals", {}).keys()))

    for horizon in sorted(set(r["horizon"] for r in outcomes)):
        rows = [r for r in outcomes if r["horizon"] == horizon]

        diag = []

        for sig in signal_names:
            pairs = [
                (safe_float(r["signals"].get(sig), 0.0), r["return_pct"])
                for r in rows
                if sig in r.get("signals", {})
            ]

            if len(pairs) < 20:
                continue

            pairs.sort(key=lambda p: p[0])
            q = max(1, len(pairs) // 4)

            bottom = pairs[:q]
            top = pairs[-q:]

            top_ret = mean([p[1] for p in top])
            bottom_ret = mean([p[1] for p in bottom])
            spread = None

            if top_ret is not None and bottom_ret is not None:
                spread = top_ret - bottom_ret

            diag.append([sig, len(pairs), pct(top_ret), pct(bottom_ret), pct(spread)])

        diag.sort(key=lambda row: abs(safe_float(str(row[4]).replace("%", ""), 0.0)), reverse=True)

        print("")
        print(f"Horizon {horizon}")
        print_table(["signal", "n", "top quartile ret", "bottom quartile ret", "spread"], diag[:20])


def main():
    print("")
    print("Quiet Money Engine Historical Technical Backtest")
    print("Generated:", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    outcomes = run_backtest()

    if not outcomes:
        print("No outcomes produced.")
        return

    report_overall(outcomes)
    report_rank_buckets(outcomes)
    report_best_worst(outcomes)
    report_signal_spread(outcomes)


if __name__ == "__main__":
    main()
