"""Per-coin features on any candle size. Windows are counted in CANDLES (b = bars), so the same
definitions work for 5-minute, 15-minute and 1-hour candles; each trading speed has its own model.
Every value at row t uses only data up to the close of candle t (tested)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["ret_1b", "ret_4b", "ret_24b", "ret_72b", "ema24_dist", "ema72_dist", "atr_pct", "vol_ratio",
            "buy_pressure_6b", "buy_pressure_24b", "volume_surge", "rsi_14", "dist_high_168b", "dist_low_168b",
            "rel_strength_24b", "btc_ret_24b", "hour_sin", "hour_cos", "liquidity"]


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n).mean() / df["close"]


def coin_features(df: pd.DataFrame, btc: pd.DataFrame | None = None) -> pd.DataFrame:
    c = df["close"]
    lc = np.log(c)
    f = pd.DataFrame(index=df.index)
    for h in (1, 4, 24, 72):
        f[f"ret_{h}b"] = lc.diff(h)
    f["ema24_dist"] = c / c.ewm(span=24, min_periods=12).mean() - 1
    f["ema72_dist"] = c / c.ewm(span=72, min_periods=36).mean() - 1
    f["atr_pct"] = atr_pct(df)
    r = lc.diff()
    f["vol_ratio"] = r.rolling(24, min_periods=12).std() / r.rolling(168, min_periods=48).std()
    for h in (6, 24):
        f[f"buy_pressure_{h}b"] = (df["taker_buy_volume"].rolling(h).sum() / df["volume"].rolling(h).sum()) - 0.5
    f["volume_surge"] = df["qv"].rolling(24).sum() / (df["qv"].rolling(168, min_periods=72).sum() / 7)
    f["rsi_14"] = rsi(c)
    f["dist_high_168b"] = c / df["high"].rolling(168, min_periods=48).max() - 1
    f["dist_low_168b"] = c / df["low"].rolling(168, min_periods=48).min() - 1
    if btc is not None:
        b = np.log(btc["close"]).diff(24).reindex(df.index)
        f["btc_ret_24b"] = b
        f["rel_strength_24b"] = f["ret_24b"] - b
    else:
        f["btc_ret_24b"] = np.nan
        f["rel_strength_24b"] = np.nan
    hours = df.index.hour + df.index.minute / 60
    f["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    f["liquidity"] = np.log10(df["qv"].rolling(24).sum().clip(lower=1))
    return f[FEATURES].replace([np.inf, -np.inf], np.nan)


def bar_sigma(df: pd.DataFrame) -> pd.Series:
    """Per-candle volatility (EWMA std of log returns) used for take-profit and safety-exit distances."""
    return np.log(df["close"]).diff().ewm(span=48, min_periods=24).std()


def big_mover_stats(daily: pd.DataFrame, threshold: float = 0.20, window: int = 7) -> dict:
    """How often (last ~90 days) the coin rose / fell by >= threshold at some point within a week."""
    if len(daily) < window + 10:
        return {"up_pct": np.nan, "down_pct": np.nan, "days": len(daily)}
    c = daily["close"].to_numpy()
    hi = daily["high"].to_numpy()
    lo = daily["low"].to_numpy()
    ups = downs = n = 0
    for i in range(len(c) - window):
        ups += (hi[i + 1:i + 1 + window].max() / c[i] - 1) >= threshold
        downs += (1 - lo[i + 1:i + 1 + window].min() / c[i]) >= threshold
        n += 1
    return {"up_pct": float(ups / n), "down_pct": float(downs / n), "days": int(len(daily))}
