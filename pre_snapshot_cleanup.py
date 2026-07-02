import os
import psycopg2

def main():
    model = os.getenv("QME_MODEL_VERSION", "quality_heavy_v2")
    con = psycopg2.connect(os.getenv("DATABASE_URL"))

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) FROM watchlist_scores")
            run_date = cur.fetchone()[0]

            if run_date is None:
                print("No watchlist run found. Nothing to clean.")
                return

            print(f"Pre-snapshot cleanup run_date={run_date} model={model}")

            cur.execute(
                """
                DELETE FROM prediction_snapshots
                WHERE run_date = %s
                  AND source = %s
                """,
                [run_date, model],
            )
            print(f"Deleted prediction_snapshots: {cur.rowcount}")

            cur.execute(
                """
                DELETE FROM prediction_runs
                WHERE run_date = %s
                  AND model_version = %s
                """,
                [run_date, model],
            )
            print(f"Deleted prediction_runs: {cur.rowcount}")

    con.close()

if __name__ == "__main__":
    main()
