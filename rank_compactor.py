import os
import psycopg2

def compact_table(cur, table_name):
    cur.execute(f"""
        WITH latest AS (
            SELECT MAX(run_date) AS d FROM {table_name}
        ),
        ordered AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    ORDER BY
                        CASE WHEN show_on_main THEN 0 ELSE 1 END,
                        rank ASC,
                        ticker ASC
                ) AS new_rank
            FROM {table_name}
            WHERE run_date = (SELECT d FROM latest)
        )
        UPDATE {table_name} t
        SET rank = ordered.new_rank
        FROM ordered
        WHERE t.id = ordered.id
    """)
    return cur.rowcount

def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"))
    with con:
        with con.cursor() as cur:
            watchlist_count = compact_table(cur, "watchlist_scores")
            print(f"watchlist_scores ranks compacted: {watchlist_count}")
    con.close()

if __name__ == "__main__":
    main()
