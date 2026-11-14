# GitHub-only paper trader (no Render, no brokerage)

This is **experimental cash-only PAPER trading**, not a production trader.
A company research result is never an order. The existing
`autonomous-research-radar.yml` runs independently for SEC/company evidence;
`github-paper-trader.yml` performs an isolated price/volume-only portfolio
experiment. Both work without Render.

## Start and read

1. Merge the PR containing the new files into `main`. GitHub schedules
   only run from the default branch.
2. Open [GitHub Actions](../../actions/workflows/github-paper-trader.yml).
   The paper workflow first attempts at **6:35 p.m. Eastern (7:35 p.m. during daylight time) on weekdays**,
   after the daily bar is eligible; GitHub scheduling may be delayed or skipped. Late jobs use the latest\n   **completed session**, even after midnight. Additional UTC retry windows\n   attempt to avoid GitHub cron delays/skips; repeats cannot buy twice on one signal.\n   A change to the workflow merged into `main` also triggers a one-time paper bootstrap. Scheduled runs are not\n   discarded just because GitHub starts them outside the requested hour.
   For a manual run choose **Run workflow** on `main`.
3. Read the run's **Summary**, or open the
   [paper-state portfolio history](../../tree/paper-state/paper).
   The first successful run creates the `paper-state` branch. Subsequent runs
   load the exact previous portfolio. The run will **fail rather than reset**
   a corrupted/missing portfolio on an existing state branch.
4. GitHub commits these files on `paper-state/paper/`:
   `portfolio.json` (complete position/order/event ledger),
   `dashboard.md`, `closed_trades.csv`, `equity_history.csv`,
   and `latest_run.json`. Git history preserves each snapshot.
   30-day workflow artifacts are secondary, NOT the primary ledger.

## Paper methodology and limitations

- Begin with $25 simulated USD; no broker, no deposits, no leverage, no shorts.
- Daily listed U.S. stock scan using existing Nasdaq Trader directory and
  Stooq/Yahoo daily OHLCV, including predefined anchor tickers.
  Up to 70 symbols per run; this is NOT whole-market exhaustive coverage.
- Unvalidated signal hypothesis: price/liquidity baseline, 5-day move of
  +2% to +12%, 20-day move -10% to +20%, increased recent volume (>=1.25x)
  and strong latest volume. Selection is NOT based on media hype.
- A signal after a completed daily bar queues a candidate. Entry may occur
  only on the **next session's opening price**, and is recorded only AFTER
  that day's bar becomes available. No entry at a previously observed close.
- Daily OHLCV cannot establish which intraday price occurred first. If the
  stop and profit target both fit the day, the loss/stop is selected first.
  Gap-through-stop assumes the worse opening price.
- Paper execution assumes 0.75% impact on BOTH the buy and sell side.
  Equity marks also reserve the estimated exit impact. Real slippage may
  be greater and there is no real broker fill/quote or borrow availability.
- Long only, whole shares, up to two positions; up to 60% of portfolio equity
  allocated per position and a nominal 5% equity stop-risk budget.
  8% stops, 16% targets, maximum seven bars held. Corporate actions,
  taxes, exchange fees, market halts and liquidity constraints beyond a
  20-day dollar-volume proxy are not faithfully modeled.
- Candidate price/volume data is distinct from the existing financial
  intelligence research; a strong research classification does NOT make
  the experimental strategy profitable or a company investable.
- Provider outage/malformed OHLC: skip the missing symbol. If all prices
  fail, **fail the entire run without committing new portfolio state**.
- No realistic path to guarantee the user's $100 net/week goal from $25
  starting funds. The dashboard reports weekly marked return against the
  target transparently; 400% weekly is not an evidence-based return forecast.
- GitHub Actions is scheduled batch compute, **NOT** an always-on real-time
  execution environment. Holiday schedules can mean no new price bar.
  Prospective results require a large sample, calibrated testing, costs,
  time-separated holdouts and a stable quote/broker environment before
  contemplating real-money use.

## Developer verification

```bash
python -m unittest discover -s research/autopilot -p 'test_*.py' -v
python -m unittest discover -s research/long_horizon -p 'test_*.py' -v
python -m unittest discover -s research/two_clock -p 'test_*.py' -v

python -m research.autopilot.paper_runner --output-dir paper_output --max-symbols 70
```

Never put broker credentials or personal information in repository files.
This system has no brokerage dependency and never submits an order.

GitHub Actions `workflow_run` also wakes the paper portfolio after a successful
scheduled `autonomous-research-radar` run on the main branch. This couples the
research completion signal to the paper processor and reduces dependence on
GitHub cron reliability; each completed session is processed idempotently.
