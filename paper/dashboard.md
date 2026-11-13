# Quiet Money | GitHub paper portfolio

**SIMULATED ONLY — no broker or real orders.**
Executed with completed daily bars, next-session hypothetical opening fills,
75 bps assumed cost EACH way, and stop-first when OHLC order is unknown.
The strategy is unvalidated. This is NOT an investment performance claim.

| Metric | Value |
|---|---:|
| Latest evaluation date | 2026-11-12 |
| Initial bankroll | $25.00 |
| Marked portfolio equity | $25.00 |
| Cash | $25.00 |
| Total net P&L (marked) | $+0.00 |
| Total return | +0.00% |
| SPY passive return (observed period) | +0.00% |
| Return minus SPY (percentage points) | +0.00 |
| Current week net P&L (marked) | $+0.00 |
| Weekly target (not guaranteed) | +$100.00 |
| Progress against weekly target | 0.0% |
| Realized P&L | $+0.00 |
| Unrealized P&L, price marks | $+0.00 |
| Closed trades | 0 |
| Winning trades | 0 |
| Closed-trade win rate | Not yet measurable |
| Max snapshot-to-snapshot drawdown | 0.00% |
| Open positions | 0 |
| Pending next-session entries | 2 |
| Price feed errors | 0 |
| Stale position marks | None |

## Open positions

| Ticker | Shares | Entry | Stop | Target | Opened |
|---|---:|---:|---:|---:|---|
| None | — | — | — | — | — |

## Pending signals

| Ticker | Recorded session | Close when signaled |
|---|---|---:|
| AMPX | 2026-11-12 | $8.3500 |
| ACHR | 2026-11-12 | $4.3300 |

## Most recent closed paper trades

| Ticker | Entry | Exit | Shares | Net P&L | Exit trigger |
|---|---|---|---:|---:|---|
| None | — | — | — | — | — |

## Evidence and operating rules

- Generated from a GitHub Actions scheduled run; portfolio history is committed to the `paper-state` branch.
- Market scans use price/volume data only; financial company research is separately available in the research radar.
- Cash-only, whole-share, long-only portfolio; 2 open slots and 60% maximum nominal exposure per position.
- Stops are simulated from daily OHLC. Actual gap losses and broker execution may be worse.
- Broken/stale OHLC bars, provider outages, and an unavailable position price must not invent an order or price.
- A first-day equity snapshot is not evidence of strategy quality. Independent out-of-sample testing, more trades, and liquidity/quotes are required before real-money consideration.
- A +$100 weekly target from $25 starting capital would be a 400% weekly return; it is an aspirational measurement, not a plausible guarantee.

