"""Universe selection and candles (Binance public data mirror, no key). Thread-pooled for live use."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

from core.log import get_logger
from ingest.cex import _binance_get

log = get_logger("scanner.data")
CFG_PATH = Path(__file__).with_name("universe.yaml")
INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load(CFG_PATH.read_text())


def live_pairs(tick: pd.DataFrame, max_age_h: float = 2.0) -> pd.DataFrame:
    """Only pairs that still trade. Binance's 24 h ticker keeps delisted pairs with their last day's numbers frozen
    (no bid, closeTime long past): 255 USDT pairs in Oct 2026, some with millions of 'volume' (D96)."""
    if not len(tick) or "closeTime" not in tick or "bidPrice" not in tick:
        return tick
    fresh = pd.to_numeric(tick["closeTime"], errors="coerce") >= (time.time() - max_age_h * 3600) * 1000
    bid = pd.to_numeric(tick["bidPrice"], errors="coerce") > 0
    return tick[fresh & bid]


def select_universe(max_coins: int | None = None) -> pd.DataFrame:
    """Most-traded USDT pairs after removing stablecoins, gold, wrapped and stock tokens."""
    cfg = config()
    tick = pd.DataFrame(_binance_get("/api/v3/ticker/24hr", {}))
    tick = live_pairs(tick[tick["symbol"].str.endswith("USDT")]).copy()
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


def select_small_universe() -> pd.DataFrame:
    """'Small coins': the next tier after the main universe, down to small_min_quote_volume_usd."""
    cfg = config()
    tick = pd.DataFrame(_binance_get("/api/v3/ticker/24hr", {}))
    tick = live_pairs(tick[tick["symbol"].str.endswith("USDT")]).copy()
    tick["base"] = tick["symbol"].str[:-4]
    excluded = set(cfg["exclude_stablecoins"]) | set(cfg["exclude_other"]) | set(cfg["exclude_stock_tokens"])
    ok = tick["base"].map(lambda b: bool(re.fullmatch(r"[A-Z0-9]{2,15}", b)) and b not in excluded)
    ok &= ~tick["base"].str.contains(r"(?:UP|DOWN|BULL|BEAR)$")
    tick = tick[ok]
    for c in ("quoteVolume", "lastPrice", "priceChangePercent", "count"):
        tick[c] = pd.to_numeric(tick[c], errors="coerce")
    main = set(select_universe()["symbol"])
    tick = tick[(tick["quoteVolume"] >= cfg["small_min_quote_volume_usd"]) & ~tick["symbol"].isin(main)]
    tick = tick.sort_values("quoteVolume", ascending=False).head(cfg["small_max_coins"])
    return tick[["symbol", "base", "quoteVolume", "lastPrice", "priceChangePercent"]].reset_index(drop=True)


def candles(symbol: str, interval: str = "1h", n: int = 240) -> pd.DataFrame:
    """OHLCV + taker-buy volume, CLOSED candles only."""
    rows: list = []
    end = None
    while len(rows) < n:
        params = {"symbol": symbol, "interval": interval, "limit": min(1000, n - len(rows))}
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
    df = df[["open", "high", "low", "close", "volume", "qv", "taker_buy_volume", "n_trades"]].astype(float)
    df = df[~df.index.duplicated()].sort_index()
    now = pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=INTERVAL_MIN[interval]) <= now]


def hourly_candles(symbol: str, n: int = 240) -> pd.DataFrame:
    return candles(symbol, "1h", n)


def daily_candles(symbol: str, n: int = 120) -> pd.DataFrame:
    d = _binance_get("/api/v3/klines", {"symbol": symbol, "interval": "1d", "limit": n})
    df = pd.DataFrame([r[:6] for r in d], columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)


def load_all(symbols: list[str], n: int = 240, interval: str = "1h", workers: int = 8) -> dict[str, pd.DataFrame]:
    def one(s):
        try:
            df = candles(s, interval, n)
            return s, df if len(df) >= min(200, n - 5) else None
        except Exception as e:  # noqa: BLE001
            log.warning("skip %s: %s", s, e)
            return s, None

    with ThreadPoolExecutor(workers) as ex:
        return {s: df for s, df in ex.map(one, symbols) if df is not None}


def last_prices(symbols: list[str]) -> dict[str, float]:
    """Current prices (one request for everything)."""
    d = _binance_get("/api/v3/ticker/price", {})
    want = set(symbols)
    return {x["symbol"]: float(x["price"]) for x in d if x["symbol"] in want}
