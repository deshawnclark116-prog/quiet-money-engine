# Quiet Money Engine — Structural Entry Research Registry

**Research frozen November 20, 2026.** Branch `research/structural-edge-holdout-20261120`, GitHub PR #23. This is a historical diagnostics study and **NOT a promoted trading model**.

## Scientific question

Can daily-bar market-down-day resilience, quiet volume accumulation and range compression, or post-selloff stabilization identify entry points earlier and more profitably than the existing deployed `paper._signal` recent-price-acceleration approach?

## Controls

- Frozen cohort: 70 current-listed symbols from the November 12, 2026 scan; **strong historical survivorship and selection bias**.
- Same capital per arm: $25 cash, whole shares, max two positions, next-session open fill, 75 bps buy and sell impact, 8% stop, 16% target, seven-session time exit, conservative stop-first ambiguous OHLC.
- A matched price/liquidity-eligible date-hashed placebo is a negative control; it does not use future winning returns.
- Windows are independently reset (not one investable continuous portfolio); 2024–2025 exploratory, Jan–Oct 2026 **previously exposed to strategy research**, not a pristine holdout.
- Research test workflow **44808650939 completed successfully**, with complete signal-level receipts, trades, and equity curves saved in its artifact. No changes made to the official paper strategy or historical ledger.

## Historical results (after modeled costs)

| Strategy | 2024–25 closed trades | 2024–25 net paper return | Jan–Oct 2026 closed trades | 2026 net paper return |
|---|---:|---:|---:|---:|
| Original momentum/volume entry | 77 | −57.37% | 41 | −22.11% |
| Down-market resilience | 51 | −10.80% | 23 | −30.78% |
| Quiet accumulation and compression | 26 | −9.57% | 11 | −16.41% |
| Selloff recovery/stabilization | 9 | −12.20% | 2 | −4.93% |
| Liquid-stock random placebo | 180 | −50.93% | 81 | −53.24% |

Eligibility diagnostic: 12,942 liquid stock-session candidates. Full raw eligible signals before portfolio capacity: original 416, resilience 279, quiet accumulation 67, reversion 21, matched placebo 12,942. Outcomes should be evaluated at the matched portfolio level, not cherry-picked signal return.

### Failure patterns

- Original formula 2024–25: 28 protective-stop exits, 3 gap-through-stop exits, only 8 profit-target exits across 77 closed trades.
- Original formula 2026: 17 protective-stop, 1 gap-through-stop, 7 profit-target exits across 41 closed trades.
- Resilience 2026: 8 protective stops and **zero profit-target exits** across 23 closed trades. The 2024–25 improvement was **not stable across periods**.
- Quiet accumulation 2026: 4 protective stops, 2 gap-through stops, **zero profit-target exits** across 11 closed trades.
- Reversion 2026 has only 2 closed trades; insufficient for an edge claim.

## Decision

**Reject model promotion.** No candidate had positive modeled net portfolio return in either research window. Even the apparently less-loss-making alternatives fail a consistent out-of-period test. Do not tighten the existing filters using already-seen 2026 to manufacture success. Do not relax stops or trade size to hide negative expectancy.

## Reopen criteria

1. Obtain historical point-in-time stock membership **including delistings, splits and bankruptcies**; the current-listing universe cannot support credible historical strategy selection.
2. Add observed primary-source corporate change dates, dilution and cash runway, actual tradable quotes and a realistic cost/liquidity model. SEC research remains limited while the `EDGAR_CONTACT` identification is not configured.
3. Validate separately by calendar year, ticker and sector with a genuinely unseen prospective window. At minimum positive net returns after credible costs, stable superiority to the incumbent **and** null controls, and adequate independent event counts.
4. Do not present the $25 paper account as evidence of a $100/week edge. Continue tracking paper decisions without changing existing rules pending validation.

Research artifact: https://github.com/deshawnclark116-prog/quiet-money-engine/actions/runs/44808650939

