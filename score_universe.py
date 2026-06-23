#!/usr/bin/env python3
"""
Quiet Money Engine — daily cross-sectional scorer (entrypoint).

Fetches price history for the universe, computes every signal, z-scores and
ranks them into a watchlist, then SAVES the ranking to Postgres (history).
Runs as a daily Render Cron Job after market close.
"""
import os
import logging
from datetime import date

from data_layer import get_price_history
from signals import SIGNALS
from scoring import score_universe
from db import init_db, save_watchlist

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scorer")

UNIVERSE = [t.strip().upper() for t in os.getenv(
    "UNIVERSE", "AAPL,MSFT,NVDA,AMD,INTC,F,GM,RIOT,SOFI,PLTR"
).split(",") if t.strip()]


def build_universe_data(tickers: list[str]) -> dict:
    data = {}
    for t in tickers:
        bars = get_price_history(t, days=400)
        if bars:
            data[t] = {"bars": bars}
        else:
            log.warning("No price history for %s; skipping", t)
    return data


def main() -> None:
    init_db()  # safe to call every run; creates tables if missing
    log.info("Scoring %d tickers on signals: %s", len(UNIVERSE), ", ".join(SIGNALS))
    data = build_universe_data(UNIVERSE)
    if not data:
        log.error("No data fetched — check FMP_API_KEY")
        return

    ranked = score_universe(data, SIGNALS)

    # attach rank and persist the whole ranking for today
    rows = []
    for i, row in enumerate(ranked, 1):
        row["rank"] = i
        rows.append(row)
        sig_str = " ".join(f"{n}={z:+.2f}" for n, z in row["signals"].items())
        log.info("%2d. %-6s  composite %+.2f | %s", i, row["ticker"], row["composite"], sig_str)

    save_watchlist(date.today(), rows)
    log.info("Saved %d ranked names to DB for %s", len(rows), date.today())


if __name__ == "__main__":
    main()
