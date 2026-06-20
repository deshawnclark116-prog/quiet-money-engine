#!/usr/bin/env python3
"""
Quiet Money Engine — Render background worker (entrypoint).

Runs the EDGAR poller on a loop forever. Render keeps this process alive;
each cycle pulls new filings and (for now) logs them. Later stages plug in
exactly where marked below — you won't have to restructure anything.
"""
import os
import time
import logging

from edgar_poller import poll_latest_filings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))


def run_once() -> None:
    filings = poll_latest_filings()
    for f in filings:
        log.info("NEW %s | %s | %s", f["form"], f["filer"][:50], f["index_url"])
        # >>> Stage 2 plugs in here: fetch + parse the filing's primary doc <<<
        # >>> Stage 3: run it through the tradability gate <<<
        # >>> Stage 5: persist to your database <<<


def main() -> None:
    log.info("Quiet Money worker starting; polling every %ss", POLL_INTERVAL)
    while True:
        try:
            run_once()
        except Exception:
            log.exception("Cycle failed; will retry next interval")  # one bad cycle won't kill the worker
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
