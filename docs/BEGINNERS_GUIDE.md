# Beginner's guide: no market knowledge needed

**The app:** https://ljmacdonald.github.io/mempool-omega/ (open it on your phone or computer, nothing to install).

## What this app does, in one paragraph
A computer program watches about 60 popular cryptocurrencies. On the **live dashboard** it re-ranks them as often
as you choose (every 1, 2, 5, 15, 30 or 60 minutes, or when you click *Refresh now*) and shows the **5 best buy
ideas** for the trading speed you pick. Each idea gives you the buy price, when to take profit, when to get out to
limit a loss, and **the latest time to sell**. When you take a trade, the **My trades** page watches it and tells
you what to do *right now*: hold, take profit, exit, or sell because time is up. It never touches real money.

### Pick your trading speed
| Speed | You hold a trade for at most | Prices looked at | Good for |
|---|---|---|---|
| **Quick** | 1 hour | 5-minute candles | Watching the screen closely. Fees eat a lot of small moves. |
| **Short** (default) | 4 hours | 15-minute candles | Checking a few times a day |
| **Day** | 24 hours | 1-hour candles | Checking morning and evening |

### How often it refreshes
In the dashboard's left panel choose **Refresh the ideas**: Off, every 1, 2, 5, 15 or 30 minutes, or every hour.
Faster refresh means newer prices. The ranking itself only changes when a new candle closes (every 5 minutes for
Quick, 15 for Short, 60 for Day), so refreshing every minute mostly updates prices and your trade monitor.
Separately, every hour GitHub saves a copy of the ideas to `state/suggestions/LATEST.md`, sends them to Telegram
(if you set it up) and checks old ideas for the track record.

> **The honest truth first.** Nobody, human or computer, can reliably predict which coin will rise 20–50%.
> Coins that *can* jump 20% in a week *can also drop 20%* in a week, and they often do. This app ranks ideas
> and keeps a public **track record**, so you can judge it by results instead of promises. Treat it as a
> learning tool. If you ever use real money, only use money you can afford to lose completely.

---

## Where to look
| You want to see | Go to |
|---|---|
| This hour's top 5 ideas | Dashboard → **⭐ Top 5 ideas**, or the file `state/suggestions/LATEST.md` on GitHub |
| Whether past ideas actually worked | Dashboard → **📈 Track record**, or `state/suggestions/scoreboard.json` |
| The pretend-money account | Dashboard → **💼 Pretend account** |
| Alerts on your phone | Set up Telegram (see `MISSING_SECRETS.md` §1). You'll get the top 5 every hour. |

## How to read one idea card

```
1. SOL: score 7.1/10 (Strong) · risk: Medium
Buy near        $142.10
Take profit at  $148.60   (+4.6%)
Safety exit at  $138.90   (-2.3%)
Sell by         15:30 UTC (at the latest)
Chance it beats the market: 58%
To risk only $10, buy about $435 worth.
Why: Buyers have been more eager than sellers... Price is above its 1-day and 3-day average...
⚠️ Be careful: ...
```

| Line | What it means |
|---|---|
| **Score (0–10)** | The computer's overall rating. **5 = break-even** (fees included). Above 5 = it expects a small gain on average; below 5 = it expects a loss. Higher is better. |
| **Grade** | Strong (≥ 6.5) · Moderate (5.6–6.5) · Weak (5–5.6) · Avoid (< 5). "Avoid" ideas are still listed so you always see 5, but the computer doesn't like them. |
| **Risk level** | How wildly the coin usually moves in a day. Low < 3% · Medium 3–6% · High 6–9% · Very high > 9%. Higher risk means bigger possible gains *and* losses. |
| **Buy near** | The latest price. Prices change every second, so this is approximate. |
| **Take profit** | If the price climbs to here, sell and keep the gain. It's set at **twice** the safety-exit distance (a "2 to 1" reward-to-risk). |
| **Safety exit** (a "stop-loss") | If the price falls to here, sell and accept a small loss. **This is the most important line.** It stops a small loss turning into a big one. |
| **Sell by** | The time limit for the speed you chose (1 hour, 4 hours or 24 hours). If neither the take-profit nor the safety exit has happened by then, sell at whatever the price is. |
| **Chance it beats the market** | The computer's estimate that this coin will do better than the *average* coin over the same period. 50% = no better than average. It is *not* a guarantee. |
| **To risk $10, buy …** | Sizing help: if you buy this amount and the safety exit is hit, you lose about $10. Pick how much you are willing to lose *first*, then size the purchase. Never the other way round. |
| **Why** | The patterns that made the computer pick it, in plain words. |
| **⚠️ Be careful** | Warning signs: it already spiked (you might be buying the top), suspicious fake-looking volume, thin trading, overheating, or the whole market falling. |
| **Big-mover history** | In the last 90 days, how often it rose 20%+ within a week *and how often it fell 20%+*. Both numbers matter. |

## The "My trades" monitor: when to get out
Click **"I bought …"** on an idea (or add your own trade with the form). The **🧭 My trades** tab then shows, live:

| It says | What to do |
|---|---|
| **HOLD** | Nothing yet. Neither exit has been reached. It shows the time left. |
| **TAKE PROFIT NOW** | The price reached your take-profit. Sell and keep the gain. |
| **EXIT NOW (safety exit)** | The price fell to your safety exit. Sell to keep the loss small. |
| **TIME'S UP: SELL NOW** | The time limit passed without either exit. The idea didn't work out in time, so sell. |
| **CONSIDER LEAVING EARLY** | The reasons for the trade have faded (its score dropped below 4.5). Leaving early is reasonable. |

Your trades are stored in the page's web address. **Bookmark the page after adding a trade** to keep them.
Click "I've sold it" to remove a trade.

## Market mood
At the top of the list: **Favourable / Mixed / Unfavourable**. This is how many of the ~60 coins the computer
expects to end in profit this hour. When it says *Unfavourable*, the best move is usually **to do nothing**.
Even when every idea is weak, the app still shows its top 5, ranked. Look at the scores, not just the order.

## Where to buy it, and what you'd really take out
Open **"Where to buy it cheapest"** on any idea card. For the amount you typed, the app reads the live order books
of Binance, OKX, Bitget, Gate.io, HTX, Kraken and Coinbase, and the biggest decentralised-exchange (DEX) pools.
For each place it shows the money you'd actually **take out** if the coin reaches the take profit or the safety
exit, after every cost:
- **Trading fees** (the "taker" fee you pay on a normal buy or sell), charged on the way in and on the way out.
- **Slippage**: your amount is "walked" through the real orders waiting on the book, so a big order pays more.
- **Withdrawal fee** to move your money off the exchange.
- On a DEX: the **pool fee**, **price impact**, **network (gas) fees** for both swaps, and the cost of
  **MEV / front-running**. That's a bot that sees your swap before it's confirmed, buys just ahead of you and
  sells just after ("sandwich"). The app shows the cost with and without protection and how to protect
  yourself on that network (for example a private RPC such as Flashbots Protect, or a low slippage limit).

The fees are standard entry-level rates. Yours may be lower. Fake tokens often copy popular names, so on a DEX
always check the token address.

## The DEX tokens page
At the top of the app, switch between **Exchange coins** and **DEX tokens**. The DEX page looks for tokens traded on
decentralised exchanges on Solana, BNB Chain, Ethereum and Robinhood Chain. Use the network buttons to pick one, or
*All*.

DEX tokens move much more, and scams are common, so every token must pass strict safety rules first:
- at least $500,000 of real money in its pool;
- at least 14 days old and actively traded;
- hundreds of real people buying **and selling**;
- taxes of 5% or less that nobody can raise;
- no owner who can print tokens, freeze wallets or block sales.

The rules are designed assuming the scammer has read them (see *How it works* on that page). If nothing passes, the
page says so. That's the system protecting you, not a fault.

Each card shows the **real token address**: always check it before buying, because fakes copy names. The calculator
shows the money you'd actually take out after every DEX cost, including front-running bots and snipers. If you tap
*I bought this*, the page watches the pool and the token's safety every 2 minutes, and tells you to **EXIT NOW** if
money is being pulled or selling gets blocked.

## Day and night colours
Use the **☀️ Day / 🌙 Night / Auto** switch at the top of any page. *Auto* follows your phone or computer.

## The self-improvement tab
Every night the system reviews itself, using evidence only:
- It tests several versions of each model on recent data they never saw and keeps the best. A new version must be
  clearly better to replace the current one.
- It re-weighs each fake-signal check by how well it actually predicted bad outcomes.
- It checks whether its own ideas are being used as bait by manipulators.
- It puts a trading speed **on probation** (lower scores, with a label) if its last 50 ideas did worse than random picks.

Every change is written in plain English in the **Self-improvement** tab.

## The track record: the most important page
Every idea is checked 24 hours later against what really happened: bought at the next hour's price, then sold at
the take-profit, at the safety exit, or after 24 hours, minus 0.2% in fees. You'll see:
- **Ended in profit**: what share of ideas made money.
- **Average per idea**: the average gain or loss.
- **Random pick**: what you'd have got by picking any of the scanned coins at random. If the top 5 don't beat
  random picks over a few weeks, the ranking isn't adding value. That's important to know.
- **$100 in each idea**: total pretend profit or loss.

Give it **at least 2–4 weeks** (hundreds of ideas) before drawing conclusions. A few days means nothing.

## Simple rules that protect beginners
1. **Decide how much you could lose before buying.** Many traders risk only 1–2% of their money on one idea.
2. **Always use the safety exit.** Most big losses come from "it'll come back" thinking.
3. **Don't chase coins that already jumped 30%+ in a day.** The app warns you when this happens.
4. **Bitcoin leads the market.** If Bitcoin is falling hard, most coins fall too.
5. **Avoid coins with thin trading.** They're easy for big players to push around.
6. **No idea is a sure thing**, including "Strong" ones. A 60% chance still loses 4 times out of 10.

## Glossary
| Word | Meaning |
|---|---|
| **USDT** | A "stablecoin" worth about $1. Coins are priced in it (SOLUSDT = SOL's price in dollars). |
| **Long / buy** | Profit if the price goes **up**. All scanner ideas are buys. |
| **Short** | Profit if the price goes **down**. Only the pretend BTC/ETH engine does this. |
| **Volatility** | How much a price swings. High volatility = big moves both ways. |
| **Take-profit** | A planned price to sell at for a gain. |
| **Stop-loss / safety exit** | A planned price to sell at to cap a loss. |
| **Reward-to-risk (2:1)** | You aim to win $2 for every $1 you risk. |
| **Fees** | What the exchange charges (about 0.1% each time you buy or sell). Included in all numbers here. |
| **Volume** | How much of a coin was traded. More volume = harder to manipulate. |
| **Buyers more eager than sellers** | More trades happened because buyers accepted the asking price than because sellers accepted the bid. It's a sign of buying pressure. |
| **RSI** | A 0–100 "overheating" meter. Above 80 = maybe overheated, below 30 = maybe oversold. |
| **Pump** | A fast price spike, sometimes pushed by people who plan to sell to latecomers. |
| **Wash trading** | Fake trading (someone trading with themselves) to make a coin look popular. |
| **Spoofing** | Placing big fake orders to trick others, then cancelling them. |
| **Paper trading** | Practising with pretend money. |
| **Trust score** | The app's 0–1 rating of how likely a market signal is real rather than faked. |
| **Kill switch / emergency stop** | Automatically closes all pretend trades if losses or data problems get too big. |
| **Backtest** | Testing the rules on past data, using only information that was available at the time. |

## How the 5 ideas are chosen
1. Take Binance's most-traded coins (about 60), skipping stablecoins, gold tokens, stock tokens and tiny or new coins.
2. For each coin, measure simple things: recent price change, trend, buying pressure, activity versus normal,
   overheating, strength versus Bitcoin, and how much it usually swings.
3. A model for each speed, retrained every night on recent data for all these coins, estimates the chance each
   coin does **better than the average coin** over the hold time. Comparing with the average removes "everything
   went up lately" luck.
4. Turn that into an expected result per $1 risked, **assuming the market as a whole goes nowhere** and including
   fees, subtract penalties for warning signs, and convert it to the 0–10 score.
5. Show the 5 highest scores, then check every one of them after its time limit in the track record.

**Known weakness (being honest):** the coin list is picked by *today's* trading volume, which favours coins that
recently rose. That makes the computer's past-data tests look better than reality. The **live track record** has
no such bias, so trust it over everything else.
