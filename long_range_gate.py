import os
import psycopg2
from psycopg2.extras import RealDictCursor
from data_layer import get_price_history

HARD_PREPOP_STATUSES = {
    "ALREADY POPPED",
    "LATE / HIDE",
    "HIGH RISK",
    "NO PRICE CONTEXT",
}

HARD_ENTRY_PREFIXES = (
    "HIDDEN / ALREADY",
    "HIDDEN / LATE",
    "HIDDEN / HIGH",
    "HIDDEN / NO",
)

def hist(ticker):
    raw = get_price_history(ticker, days=430)
    raw = raw.to_dict("records") if hasattr(raw, "to_dict") else raw

    out = []
    for r in raw:
        try:
            d = str(r.get("date") or r.get("Date") or r.get("datetime") or "")[:10]
            c = float(r.get("close") or r.get("Close"))
            lo = float(r.get("low") or r.get("Low") or c)
            hi = float(r.get("high") or r.get("High") or c)
            if d and c > 0:
                out.append((d, c, lo, hi))
        except Exception:
            pass

    return sorted({x[0]: x for x in out}.values())


def pct(now, old):
    if old is None or old <= 0:
        return None
    return (now / old - 1.0) * 100.0


def gate(ticker):
    rows = hist(ticker)

    if len(rows) < 90:
        return None, None, None, True

    now = rows[-1][1]

    def ret(n):
        if len(rows) <= n:
            return None
        return pct(now, rows[-1 - n][1])

    def rng(n):
        w = rows[-n:] if len(rows) >= n else rows
        lo = min(x[2] for x in w)
        hi = max(x[3] for x in w)
        return pct(now, lo), pct(now, hi)

    r60 = ret(60)
    r90 = ret(90)
    r120 = ret(120)
    r252 = ret(252)

    from120, below120high = rng(120)
    from252, below252high = rng(252)

    near_high = (
        (below120high is not None and below120high >= -10.0)
        or (below252high is not None and below252high >= -10.0)
    )

    fatigue_parts = []

    if r60 is not None and r60 >= 70.0:
        fatigue_parts.append(f"60d return {r60:.1f}%")
    if r90 is not None and r90 >= 100.0:
        fatigue_parts.append(f"90d return {r90:.1f}%")
    if from120 is not None and from120 >= 100.0:
        fatigue_parts.append(f"from 120d low {from120:.1f}%")
    if from252 is not None and from252 >= 125.0:
        fatigue_parts.append(f"from 252d low {from252:.1f}%")

    if fatigue_parts and near_high:
        return (
            "HIDDEN / EXTENDED / FATIGUE RISK",
            "EXTENDED / FATIGUE RISK",
            "; ".join(fatigue_parts) + "; within 10% of multi-month high",
            False,
        )

    repriced_parts = []

    if r60 is not None and r60 >= 45.0:
        repriced_parts.append(f"60d return {r60:.1f}%")
    if r90 is not None and r90 >= 65.0:
        repriced_parts.append(f"90d return {r90:.1f}%")
    if r120 is not None and r120 >= 75.0:
        repriced_parts.append(f"120d return {r120:.1f}%")
    if r252 is not None and r252 >= 100.0:
        repriced_parts.append(f"252d return {r252:.1f}%")
    if from120 is not None and from120 >= 80.0:
        repriced_parts.append(f"from 120d low {from120:.1f}%")
    if from252 is not None and from252 >= 100.0:
        repriced_parts.append(f"from 252d low {from252:.1f}%")

    if repriced_parts:
        return (
            "WATCH ONLY / CONTINUATION",
            "CONTINUATION / REPRICED",
            "; ".join(repriced_parts),
            False,
        )

    return None, None, None, True


def is_existing_hard_reject(row):
    old_entry = str(row.get("entry_status") or "")
    old_pre = str(row.get("pre_pop_status") or "")

    return (
        old_entry.startswith(HARD_ENTRY_PREFIXES)
        or old_pre in HARD_PREPOP_STATUSES
    )


def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT ticker, rank, entry_status, pre_pop_status, show_on_main
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )

            rows = cur.fetchall()
            changed = 0
            preserved = 0

            print(f"Long-range repricing gate run_date={run_date} rows={len(rows)}")

            for row in rows:
                ticker = row["ticker"]

                if row.get("show_on_main") is False and is_existing_hard_reject(row):
                    print(f"{ticker} rank {row['rank']} -> preserved existing hard reject")
                    preserved += 1
                    continue

                entry_status, pre_pop_status, reason, show_on_main = gate(ticker)

                if not entry_status:
                    continue

                cur.execute(
                    """
                    UPDATE watchlist_scores
                    SET entry_status = %s,
                        pre_pop_status = %s,
                        pre_pop_reason = %s,
                        show_on_main = %s
                    WHERE run_date = %s
                      AND ticker = %s
                    """,
                    [
                        entry_status,
                        pre_pop_status,
                        "Long-range repricing gate: " + reason,
                        show_on_main,
                        run_date,
                        ticker,
                    ],
                )

                print(f"{ticker} rank {row['rank']} -> {entry_status} | {reason}")
                changed += 1

            print(f"Long-range gate changed={changed} preserved={preserved}")

    con.close()


if __name__ == "__main__":
    main()
