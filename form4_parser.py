#!/usr/bin/env python3
"""
Quiet Money Engine — Stage 2: Form 4 signal parser.

Takes a filing record from Stage 1, fetches the Form 4 ownership XML, and keeps
ONLY open-market purchases (transaction code "P") — i.e. insiders spending their
own cash. Grants, option exercises, sells, and gifts are dropped, because those
aren't the signal.

Returns one aggregated buy-signal per filing (or None if it held no buys),
including the ticker — which Stage 3's tradability gate needs.

Reuses the EDGAR_CONTACT you already set on Render. No new env var required.
"""
import os
import time
import logging
import xml.etree.ElementTree as ET

import requests

log = logging.getLogger("form4_parser")

EDGAR_CONTACT = os.getenv("EDGAR_CONTACT", "Quiet Money Engine admin@example.com")

# Politeness throttle: stay comfortably under SEC's 10 req/sec ceiling.
_MIN_INTERVAL = float(os.getenv("EDGAR_MIN_INTERVAL", "0.15"))
_last_request = 0.0


def _polite_get(url: str) -> requests.Response:
    global _last_request
    wait = _MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.monotonic()
    resp = requests.get(url, headers={"User-Agent": EDGAR_CONTACT}, timeout=20)
    resp.raise_for_status()
    return resp


def _get(parent, path: str) -> str:
    """Text of an element, transparently unwrapping a nested <value> child."""
    if parent is None:
        return ""
    el = parent.find(path)
    if el is None:
        return ""
    v = el.find("value")
    if v is not None and v.text:
        return v.text.strip()
    return (el.text or "").strip()


def parse_form4_xml(xml_bytes: bytes) -> dict | None:
    """Parse ownership XML -> aggregated open-market BUY signal, or None."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    if root.tag != "ownershipDocument":
        return None

    issuer = root.find("issuer")
    ticker = _get(issuer, "issuerTradingSymbol")
    issuer_name = _get(issuer, "issuerName")

    owner = root.find("reportingOwner")
    insider = _get(owner, "reportingOwnerId/rptOwnerName") if owner is not None else ""
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None
    roles = []
    if rel is not None:
        if _get(rel, "isOfficer") in ("1", "true"):
            roles.append(_get(rel, "officerTitle") or "Officer")
        if _get(rel, "isDirector") in ("1", "true"):
            roles.append("Director")
        if _get(rel, "isTenPercentOwner") in ("1", "true"):
            roles.append("10% Owner")

    # Keep ONLY code "P" = open-market purchase. This is the whole edge:
    # the insider chose to spend their own money buying shares on the market.
    total_shares = 0.0
    total_value = 0.0
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _get(txn, "transactionCoding/transactionCode") != "P":
            continue
        try:
            shares = float(_get(txn, "transactionAmounts/transactionShares") or 0)
            price = float(_get(txn, "transactionAmounts/transactionPricePerShare") or 0)
        except ValueError:
            continue
        total_shares += shares
        total_value += shares * price

    if total_shares <= 0:
        return None  # this Form 4 had no open-market buys -> not a signal

    return {
        "ticker": ticker,
        "issuer": issuer_name,
        "insider": insider,
        "role": ", ".join(roles) or "insider",
        "buy_shares": total_shares,
        "buy_price": total_value / total_shares if total_shares else 0.0,
        "buy_value": total_value,
    }


def _find_form4_xml_url(cik: str, accession: str) -> str | None:
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}"
    items = _polite_get(f"{base}/index.json").json().get("directory", {}).get("item", [])
    xmls = [it["name"] for it in items if it.get("name", "").lower().endswith(".xml")]
    if not xmls:
        return None
    for name in xmls:
        if name.lower() == "primary_doc.xml":  # modern standard name for ownership XML
            return f"{base}/{name}"
    return f"{base}/{xmls[0]}"


def fetch_form4_signal(filing: dict) -> dict | None:
    """End-to-end: a Stage 1 filing record -> buy signal (or None). Network-bound."""
    cik, accession = filing.get("cik"), filing.get("accession")
    if not cik or not accession:
        return None
    try:
        xml_url = _find_form4_xml_url(cik, accession)
        if not xml_url:
            return None
        signal = parse_form4_xml(_polite_get(xml_url).content)
    except (requests.RequestException, ValueError) as exc:
        log.warning("Could not parse %s: %s", accession, exc)
        return None
    if signal:
        signal["accession"] = accession
        signal["filed_at"] = filing.get("filed_at", "")
    return signal
