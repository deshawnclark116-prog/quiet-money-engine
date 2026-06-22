#!/usr/bin/env python3
"""
Quiet Money Engine — Render background worker (entrypoint).

Pipeline:
  Stage 1  poll EDGAR for filings
  Stage 2  parse each Form 4, keep only open-market buys
  Stage 3  run each buy's ticker through the tradability gate
Buys are grouped by ticker per cycle: a single insider logs as BUY, while
2+ insiders buying the same name collapse into one CLUSTER line (the stronger
signal), with an officer count so high-conviction setups stand out.
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


def _short_name(full: str) -> str:
    parts = full.split()
    if len(parts) >= 2:
        return parts[0].title() + " " + parts[-1].title()
    return full.title()


def run_once() -> None:
    filings = poll_latest_filings()
    blocked = 0
    clusters = {}  # ticker -> {exchange, cap, vol, buys: [...]}

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

        c = clusters.setdefault(sig["ticker"], {
            "exchange": gate["exchange"],
            "cap": gate["market_cap"],
            "vol": gate["avg_dollar_volume"],
            "buys": [],
        })
        c["buys"].append(sig)
        # >>> Stage 5: persist to your database <<<

    # one line per ticker; clusters (2+ insiders) flagged loud
    for ticker, c in clusters.items():
        buys = c["buys"]
        total = sum(b["buy_value"] for b in buys)
        shares = sum(b["buy_shares"] for b in buys)
        officers = sum(1 for b in buys if any(k in b["role"] for k in ("Officer", "CEO", "CFO", "President", "Chief")))

        if len(buys) >= 2:
            names = ", ".join(_short_name(b["insider"]) for b in buys[:6])
            more = f" +{len(buys) - 6} more" if len(buys) > 6 else ""
            log.info(
                "CLUSTER %-6s [%s] | %d insiders (%d officers) bought %s sh = $%s total | cap $%s  $vol $%s | %s%s",
                ticker, c["exchange"], len(buys), officers,
                f"{shares:,.0f}", f"{total:,.0f}", f"{c['cap']:,.0f}", f"{c['vol']:,.0f}", names, more,
            )
        else:
            b = buys[0]
            log.info(
                "BUY   %-6s [%s] | %s (%s) %s sh @ $%s = $%s | cap $%s  $vol $%s",
                ticker, c["exchange"], b["insider"][:22], b["role"],
                f"{b['buy_shares']:,.0f}", f"{b['buy_price']:,.2f}", f"{b['buy_value']:,.0f}",
                f"{c['cap']:,.0f}", f"{c['vol']:,.0f}",
            )

    passed = sum(len(c["buys"]) for c in clusters.values())
    n_clusters = sum(1 for c in clusters.values() if len(c["buys"]) >= 2)
    log.info("Cycle done: %d filings, %d buys in %d names (%d clusters), %d blocked",
             len(filings), passed, len(clusters), n_clusters, blocked)


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
