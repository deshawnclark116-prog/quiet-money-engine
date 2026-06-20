#!/usr/bin/env python3
"""
Quiet Money Engine — Render background worker (entrypoint).

Pipeline so far:
  Stage 1  poll EDGAR for filings
  Stage 2  parse each Form 4, keep only open-market buys
  Stage 3  run each buy's ticker through the tradability gate
The logs now show only buys on Nasdaq / NYSE / NYSE American that clear the
floors, each with its exchange, market cap, and average dollar volume. Blocked
names are logged with the reason so you can see exactly what got filtered.
"""
import os
import time
import logging

from edgar_poller import poll_latest_filings
from form4_parser import fetch_form4_signal
from tradability_gate import evaluate as gate_check

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


def run_once() -> None:
    filings = poll_latest_filings()
    passed = 0
    blocked = 0
    for f in filings:
        if f["form"] != "4":
            # 13D / 13G stake filings — parser for these is the quick next add-on.
            log.info("STAKE %s | %s | %s", f["form"], f["filer"][:40], f["index_url"])
            continue

        sig = fetch_form4_signal(f)
        if not sig:
            continue  # Form 4 with no open-market buy (grant/sale/exercise)

        gate = gate_check(sig["ticker"])
        if not gate["allowed"]:
            blocked += 1
            log.info("SKIP  %-6s | %s | %s", sig["ticker"] or "?", sig["insider"][:20], gate["reason"])
            continue

        passed += 1
        log.info(
            "BUY   %-6s [%s] | %s (%s) %s sh @ $%s = $%s | cap $%s  $vol $%s",
            sig["ticker"], gate["exchange"],
            sig["insider"][:22], sig["role"],
            f"{sig['buy_shares']:,.0f}", f"{sig['buy_price']:,.2f}", f"{sig['buy_value']:,.0f}",
            f"{gate['market_cap']:,.0f}", f"{gate['avg_dollar_volume']:,.0f}",
        )
        # >>> Stage 4: score it   |   Stage 5: persist to your database <<<

    log.info("Cycle done: %d filings, %d buys passed gate, %d blocked as untradable",
             len(filings), passed, blocked)


def main() -> None:
    log.info("Quiet Money worker starting; polling every %ss", POLL_INTERVAL)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Cycle failed; will retry next interval")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
