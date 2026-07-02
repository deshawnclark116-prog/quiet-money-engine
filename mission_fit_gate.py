import os
import json
import psycopg2
from psycopg2.extras import RealDictCursor

# Temporary obvious-name quarantine until a dynamic market-cap/discoverability gate is added.
DEFAULT_TOO_DISCOVERED = {
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "TSM", "IBKR", "SPG", "GM", "F", "TPL", "CASY", "USFD", "ENPH",
}

MAIN_ENTRY_STATUSES = {
    "PRE-POP BUY CANDIDATE",
    "WATCH FOR ENTRY",
}

MAIN_PREPOP_STATUSES = {
    "EARLY / CLEAN",
    "EARLY / WAKING",
}

def env_ticker_set(name, default):
    raw = os.getenv(name, "")
    if not raw.strip():
        return set(default)
    return {x.strip().upper() for x in raw.split(",") if x.strip()}

TOO_DISCOVERED = env_ticker_set("QME_TOO_DISCOVERED_TICKERS", DEFAULT_TOO_DISCOVERED)

def f(x):
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0

def signals_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return {}

def is_main_candidate(row):
    return (
        str(row.get("entry_status") or "") in MAIN_ENTRY_STATUSES
        and str(row.get("pre_pop_status") or "") in MAIN_PREPOP_STATUSES
    )

def classify(row):
    ticker = str(row.get("ticker") or "").upper()
    s = signals_dict(row.get("signals"))

    volume = f(s.get("volume_pressure_score"))
    accum = f(s.get("accumulation_quality_score"))
    breakout = f(s.get("breakout_setup_score"))
    insider = f(s.get("insider_buy_score"))
    news = f(s.get("news_catalyst_score"))
    filing = f(s.get("filing_catalyst_score"))

    quality_safe = (
        f(s.get("capital_efficiency_score"))
        + f(s.get("company_quality_score"))
        + f(s.get("liquidity_quality_score"))
        + f(s.get("volatility_control_score"))
    )

    # Filing is support only. Do not let generic filing activity create mission fit.
    nonfiling_raw = volume + accum + breakout + insider + news
    positive_core = (
        max(volume, 0.0)
        + max(accum, 0.0)
        + max(breakout, 0.0)
        + max(insider, 0.0)
        + max(news, 0.0)
    )

    positive_count = 0
    if volume >= 0.40:
        positive_count += 1
    if accum >= 0.30:
        positive_count += 1
    if breakout >= 0.80:
        positive_count += 1
    if insider >= 0.50:
        positive_count += 1
    if news >= 0.30:
        positive_count += 1

    detail = (
        f"nonfiling_raw={nonfiling_raw:.2f}; positive_core={positive_core:.2f}; "
        f"positive_count={positive_count}; volume={volume:.2f}; accumulation={accum:.2f}; "
        f"breakout={breakout:.2f}; insider={insider:.2f}; news={news:.2f}; "
        f"filing={filing:.2f}; quality_safe={quality_safe:.2f}"
    )

    if ticker in TOO_DISCOVERED:
        return (
            "WATCH ONLY / TOO DISCOVERED",
            "TOO DISCOVERED / NOT QUIET",
            f"Mission-fit gate: obvious/established ticker is not a quiet pre-pop target; {detail}",
            False,
        )

    if nonfiling_raw <= 0.0:
        return (
            "HIDDEN / LOW PRE-POP EVIDENCE",
            "LOW PRE-POP EVIDENCE",
            f"Mission-fit gate: non-filing early-pressure evidence is negative; {detail}",
            False,
        )

    if positive_core < 1.25:
        return (
            "WATCH ONLY / LOW PRE-POP EVIDENCE",
            "LOW PRE-POP EVIDENCE",
            f"Mission-fit gate: weak non-filing early-pressure evidence; {detail}",
            False,
        )

    if positive_count < 2 and insider < 1.00:
        return (
            "WATCH ONLY / WEAK SETUP",
            "WEAK SETUP / NOT ENOUGH CONFIRMATION",
            f"Mission-fit gate: fewer than two independent early-pressure confirmations; {detail}",
            False,
        )

    if filing >= 1.50 and positive_core < 2.00:
        return (
            "WATCH ONLY / GENERIC FILING SIGNAL",
            "GENERIC FILING / WEAK SETUP",
            f"Mission-fit gate: filing score is support only and real pressure is weak; {detail}",
            False,
        )

    if quality_safe >= 3.50 and positive_core < 2.50:
        return (
            "WATCH ONLY / QUALITY ONLY",
            "QUALITY ONLY / LOW PRE-POP EVIDENCE",
            f"Mission-fit gate: quality/safety is high but early-pressure evidence is weak; {detail}",
            False,
        )

    return None, None, None, True

def main():
    con = psycopg2.connect(os.getenv("DATABASE_URL"), cursor_factory=RealDictCursor)

    with con:
        with con.cursor() as cur:
            cur.execute("SELECT MAX(run_date) AS d FROM watchlist_scores")
            run_date = cur.fetchone()["d"]

            cur.execute(
                """
                SELECT id, ticker, rank, entry_status, pre_pop_status, show_on_main, signals
                FROM watchlist_scores
                WHERE run_date = %s
                ORDER BY rank ASC
                """,
                [run_date],
            )
            rows = [dict(r) for r in cur.fetchall()]

            changed = 0
            checked = 0

            print(f"Mission-fit gate run_date={run_date} rows={len(rows)}")
            print(f"Too-discovered quarantine count={len(TOO_DISCOVERED)}")

            for row in rows:
                if not is_main_candidate(row):
                    continue

                checked += 1
                entry_status, pre_pop_status, reason, show_on_main = classify(row)

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

                print(f"{row['ticker']} rank {row['rank']} -> {entry_status} | {pre_pop_status}")
                changed += 1

            print(f"Mission-fit gate checked={checked} changed={changed}")

    con.close()

if __name__ == "__main__":
    main()
