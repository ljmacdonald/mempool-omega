"""Famous public trading strategies, copied rule for rule from where they were published, so they can be re-tested
honestly (lab/run.py): on our markets, after trading costs, against random entries.

Every strategy takes a price table (open, high, low, close, volume; UTC index) and returns its trades:
{"side": +1 long / -1 short, "i_in", "i_out", "entry", "exit", "gross" (return before costs), "reason"} plus "open": True
for a trade that hasn't finished yet (left out of the results, shown on the page as "in a trade"). Trades enter only
on prices that were known at the time: a signal on a candle's close fills at that close (if the source says so) or
at the next candle's open.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------------------------- indicators
def rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder's RSI (TA-Lib's RSI after its warm-up)."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    out[dn == 0] = 100.0
    out.iloc[:n] = np.nan
    return out


def ema(x: pd.Series, n: int) -> pd.Series:
    out = x.ewm(span=n, adjust=False).mean()
    out.iloc[: n - 1] = np.nan
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    return tr


def supertrend_dir(df: pd.DataFrame, period: int, multiplier: float) -> np.ndarray:
    """freqtrade/technical `supertrend` (technical/indicators/supertrend.py), line for line: 'up', 'down' or None."""
    high, low, close = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    n = len(df)
    atr = true_range(df).rolling(period).mean().to_numpy()
    basic_ub = (high + low) / 2 + multiplier * atr
    basic_lb = (high + low) / 2 - multiplier * atr
    fu, fl, st = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(period, n):
        fu[i] = basic_ub[i] if basic_ub[i] < fu[i - 1] or close[i - 1] > fu[i - 1] else fu[i - 1]
        fl[i] = basic_lb[i] if basic_lb[i] > fl[i - 1] or close[i - 1] < fl[i - 1] else fl[i - 1]
    for i in range(period, n):
        if st[i - 1] == fu[i - 1]:
            st[i] = fu[i] if close[i] <= fu[i] else fl[i]
        elif st[i - 1] == fl[i - 1]:
            st[i] = fl[i] if close[i] >= fl[i] else fu[i]
    with np.errstate(invalid="ignore"):
        return np.where(st > 0, np.where(close < st, "down", "up"), None)


def heikin_ashi(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    hc = (o + h + lo + c) / 4
    ho = np.empty(len(df))
    ho[0] = (o[0] + c[0]) / 2
    for i in range(1, len(df)):
        ho[i] = (ho[i - 1] + hc[i - 1]) / 2
    return ho, hc


def crossed_above(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift() <= b.shift())


# ---------------------------------------------------------------------------------------------- freqtrade back-tester
def freqtrade_sim(df: pd.DataFrame, enter: np.ndarray, exit_sig: np.ndarray, roi: dict, stoploss: float, bar_min: int,
                  trailing: tuple | None = None, exit_profit_only: bool = False) -> list[dict]:
    """Long-only, one trade at a time, like freqtrade's backtesting: a signal on a closed candle enters at the next
    open; the stop loss is checked before the take-profit table ("minimal_roi": minutes in trade -> profit to take);
    an exit signal leaves at the next open. trailing = (positive, offset, only_offset_is_reached)."""
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    n = len(df)
    steps = sorted((int(k), float(v)) for k, v in roi.items())
    out: list[dict] = []
    i = 1
    while i < n:
        if not (enter[i - 1] and not exit_sig[i - 1]):
            i += 1
            continue
        i_in, entry = i, o[i]
        stop, top, done = entry * (1 + stoploss), entry, None
        j = i
        while j < n:
            if j > i_in and exit_sig[j - 1] and (not exit_profit_only or o[j] > entry):
                done = (j, o[j], "exit signal")
                break
            if lo[j] <= stop:
                done = (j, min(o[j], stop) if j > i_in else stop, "stop loss")
                break
            mins = (j - i_in) * bar_min
            thr = [v for k, v in steps if k <= mins][-1] if steps and steps[0][0] <= mins else None
            if thr is not None and h[j] >= entry * (1 + thr):
                done = (j, max(o[j], entry * (1 + thr)) if j > i_in else entry * (1 + thr), "take profit")
                break
            top = max(top, h[j])
            if trailing:
                pos, off, only_off = trailing
                if top / entry - 1 > off:
                    stop = max(stop, top * (1 - pos))
                elif not only_off:
                    stop = max(stop, top * (1 + stoploss))
            j += 1
        if done is None:
            out.append({"side": 1, "i_in": i_in, "i_out": n - 1, "entry": entry, "exit": c[-1], "gross": c[-1] / entry - 1,
                        "reason": "", "open": True, "stop": stop})
            break
        jx, px, why = done
        out.append({"side": 1, "i_in": i_in, "i_out": jx, "entry": entry, "exit": px, "gross": px / entry - 1, "reason": why})
        i = jx + 1 if why != "exit signal" else jx
    return out


# ---------------------------------------------------------------------------------------------- the strategies
def orb(df: pd.DataFrame) -> list[dict]:
    """Opening range breakout (Zarattini & Aziz 2023, "Can Day Trading Really Be Profitable?"): the first 5-minute
    candle of the New York session sets the direction (up candle: buy; down candle: sell; no change: no trade).
    Enter at the open of the second candle, stop at the other end of the first candle, take profit at 10 times the
    risk, otherwise close at the end of the day."""
    ny = df.index.tz_convert("America/New_York")
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    pos = np.arange(len(df))
    out = []
    for _, ix in pd.Series(pos, index=ny).groupby(ny.date):
        ix = ix.to_numpy()
        if len(ix) < 3 or (ny[ix[0]].hour, ny[ix[0]].minute) != (9, 30):
            continue
        f = ix[0]
        side = 1 if c[f] > o[f] else -1 if c[f] < o[f] else 0
        if not side:
            continue
        entry = o[ix[1]]
        stop = lo[f] if side == 1 else h[f]
        risk = (entry - stop) * side
        if risk <= 0:
            continue
        target = entry + 10 * risk * side
        done = None
        for j in ix[1:]:
            if (lo[j] <= stop) if side == 1 else (h[j] >= stop):
                done = (j, min(o[j], stop) if side == 1 and j > ix[1] else max(o[j], stop) if j > ix[1] else stop, "stop loss")
                break
            if (h[j] >= target) if side == 1 else (lo[j] <= target):
                done = (j, target, "take profit")
                break
        live = ny[ix[-1]].hour < 15 or (ny[ix[-1]].hour == 15 and ny[ix[-1]].minute < 55)
        if done is None and live:
            out.append({"side": side, "i_in": ix[1], "i_out": ix[-1], "entry": entry, "exit": c[ix[-1]], "gross": side * (c[ix[-1]] / entry - 1),
                        "reason": "", "open": True, "stop": stop, "target": target})
            continue
        j, px, why = done or (ix[-1], c[ix[-1]], "end of day")
        out.append({"side": side, "i_in": ix[1], "i_out": j, "entry": entry, "exit": px, "gross": side * (px / entry - 1), "reason": why,
                    "stop": stop, "target": target, "r": side * (px - entry) / risk})
    return out


def rsi2(df: pd.DataFrame) -> list[dict]:
    """Connors RSI(2) ("Short Term Trading Strategies That Work", 2008): buy at the close when the price is above its
    200-day average and the 2-day RSI is below 10; sell at the close when the price closes above its 5-day average."""
    c = df["close"]
    sma200, sma5, r2 = c.rolling(200).mean().to_numpy(), c.rolling(5).mean().to_numpy(), rsi(c, 2).to_numpy()
    cc = c.to_numpy(float)
    out, i, n = [], 200, len(df)
    while i < n:
        if not (cc[i] > sma200[i] and r2[i] < 10):
            i += 1
            continue
        j = next((k for k in range(i + 1, n) if cc[k] > sma5[k]), None)
        if j is None:
            out.append({"side": 1, "i_in": i, "i_out": n - 1, "entry": cc[i], "exit": cc[-1], "gross": cc[-1] / cc[i] - 1, "reason": "", "open": True})
            break
        out.append({"side": 1, "i_in": i, "i_out": j, "entry": cc[i], "exit": cc[j], "gross": cc[j] / cc[i] - 1, "reason": "close above 5-day average"})
        i = j + 1
    return out


def rsi2_watch(df: pd.DataFrame) -> dict:
    """How close the latest closed day is to a Connors RSI(2) buy signal."""
    c = df["close"]
    return {"rsi2": float(rsi(c, 2).iloc[-1]), "above200": bool(c.iloc[-1] > c.rolling(200).mean().iloc[-1]),
            "sma5": float(c.rolling(5).mean().iloc[-1]), "close": float(c.iloc[-1])}


def turtle(df: pd.DataFrame) -> list[dict]:
    """Turtle Traders' System 1 (Richard Dennis and William Eckhardt, 1983; rules published by Curtis Faith): buy when
    the price breaks above the highest high of the last 20 days, sell short below the lowest low; exit on a break of
    the 10-day low (high for shorts) or at a stop 2 N (2 x the 20-day average true range) from the entry. The
    original "skip after a winning trade" filter is left out."""
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    hi20, lo20 = df["high"].rolling(20).max().shift().to_numpy(), df["low"].rolling(20).min().shift().to_numpy()
    hi10, lo10 = df["high"].rolling(10).max().shift().to_numpy(), df["low"].rolling(10).min().shift().to_numpy()
    N = true_range(df).ewm(alpha=1 / 20, adjust=False).mean().shift().to_numpy()
    out, i, n = [], 21, len(df)
    while i < n:
        up, dn = h[i] > hi20[i], lo[i] < lo20[i]
        if up == dn or not N[i] == N[i]:
            i += 1
            continue
        side = 1 if up else -1
        entry = max(o[i], hi20[i]) if up else min(o[i], lo20[i])
        stop = entry - 2 * N[i] * side
        done = None
        for j in range(i, n):
            if j == i:
                if (lo[j] <= stop) if side == 1 else (h[j] >= stop):
                    done = (j, stop, "2N stop")
                    break
                continue
            ex = max(lo10[j], stop) if side == 1 else min(hi10[j], stop)
            if (lo[j] <= ex) if side == 1 else (h[j] >= ex):
                done = (j, min(o[j], ex) if side == 1 else max(o[j], ex), "2N stop" if ex == stop else "10-day exit")
                break
        if done is None:
            out.append({"side": side, "i_in": i, "i_out": n - 1, "entry": entry, "exit": c[-1], "gross": side * (c[-1] / entry - 1),
                        "reason": "", "open": True, "stop": stop})
            break
        j, px, why = done
        out.append({"side": side, "i_in": i, "i_out": j, "entry": entry, "exit": px, "gross": side * (px / entry - 1), "reason": why})
        i = j + 1
    return out


def golden_cross(df: pd.DataFrame) -> list[dict]:
    """Golden cross: buy at the next open after the 50-day average closes above the 200-day average; sell at the next
    open after it closes back below."""
    c = df["close"]
    s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
    up, dn = crossed_above(s50, s200).to_numpy(), crossed_above(s200, s50).to_numpy()
    out = freqtrade_sim(df, up, dn, {}, -0.99, 1440)
    for t in out:
        t["stop"] = None            # no safety exit in this strategy
    return out


ST_BUY = [(4, 8), (7, 9), (1, 8)]       # (multiplier, period) from the strategy's tuned buy_params / sell_params
ST_SELL = [(1, 16), (3, 18), (6, 18)]


def supertrend(df: pd.DataFrame) -> list[dict]:
    """freqtrade-strategies "Supertrend" (@juankysoriano), with its published hyperopt-tuned settings: buy when three
    Supertrends are all up, sell when three others are all down; take-profit table, -26.5% stop, trailing stop."""
    vol = df["volume"].to_numpy(float) > 0
    buy = np.all([supertrend_dir(df, p, m) == "up" for m, p in ST_BUY], axis=0) & vol
    sell = np.all([supertrend_dir(df, p, m) == "down" for m, p in ST_SELL], axis=0) & vol
    return freqtrade_sim(df, buy, sell, {"0": 0.087, "372": 0.058, "861": 0.029, "2221": 0}, -0.265, 60,
                         trailing=(0.05, 0.144, False))


def bband_rsi(df: pd.DataFrame) -> list[dict]:
    """freqtrade-strategies "BbandRsi" (Gert Wohlgemuth): buy when the 14-hour RSI is below 30 and the price closes
    under the lower Bollinger band (20, 2 on the typical price); sell when the RSI is above 70; take 10% profit;
    stop at -25%."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mid, sd = tp.rolling(20).mean(), tp.rolling(20).std(ddof=0)
    r = rsi(df["close"], 14)
    buy = ((r < 30) & (df["close"] < mid - 2 * sd)).to_numpy()
    return freqtrade_sim(df, buy, (r > 70).to_numpy(), {"0": 0.1}, -0.25, 60)


def strategy001(df: pd.DataFrame) -> list[dict]:
    """freqtrade-strategies "Strategy001" (Gerald Lonlas), 5-minute candles: buy when the 20 EMA crosses above the 50
    EMA on a green Heikin-Ashi candle above the 20 EMA; sell on the opposite pattern only if in profit; take profit
    5% at once, 4% after 20 min, 3% after 30 min, 1% after 60 min; stop at -10%."""
    c = df["close"]
    e20, e50, e100 = ema(c, 20), ema(c, 50), ema(c, 100)
    ho, hc = heikin_ashi(df)
    buy = (crossed_above(e20, e50) & (hc > e20) & (ho < hc)).to_numpy()
    sell = (crossed_above(e50, e100) & (hc < e20) & (ho > hc)).to_numpy()
    return freqtrade_sim(df, buy, sell, {"60": 0.01, "30": 0.03, "20": 0.04, "0": 0.05}, -0.10, 5, exit_profit_only=True)


def random_baseline(df: pd.DataFrame, trades: list[dict], cost: float, draws: int = 20, seed: int = 7) -> list[float]:
    """For each trade: the average result of entering at a random time instead (same direction, same number of
    candles held, same costs). If a strategy doesn't beat this, its timing adds nothing."""
    o, c = df["open"].to_numpy(float), df["close"].to_numpy(float)
    rng = np.random.default_rng(seed)
    n, out = len(df), []
    for t in trades:
        d = max(1, t["i_out"] - t["i_in"])
        if n - d - 1 <= 0:
            out.append(float("nan"))
            continue
        starts = rng.integers(0, n - d - 1, draws)
        out.append(float(np.mean(t["side"] * (c[starts + d] / o[starts] - 1))) - cost)
    return out
