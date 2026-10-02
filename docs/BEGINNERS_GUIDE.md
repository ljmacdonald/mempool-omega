# Beginner's guide: no market knowledge needed

## What this app does, in one paragraph
Every hour a computer program looks at about 60 popular cryptocurrencies. It picks the **5 it rates best right
now** for a short "buy, then sell within a day" trade and explains each pick in plain English. It also practises
trading Bitcoin and Ether with **$100,000 of pretend money**, so you can watch how it would have done without
risking anything. It never touches real money.

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
Give up after   24 hours
Chance this idea ends in profit: 58%   (average coin right now: 49%)
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
| **Give up after 24 hours** | If neither happens within a day, sell at whatever the price is. These are short-term ideas. |
| **Chance of profit** | The computer's estimate, learned from past data. It is *not* a guarantee. Compare it with "average coin right now". |
| **To risk $10, buy …** | Sizing help: if you buy this amount and the safety exit is hit, you lose about $10. Pick how much you are willing to lose *first*, then size the purchase. Never the other way round. |
| **Why** | The patterns that made the computer pick it, in plain words. |
| **⚠️ Be careful** | Warning signs: it already spiked (you might be buying the top), suspicious fake-looking volume, thin trading, overheating, or the whole market falling. |
| **Big-mover history** | In the last 90 days, how often it rose 20%+ within a week *and how often it fell 20%+*. Both numbers matter. |

## Market mood
At the top of the list: **Favourable / Mixed / Unfavourable**. This is how many of the ~60 coins the computer
expects to end in profit this hour. When it says *Unfavourable*, the best move is usually **to do nothing**.
Even when every idea is weak, the app still shows its top 5, ranked. Look at the scores, not just the order.

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
3. A model trained every night on the last ~6 weeks of hourly data for all these coins estimates the chance each
   buy idea ends in profit.
4. Turn that into an expected result per $1 risked (fees included), subtract penalties for warning signs, and
   convert it to the 0–10 score.
5. Show the 5 highest scores, then check every one of them 24 hours later in the track record.

**Known weakness (being honest):** the coin list is picked by *today's* trading volume, which favours coins that
recently rose. That makes the computer's past-data tests look better than reality. The **live track record** has
no such bias, so trust it over everything else.
