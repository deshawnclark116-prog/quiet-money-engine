import os
import psycopg2
from psycopg2.extras import RealDictCursor

# Price-band gate for Quiet Money Engine.
# Purpose: keep the main board focused on cheaper, attainable stocks instead of
# large/expensive names that may be tradable but do not fit the user's mission.

MIN_MAIN_PRICE = float(os.getenv("QME_MIN_MAIN_PRICE", "0.25"))
MAX_MAIN_PRICE = float(os.getenv("QME_MAX_MAIN_PRICE", "15.00"))
MAX_WATCH_PRICE = float(os.getenv("QME_MAX_WATCH_PRICE", "25.00"))

MAIN_ENTRY_STATUSES = {
    "PRE-POP BUY CANDIDATE",
    "WATCH FOR ENTRY",
}

MAIN_PREPOP_STATUSES = {
    "EARLY / CLEAN",
    "EARLY / WAKING",
}

def f(x):
    try:
        return float(x)
    except Exception:
        return None

def is_main_candidate(row):
    return (
        str(row.get("entry_status") or "") in MAIN_ENTRY_STATUSES
        and str(row.get("pre_pop_status") or "") in MAIN_PREPOP_STATUSES
    )

def classify_price(price):
    if price is None or price <= 0:
        return (
            "HIDDEN / NO PRICE CONTEXT",
            "NO PRICE CONTEXT",
            "Price-band gate: missing or invalid price.",
            False,
        )

    if price < MIN_MAIN_PRICE:
        return (
            "WATCH ONLY / MICRO-PENNY RISK",
            "MICRO-PENNY RISK",
            f"Price-band gate: ${price:.2f} is below minimum main-board price ${MIN_MAIN_PRICE:.2f}.",
            False,
        )

    if price <= MAX_MAIN_PRICE:
        return None, None, None, True

    if price <= MAX_WATCH_PRICE:
        return (
            "WATCH ONLY / PRICE STRETCH",
            "PRICE STRETCH / NOT CHEAP",
            f"Price-band gate: ${price:.2f} is above main-board max ${MAX_MAIN_PRICE:.2f}.",
            False,
        )

    return (
        "WATCH ONLY / TOO EXPENSIVE",
        "TOO EXPENSIVE / NOT CHEAP",
        f"Price-band gate: ${price:.2f} is above watch max ${MAX_WATCH_PRICE:.2f}.",
        False,
    )

def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT id, ticker, rank, price_at_signal, entry_status, pre_pop_status, show_on_main
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = [dict(r) for r in cur.fetchall()]

            checked = 0
            changed = 0

            print(
                f"Price-band gate run_date={run_date} rows={len(rows)} "
                f"min_main=${MIN_MAIN_PRICE:.2f} max_main=${MAX_MAIN_PRICE:.2f} max_watch=${MAX_WATCH_PRICE:.2f}"
            )

            for row in rows:
                if not is_main_candidate(row):
                    continue

                checked += 1
                price = f(row.get("price_at_signal"))
                entry_status, pre_pop_status, reason, show_on_main = classify_price(price)

                if not entry_status:
                    continue

                cur.execute(
                    """
                    UPDATE watchlist_scores
                    SET entry_status = %s,
                        pre_pop_status = %s,
                        pre_pop_reason = %s,
                        show_on_main = %s
                    WHERE id = %s
                    """,
                    [entry_status, pre_pop_status, reason, show_on_main, row["id"]],
                )

                print(
                    f"{row['ticker']} rank {row['rank']} price={price} -> "
                    f"{entry_status} | {pre_pop_status}"
                )
                changed += 1

            print(f"Price-band gate checked={checked} changed={changed}")

    con.close()

if __name__ == "__main__":
    main()
