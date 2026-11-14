# Quiet Money | GitHub paper portfolio

**SIMULATED ONLY — no broker or real orders.**
Executed with completed daily bars, next-session hypothetical opening fills,
75 bps assumed cost EACH way, and stop-first when OHLC order is unknown.
The strategy is unvalidated. This is NOT an investment performance claim.

| Metric | Value |
|---|---:|
| Latest evaluation date | 2026-11-13 |
| Initial bankroll | $25.00 |
| Marked portfolio equity | $23.37 |
| Cash | $15.50 |
| Total net P&L (marked) | $-1.63 |
| Total return | -6.54% |
| SPY passive return (observed period) | -0.36% |
| Return minus SPY (percentage points) | -6.17 |
| Current week net P&L (marked) | $-1.63 |
| Weekly target (not guaranteed) | +$100.00 |
| Progress against weekly target | -1.6% |
| Realized P&L | $-1.14 |
| Unrealized P&L, price marks | $-0.49 |
| Closed trades | 1 |
| Winning trades | 0 |
| Closed-trade win rate | 0.0% |
| Max snapshot-to-snapshot drawdown | 6.54% |
| Open positions | 1 |
| Pending next-session entries | 0 |
| Price feed errors | 0 |
| Stale position marks | None |

## Open positions

| Ticker | Shares | Entry | Stop | Target | Opened |
|---|---:|---:|---:|---:|---|
| AMPX | 1 | $8.3622 | $7.6933 | $9.7002 | 2026-11-13 |

## Pending signals

| Ticker | Recorded session | Close when signaled |
|---|---|---:|
| None | — | — |

## Most recent closed paper trades

| Ticker | Entry | Exit | Shares | Net P&L | Exit trigger |
|---|---|---|---:|---:|---|
| ACHR | 2026-11-13 | 2026-11-13 | 3 | $-1.14 | protective_stop |

## Evidence and operating rules

- Generated from a GitHub Actions scheduled run; portfolio history is committed to the `paper-state` branch.
- Market scans use price/volume data only; financial company research is separately available in the research radar.
- Cash-only, whole-share, long-only portfolio; 2 open slots and 60% maximum nominal exposure per position.
- Stops are simulated from daily OHLC. Actual gap losses and broker execution may be worse.
- Broken/stale OHLC bars, provider outages, and an unavailable position price must not invent an order or price.
- A first-day equity snapshot is not evidence of strategy quality. Independent out-of-sample testing, more trades, and liquidity/quotes are required before real-money consideration.
- A +$100 weekly target from $25 starting capital would be a 400% weekly return; it is an aspirational measurement, not a plausible guarantee.

