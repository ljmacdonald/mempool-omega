# How every trade ends — the six exit rules, in plain English

Mempool Omega never opens a position without knowing in advance how it will close it.
At the moment a (paper) trade is opened, an **exit plan** is attached to it. Six rules watch the trade.
**Whichever one fires first wins** and closes the trade.

| # | Rule | Plain English | Default setting | Code |
|---|------|---------------|-----------------|------|
| 1 | **Profit target** | "We were right. Take the money." | 1.5 × the expected 1-hour price wiggle (or the model's expected edge, if bigger) | `risk/exits.py` |
| 2 | **Stop loss** | "We were wrong. Get out before it gets worse." | 1.0 × the expected 1-hour price wiggle | `risk/exits.py` |
| 3 | **Time stop** | "Nothing happened. Our idea had a shelf life, and it expired." | 12 bars (12 × 5 min = 1 hour) | `ExitConfig.time_stop_bars` |
| 4 | **Signal decay** | "The reason we entered has gone away or reversed." | Model's conviction flips against us by more than 3 points | `ExitConfig.decay_threshold` |
| 5 | **Trust collapse** | "The data that convinced us now looks fake. Assume it was bait." | Signal trust < 0.30 | `RiskConfig.trust_collapse` |
| 6 | **Kill switch** | "Something is wrong with the whole system. Close everything and stop." | Daily loss ≥ 3 %, 5 losses in a row, data failure, or system-wide trust collapse | `risk/limits.py` |

## The details, one by one

### 1. Profit target
- When the trade opens we estimate how much the price normally moves in an hour (its *volatility*).
- The target is set 1.5 × that typical move away from our entry, in our favour.
- It sits as a **resting limit order**, so if price touches it we get our price and pay the lower *maker* fee.

### 2. Stop loss
- Set 1.0 × the typical hourly move away from our entry, against us.
- Because it is scaled by volatility, it is wider on wild days and tighter on calm days. A fixed "2 %" stop would get hit by noise on wild days and be too loose on calm ones.
- If price *gaps* through the stop (opens beyond it), we assume we got the worse gap price. No wishful thinking.
- If a single 5-minute bar touches **both** the stop and the target, we assume the **stop** happened first (pessimistic on purpose).
- The stop distance also sizes the trade: we size so that hitting the stop loses at most **1 % of equity**.

### 3. Time stop
- Our signals are short-lived (minutes to an hour). If the trade has neither won nor lost after 12 bars (one hour), the idea has gone stale and we close it.
- This stops capital being tied up in "dead" trades and limits exposure to surprise news.

### 4. Signal decay
- Every 5 minutes the model re-scores the market. If its view flips against our position (e.g. we're long and it now leans meaningfully bearish), we leave.
- A small buffer (3 percentage points) stops us flip-flopping on noise.

### 5. Trust collapse (the anti-manipulation exit)
- Every signal carries a **Trust Score** from 0 (probably fake) to 1 (very reliable). See `asi/trust.py`.
- Manipulators often fake a signal (a huge order-book wall, a burst of fake volume, a spammy mempool) to lure traders in, then pull it.
- If the trust behind our position drops below 0.30, we **assume we were baited** and exit immediately, even at a small loss.

### 6. Kill switch (flatten everything)
The kill switch closes **all** positions and blocks new ones when any of these happens:
- **Daily drawdown**: equity is down 3 % or more since the start of the UTC day. Resets the next UTC day.
- **Losing streak**: 5 losing trades in a row. Resets the next UTC day.
- **Data failure**: data quality collapses (stale prices, primary exchange unreachable, venues disagreeing wildly). Re-arms automatically when data is healthy again.
- **System-wide trust collapse**: the average trust across all symbols is extremely low (the whole market looks manipulated). Re-arms automatically.

Every kill-switch event is written to the ledger (`state/ledger/events_*.csv`) and sent as a Telegram alert.

## Where to see it
- Every closed trade in `state/ledger/trades_YYYY-MM.csv` has an `exit_reason` column with one of:
  `profit_target`, `stop_loss`, `time_stop`, `signal_decay`, `trust_collapse`, `kill_switch`.
- The dashboard's **Trades** tab shows a bar chart of how often each rule fired.

## How to change the settings
All defaults live in `core/config.py` (`ExitConfig` and `RiskConfig`). The daily kill-switch limit can also be set
without code through the GitHub repository variable `OMEGA_DAILY_DD_KILL` (e.g. `0.02` for 2 %).
After changing exit settings, run the backtest again (`make backtest`). The labels the model learns from use the same
barriers, so the model and the exits stay consistent.
