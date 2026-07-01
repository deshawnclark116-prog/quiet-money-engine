import os
import psycopg2
from psycopg2.extras import RealDictCursor

MAIN_TARGET = int(os.getenv("QME_MAIN_TARGET", "25"))

MAIN_ENTRY_STATUSES = {
    "PRE-POP BUY CANDIDATE",
    "WATCH FOR ENTRY",
}

MAIN_PREPOP_STATUSES = {
    "EARLY / CLEAN",
    "EARLY / WAKING",
}

def is_main_eligible(row):
    entry = str(row.get("entry_status") or "")
    pre = str(row.get("pre_pop_status") or "")
    return entry in MAIN_ENTRY_STATUSES and pre in MAIN_PREPOP_STATUSES

def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT id, ticker, rank, entry_status, pre_pop_status, show_on_main
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = cur.fetchall()

            eligible = [r for r in rows if is_main_eligible(r)]
            main_ids = {r["id"] for r in eligible[:MAIN_TARGET]}

            main_count = 0
            backup_count = 0
            hidden_count = 0

            for r in rows:
                row_id = r["id"]

                if row_id in main_ids:
                    cur.execute(
                        """
                        UPDATE watchlist_scores
                        SET show_on_main = true
                        WHERE id = %s
                        """,
                        [row_id],
                    )
                    main_count += 1
                    continue

                if is_main_eligible(r):
                    cur.execute(
                        """
                        UPDATE watchlist_scores
                        SET show_on_main = false,
                            entry_status = 'BACKUP / CLEAN OUTSIDE TOP 25',
                            pre_pop_reason = COALESCE(pre_pop_reason, '') || ' | Finalizer: clean candidate but outside top 25 after long-range gate.'
                        WHERE id = %s
                        """,
                        [row_id],
                    )
                    backup_count += 1
                else:
                    cur.execute(
                        """
                        UPDATE watchlist_scores
                        SET show_on_main = false
                        WHERE id = %s
                        """,
                        [row_id],
                    )
                    hidden_count += 1

            print(
                f"Finalized main board run_date={run_date} "
                f"main={main_count} backup={backup_count} hidden_or_watch_only={hidden_count} total={len(rows)}"
            )

    con.close()

if __name__ == "__main__":
    main()
