#!/usr/bin/env python3
"""
Quiet Money Engine — the quiet-money detectors.

These are the edge layers. Price/volume hygiene (chart shape, wake-up)
defines WHERE the quiet base is; these detect WHO is quietly positioning
in it. Both are pure functions over data the pipeline already collects,
so they are unit-testable without a database or network.

1. absorption_score(closes, vols)
   Finds absorption days: volume far above the stock's own norm while
   price barely moves — someone eating every share offered without
   letting the price run. Repeated absorption inside the lower half of
   the yearly range is the fingerprint of quiet accumulation. Retail
   scanners flag price moves; they do not flag volume WITHOUT price
   movement. 0-25 points.

2. insider_cluster_score(buys, current_price)
   Reads the open-market insider buys (transaction code P only — their
   own cash) that edgar_poller/form4_parser already store: multiple
   DISTINCT insiders buying within a tight window, executive roles
   weighted above directors, meaningful dollar size, at prices near the
   current price (their entry is still buyable). A cluster is people
   with non-public context putting salary on the line. 0-30 points —
   deliberately the highest-capped signal in the stack, because it is
   the most literal form of quiet money.
"""

from datetime import date, datetime, timezone

ABSORPTION_VOL_MULT = 2.5        # day volume >= this x 60d median volume
ABSORPTION_MAX_MOVE_PCT = 1.5    # ...while |close change| stays under this
ABSORPTION_LOOKBACK = 40         # recent window scanned for absorption days
ABSORPTION_RANGE_BARS = 252      # yearly range used for the low-half bonus

CLUSTER_WINDOW_DAYS = 90         # buys older than this are ignored
CLUSTER_TIGHT_DAYS = 14          # 2+ distinct insiders within this = cluster
PROXIMITY_MAX_PCT = 20.0         # current price within this % of their avg buy


def _pct(now, old):
    if now is None or old is None or old <= 0:
        return None
    return (now / old - 1.0) * 100.0


def _median(values):
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def absorption_score(closes, vols):
    """Return (points 0-25, detail dict)."""
    n = len(closes)

    if n < 80 or len(vols) != n:
        return 0.0, None

    range_window = closes[-ABSORPTION_RANGE_BARS:]
    range_lo = min(range_window)
    range_hi = max(range_window)
    range_mid = range_lo + 0.5 * (range_hi - range_lo)

    days = []

    start = max(61, n - ABSORPTION_LOOKBACK)
    for i in range(start, n):
        base_vols = [v for v in vols[i - 60:i] if v > 0]
        if len(base_vols) < 20:
            continue

        med = _median(base_vols)
        if med <= 0 or vols[i] < ABSORPTION_VOL_MULT * med:
            continue

        move = _pct(closes[i], closes[i - 1])
        if move is None or abs(move) > ABSORPTION_MAX_MOVE_PCT:
            continue

        in_lower_half = closes[i] <= range_mid
        days.append(
            {
                "index_from_end": n - 1 - i,
                "vol_mult": vols[i] / med,
                "move_pct": move,
                "in_lower_half": in_lower_half,
            }
        )

    if not days:
        return 0.0, {"absorption_days": 0}

    # 10 points for the strongest absorption day, 7 for the second, 5 for
    # the third. Quiet accumulation matters most near lows: a day in the
    # lower half of the yearly range earns full credit, upper half only
    # 40% (block prints near highs are often mechanics, not accumulation).
    points = 0.0
    ladder = [10.0, 7.0, 5.0]
    for rank, day in enumerate(sorted(days, key=lambda d: -d["vol_mult"])):
        base = ladder[rank] if rank < len(ladder) else 2.0
        points += base * (1.0 if day["in_lower_half"] else 0.4)

    points = min(points, 25.0)

    biggest = max(days, key=lambda d: d["vol_mult"])
    detail = {
        "absorption_days": len(days),
        "lower_half_days": sum(1 for d in days if d["in_lower_half"]),
        "biggest_vol_mult": round(biggest["vol_mult"], 1),
        "biggest_move_pct": round(biggest["move_pct"], 2),
        "most_recent_bars_ago": min(d["index_from_end"] for d in days),
    }
    return points, detail


def _parse_when(buy):
    """Best-effort filing/seen date for recency math."""
    for key in ("filed_at", "seen_at"):
        raw = buy.get(key)
        if not raw:
            continue
        s = str(raw)[:10]
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            continue
    return None


def _role_weight(role):
    r = str(role or "").lower()
    if any(k in r for k in ("ceo", "cfo", "chief", "president")):
        return 2.0
    if "10%" in r:
        return 1.5
    if "director" in r:
        return 1.2
    return 1.0


def insider_cluster_score(buys, current_price, today=None, avg_dollar_volume=None):
    """Return (points 0-30, detail dict).

    buys: rows shaped like the insider_buys table (insider, role, value,
    price, filed_at/seen_at). Only recent rows count; the score rises
    with distinct insiders, executive weight, tight timing, dollar size,
    and whether their entry price is still near the current price.

    avg_dollar_volume: the stock's average daily dollar volume. A buy is
    conviction only relative to the stock's own size — $150K in a $50M
    microcap is a signal, $150K in a mega-cap is noise — so when
    liquidity is known, the whole score is scaled by how meaningful the
    total purchase is against one day's trading.
    """
    today = today or date.today()

    recent = []
    for b in buys or []:
        when = _parse_when(b)
        if when is None:
            continue
        age = (today - when).days
        if 0 <= age <= CLUSTER_WINDOW_DAYS:
            recent.append({**b, "_when": when, "_age": age})

    if not recent:
        return 0.0, None

    by_insider = {}
    for b in recent:
        name = str(b.get("insider") or "unknown").strip().lower()
        by_insider.setdefault(name, []).append(b)

    distinct = len(by_insider)
    weighted_heads = sum(
        max(_role_weight(b.get("role")) for b in rows)
        for rows in by_insider.values()
    )

    # Distinct weighted insiders: 1 -> ~6, 2 -> ~14, 3+ -> up to 20.
    if weighted_heads >= 3.5:
        head_pts = 20.0
    elif weighted_heads >= 2.0:
        head_pts = 14.0 + (weighted_heads - 2.0) / 1.5 * 6.0
    else:
        head_pts = 6.0 * weighted_heads

    # Tight cluster: two+ DISTINCT insiders within CLUSTER_TIGHT_DAYS.
    tight = False
    if distinct >= 2:
        dates = sorted(rows[0]["_when"] for rows in by_insider.values())
        for a, b in zip(dates, dates[1:]):
            if (b - a).days <= CLUSTER_TIGHT_DAYS:
                tight = True
                break
    tight_pts = 5.0 if tight else 0.0

    # Dollar size: insiders risking real money, not token buys.
    total_value = sum(float(b.get("value") or 0) for b in recent)
    if total_value >= 500_000:
        size_pts = 5.0
    elif total_value >= 100_000:
        size_pts = 3.0
    elif total_value >= 25_000:
        size_pts = 1.5
    else:
        size_pts = 0.0

    # Proximity: their average entry is still near the current price,
    # so the signal is still actionable rather than long gone.
    prox_pts = 0.0
    avg_price = None
    priced = [b for b in recent if float(b.get("price") or 0) > 0 and float(b.get("value") or 0) > 0]
    if priced and current_price:
        total_v = sum(float(b["value"]) for b in priced)
        total_sh = sum(float(b["value"]) / float(b["price"]) for b in priced)
        if total_sh > 0:
            avg_price = total_v / total_sh
            drift = _pct(current_price, avg_price)
            if drift is not None and abs(drift) <= PROXIMITY_MAX_PCT:
                prox_pts = 5.0 * (1.0 - abs(drift) / PROXIMITY_MAX_PCT)

    # Core cluster evidence caps at 25; proximity ("their entry is still
    # buyable") supplies the final 5, so a cluster whose price already ran
    # away can never reach a full score.
    points = min(head_pts + tight_pts + size_pts, 25.0) + prox_pts

    # Meaningfulness scaling: total purchases at >= 25% of one day's
    # dollar volume earn full credit, fading to a 20% floor for buys that
    # are rounding errors against the stock's own liquidity.
    liquidity = avg_dollar_volume
    if not liquidity:
        row_liq = [float(b.get("avg_dollar_vol") or 0) for b in recent]
        liquidity = max(row_liq) if any(row_liq) else 0.0

    meaning_mult = 1.0
    if liquidity and liquidity > 0:
        meaning_mult = max(0.2, min(1.0, total_value / (0.25 * liquidity)))
        points *= meaning_mult

    detail = {
        "meaning_mult": round(meaning_mult, 2),
        "distinct_insiders": distinct,
        "weighted_heads": round(weighted_heads, 1),
        "tight_cluster": tight,
        "total_value": round(total_value),
        "avg_buy_price": round(avg_price, 4) if avg_price else None,
        "newest_days_ago": min(b["_age"] for b in recent),
        "roles": sorted({str(b.get("role") or "insider") for b in recent}),
    }
    return points, detail


def describe_quiet_money(absorption, absorption_detail, cluster, cluster_detail):
    """One plain-English sentence per detector, for entry_reason strings."""
    parts = []

    if absorption_detail and absorption_detail.get("absorption_days"):
        d = absorption_detail
        parts.append(
            f"{d['absorption_days']} absorption day(s) — up to "
            f"{d['biggest_vol_mult']}x volume moving price only "
            f"{d['biggest_move_pct']:+.1f}%"
            + (f", {d['lower_half_days']} in the lower half of the range"
               if d.get("lower_half_days") else "")
        )

    if cluster_detail:
        d = cluster_detail
        parts.append(
            f"{d['distinct_insiders']} insider(s) bought "
            f"${d['total_value']:,} open-market in 90d"
            + (" in a tight cluster" if d.get("tight_cluster") else "")
            + (f" (roles: {', '.join(d['roles'][:3])})" if d.get("roles") else "")
        )

    return "; ".join(parts) if parts else "no quiet-money evidence yet"
