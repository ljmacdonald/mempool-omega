# Accumulation lab: method, data, limits and requirements traceability

The Accumulation lab is the founder's "Crypto Intelligence Engine" specification, built inside Mempool Omega as one
page with its own engine (DECISIONS D119). This document is the single reference for how it works. It covers what
the spec asked for in its architecture, data-source, metrics, watchlist, backtesting, limitations and daily-operations
documents, plus a requirements traceability matrix.

**Page:** https://ljmacdonald.github.io/mempool-omega/accum/

**Paper only.** No wallet, no seed phrase, no key that can trade, no orders. Live trading does not exist in the code.

## 1. Architecture (approved deviation from the spec's stack)

The spec suggested Next.js, Supabase and Cloudflare with a paid domain. The owner approved building it inside the
existing free GitHub system instead (proposal item 1).

| Spec component | Built as |
|---|---|
| Backend services and scheduled ingestion | `accum/` Python package, run hourly by `.github/workflows/accum.yml` and started by the Tick job (`infra/tick.py`) |
| Database (35 tables) | Versioned files. `state/accum/`: `universe.json`, `watchlist.json`, `events.csv`, `detections.csv`, `forward.csv`, `paper.json`, `paper_equity.csv`, `recovery.json`, `backtest.json`, `registry.json`, plus `days/YYYY-MM-DD.csv` (finished days, written once). The job cache (`.cache/accum`): raw swaps for 3 days, poll times, candles, balances, backtest price history. |
| Dashboard | `site/accum/` (one page, sections instead of 12 separate screens), linked from every page's menu |
| Alerts | The site's existing pop-up alerts, through the Proven/Testing gate (`site/gate.js`) |
| Hosting | GitHub Pages; the page reads `data` branch `accum/snapshot.json` |

| Module | File |
|---|---|
| Data sources and registry | `accum/sources.py` |
| Evidence | `accum/evidence.py` |
| Score, routes, states | `accum/watch.py` |
| Forward evaluation | `accum/forward.py` |
| Paper account | `accum/paper.py` |
| Distressed recovery and its backtest | `accum/recovery.py` |
| Orchestration | `accum/run.py` |
| Settings | `accum/config.py` |

Why: it costs nothing, it runs on a schedule without anything to maintain, and it shares the honest-testing and
watchdog machinery of every other page.

## 2. Data sources (checked live on 19 Oct 2026)

| Source | Key | Gives | Doesn't give |
|---|---|---|---|
| GeckoTerminal | none | DEX pools, prices, liquidity, the **signing wallet of each recent swap** (Solana, Ethereum) | Only the last 300 swaps per pool, last 24 h. History is built forward from the lab's first run. |
| Solana public network | none | Token supply; a wallet's balance of a token | Top-holder lists: the call is refused from shared machines; it is tried, and shown as unavailable when refused. |
| Etherscan (free key) | `ETHERSCAN_API_KEY` secret | Ethereum token transfers and wallet balances | Other chains (paid plan); holder lists (paid "Pro" endpoint) |
| Binance public data | none | Daily candles with taker-buy volume; **delisted coins' history** | Who the buyers are |
| CoinGecko public | none | Market cap, supply, all-time high | Historical supply |
| DefiLlama | none | TVL and fees for DeFi protocols | Anything for non-DeFi coins; unlock schedules (paid) |

The page's **Data sources and health** section shows each source's last success, last error and connection state,
every hour. Missing values are never shown as zero.

## 3. Evidence categories (spec 4.3)

| Category | Meaning in this lab |
|---|---|
| **A, verified on-chain swap** | A DEX swap whose signing wallet (`tx_from_address`) bought or sold the token |
| **B, balance change** | A wallet's balance (Solana public network, Etherscan). Used only to estimate what a buyer held before. Never called a purchase. |
| **C, exchange trading activity** | Binance taker-buy share of volume. Never identifies a buyer. |
| **D, transfers** | Ethereum transfers that are not swap legs. Never counted as buying. Transfers into watched addresses and from them to exchange custody wallets are shown as warnings. |
| **E, inferred relationships** | Not built: there is no defensible free heuristic. Every count is "addresses", never "investors". |

## 4. Metrics (spec 5.4)

Only swaps of **$500+** are collected. Bots (30+ trades a day in one token) and round trips (daily net under 20% of
what was traded) are excluded from every acquisition figure and reported separately.

| # | Metric | Formula | When unavailable |
|---|---|---|---|
| 1 | Net acquired | Σ buy qty − Σ sell qty over the window (category A, non-bot) | `None` |
| 2 | Share of supply | net acquired / circulating supply. Circulating = CoinGecko market cap / price (through GeckoTerminal). | "unknown share"; no percentage is made up |
| 3 | Buyer-relative | net bought / (balance now − net bought) | "newly observed holder" if it held ~nothing; "reference balance unavailable" if no balance. Transfers in between make it an estimate, and the page says so. |
| 4 | Against other holders | Addresses net buying vs net selling ($1k+ a day); share of the buying done by the top 3 | Top-holder ranking unavailable (see section 2) |
| 5 | Persistence | 7/14/30/60/90-day windows. A window counts only when every day in it was observed (coverage ≥ 50% of the day), so there is no 90-day claim from 20 days of data. | Grey bars and "X of N days observed" |
| 6 | Against liquidity | 14-day net $ / pool liquidity, and net $ / traded $ | `None` |
| 7 | Price context | 14-day change and realised volatility from daily candles; "already repricing" at +30% | Context only, never required |
| 8 | Distribution | The addresses buying at detection have since sold half of what they had bought, or net selling of 0.05%+ of supply in 7 days. Transfers to an exchange are a warning, not proof. | — |

**Coverage.** Each hourly poll covers the time since the previous poll. If the feed returned its full 300 swaps, the
poll only covers back to its oldest swap. Each day's coverage is the share of the day covered, and gaps are shown.

## 5. Score v1 and confidence (spec 5.6)

Provisional, configurable in `accum/config.py`. It is a research ranking, not a probability. Missing data earns 0.

| Part | Max | From |
|---|---|---|
| Verified acquisition | 25 | 7-day net share, scaled to 2× route A's threshold, times swap coverage |
| Buyer-relative | 20 | Biggest buyer's multiple: 5×+ scores 20, 2×+ scores 14, 1×+ scores 8; a material new holder scores 12 |
| Persistence | 15 | 3 points per fully observed window with net buying |
| Share of supply | 10 | Longest fully observed window's share, scaled to 0.5% |
| Liquidity | 10 | 14-day net $ / liquidity, scaled to 25% |
| Participation | 10 | Addresses net buying (target 15) and buyers ÷ sellers (target 2.6) |
| Price | 5 | Full if the 14-day change is between −20% and +10%; 0 if already repricing |
| Data quality | 5 | Supply known, plus swap coverage |

The verified and supply parts both use supply share, but over different windows (7 days vs the longest complete
one). This is stated rather than hidden.

**Confidence:**
- **High:** coverage ≥ 90%, 30+ days observed, supply known, and buyers' balances known.
- **Medium:** coverage ≥ 70%, 7+ days observed, and supply known.
- **Low:** anything else.

**Hard disqualification:** pool liquidity under $100k.

## 6. Watchlist rules (spec 5.3, 5.7-5.9)

| Route | Rule |
|---|---|
| **A, verified acquisition** | 7 days: net ≥ 0.10% of supply, ≥ $25k, net buying on ≥ 4 days, coverage ≥ 80% |
| **B, concentrated** | 14 days: one non-exchange address net ≥ 0.05% of supply and ≥ $10k, and ≥ 2× its earlier holding (or a new holder) |
| **C, distributed** | 14 days: ≥ 15 addresses net buying $1k+, net ≥ 0.05% of supply, buyers ≥ 1.3× sellers |
| **X, exchange flow** | Binance, ≥ $2M a day: the 14-day taker-buy share at least 3 points above the previous 90 days, with the price between −15% and +10% |

A price rise is never required. A token qualifies with a flat or falling price; tests prove both.

**States:** Investigating, Accumulation detected, Persistent accumulation, Concentrated ownership risk, Accumulation
weakening, Distribution detected, Data insufficient, Disqualified, Archived. Risk flags sit alongside the state.

**Transitions:**
- **Detected → Persistent:** net buying in 3 or more of the last 4 weeks, and in every 7, 14 and 30-day window.
- **→ Weakening:** 7-day net at or below 0, or under 25% of the 7-day figure at detection.
- **→ Distribution detected:** see metric 8.
- **→ Archived:** 30 days without renewed accumulation.

Every transition is appended to `events.csv` with its time, reason, score and version. The first detection is written
to `detections.csv` with its frozen evidence and is never moved or deleted.

## 7. Testing (spec 9)

**Forward evaluation of the watchlist.** At 7/14/30/60/90 days after each detection:
- Return from the daily close to the daily close, minus a round trip of pool fees.
- Compared with the average of every other token watched on the same chain over the same days.
- Best and worst points along the way (MFE/MAE).
- Vanished tokens are kept as "no price".

It is judged like every other section: at least 30 detections judged at 30 days, a profit, an edge over the other
tokens with t ≥ 2. Until then the watchlist is **Testing only** (infra/proof.py unit `accum:watchlist`).

Wallet-level history isn't free, so **no historical backtest of accumulation is possible**. This is stated and not
faked. The first verdict needs roughly 90 days.

**Recovery backtest** (weekly, started by the first hourly run that finds it due):
- **Universe:** every Binance USDT coin, including 250+ delisted ones whose history Binance keeps.
- **Signal, at each month start:** 2+ years of history, 75%+ below the previous 2-year high, $1M+ average daily volume
  over 30 days, and 10%+ above the 30-day low.
- **Outcome:** forward 30/90/180-day returns after a 0.2% round trip, compared with every other eligible coin that
  day and with Bitcoin. A coin delisted before the horizon exits at its last price.
- **No lookahead:** only data up to each signal date is used. An inputs fingerprint makes re-runs comparable.
- **Verdict:** it uses the same Held-up rule as the Strategy lab, through unit `accum:recovery`.
- Fundamentals can't be backtested (no historical source), and this is said.

## 8. The $100 USDC paper account (spec 8, approved at $100 instead of 10,000)

- **Risk per trade:** 1% of equity. The invalidation level is the 14-day low minus 2%, and it must sit 3–25% below the entry.
  Otherwise the trade is ineligible; no stop is invented.
- **Limits:** a position is capped at 5% of equity, and total planned risk at 4%. The target must be at least 2× the risk.
- **Costs:** 0.3% pool fee each way, constant-product price impact, and a network fee ($0.01 on Solana, $0.30 assumed on
  Ethereum). Costs must stay under a quarter of the planned gain, which rules out most tiny Ethereum trades.
- **USDC:** converted 1:1 only while USDC is within 1% of a dollar on Binance. Otherwise new trades are blocked.
- **Exits:** the stop (a gap fills at the worse price), the target, a state of distribution, weakening, disqualified or
  archived, or 30 days held.
- **Records:** every decision is recorded with the rules in force.

## 9. Known limitations

- History starts on the lab's first day (19 Oct 2026). Persistence and the forward record need weeks to months.
- **Swap feed:** only $500+ swaps are collected, and at most 300 per pool per poll, so very busy pools have gaps (shown). The
  very largest pools (over $80M liquidity) are left out for this reason.
- **Addresses, not people:** an exchange custody wallet holds many people's coins (a curated list is flagged).
- **Ethereum:** balances and transfers need the free Etherscan key, and Etherscan's free plan covers no other chain.
- **Solana top-holder lists:** usually unavailable from free sources.
- **Recovery data:** unlock schedules, catalysts and security incidents have no reliable free source, so they score 0 and say so.
- **Supply:** CoinGecko's current circulating supply is used. Historical supply is not available.
- **Schedule:** GitHub's free scheduler can skip runs; the Tick job fills gaps, and the watchdog reports stale data.

## 10. Daily operations (for the owner)

1. Open the page. The banner says when it last updated. Over 2.5 hours old means a run failed; check the System
   status page.
2. Read **Signals and changes** for what changed today.
3. Click any candidate for its evidence, and **always read "Evidence against"**.
4. Nothing here is a trade. Only parts marked Proven on the Results page reach the alerts.
5. To switch on Ethereum balances and transfers, add the free `ETHERSCAN_API_KEY` repository secret (GitHub →
   Settings → Secrets and variables → Actions). The Data sources section then shows Etherscan as working.

## 11. Requirements traceability (spec parts 1-18)

Statuses:
- **Done:** built and tested.
- **Built:** in place, but its result needs time or data to show.
- **Limited:** constrained by free data.
- **Not built:** with the reason.

| Spec | Requirement | Status | Where |
|---|---|---|---|
| 1.1/1.2 | Real data only, no invented values, live trading off | Done | `accum/sources.py`; tests `test_live_trading_does_not_exist`, fixtures marked in `tests/test_accum.py` |
| 2.1 | Two separate strategies, no merged score | Done | `watch.py` vs `recovery.py`; separate page sections and proof units |
| 3 | Zero-budget architecture | Done (approved deviation) | Section 1 |
| 3.3 | Local, research, read-only, paper modes; live disabled | Done | `python -m accum.run` locally; no live code |
| 4.1 | Data-source registry with health | Done | `sources.REGISTRY`, page "Data sources and health" |
| 4.2 | Adapters: validation, retries, no duplicates, provenance, no 0 for missing | Done | `sources.Http`, `evidence.merge_trades`; tests `test_duplicate_swaps_are_counted_once`, `test_missing_supply...` |
| 4.3 | Categories A–E kept apart | Done | Section 3; `classify_transfers`; test `test_transfers_never_count...` |
| 5.2 | Universe with exclusion reasons | Done | `run.build_universe`; page list of exclusions |
| 5.3 | Routes A, B, C and exchange flow | Done | `watch.routes`, `run.exchange_flow` |
| 5.4 | Metrics 1–8 | Done; metric 4 limited | `evidence.window_metrics`, `buyer_relative`, `price_context` |
| 5.5 | Wallet clustering | Not built | No defensible free heuristic; counts say "addresses" |
| 5.6 | Score, confidence, versioning, hard rules | Done | `watch.score`, `confidence`, `hard_fail`; test `test_score_is_explained...` |
| 5.7 | Price-independent admission, frozen first detection | Done | tests `test_qualifies_with_a_flat_or_falling_price`; `detections.csv` |
| 5.8/5.9 | 9 states, transitions logged, history kept | Done | `watch.next_state`, `events.csv`; tests on weakening, distribution, insufficient data |
| 6 | Distressed recovery: screen, score, exclusions, separate view | Done; catalysts and unlocks limited | `recovery.py`; tests `test_recovery_screen_and_hard_exclusions` |
| 7 | Dashboard sections, evidence page, explainable alerts | Done (one page) | `site/accum/` |
| 8 | USDC paper account, sizing, limits, costs, separation from signals | Done | `paper.py`; 5 tests |
| 9 | Forward evaluation, no lookahead, survivorship, benchmarks | Built; results need time | `forward.py`, `recovery.bt_signals`; tests on lookahead, delisted coins, reproducibility |
| 10 | Database design | Done as versioned files | Section 1 |
| 11 | Service layers | Done | Module list in section 1 |
| 12 | Security: no secrets in code or logs, fail safe | Done | Key only as a GitHub secret; stale price or USDC depeg blocks trades |
| 13 | Alerts and scheduling | Done | Hourly job and Tick; alerts only for proven parts |
| 14 | Plain-language labels: fact, observed, inferred, missing | Done | Page wording; "addresses, not people"; "possible sale, not proof" |
| 15 | Phases and acceptance gates | Done | This build; tests run in CI |
| 16 | Test suite (32 cases) | Done; 24 tests cover them | `tests/test_accum.py` |
| 17 | Documentation | Done, consolidated here | This file, plus DECISIONS D119 |
| 18 | Final report | Done | Given to the owner in the build conversation |
