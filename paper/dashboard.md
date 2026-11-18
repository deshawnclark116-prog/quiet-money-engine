# Quiet Money | GitHub paper portfolio

**SIMULATED ONLY — no broker or real orders.**
Executed with completed daily bars, next-session hypothetical opening fills,
75 bps assumed cost EACH way, and stop-first when OHLC order is unknown.
The strategy is unvalidated. This is NOT an investment performance claim.

| Metric | Value |
|---|---:|
| Latest evaluation date | 2026-11-18 |
| Initial bankroll | $25.00 |
| Marked portfolio equity | $22.97 |
| Cash | $22.97 |
| Total net P&L (marked) | $-2.03 |
| Total return | -8.13% |
| SPY passive return (observed period) | -3.56% |
| Return minus SPY (percentage points) | -4.57 |
| Current week net P&L (marked) | $-0.40 |
| Weekly target (not guaranteed) | +$100.00 |
| Progress against weekly target | -0.4% |
| Realized P&L | $-2.03 |
| Unrealized P&L, price marks | $+0.00 |
| Closed trades | 2 |
| Winning trades | 0 |
| Closed-trade win rate | 0.0% |
| Max snapshot-to-snapshot drawdown | 8.13% |
| Open positions | 0 |
| Pending next-session entries | 0 |
| Price feed errors | 0 |
| Stale position marks | None |

## Open positions

| Ticker | Shares | Entry | Stop | Target | Opened |
|---|---:|---:|---:|---:|---|
| None | — | — | — | — | — |

## Pending signals

| Ticker | Recorded session | Close when signaled |
|---|---|---:|
| None | — | — |

## Most recent closed paper trades

| Ticker | Entry | Exit | Shares | Net P&L | Exit trigger |
|---|---|---|---:|---:|---|
| AMPX | 2026-11-13 | 2026-11-16 | 1 | $-0.89 | gap_through_stop |
| ACHR | 2026-11-13 | 2026-11-13 | 3 | $-1.14 | protective_stop |

## Evidence and operating rules

- Generated from a GitHub Actions scheduled run; portfolio history is committed to the `paper-state` branch.
- Market scans use price/volume data only; financial company research is separately available in the research radar.
- Cash-only, whole-share, long-only portfolio; 2 open slots and 60% maximum nominal exposure per position.
- Stops are simulated from daily OHLC. Actual gap losses and broker execution may be worse.
- Broken/stale OHLC bars, provider outages, and an unavailable position price must not invent an order or price.
- A first-day equity snapshot is not evidence of strategy quality. Independent out-of-sample testing, more trades, and liquidity/quotes are required before real-money consideration.
- A +$100 weekly target from $25 starting capital would be a 400% weekly return; it is an aspirational measurement, not a plausible guarantee.

