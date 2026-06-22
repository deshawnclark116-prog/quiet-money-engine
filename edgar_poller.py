#!/usr/bin/env python3
"""
Quiet Money Engine — Stage 1: EDGAR filing poller.

Pulls the most recent filings from SEC EDGAR's "get current" feed, keeps only
the form types that carry a quiet-money signal (insider open-market buys and
5%+ ownership stakes), de-duplicates against what we've already seen, and
returns structured filing records for the next stage to enrich.

SEC fair-access rules are baked in:
  - A descriptive User-Agent with contact info is REQUIRED. No UA -> 403.
  - Stay under 10 requests/sec. We poll once per cycle so this is trivial.

Run it directly to watch live filings stream in:
    EDGAR_CONTACT="Your Name your@email.com" python edgar_poller.py

Requires Python 3.10+ and: pip install requests feedparser
"""

import os
import re
import json
import logging
from pathlib import Path

import requests
import feedparser

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("edgar_poller")

# --- Config (env-var driven) -------------------------------------------------

# SEC REQUIRES this. Put a real "Name email@domain". Without it EDGAR 403s you.
EDGAR_CONTACT = os.getenv("EDGAR_CONTACT", "Quiet Money Engine admin@example.com")

# Form types that carry the signal. Change THIS ONE LINE to repoint the engine.
#   4         insider transactions (buys AND sells -> Stage 2 splits them)
#   SC 13D    activist / control-intent 5%+ stake   (+ /A amendments)
#   SC 13G    passive 5%+ stake                     (+ /A amendments)
WATCH_FORMS = [
    f.strip()
    for f in os.getenv("WATCH_FORMS", "4,SC 13D,SC 13D/A,SC 13G,SC 13G/A").split(",")
    if f.strip()
]

# How many recent entries to pull per cycle (EDGAR caps this feed at 100).
POLL_COUNT = int(os.getenv("EDGAR_POLL_COUNT", "100"))

# Where we remember what we've already processed. In the deployed worker this
# becomes a lookup against your `filings` table; for the standalone test a
# local file is enough.
SEEN_PATH = Path(os.getenv("EDGAR_SEEN_PATH", ".edgar_seen.json"))

EDGAR_CURRENT_URL = "https://www.sec.gov/cgi-bin/browse-edgar"


def _headers() -> dict:
    return {"User-Agent": EDGAR_CONTACT, "Accept-Encoding": "gzip, deflate"}


def _load_seen() -> set:
    if SEEN_PATH.exists():
        try:
            return set(json.loads(SEEN_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            log.warning("Could not read %s, starting fresh", SEEN_PATH)
    return set()


def _save_seen(seen: set) -> None:
    SEEN_PATH.write_text(json.dumps(list(seen)[-5000:]))  # cap growth


def _accession_from_entry(entry) -> str | None:
    # getcurrent puts it in the <id>: "...accession-number=0001234567-25-000123"
    eid = entry.get("id", "")
    if "accession-number=" in eid:
        return eid.split("accession-number=", 1)[1].strip()
    # fallback: scan the link for a dddddddddd-dd-dddddd pattern
    m = re.search(r"\d{10}-\d{2}-\d{6}", entry.get("link", ""))
    return m.group(0) if m else None


def _cik_from_entry(entry) -> str | None:
    m = re.search(r"CIK=(\d+)", entry.get("link", ""))
    if m:
        return m.group(1)
    m = re.search(r"\((\d{10})\)", entry.get("title", ""))  # "(0001234567)"
    return m.group(1) if m else None


def _build_index_url(cik: str | None, accession: str) -> str:
    # The canonical filing-index URL Stage 2 will use to find the primary doc.
    if not cik:
        return ""
    acc_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/{accession}-index.htm"


def _form_of(entry) -> str:
    # Prefer the <category term="...">, fall back to the title prefix.
    if getattr(entry, "tags", None):
        term = (entry.tags[0].get("term") or "").strip()
        if term:
            return term
    title = entry.get("title", "")
    return title.split(" - ", 1)[0].strip() if " - " in title else ""


def parse_feed(content: bytes) -> list[dict]:
    """Pure parse: atom bytes -> filing records for our watched forms. No I/O."""
    feed = feedparser.parse(content)
    out: list[dict] = []
    for entry in feed.entries:
        form = _form_of(entry)
        if form not in WATCH_FORMS:
            continue
        accession = _accession_from_entry(entry)
        if not accession:
            continue
        cik = _cik_from_entry(entry)
        title = entry.get("title", "")
        filer = title.split(" - ", 1)[-1].strip() if " - " in title else title
        out.append(
            {
                "accession": accession,
                "form": form,
                "filer": filer,
                "cik": cik,
                "filed_at": entry.get("updated", ""),
                "index_url": _build_index_url(cik, accession),
            }
        )
    return out


def poll_latest_filings() -> list[dict]:
    """Fetch the live feed, drop anything we've already processed, return the new."""
    params = {
        "action": "getcurrent",
        "type": "",
        "company": "",
        "dateb": "",
        "owner": "include",
        "count": str(POLL_COUNT),
        "output": "atom",
    }
    resp = requests.get(EDGAR_CURRENT_URL, params=params, headers=_headers(), timeout=20)
    resp.raise_for_status()

    records = parse_feed(resp.content)
    seen = _load_seen()
    new = []
    for r in records:
        if r["accession"] in seen:
            continue  # skip: seen in an earlier cycle OR a duplicate within this same feed
        seen.add(r["accession"])  # mark immediately so a repeat later in THIS batch is caught
        new.append(r)
    if new:
        _save_seen(seen)

    log.info("Parsed %d watched filings, %d new this cycle", len(records), len(new))
    return new


if __name__ == "__main__":
    if "@" not in EDGAR_CONTACT:
        log.warning("Set EDGAR_CONTACT to a real 'Name email@domain' or SEC will 403 you.")
    filings = poll_latest_filings()
    for f in filings:
        print(f"{f['form']:<9} {f['filer'][:42]:<42} {f['accession']}")
        print(f"          {f['index_url']}")
    if not filings:
        print("No new watched filings this cycle (or all already seen). Run again shortly.")
