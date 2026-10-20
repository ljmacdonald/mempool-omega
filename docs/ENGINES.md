# Recovery engines A and B: method, data, results and limits

Two engines built from the founder's specifications (ENGINE_A_SPEC.md "Hayes Recovery" and ENGINE_B_SPEC.md "Deep
Distress"), each with its own page (DECISIONS D121, D122).

**Pages:** https://ljmacdonald.github.io/mempool-omega/hayes/ and https://ljmacdonald.github.io/mempool-omega/deep/

**Paper only.** No wallet, no key that can trade, no orders. An engine that fails its own tests is **halted** and opens
nothing, not even on paper.

## How each engine is judged

Each specification says the engine must prove itself on history before anything else is built, and lists kill
criteria. Those criteria run automatically every Sunday (`.github/workflows/engines.yml` → `python -m deep.run`) on
the latest data, with the thresholds fixed in `hayes/profile.yaml` and `deep/profile.yaml`. Nothing is tuned on the
results, so a future pass would be a genuine pass.

| Engine | Test | Pass if |
| --- | --- | --- |
| A | Event study | Confirmed entries in risk-on beat Bitcoin's median 180-day return |
| A | Event study | At least 10% of asymmetric setups reach 3x within a year |
| B | Coverage | 80%+ of known band entries have price history (delisted coins included) |
| B | Event study | At most 40% of setups end delisted or another 80% lower |
| B | Event study | At least 15% of setups reach 3x within a year |
| B | Backtest | Profit after costs in at least 2 of 3 periods (2019–22, 2023–24, 2025–now) |
| B | Backtest | No single coin supplies over 50% of the profit |

## Data: all free, nothing manual

| Source | Used for | Notes |
| --- | --- | --- |
| Binance public data (`data-api.binance.vision`) | Daily candles of every USDT coin since 2017 | Delisted ("BREAK") coins keep their history |
| Coinbase Exchange public API | Daily candles for USD/USDC coins Binance never listed | Delisted products keep their candles |
| FRED CSV downloads | WALCL, RRPONTSYD (billions, ×1000), WTREGEN | Each value used only from its publication date |
| DefiLlama | Total stablecoin supply; holders revenue; open unlock schedules | Catalysts for Engine B (live engine) |

## Results (first run, October 2026)

**Engine A: halted.** 683 coins (194 delisted), 1,369 crashes into the −75% to −90% band. Confirmed entries in
risk-on had a median 180-day return of −7%, against Bitcoin's +24% (30 setups). Of the asymmetric setups, 8.4%
reached 3x within a year (it needs 10%).

**Engine B: halted.**

- **Coverage.** Measured at 100% of the 879 known coins. This is optimistic: it only counts coins that free lists still show.
- **Event study: passed.** 530 crashes into the −92% to −98% band, and 88 setups where all the entry rules passed. Of those, 4.5% ended delisted or fell another 80%, and 21% reached 3x.
- **Backtest: failed.** As specified (risk-on entries only) it made just 3 trades since 2019: profit in 1 of 3 periods, and STRK gave 55% of the profit.
- **Sensitivity.** Entering in every regime gave 48 trades at +1.3% each on average after costs, far behind holding Bitcoin. It is shown but not adopted.

## What the history can't include (stated on the pages)

- Past token unlock schedules, fees, developer activity and catalysts aren't available free for every coin, day by day. The history tests therefore use the price rules only.
- There is no free perpetual-futures history. Without it, the derivatives parts of the confirmation score are dropped and the thresholds rise 5 points, as the specification says.
- The influencer gate (A's U9) has no free source and is dropped.
- Coins removed from every free list since 2019 can't be counted, because CoinGecko's dead-coin list is paid.
- "Reached 3x" is the best price within a year, which no real exit would fully catch.

## Code

| File | What it does |
| --- | --- |
| `hayes/regime.py` | Liquidity regime: three confirmations, publication lags, stale data counts as risk-off |
| `hayes/scoring.py` | Distress, confirmation, supply pressure, quality and the hard gates (pure, profile-driven) |
| `hayes/research.py` | Shared event study for both engines; Engine A's kill criteria |
| `deep/catalyst.py` | Catalysts from measured data, with effective and expiry dates |
| `deep/coverage.py` | Coverage check |
| `deep/research.py` | Engine B's study, context band (−75% to −92%) and kill criteria |
| `deep/backtest.py` | Exit plan simulation, position limits, periods, backtest kill criteria |
| `deep/data.py` | Coinbase products and daily candles |
| `deep/run.py` | Weekly job: inputs, both engines, reports to the data branch |
| `site/engines.js` | Renders both pages from `hayes/research.json` and `deep/research.json` |
