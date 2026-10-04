"""Forex manipulation and market-condition checks.

Built around what regulators actually fined banks for (2014-2015, about $10 billion across Citigroup, JPMorgan,
Barclays, RBS, UBS, HSBC and Bank of America) and what retail traders still meet today:

* FIX RIGGING. Dealers pushed prices in the minutes around the WM/Reuters 4 pm London fix to profit on client
  orders. -> No entries or sell-by times inside the London, ECB (14:15 Frankfurt) and Tokyo (9:55) fix windows, and
  an abnormal spike in a recent fix window that then reversed counts as a manipulation footprint.
* STOP HUNTING AND FRONT-RUNNING. Prices are pushed through levels where many stop orders sit (round numbers,
  recent lows/highs), the stops are filled, and the price snaps back. -> "Swept a recent low/high and closed back"
  is detected; failed breakouts against the idea count against it; exits avoid round levels and swept levels.
* THIN MARKETS. The Sunday open, the 5 pm New York rollover and Friday evening have little liquidity, wide
  spreads and are the easiest times to push prices. -> Warnings and a small penalty.
* BAD OR OFF-MARKET QUOTES. A pair's price must agree with the price implied by the two related pairs
  (EUR/JPY = EUR/USD x USD/JPY). A large gap means a broken or manipulated quote. -> Excluded.
* NEWS AMBUSHES. Central bank decisions and big data releases move prices in seconds and spreads explode.
  -> Heavy penalty inside the holding time, and no entry in the 30 minutes before.
* MANAGED CURRENCIES. Some central banks set or steer the official rate (naira, Egyptian pound, ...); what you can
  really trade at can differ a lot. -> Information only, with a warning.

Limits are jittered by a private, hourly-changing seed, always in the strict direction (as in dex/security.py).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from fx.pairs import MANAGED, NAMES, currencies, label
from scanner.integrity import mulberry32

BASE = {"fix_k": 4.0, "fix_retrace": 0.5, "hunt_atr": 0.10, "tri_dev": 0.0015, "news_before_min": 30.0}
PEN = {"fix_spike": 0.10, "in_fix": 0.10, "failed_breakout": 0.10, "stop_hunt": 0.05, "thin": 0.05,
       "news_today": 0.25, "news_days": 0.30}
FIXES = [("London 4 pm fix", "Europe/London", 16, 0, 15), ("ECB reference rate", "Europe/Berlin", 14, 15, 10),
         ("Tokyo fix", "Asia/Tokyo", 9, 55, 10)]


def thresholds(seed: int | None) -> dict:
    """fix_k, hunt_atr, tri_dev only get stricter (smaller); news_before_min only longer."""
    if seed is None:
        return dict(BASE)
    rnd = mulberry32(seed)
    out = {}
    for k, v in BASE.items():
        r = rnd()
        out[k] = v * (1 + 0.5 * r) if k == "news_before_min" else v if k == "fix_retrace" else v * (1 - 0.25 * r)
    return out


# ------------------------------------------------------------------------------------------- time windows
def fix_windows(day_utc: pd.Timestamp) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    out = []
    for name, tz, h, m, half in FIXES:
        for d in (-1, 0, 1):
            local = (day_utc.tz_convert(tz).normalize() + pd.Timedelta(days=d)).replace(hour=h, minute=m)
            if local.weekday() < 5:
                c = local.tz_convert("UTC")
                out.append((name, c - pd.Timedelta(minutes=half), c + pd.Timedelta(minutes=half)))
    return out


def in_fix(t: pd.Timestamp) -> str | None:
    for name, a, b in fix_windows(t):
        if a <= t <= b:
            return name
    return None


def avoid_fix(t: pd.Timestamp) -> pd.Timestamp:
    """Move a sell-by time to just before any fix window it falls into."""
    for _, a, b in fix_windows(t):
        if a <= t <= b:
            return a - pd.Timedelta(minutes=5)
    return t


def thin_market(t: pd.Timestamp) -> str | None:
    ny = t.tz_convert("America/New_York")
    mins = ny.hour * 60 + ny.minute
    if ny.weekday() == 4 and mins >= 15 * 60:
        return "Friday evening: liquidity is drying up before the weekend, spreads widen and prices are easier to push."
    if ny.weekday() == 6 or (ny.weekday() == 0 and mins < 2 * 60) or (ny.weekday() == 6 and mins >= 17 * 60):
        return "The market has only just opened for the week: thin trading, wide spreads and gaps."
    if 16 * 60 + 55 <= mins <= 17 * 60 + 15:
        return "Daily 5 pm New York changeover: spreads are at their widest for a few minutes."
    return None


# ------------------------------------------------------------------------------------------- price footprints
def atr(df: pd.DataFrame, n: int = 14) -> float:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def stop_hunt(df: pd.DataFrame, t: dict, look: int = 12, ref: int = 48) -> dict:
    """Swept-low (stops below a recent low were triggered, then price closed back above) and failed breakout
    (pushed above a recent high, closed back below) in the last `look` candles. Prices as given (a 'sell' idea
    passes inverted candles, so the same test works both ways)."""
    out = {"swept_low": None, "failed_high": None}
    if len(df) < ref + look + 2:
        return out
    a = atr(df) * t["hunt_atr"]
    for i in range(len(df) - look, len(df)):
        prior = df.iloc[i - ref:i]
        lo, hi = prior["low"].min(), prior["high"].max()
        bar = df.iloc[i]
        if bar["low"] < lo - a and bar["close"] > lo:
            out["swept_low"] = {"level": float(lo), "bars_ago": len(df) - 1 - i}
        if bar["high"] > hi + a and bar["close"] < hi:
            out["failed_high"] = {"level": float(hi), "bars_ago": len(df) - 1 - i}
    return out


def fix_spikes(df15: pd.DataFrame, t: dict, days: int = 3) -> list[dict]:
    """Fix windows in the last few days where the price jumped far more than normal and then mostly reversed:
    the footprint of fix rigging."""
    if df15 is None or len(df15) < 200:
        return []
    moves = (df15["close"] - df15["open"]).abs()
    typical = float(moves.tail(400).median()) or 1e-12
    out = []
    end = df15.index[-1]
    for name, a, b in [w for d in range(days) for w in fix_windows(end - pd.Timedelta(days=d))]:
        win = df15[(df15.index >= a) & (df15.index <= b)]
        if win.empty or b > end:
            continue
        move = float(win["close"].iloc[-1] - win["open"].iloc[0])
        after = df15[(df15.index > b)].head(4)
        if abs(move) > t["fix_k"] * typical * max(len(win), 1) ** 0.5 and len(after):
            back = float(after["close"].iloc[-1] - win["close"].iloc[-1])
            if np.sign(back) == -np.sign(move) and abs(back) >= t["fix_retrace"] * abs(move):
                out.append({"fix": name, "at": str(a), "move": move / float(win["open"].iloc[0])})
    return out


def triangular(last: dict[str, float]) -> dict[str, float]:
    """Gap between each cross pair's price and the price implied by the two USD pairs."""
    usd = {}
    for c in ("EUR", "GBP", "AUD", "NZD"):
        if f"{c}USD" in last:
            usd[c] = last[f"{c}USD"]                     # USD per 1 unit
    for c in ("JPY", "CHF", "CAD"):
        if f"USD{c}" in last:
            usd[c] = 1 / last[f"USD{c}"]
    usd["USD"] = 1.0
    out = {}
    for pair, px in last.items():
        b, q = currencies(pair)
        if pair[:3] == "USD" or pair[3:] == "USD" or b not in usd or q not in usd:
            continue
        out[pair] = px / (usd[b] / usd[q]) - 1
    return out


# ------------------------------------------------------------------------------------------- news and managed
def news_in(events: list[dict], pair: str, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    b, q = currencies(pair)
    out = []
    for e in events:
        if e.get("impact") != "High" or e.get("country") not in (b, q):
            continue
        at = pd.Timestamp(e["date"]).tz_convert("UTC")
        if start <= at <= end:
            out.append({**e, "at": at})
    return out


def managed_note(pair: str) -> str | None:
    for c in currencies(pair):
        if c in MANAGED:
            return (f"The {NAMES.get(c, c)} is managed by {MANAGED[c]}'s central bank. The official rate shown here "
                    "can differ a lot from the rate you can actually trade or get from a bank or exchange bureau.")
    return None


def side_text(pair: str, side: str) -> str:
    b, q = currencies(pair)
    up, down = (NAMES.get(b, b), NAMES.get(q, q)) if side == "buy" else (NAMES.get(q, q), NAMES.get(b, b))
    return f"{'Buy' if side == 'buy' else 'Sell'} {label(pair)}: expecting the {up} to strengthen against the {down}."
