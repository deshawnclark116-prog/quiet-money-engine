#!/usr/bin/env python3
"""
Quiet Money Engine — Pre-Pop Data Integrity Audit.

Purpose:
Before deploying a pre-pop gate into production, verify that the lab and
watchlist are using the same price history.

This checks:
1. watchlist_scores.price_at_signal vs data_layer bar close on/near run_date
2. stored pre-alert returns vs recomputed returns
3. case-study ticker recent bars
4. whether production should be blocked due to price mismatch

This script does NOT change production.
"""

import os
from datetime import date, datetime, timedelta
from typing import Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from data_layer import get_price_history


DATABASE_URL = os.getenv("DATABASE_URL")

AUDIT_DAYS_BACK = int(os.getenv("AUDIT_DAYS_BACK", "10"))
AUDIT_LIMIT = int(os.getenv("AUDIT_LIMIT", "150"))
PRICE_HISTORY_DAYS = int(os.getenv("AUDIT_PRICE_HISTORY_DAYS", "520"))

CASE_TICKERS = [
    t.strip().upper()
    for t in os.getenv(
        "AUDIT_CASE_TICKERS",
        "BOLD,LILA,GDC,FTH,TOI,ARTV,CGON,IMRX,MRBK,IX,FOEL,PLUG,AEHR,INTC,AMD,RIOT,SOFI",
    ).split(",")
    if t.strip()
]

PRICE_MISMATCH_WARN_PCT = float(os.getenv("PRICE_MISMATCH_WARN_PCT", "3.0"))
PRICE_MISMATCH_BLOCK_PCT = float(os.getenv("PRICE_MISMATCH_BLOCK_PCT", "8.0"))
RETURN_MISMATCH_WARN_PCT = float(os.getenv("RETURN_MISMATCH_WARN_PCT", "3.0"))


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def parse_date(value: Any) -> Optional[date]:
    if value is None:
        return None

    if isinstance(value, date):
        return value

    s = str(value)

    if "T" in s:
        s = s.split("T")[0]

    s = s[:10]

    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def normalize_bars(raw_bars: list[dict]) -> list[dict]:
    out = []

    for bar in raw_bars or []:
        d = None

        for key in ["date", "datetime", "timestamp", "time"]:
            if key in bar:
                d = parse_date(bar.get(key))
                if d:
                    break

        close = safe_float(bar.get("close"), 0.0)

        if not d or close is None or close <= 0:
            continue

        out.append(
            {
                "date": d,
                "date_str": d.isoformat(),
                "open": safe_float(bar.get("open"), close),
                "high": safe_float(bar.get("high"), close),
                "low": safe_float(bar.get("low"), close),
                "close": close,
                "volume": safe_float(bar.get("volume"), 0.0),
            }
        )

    out.sort(key=lambda x: x["date"])
    return out


def pct(now: Optional[float], then: Optional[float]) -> Optional[float]:
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1.0) * 100.0


def fmt_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def find_bar_on_or_before(bars: list[dict], target: date) -> Optional[int]:
    idx = None

    for i, bar in enumerate(bars):
        if bar["date"] <= target:
            idx = i
        else:
            break

    return idx


def recompute_context(bars: list[dict], idx: int, signal_price: Optional[float]) -> dict:
    closes = [safe_float(b.get("close"), 0.0) for b in bars]

    signal = safe_float(signal_price, None)

    if signal is None or signal <= 0 or idx is None or idx < 20:
        return {
            "calc_1d": None,
            "calc_3d": None,
            "calc_5d": None,
            "calc_10d": None,
            "calc_vs20": None,
        }

    c1 = closes[idx - 1] if idx >= 1 else None
    c3 = closes[idx - 3] if idx >= 3 else None
    c5 = closes[idx - 5] if idx >= 5 else None
    c10 = closes[idx - 10] if idx >= 10 else None

    sma20_vals = closes[idx - 19 : idx + 1]
    sma20 = sum(sma20_vals) / len(sma20_vals) if len(sma20_vals) == 20 else None

    return {
        "calc_1d": pct(signal, c1),
        "calc_3d": pct(signal, c3),
        "calc_5d": pct(signal, c5),
        "calc_10d": pct(signal, c10),
        "calc_vs20": pct(signal, sma20),
    }


def fetch_watchlist_rows() -> list[dict]:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")

    cutoff = date.today() - timedelta(days=AUDIT_DAYS_BACK)

    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        run_date,
                        ticker,
                        rank,
                        composite,
                        price_at_signal,
                        entry_status,
                        pre_pop_status,
                        show_on_main,
                        pre_alert_return_1d,
                        pre_alert_return_3d,
                        pre_alert_return_5d,
                        pre_alert_return_10d,
                        distance_from_sma20
                    FROM watchlist_scores
                    WHERE run_date >= %s
                    ORDER BY run_date DESC, rank ASC
                    LIMIT %s
                    """,
                    [cutoff, AUDIT_LIMIT],
                )
                return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def latest_watchlist_by_ticker() -> dict:
    rows = fetch_watchlist_rows()
    out = {}

    for row in rows:
        t = str(row.get("ticker") or "").upper().strip()

        if t and t not in out:
            out[t] = row

    return out


def fetch_bars_cached(ticker: str, cache: dict) -> list[dict]:
    ticker = ticker.upper().strip()

    if ticker in cache:
        return cache[ticker]

    try:
        bars = normalize_bars(get_price_history(ticker, days=PRICE_HISTORY_DAYS))
    except Exception as exc:
        print(f"{ticker:<7} PRICE FETCH FAILED: {exc}")
        bars = []

    cache[ticker] = bars
    return bars


def audit_watchlist_alignment() -> dict:
    rows = fetch_watchlist_rows()
    cache = {}

    total = 0
    missing = 0
    warn_price = 0
    block_price = 0
    warn_return = 0

    print()
    print("WATCHLIST PRICE / RETURN ALIGNMENT AUDIT")
    print("=" * 150)
    print(
        "date       | ticker | rk | db_price | bar_date   | bar_close | px_diff | stored5 | calc5  | stored10 | calc10 | status | entry"
    )
    print("-" * 150)

    for row in rows:
        total += 1

        ticker = str(row.get("ticker") or "").upper().strip()
        run_date = parse_date(row.get("run_date"))
        db_price = safe_float(row.get("price_at_signal"), None)

        if not ticker or not run_date or db_price is None or db_price <= 0:
            missing += 1
            print(f"{str(row.get('run_date')):<10} | {ticker:<6} | missing required DB fields")
            continue

        bars = fetch_bars_cached(ticker, cache)
        idx = find_bar_on_or_before(bars, run_date) if bars else None

        if idx is None:
            missing += 1
            print(f"{run_date.isoformat():<10} | {ticker:<6} | no historical bar on/before run_date")
            continue

        bar = bars[idx]
        bar_close = safe_float(bar.get("close"), None)
        px_diff = pct(db_price, bar_close)

        ctx = recompute_context(bars, idx, db_price)

        stored5 = safe_float(row.get("pre_alert_return_5d"), None)
        stored10 = safe_float(row.get("pre_alert_return_10d"), None)

        calc5 = ctx["calc_5d"]
        calc10 = ctx["calc_10d"]

        ret_mismatch = 0.0

        if stored5 is not None and calc5 is not None:
            ret_mismatch = max(ret_mismatch, abs(stored5 - calc5))

        if stored10 is not None and calc10 is not None:
            ret_mismatch = max(ret_mismatch, abs(stored10 - calc10))

        status = "OK"

        if px_diff is None:
            status = "NO_PX_DIFF"
        elif abs(px_diff) >= PRICE_MISMATCH_BLOCK_PCT:
            status = "BLOCK_PRICE_MISMATCH"
            block_price += 1
        elif abs(px_diff) >= PRICE_MISMATCH_WARN_PCT:
            status = "WARN_PRICE_MISMATCH"
            warn_price += 1

        if ret_mismatch >= RETURN_MISMATCH_WARN_PCT:
            warn_return += 1
            if status == "OK":
                status = "WARN_RETURN_MISMATCH"
            else:
                status += "+RETURN"

        print(
            f"{run_date.isoformat():<10} | "
            f"{ticker:<6} | "
            f"{int(safe_float(row.get('rank'), 0)):>2d} | "
            f"{fmt_price(db_price):>8s} | "
            f"{bar['date_str']:<10} | "
            f"{fmt_price(bar_close):>9s} | "
            f"{fmt_pct(px_diff):>7s} | "
            f"{fmt_pct(stored5):>7s} | "
            f"{fmt_pct(calc5):>6s} | "
            f"{fmt_pct(stored10):>8s} | "
            f"{fmt_pct(calc10):>6s} | "
            f"{status:<22s} | "
            f"{row.get('entry_status')}"
        )

    print()
    print("AUDIT SUMMARY")
    print("-" * 80)
    print("rows_checked:", total)
    print("missing_or_unmatched:", missing)
    print("warn_price_mismatch:", warn_price)
    print("block_price_mismatch:", block_price)
    print("warn_return_mismatch:", warn_return)

    production_blocked = block_price > 0 or warn_return > 0

    if production_blocked:
        print()
        print("PRODUCTION STATUS: BLOCKED")
        print("Reason: data alignment mismatch must be fixed before trusting the pre-pop gate.")
    else:
        print()
        print("PRODUCTION STATUS: DATA ALIGNMENT PASSED")
        print("Next step may proceed to production gate patch review.")

    return {
        "rows_checked": total,
        "missing_or_unmatched": missing,
        "warn_price_mismatch": warn_price,
        "block_price_mismatch": block_price,
        "warn_return_mismatch": warn_return,
        "production_blocked": production_blocked,
    }


def print_case_ticker_bars() -> None:
    cache = {}
    latest_db = latest_watchlist_by_ticker()

    print()
    print("CASE-STUDY RAW PRICE BARS")
    print("=" * 120)

    for ticker in CASE_TICKERS:
        bars = fetch_bars_cached(ticker, cache)

        print()
        print(ticker)
        print("-" * 120)

        row = latest_db.get(ticker)

        if row:
            run_date = parse_date(row.get("run_date"))
            db_price = safe_float(row.get("price_at_signal"), None)
            idx = find_bar_on_or_before(bars, run_date) if run_date and bars else None
            bar = bars[idx] if idx is not None else None
            bar_close = safe_float(bar.get("close"), None) if bar else None
            diff = pct(db_price, bar_close)

            print(
                "latest_db:",
                f"run_date={run_date}",
                f"rank={row.get('rank')}",
                f"price_at_signal={fmt_price(db_price)}",
                f"bar_date={bar['date_str'] if bar else 'n/a'}",
                f"bar_close={fmt_price(bar_close)}",
                f"diff={fmt_pct(diff)}",
                f"entry={row.get('entry_status')}",
                f"prepop={row.get('pre_pop_status')}",
            )
        else:
            print("latest_db: no recent watchlist row")

        if not bars:
            print("no bars")
            continue

        print("last 15 bars:")
        for b in bars[-15:]:
            print(
                f"  {b['date_str']} close={fmt_price(b['close'])} "
                f"high={fmt_price(b['high'])} low={fmt_price(b['low'])} "
                f"vol={int(safe_float(b['volume'], 0))}"
            )


def main() -> None:
    result = audit_watchlist_alignment()
    print_case_ticker_bars()

    print()
    print("FINAL DECISION")
    print("=" * 80)

    if result["production_blocked"]:
        print("Do NOT deploy production scoring changes yet.")
        print("First fix the data mismatch or prove the mismatch is expected adjustment behavior.")
    else:
        print("Data alignment is clean enough to continue.")
        print("Next step: production pre-pop gate patch can be reviewed/deployed.")


if __name__ == "__main__":
    main()
