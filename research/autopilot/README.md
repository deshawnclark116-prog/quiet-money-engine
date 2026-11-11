# Quiet Money autonomous SEC-first research radar

Runs on GitHub Actions without a Render server, database or brokerage account.
Produces inspectable JSON and Markdown artifacts for every scanned ticker,
including rejects. This is a **research scanner**, not a broker, and no returns,
trade fills, probabilities or $100/week profit claims are manufactured.

## Research process
1. Read Nasdaq Trader's common-stock-like listed-symbol directory, and a small
   explicit anchor group. Rotate the remaining ticker subset each ISO week.
   No stock articles or promotional hype are used in discovery.
2. Fetch daily OHLCV from the existing Stooq/Yahoo fallback, and reject
   incomplete current-day candles, stale data, <100 sessions, price outside
   $0.25-$15, median dollar volume below $1m, high 20/60-day runups and
   deep drawdowns. These are provisional **discovery** thresholds, not a
   validated entry model or optimal parameter set.
3. Report *every* scanned stock. Rank qualifying price-change observations
   only to prioritize SEC research; this ranking is not a buy probability.
4. On the top 12, call the existing Company Conviction SEC adapter. Data
   errors, missing finances, no listing verification, incomplete filing risk
   review and missing independent milestone verification stay unknown.
5. Run the Two-Clock price-response hypothesis **only** against properly
   attested positive SEC events first seen before the decision date.
   Without authenticated evidence it abstains; never turn generic 8-K or
   hype sentiment into a verified positive event.
6. Keep short-term and long-term research independent. A capital-structure
   proxy estimates market cap, enterprise value and net cash/share **only**
   when supporting financial observations exist. It does not pretend to
   be a fair-value or entry model.
7. Write files to Actions artifact: research_scan.md and research_scan.json.

All orders are explicitly disabled (including fake/paper fills) until a
separate executable bid/ask, liquidity, broker eligibility, cash settlement,
portfolio controls and prospective proof are implemented and validated.

## Schedule / operation
- GitHub Actions -> autonomous-research-radar; scheduled near 09:45 and
  16:45 America/New_York on weekdays; DST handled by UTC candidates and
  Eastern time gate. GitHub's queue may delay or miss schedules.
- Run manually in GitHub with workflow_dispatch if desired.
- Each research run uploads a report artifact retained for 30 days.
- A code push/PR runs offline contract tests rather than executing a live scan.
- API/worker/cron on Render remain separate; if suspended for billing they
  **do not start** because this GitHub workflow runs.
- EDGAR_CONTACT must be configured as a **repository Actions secret**, with a
  real SEC-compliant User-Agent contact. Never commit a private email into
  this public repository. Without that secret, results are explicitly labeled
  price-only/missing-contact, and company research refuses to promote.
- Public data providers can be stale, throttled or disappear. No useful
  market data marks a run degraded, not falsely successful.

## Local commands
From the repository root with requirements installed:

    python -m unittest discover -s research/autopilot -p 'test_*.py' -v
    python -m unittest discover -s research/long_horizon -p 'test_*.py' -v
    python -m unittest discover -s research/two_clock -p 'test_*.py' -v
    python -m research.autopilot.run --tickers ZOM,SOFI,RKLB --max-sec-reviews 3

## Blocked autonomous-trading requirements
Render was suspended for billing when this extension was created. A database
instance was not discoverable through the connected Render workspace, so its
state must be independently verified. Real-money automated trading additionally
needs a permitted broker connection, appropriate feed, failsafe kill switches,
position/stop limits, opportunity-level forward validation and an explicit
trading authorization. The user goal of $100 net/week from a starting $25 is a
400% weekly return target, not a supported expectation or achievable guarantee.
