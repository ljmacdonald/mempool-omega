"""Red-team attack simulators.

Each attack mutates a *raw* bar frame to plant a bait signal at random bars and
then moves the subsequent price AGAINST the bait direction (that is how a
manipulator profits from followers). The back-tester then compares a naive
strategy (no ASI) against the ASI-gated strategy on the attacked data.

Attacks: spoofing, wash trading, mempool spam/poisoning, liquidation hunting
cascades, funding manipulation, cross-venue lead-lag spoofing.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

ATTACKS: dict[str, Callable] = {}


def attack(name: str):
    def deco(fn):
        ATTACKS[name] = fn
        return fn
    return deco


def _shift_price_after(df: pd.DataFrame, t: int, direction: int, sigma: float, k: float = 2.5, h: int = 6) -> None:
    """Move price by -direction * k * sigma over the h bars after t (bait reversal)."""
    n = len(df)
    end = min(t + 1 + h, n)
    if t + 1 >= n:
        return
    path = np.linspace(0, -direction * k * sigma, end - (t + 1) + 1)[1:]
    factor = np.ones(n)
    factor[t + 1:end] = np.exp(path)
    factor[end:] = np.exp(path[-1]) if len(path) else 1.0
    for c in ("open", "high", "low", "close", "close_coinbase", "close_kraken", "close_okx", "mark_price"):
        if c in df:
            df[c] = df[c].to_numpy() * factor


def _sigma(df: pd.DataFrame) -> float:
    return float(np.log(df["close"]).diff().std())


@attack("spoofing")
def spoofing(df, rng, t):
    d = rng.choice([-1, 1])
    i = df.index[t]
    df.loc[i, "book_ofi"] = 0.95 * d
    big, small = df["bid_depth"].median() * 6, df["ask_depth"].median() * 0.5
    df.loc[i, "bid_depth"], df.loc[i, "ask_depth"] = (big, small) if d > 0 else (small, big)
    df.loc[i, "depth_persist"] = 0.08          # walls vanish on approach
    df.loc[i, "cancel_rate"] = 0.92
    return d


@attack("wash_trading")
def wash_trading(df, rng, t):
    d = rng.choice([-1, 1])
    i = df.index[t]
    df.loc[i, "volume"] = df["volume"].median() * 8
    df.loc[i, "taker_buy_volume"] = df.loc[i, "volume"] * (0.97 if d > 0 else 0.03)
    # no price impact: flatten the bar and carry the level forward so the path stays continuous
    f = df.loc[i, "open"] / df.loc[i, "close"]
    for c in ("close", "close_coinbase", "close_kraken", "close_okx", "mark_price"):
        if c in df:
            df.iloc[t:, df.columns.get_loc(c)] *= f
    df.iloc[t + 1:, df.columns.get_loc("open")] *= f
    df.iloc[t + 1:, df.columns.get_loc("high")] *= f
    df.iloc[t + 1:, df.columns.get_loc("low")] *= f
    return d


@attack("mempool_spam")
def mempool_spam(df, rng, t):
    d = rng.choice([-1, 1])
    i = df.index[t]
    hi = df["dex_buy_intent"].median() * 15
    df.loc[i, "dex_buy_intent"], df.loc[i, "dex_sell_intent"] = (hi, 1.0) if d > 0 else (1.0, hi)
    df.loc[i, "priority_fee_gwei"] = df["priority_fee_gwei"].median() * 0.5  # spam is cheap, not urgent
    return d


@attack("liquidation_hunt")
def liquidation_hunt(df, rng, t):
    """Push price to trigger a long-liquidation burst (bearish-looking), then rip higher."""
    i = df.index[t]
    df.loc[i, "liq_long_qty"] = df["liq_long_qty"].max() * 3 + 50
    df.loc[i, "liq_count"] = df.loc[i, "liq_long_qty"] + df.loc[i, "liq_short_qty"]
    df.loc[i, "low"] = df.loc[i, "low"] * 0.99
    return -1  # bait says "down"


@attack("funding_manipulation")
def funding_manipulation(df, rng, t):
    d = rng.choice([-1, 1])
    i = df.index[t]
    df.loc[i, "funding_rate"] = df["funding_rate"].median() + d * 8 * df["funding_rate"].std()
    df.loc[i, "mark_price"] = df.loc[i, "close"] * (1 + d * 0.004)
    df.loc[i, "minutes_to_funding"] = 5.0
    return -d  # high funding bait says "fade longs" -> attacker squeezes the other way


@attack("leadlag_spoof")
def leadlag_spoof(df, rng, t):
    d = rng.choice([-1, 1])
    i = df.index[t]
    df.loc[i, "close_coinbase"] = df.loc[i, "close_coinbase"] * (1 + d * 0.006)
    return d


def apply_attack(raw: pd.DataFrame, name: str, rate: float = 0.02, seed: int = 0,
                 start_frac: float = 0.0) -> tuple[pd.DataFrame, list[int]]:
    """Inject ``name`` at ~``rate`` of bars (after ``start_frac`` of the sample).
    Returns (attacked, bar positions); ``attacked.attrs["baits"]`` holds (position, bait_direction)."""
    rng = np.random.default_rng(seed)
    df = raw.copy()
    sig = _sigma(df)
    n = len(df)
    lo = max(int(n * start_frac), 100)
    k = max(1, int((n - lo) * rate))
    ts = np.sort(rng.choice(np.arange(lo, n - 8), size=min(k, max(n - 8 - lo, 1)), replace=False))
    fn = ATTACKS[name]
    baits = []
    for t in ts:
        bait = fn(df, rng, int(t))
        baits.append((int(t), int(bait)))
        _shift_price_after(df, int(t), int(bait), sig)
    # keep OHLC consistent
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df.attrs = dict(raw.attrs, attack=name, baits=baits)
    return df, ts.tolist()
