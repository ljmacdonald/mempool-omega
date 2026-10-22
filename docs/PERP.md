# Perp DEX lab: method, data, limits

The owner's two specifications are built as one paper-only lab (DECISIONS D123):
- **ANEE (Adversarial Net-Edge Engine):** its rules, accounting, adversarial tests and go/no-go gates.
- **The Perp DEX Multi-Strategy System:** its four strategies.

**Pages:** https://ljmacdonald.github.io/mempool-omega/perp/ (an overview plus one page per strategy).

**Paper only, for good.** There is no wallet, no private key, and no signing or sending code. A test checks this. The owner is in the US; as far as we know GMX's app, Hyperliquid and dYdX bar US users. The Multi-Strategy spec's live mode, hot wallet, exchange keys, paid RPC, server, database and private dashboard were therefore left out.

## What runs

| Job | When | What |
| --- | --- | --- |
| `python -m perp.run` | hourly (`perp.yml`, Tick) | Live snapshot, data checks, kill switches, then each strategy's paper step on the $100 account and the $10,000 reference account. Writes the decision log and publishes `perp/snapshot.json`. |
| `python -m perp.run --research` | daily 02:40 UTC | Every history test, the benchmarks, the execution-gap study and the gates. Publishes `perp/research.json`. |

## Data (free, keyless)

| Source | Used for |
| --- | --- |
| GMX REST (`arbitrum-api.gmxinfra.io`) | Markets, open interest, liquidity, funding and borrowing rates, oracle tickers, token decimals, candles |
| GMX history service (`gmx.squids.live`, the GraphQL index GMX's own app uses) | Market settings (fee factors, price-impact factors and exponents) and hourly funding/borrowing since July 2023. Also daily pool-token (GM) prices, daily fee and trader-result APRs, every executed order (oracle price, fill price, impact, fee) and execution fees |
| Hyperliquid info API | Hourly funding since 2023, hourly candles, order book, market context (hedge venue; data only) |
| Binance public mirror | Years of 1-hour and 15-minute candles for the directional test (GMX's oracle tracks the same exchange prices) |

## Cost model (`perp/costs.py`)

Every strategy calls `estimate_costs()`. GMX costs come from the market's own published settings:
- the position fee (0.04% if the trade improves the long/short balance, 0.06% if it worsens it);
- GMX's price-impact formula, `factor × (imbalance^exponent)` before and after. The balance is in tokens at the oracle price where the market says so.
- funding and borrowing by side;
- the execution fee, measured as the median of recent real orders;
- Arbitrum gas.

Hyperliquid costs are the documented taker fee, order-book slippage (live) or a stated 2 bp assumption (history), and hourly funding.

**Validated:** for a real $285,686 order on 2026-10-22, GMX reported $116.4 of impact. The formula gave about $110; open interest had moved a little in between.

Three estimates are kept:
- **base:** the best estimate;
- **adverse:** every cost ×2, no rebates, half the income;
- **extreme:** ×4.

A missing input raises `NotTradeable`. A cost is never assumed to be zero.

## Accounting (`perp/money.py`)

- Exact Decimal money throughout.
- A trade has a gross price result and an itemised cost stack. A received amount is a negative cost.
- `net()` is the only place net P&L is computed.
- Fills are oracle or mid prices without impact, so impact is charged once, as its own line.
- The waterfall and the estimated-vs-actual cost drift come from the same records.

## Strategies

| # | Rules (spec defaults) | History test | Live paper |
| --- | --- | --- | --- |
| 1 Pool liquidity | Rank pools by trailing 7/30-day fee APR plus traders' result. Hold the top 3, at most 50% in one pool. Exit when the 7-day pool return is negative, or traders win 3 days running. Optional hedge of the pool's token exposure on Hyperliquid. | Daily GM prices of every pool since July 2023. These contain every fee and trader win/loss, so they're exact. | Daily re-ranking; hourly marks from pool value / supply |
| 2 Funding carry | Take the paid GMX side and hedge on Hyperliquid. Enter above 15% a year after costs amortised over 72 h, once positive 3 hours running. Exit below 3%, or negative 2 h. | Hourly rates since 2023 on both venues. Prices for the gap between venues: GMX's last ~180 days of hourly candles. | Hourly |
| 3 Spread | Enter beyond 2.5 standard deviations of the 7-day gap, and only if the gap is more than 2× round-trip costs. Exit at 0.5 SD, after 24 h, or at a 4 SD stop. | GMX vs Hyperliquid hourly closes, plus a measurement of how often and how long gaps exceed 2× costs | Hourly (not 10 s) |
| 4a Trend | 1 h: close above EMA50 and a break of the 20-bar high (short: mirror); skip when ATR is in its bottom 20% | Binance 1 h since July 2023 | Hourly |
| 4b Mean reversion | 15 min: fade a move beyond 2.5 SD from the 4 h VWAP when the 1 h trend is flat | Binance 15 min since January 2024 | Hourly |

**Shared rules for 4a and 4b:**
- Enter at the next bar's open.
- Stop at 1.5 ATR; take profit at 3 ATR or a 2-ATR trailing stop.
- A gap through the stop exits at the worse open.
- Size so a stop-out loses 0.5% of the sleeve including costs.

**Contrarian filter:** skip the crowded side above 70/30 open interest. It's applied live only, because open-interest history isn't free.

## Risk and kill switches (`perp/paper.py`)

**Limits.** Strategies can't change these:
- 0.5% risk per position;
- 2% daily loss (no new positions until the next day);
- 10% drawdown (pause and flatten);
- margin at most 20% of the sleeve, leverage at most 2x;
- at most 1% of a market's available liquidity;
- liquidation distance at least 25%;
- one position per side across the correlated coins.

**Kill switches:**
- an oracle more than 5 minutes stale flattens that market;
- a USDC depeg beyond 1% flattens everything;
- GMX and Hyperliquid prices more than 2% apart make that market untradeable.

## Gates (`perp/gates.py`, ANEE section 9)

1. Data integrity 99.5%+.
2. 500+ trades (500+ days for the pool strategy).
3. Positive net expectancy with a positive 95% block-bootstrap lower bound.
4. Drawdown ≤ 10%.
5. Still positive with every execution cost doubled.
6. 30 days of live paper, 100+ trades if markets allow, and 95% of cost estimates within 25% (or 1 cent) of what was charged.

Gates 3 and 5 are judged only after gate 2 passes. Passing all six only allows a review. Gates are judged on the $100 account; the $10,000 reference shows whether an edge exists apart from small-account fixed costs.

## Adversarial tests (`tests/test_perp.py`)

| Test | What it checks |
| --- | --- |
| AT-01 | Price integrity between GMX's oracle and Hyperliquid |
| AT-02 | A cost spike turns the conservative edge negative |
| AT-03 | Fills come at the next bar's prices |
| AT-04 | Capacity limit (1% of liquidity; unknown liquidity means no trade) |
| AT-05 | Funding accrues along the actual rate path |
| AT-06 | A gap through the stop exits at the worse open |
| AT-07 | An order deeper than the book can't fill |
| AT-08 | Kill switches block new positions |
| AT-09 | One position per side in the correlated group |
| AT-10 | Missing fee settings fail closed |
| AT-11 | Gate 5's doubled costs |
| AT-12 | Results without the 5 largest winners |

Separate tests cover the accounting rules, the impact formula against a real order, data cleaning, the gates, and the absence of any signing code.

## Execution-gap study (`perp/execgap.py`)

This tests ANEE's large-position hypothesis passively, on real public GMX orders from the last 7 days. For each executed market order it measures:
- the fill vs the oracle price;
- GMX's own reported impact;
- the unexplained residual (observed minus predicted);
- the fee vs the documented rate;
- the wait from creation to execution, and the oracle's move during it.

Results are reported by order size. **First run (3 days, 8,362 orders):**
- No size effect: the unexplained cost was about +1 bp for small orders and +0.2 bp for $100k–1M.
- About 3 seconds from creation to execution.
- Fees exactly 4 or 6 bp, as documented.

## Known limits

- **10-second strategies can't run.** The free scheduler runs hourly, so live and historical fills use the next hour's prices.
- **Some history isn't kept for free:** pool utilisation, open-interest balance, and past GMX fee/impact settings. Today's settings are applied to history and labelled as such.
- **The execution fee** is today's median applied to history. Keepers refund unused fees, so this is conservative.
- **Hedge slippage** in history is an assumption (2 bp per side); live, it comes from the order book.
- **Pool data cleaning:**
  - prices at or below zero are excluded;
  - one-day moves over 50% are excluded;
  - after a confirmed level change the series restarts (no return is counted across it);
  - completeness is reported.
