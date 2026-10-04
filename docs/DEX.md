# DEX tokens: scam defences and real costs

The DEX page (`site/dex/`, live at `/mempool-omega/dex/`) applies the scanner's ranking to tokens traded on decentralised exchanges on **Solana, BNB Chain, Ethereum and Robinhood Chain**. Two things matter far more there than on big exchanges:

1. **Scams and rug pulls.** Anyone can launch a token in minutes.
2. **Costs.** Pool fees, price impact, token taxes, network fees, front-running bots and snipers can easily take 2–10% of a short trade.

This page explains how both are handled. It is written for defence, at the level needed to recognise each trick.

## How it runs

| When | Where | What |
|---|---|---|
| Every hour (`.github/workflows/dex.yml`, minute 37 plus a random 0–5 min delay) | `dex/live.py` | Finds the busiest pools on each network (GeckoTerminal), measures real liquidity (DexScreener), downloads hourly candles, runs every security check (GoPlus, honeypot.is, RugCheck), ranks with the DEX model and writes `state/dex/snapshot.json`. Saves the top 5 per speed to the track record (`state/dex/history.csv`). |
| Every night (03:41 UTC) | `dex/improve.py` | Retrains the DEX models (champion/challenger, as in `scanner/improve.py`). Updates the bait monitor, probation and the learned sniper cost (`state/dex/adaptive.json`). |
| Every refresh, in your browser | `site/dex/app.js` + `site/dexengine.js` | Live prices and pool money for every candidate. Live security re-check of the top 8. All costs for **your** amount. Final ranking. In *My DEX trades*: price every 30 s, security every 2 min, and an immediate exit warning if anything turns bad. |

**Staying inside the free limits.** GeckoTerminal allows about 30 requests a minute, so the jobs use it sparingly:
- The candidate list is rediscovered every 6 hours. In between, one batch request refreshes 30 pools at once.
- Security checks (other services) run first. Price history is downloaded only for tokens that could pass.
- Each pool's history is downloaded once, then topped up with only the newest candles. It's kept in GitHub's free build cache (`.cache/dex`), not in the repository.
- Nightly training reads from that store.
- Live prices, pool money and trade counts come from DexScreener (300 requests a minute).

A normal hour needs roughly 25–45 GeckoTerminal requests. Every download step also has a time budget, so a slow day shortens the list instead of failing the run.

The security rules and the cost maths exist twice: in Python (server) and JavaScript (browser). `tests/test_dex.py` checks that both give identical answers, including on recorded real responses from GoPlus, honeypot.is and RugCheck.

## Hard rules (any one rejects a token)

| Rule | Published limit |
|---|---|
| Real liquidity: only the SOL/ETH/BNB/stablecoin side of the pool, counted twice | at least $500,000 |
| Pool age | at least 14 days |
| Days with real trading (at least $50k) out of the last 14 | at least 10 |
| Distinct buying / selling wallets in 24 h | at least 200 / 120 |
| Sellers ÷ buyers | at least 0.30 |
| Buy or sell tax | at most 5% |
| 10 biggest ordinary wallets (excluding pools, locks and burns) | at most 50% (unless listed on major exchanges) |
| Creator or owner holding | at most 20% |
| Pool money change | rejected if it falls more than 20% in 6 h or 35% in 24 h, or grows more than 4x in 3 days |
| Insider wallet networks (RugCheck) | at most 20 |

Also rejected:
- honeypots and failed sell simulations;
- taxes that can be raised while an owner exists;
- hidden owners or reclaimable ownership;
- owner powers (mint, pause, blacklist, whitelist, cooldown, sell limits, balance changes) while an owner exists;
- upgradeable code, closed-source code, self-destruct;
- on Solana: mint, freeze, permanent-delegate, transfer-hook, non-transferable or closable authorities;
- a creator with past scam tokens; "already rugged";
- copies of another token's name or symbol, and impersonators of major coins or stablecoins;
- any security setting that changed in the last 3 days;
- **any token that couldn't be verified.**

## Moves and counter-moves: a scammer who has read all of the above

| # | What a sophisticated scammer would do | Why it would beat a naive checker | What this system does |
|---|---|---|---|
| 1 | **Fake liquidity:** pair the token with a second token they also created, so the pool "holds" millions. | Liquidity totals count both sides at the pool's own price. | Only pools paired with SOL, ETH, BNB or a major stablecoin are considered, and only that side is counted. Seen live: pools showing $700M+ "liquidity" with zero trades. |
| 2 | **Fool the honeypot test:** whitelist the simulator's address, or block only wallets that bought after a certain block. | The test buys and sells fine. | **Revealed behaviour.** At least 120 different real wallets must have sold in the last 24 hours, and sellers must be at least 30% of buyers. Real selling by hundreds of strangers can't be faked cheaply. |
| 3 | **Low tax now, high tax later:** launch at 0% tax, then raise it to 99% once people hold. | A one-time tax check sees 0%. | If an owner can change the tax, the token is rejected whatever today's tax is. All security settings are fingerprinted every hour; any change in the last 3 days rejects the token. |
| 4 | **"Renounce" ownership but keep a back door** (hidden owner, reclaimable ownership, upgradeable proxy). | It looks renounced. | Hidden owners, reclaimable ownership and upgradeable code are rejected outright. Code that calls out to an owner-controlled contract is rejected. |
| 5 | **Age the token:** create it months ago, leave it dormant, then "revive" it for the pump. | An age filter passes. | Activity history: real trading on at least 10 of the last 14 days. A quiet old token doesn't count as established. |
| 6 | **Park liquidity to pass the filter,** then pull it after the pump. | The liquidity minimum passes. | Liquidity history is kept hourly. A pool whose money more than doubled in 3 days is penalised; more than 4x is rejected. A drop of 20% in 6 hours rejects it, and the browser compares live pool money with the hourly value every refresh. In *My DEX trades*, a 20% drop since you bought triggers **EXIT NOW**. |
| 7 | **Hide the supply across hundreds of fresh wallets** so holder concentration looks fine. | A top-10 check passes. | RugCheck insider-network detection (Solana); holder-count penalty; creator and owner share; tokens with a large insider bundle are rejected. *Partly undetectable*: wallets with no links between them look like real holders. |
| 8 | **Fake activity with bots** to look busy and trend. | Volume filters pass. | Distinct-wallet counts, not trades. Many trades per wallet and volume many times the pool size are penalised. |
| 9 | **Pay for promotion** (DexScreener boosts) right before selling. | Promotion brings buyers. | Paid promotion lowers the score. |
| 10 | **Copy a successful token's name,** or call it "USDC". | Name-based searches find the fake. | Same name or symbol as a token with more money behind it: rejected as a copy. Major coin or stablecoin names on the wrong address: rejected. The card always shows the real token address with a copy button. |
| 11 | **Fool one checker** (for example, a contract that behaves differently for GoPlus's simulation). | One clean report. | The worst result across GoPlus, honeypot.is and RugCheck is used. If any required checker can't run, the token isn't suggested (**fail closed**). |
| 12 | **Tune the token to sit just inside the published limits.** | The limits are public. | Every limit is tightened by a secret amount (minimums up to +30%, maximums down to −25%) from a private seed that changes every hour, per device and per server run. The published numbers are only the floor. |
| 13 | **Front-run our published list:** buy what the system is about to suggest, then sell to followers. | The list is public. | The ranking includes a learned **sniper cost**: how far our past ideas rose in the first hour after publication, compared with other candidates. The server list goes out after a random delay; the website re-ranks on each visitor's own schedule. The bait monitor penalises patterns that reverse after being suggested. |
| 14 | **Sandwich the follower's own swap** (MEV). | The swap is visible in the public mempool. | Its cost is in every calculation, per network, with and without protection, plus how to protect yourself on that network. |
| 15 | **Plant a scare** (fake warning signs) to shake holders out cheaply. | Panic selling. | The trade monitor only orders an exit for things that directly threaten the money: honeypot, tax raised, dangerous powers appearing, pool money being pulled. |

## What it can't stop (honest limits)

- **A patient, well-funded scammer** can build a clean, renounced, liquid token with real activity, then sell into buyers. Every check correctly sees a legitimate token. The safety exit and the time limit are the protection.
- **Modern (concentrated-liquidity) pools can't be locked.** Liquidity providers can withdraw at any time. The app watches and warns, but a pull can happen between checks.
- **Unlinked wallets** holding a hidden supply look like real holders.
- **New networks.** Robinhood Chain launched in mid-2026, so few of its tokens are 14 days old with $500k of liquidity. honeypot.is doesn't cover it yet, so it relies on real sellers instead.
- **Free data services can be wrong or slow,** which is why their worst answer is used and missing data rejects the token.

## Costs: the money you actually take out

`dex/costs.py` (mirrored in `site/dexengine.js`), for amount *A*:

- **Buy:** network fee → pool fee → price impact → sandwich bots (MEV) → snipers → buy tax.
  - Price impact uses the constant-product formula with depth equal to half the real liquidity: a buy of size *a* into depth *Q* gets the price × 1/(1 + *a*/*Q*).
- **Sell:** sell tax → pool fee → price impact → MEV → network fee.
- **Break-even price:** the exit price at which the money out equals *A*. It is solved exactly.
- **Ranking:** expected result per $1 risked = *p* × win − (1 − *p*) × loss − round-trip cost ÷ safety-exit distance − check penalties. High-cost tokens fall down the list.

Default MEV allowance without protection:

| Network | MEV allowance | Protection advice |
|---|---|---|
| Ethereum | 0.5% | Flashbots Protect or MEV Blocker |
| BNB Chain | 0.5% | A private RPC |
| Solana | 0.5% | Jito-protected or private transactions |
| Robinhood Chain | 0.2% | First-come, first-served ordering makes sandwiches harder |

The sniper cost starts at 0.5% and is re-learned nightly from the track record (bounded between 0.2% and 3%).
