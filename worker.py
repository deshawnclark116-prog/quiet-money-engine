#!/usr/bin/env python3
"""
Quiet Money Engine — Render background worker (entrypoint).

Stage 1 pulls filings; Stage 2 parses each Form 4 and keeps only open-market
buys. The logs now show real insider purchases with dollar amounts instead of
a wall of every Form 4. Later stages plug in exactly where marked.
"""
import os
import time
import logging

from edgar_poller import poll_latest_filings
from form4_parser import fetch_form4_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


def run_once() -> None:
    filings = poll_latest_filings()
    buys = 0
    for f in filings:
        if f["form"] == "4":
            sig = fetch_form4_signal(f)
            if sig:
                buys += 1
                log.info(
                    "BUY  %-6s | %s (%s) bought %s sh @ $%s = $%s",
                    sig["ticker"] or "?",
                    sig["insider"][:26],
                    sig["role"],
                    f"{sig['buy_shares']:,.0f}",
                    f"{sig['buy_price']:,.2f}",
                    f"{sig['buy_value']:,.0f}",
                )
                # >>> Stage 3: run sig["ticker"] through the tradability gate <<<
                # >>> Stage 4: score it   |   Stage 5: persist to your database <<<
            # Form 4 with no open-market buy (grant/sale/exercise) -> silently skipped
        else:
            # 13D / 13G stake filings — parser for these is the quick next add-on.
            log.info("STAKE %s | %s | %s", f["form"], f["filer"][:40], f["index_url"])

    log.info("Cycle done: %d filings in, %d real open-market buys", len(filings), buys)


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
