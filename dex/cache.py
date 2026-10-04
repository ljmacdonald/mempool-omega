"""Candle store for DEX pools, so each pool's history is downloaded once and then only topped up.

Lives outside the repository (GitHub's free build cache in .github/workflows/dex.yml, a local folder
otherwise), so it never bloats the project. Keeping pools that later crashed or were rugged also gives the
nightly training the failures, not only the survivors.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from core.config import REPO_ROOT
from core.log import get_logger

log = get_logger("dex.cache")
KEEP_BARS = 2400          # ~100 days of hourly candles per pool
FULL_FETCH = 1000         # one request on first sight


def cache_dir() -> Path:
    d = Path(os.environ.get("OMEGA_DEX_CACHE", REPO_ROOT / ".cache" / "dex"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(chain: str, pool: str) -> Path:
    return cache_dir() / f"{chain}_{pool}.parquet"


def load(chain: str, pool: str) -> pd.DataFrame:
    p = _path(chain, pool)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception as e:  # noqa: BLE001
        log.warning("bad cache file %s: %s", p.name, e)
        return pd.DataFrame()


def candles(chain: str, pool: str, n: int = 300, fetch=None) -> pd.DataFrame:
    """The last n closed hourly candles, downloading only what's missing (one small request per hour)."""
    from dex.data import gt_ohlcv

    fetch = fetch or gt_ohlcv
    old = load(chain, pool)
    now = pd.Timestamp.now(tz="UTC").floor("h")
    if len(old):
        missing = int((now - old.index[-1]) / pd.Timedelta(hours=1)) - 1
        if missing <= 0:
            return old.tail(n)
        if missing < FULL_FETCH - 5:
            new = fetch(chain, pool, missing + 3)
            df = pd.concat([old, new])
            df = df[~df.index.duplicated(keep="last")].sort_index().tail(KEEP_BARS)
            df.to_parquet(_path(chain, pool))
            return df.tail(n)
    df = fetch(chain, pool, max(n, FULL_FETCH))
    if len(df):
        df.tail(KEEP_BARS).to_parquet(_path(chain, pool))
    return df.tail(n)


def all_cached(max_age_days: int = 30) -> dict[str, pd.DataFrame]:
    """Every cached pool updated in the last max_age_days, keyed 'chain:pool' (for nightly training)."""
    out = {}
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=max_age_days)
    for p in sorted(cache_dir().glob("*.parquet")):
        chain, pool = p.stem.split("_", 1)
        try:
            df = pd.read_parquet(p)
        except Exception:  # noqa: BLE001
            continue
        if len(df) and df.index[-1] >= cutoff:
            out[f"{chain}:{pool}"] = df
    return out
