# Company Conviction Layer — research prototype v0.1

**Status:** independent, SEC-first research workflow; not a trading bot,
not a valuation model, not a live long-term allocation decision.

## Why this exists

A short-term trading signal and a sound long-term investment thesis answer
different questions. A good early entry can belong to a deteriorating
business; a business worth studying can be a terrible purchase at the
current valuation. The system MUST grade both tracks separately.

The long-horizon track tries to detect companies with:
- Confirmed improving revenue or cash generation, not just enthusiastic news;
- Cash runway that can support the business without imminent financing;
- Reasonable debt and measured share-count dilution;
- Verified primary-source evidence of commercial traction or regulatory
  approval, rather than a press release paraphrased by promotional sites;
- Exchange listing and a contemporaneous verified risk-review trail;
- An independently justified valuation and price entry (NOT in v0.1).

The goal is to find the next *interesting company worth investigating*
while the short-term engine independently looks for favorable entries.

## Workflow and classification

1. **First pass — automated SEC research:** Read ticker/CIK,
   companyfacts/XBRL, and submissions using the existing
   `company_insights.py` SEC client. Treat each fact as first observed
   **when this bot actually fetched it**, never retroactively from an
   older SEC filing date. Normalize period basis and reject 9-month
   year-to-date amounts masquerading as annual or quarterly cash flow.
   Tag obvious regulatory alerts, with their accession numbers.
2. **Financial triage:** `flag_for_deeper_research` can become true
   when supported financial indicators are attractive and there are no
   recognized red flags. **This is a research queue, NOT a buy/hold
   flag.** Unknown risks remain unknown.
3. **Company verification:** Independently verify product adoption,
   commercial contracts, sales, regulatory milestones and the complete
   primary filing risk review (shelf registrations, going concern,
   delisting, dilution, reverse splits, contingent liabilities).
   Upbeat descriptions alone are not proof of progress.
4. **Long-horizon flag:** `long_term_flag` can become true only for
   `long_term_research_candidate` after all mandatory inputs are
   supported. This is still research, NOT an authorization to buy.
5. **Valuation and trade:** A separate, future model must evaluate
   diluted market capitalization, enterprise value, downside cases,
   attainable entry, transaction spread, liquidity, broker eligibility,
   unsettled cash and portfolio risk. V0.1 deliberately has
   `valuation_status: unreviewed`, `trade_action: none`,
   `automatic_hold: false`, `override_short_term_exit: false`.
6. **Ongoing company monitoring:** Recheck on new 10-Q/10-K, financing,
   material 8-K, actual adoption or financial trajectory changes.
   A long-term flag is revocable and must NOT indefinitely protect
   a losing short-term trade from its planned exit.

## Data/coverage limitations

- SEC CompanyFacts lacks many small/micro-cap metrics, consistent debt
  reporting and independent product-commercialization evidence.
- A debt tag representing only long-term obligations may omit other
  liabilities, so any preliminary ratio is **a proxy only**.
- Cash runway estimated from reported annual/quarterly operating cash
  flow can change quickly; does not include all commitments.
- Financial results vary materially by sector; pre-revenue biotech
  cannot be assessed as an ordinary revenue-growth business.
- A historical SEC filing `filed` date is not the timestamp when our
  bot knew it. Today's SEC API response cannot be used to backtest
  historical event-timing trades without archived first-observed receipts.
- This adapter cannot claim a complete risk audit and does NOT
  automatically generate `verified_milestones`.
- An empty list of warning filings does not prove that company risk is low.
- One stock's spectacular multi-month run (including ZOM) cannot
  establish an expected or replicable return, and a firm with an
  attractive product can still dilute or delist shareholders.
- All new research must be evaluated prospectively and benchmarked
  against the existing Quiet Money Engine and a no-company-layer control.

## Use

`python -m unittest discover -s research/long_horizon -p 'test_*.py' -v`

For live SEC discovery only, set a **real, compliant** `EDGAR_CONTACT`
identity with an email (never a placeholder). Then from repository root:

`python -m research.long_horizon.live_sec ZOM`

Absent a verified full risk review, independent primary business
milestone, current listing verification and valuation, the output
should remain research-only; no live money or paper trades are placed.
SEC request throttling and caching are delegated to the existing
`company_insights.py` client.

### Future integration contract

The stock trade engine should publish:
`ticker`, `short_term_action`, `entry_price`, `exit_plan`,
`long_horizon_classification`, `company_evidence`, and
`flag_reviewed_at` as **separate fields**.

For hypothetical $25 paper accounts, a long-term position can only
be opened after broker/share-quantity and settlement rules have been
verified in the execution model. Do not reserve a phantom fraction
of a $1 trade just to imitate a runner position.

The mandatory long-range project objective remains **$100 net per week
from a $25 original bankroll**, an extreme 400% target that v0.1
does not claim to achieve or predict.
