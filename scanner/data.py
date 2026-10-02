"""Universe selection and hourly candles (Binance public data mirror, no key)."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from core.log import get_logger
from ingest.cex import _binance_get

log = get_logger("scanner.data")
CFG_PATH = Path(__file__).with_name("universe.yaml")


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(CFG_PATH.read_text())


def select_universe(max_coins: int | None = None) -> pd.DataFrame:
    """Most-traded USDT pairs after removing stablecoins, gold, wrapped and stock tokens."""
    cfg = config()
    tick = pd.DataFrame(_binance_get("/api/v3/ticker/24hr", {}))
    tick = tick[tick["symbol"].str.endswith("USDT")].copy()
    tick["base"] = tick["symbol"].str[:-4]
    excluded = set(cfg["exclude_stablecoins"]) | set(cfg["exclude_other"]) | set(cfg["exclude_stock_tokens"])
    ok = tick["base"].map(lambda b: bool(re.fullmatch(r"[A-Z0-9]{2,15}", b)) and b not in excluded)
    ok &= ~tick["base"].str.contains(r"(?:UP|DOWN|BULL|BEAR)$")
    tick = tick[ok]
    for c in ("quoteVolume", "lastPrice", "priceChangePercent", "count"):
        tick[c] = pd.to_numeric(tick[c], errors="coerce")
    must = set(cfg.get("always_include", []))
    keep = (tick["quoteVolume"] >= cfg["min_quote_volume_usd"]) | tick["base"].isin(must)
    tick = tick[keep].sort_values("quoteVolume", ascending=False)
    n = max_coins or cfg["max_coins"]
    top = pd.concat([tick.head(n), tick[tick["base"].isin(must)]]).drop_duplicates("symbol")
    return top[["symbol", "base", "quoteVolume", "lastPrice", "priceChangePercent"]].reset_index(drop=True)


def hourly_candles(symbol: str, n: int = 240) -> pd.DataFrame:
    """1-hour OHLCV + taker-buy volume, closed bars only."""
    rows: list = []
    end = None
    while len(rows) < n:
        params = {"symbol": symbol, "interval": "1h", "limit": min(1000, n - len(rows))}
        if end is not None:
            params["endTime"] = end
        batch = _binance_get("/api/v3/klines", params)
        if not batch:
            break
        rows = batch + rows
        end = int(batch[0][0]) - 1
        if len(batch) < params["limit"]:
            break
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "ct", "qv", "n_trades",
                                     "taker_buy_volume", "tbqv", "_"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    df.index.name = "ts"
    df = df[["open", "high", "low", "close", "volume", "qv", "taker_buy_volume"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(hours=1) <= now]


def daily_candles(symbol: str, n: int = 120) -> pd.DataFrame:
    d = _binance_get("/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": n})
    df = pd.DataFrame([r[:6] for r in d], columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_all(symbols: list[str], n_hours: int = 240) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        try:
            df = hourly_candles(s, n_hours)
            if len(df) >= 200:
                out[s] = df
        except Exception as e:  # noqa: BLE001
            log.warning("skip %s: %s", s, e)
    return out
