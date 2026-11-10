# Two-Clock Dislocation Trader — new research track, v0.1

**Status: implemented as an isolated falsifiable research prototype; ZERO evidence of a trading edge; ZERO live orders.**

### Differentiator

Our existing Quiet Money Engine already computes compression, accumulation, early relative strength, insider activity, SEC filing events and a pre-pop rank. Reweighting the same features would not be an innovative strategy. The proposed **Two-Clock Dislocation hypothesis** instead models an *information clock* and a *market-response clock*:

1. **External information clock:** A material, directionally favorable business fact is *first received* from a verified primary SEC filing, not dated backward from the company’s filing/announcement date. Preserve accession and observed receipt timestamp. Accept only proven structured events; a generic 8-K, upbeat headline or analyst opinion is **not** automatically a favorable event. Do not use revised filing data from after a decision.
2. **Counterfactual price clock:** Estimate stock-specific expected return from prior stock-vs-SPY behavior (beta fitted only on earlier daily trading sessions). Compare post-event actual return against the expected market-adjusted response. This is *not* a calibrated alpha estimate.
3. **Impact asymmetry:** Require observable survival on market-down days, followed by comparatively stronger positive residual price response per unit relative daily volume than negative response. Treat daily OHLCV as **proxies only**, not actual buying/selling transaction data. Hypothesis: when supply is being absorbed, downward shocks cease to translate into persistent price decline, and subsequent modest buying has disproportionate impact. This claim must be tested; the same pattern can arise from spurious beta and news-driven selection.
4. **Reaction delay:** Seek verified events not yet followed by large positive market-adjusted returns, and explicitly reject stocks already in extended, chase-prone moves.
5. **Execution clock:** Only after dislocation is detected do we test independent post-signal pricing, broker eligibility, spread, partial fills and cash availability. The prototype does not make trades or probabilities; no market-data feed with guaranteed executable bid/ask quotes is installed.

### Data we actually have vs. do not have

- **Have:** Quiet Money Engine’s Stooq/Yahoo fallback for *daily* OHLCV, its SEC submissions/filing-taxonomy parsing, and an exchange eligibility / dilution-check framework.
- **Not yet demonstrated:** A live, dependable, permitted, free, full-market intraday tick/Level-2 feed, verified broker Webull/Robinhood listing and share-order eligibility for *each* stock, or timestamp-accurate primary-event polarity classifications. SEC data.sec.gov requires no keys but does require declared UA and fair access.
- **Cannot infer:** Actual aggressive buyers, hidden sell orders, reliable spread, executable price, liquidity depth or unseen future moves from OHLCV alone.

### Promotion gates (predeclared, not tuned on the test set)

**Research stage A — feature integrity:** Full point-in-time event receipts, survivorship-free universe (including delisted and suspended stocks), adjusted price/share corporate actions, consistent data vendor, no missing daily sessions, future-only labels. Compare candidate coverage, rejected signals, and post-signal returns by 1, 3, 5, 10 market days.

**Research stage B — falsification:** Compare against (a) existing QME early signal, (b) price/volume momentum, (c) matched liquidity and market-cap random opportunities, and (d) event-only strategy. Validate with rolling-forward chronological splits, ticker-disjoint stress splits and no retuning on final holdout. Report drawdowns, expected return, profit factor, winning probability calibration (if a model head is ever fitted), severe-loss tail, and *opportunity-level* net P&L.

**Research stage C — paper execution:** Use future-only timestamped bid/ask and sufficient displayed volume. Require actual listing/broker eligibility. Start with an independently funded **$25** paper cash account without top-ups. Honor settlement, share availability and stock corporate actions. Reject stale quotes, halts, missing spreads, impossible $1 orders, 24h phantom trading, and uncertain executions. Include transaction costs, spreads, fees and slippage; no convenient close-to-close fills.

**Research stage D — profitability mandate:** The user's explicit hurdle is **$100+ net per week** from the $25 starting bankroll, equivalent to a **400% one-week gain**, and it must be measured across independent forward weeks without resetting losses. Log **pass/fail** honestly. This is an aspirational, extreme target, not a credible promised outcome or license to hide tail risk.

### Prototype contract

- `two_clock_research.py` computes `dislocation_candidate`, `no_dislocation`, or `abstain` from daily OHLCV + verified primary event timestamp.
- **Never** returns a `BUY` or calibrated probability; its `execution_ready` is deliberately always false.
- Event date is not backdated from SEC acceptance. A completed trading session must occur *after* event receipt before an event response can be judged.
- Tests verify no future price/event lookahead, malformed OHLCV fail-closed, missing benchmark abstention, and that sentiment-only news cannot masquerade as verified primary evidence.
- This prototype should live **beside**, not inside, QME production scoring until future data validates it.

### Public data sources

- [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) (free/no key, access rules apply)
- [SEC developer fair-access policy](https://www.sec.gov/about/developer-resources)
- [Nasdaq real-time/delayed market-data offerings](https://www.nasdaq.com/products/data/data-link/api) (subscribed market feeds must not be confused with public free data)