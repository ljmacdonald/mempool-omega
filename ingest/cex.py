"""Centralised-exchange PUBLIC REST adapters (no API keys, read-only).

Venues: Binance (spot via the public data mirror), OKX (spot + perp
derivatives), Coinbase Exchange, Kraken. Bybit is supported for the ticker
endpoint but is geo-restricted from many cloud regions.

Every function returns normalised pandas objects indexed by UTC timestamps.
Binance blocks US cloud IPs (HTTP 451) on api.binance.com / fapi; we use
``data-api.binance.vision`` for spot and OKX for derivatives. See DECISIONS.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.log import get_logger
from ingest.http import SourceUnavailable, client

log = get_logger("ingest.cex")

BINANCE_SPOT_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
BINANCE_FUT = "https://fapi.binance.com"
OKX = "https://www.okx.com"
COINBASE = "https://api.exchange.coinbase.com"
KRAKEN = "https://api.kraken.com"
BYBIT = "https://api.bybit.com"


def base_asset(symbol: str) -> str:
    for q in ("USDT", "USDC", "USD"):
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


def venue_symbol(symbol: str, venue: str) -> str:
    b = base_asset(symbol)
    return {
        "binance": f"{b}USDT",
        "okx": f"{b}-USDT",
        "okx_swap": f"{b}-USDT-SWAP",
        "coinbase": f"{b}-USD",
        "kraken": f"{'XBT' if b == 'BTC' else b}USD",
        "bybit": f"{b}USDT",
    }[venue]


# --------------------------------------------------------------------------- Binance
def _binance_get(path: str, params: dict) -> object:
    last: Exception | None = None
    for host in BINANCE_SPOT_HOSTS:
        try:
            return client().get(host + path, params)
        except SourceUnavailable as e:
            last = e
    raise SourceUnavailable(str(last))


def binance_klines(symbol: str, interval: str = "5m", n_bars: int = 1000,
                   end_ms: int | None = None) -> pd.DataFrame:
    """OHLCV + taker-buy volume (=> trade-sign order flow). Paginates backwards."""
    rows: list = []
    remaining = n_bars
    end = end_ms
    while remaining > 0:
        params = {"symbol": venue_symbol(symbol, "binance"), "interval": interval, "limit": min(1000, remaining)}
        if end is not None:
            params["endTime"] = end
        batch = _binance_get("/api/v3/klines", params)
        if not batch:
            break
        rows = batch + rows
        remaining -= len(batch)
        end = int(batch[0][0]) - 1
        if len(batch) < params["limit"]:
            break
    if not rows:
        raise SourceUnavailable("binance klines empty")
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume", "ct", "qv", "n_trades",
                                     "taker_buy_volume", "tbqv", "_"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    df.index.name = "ts"
    out = df[["open", "high", "low", "close", "volume", "taker_buy_volume", "n_trades"]].astype(float)
    return out[~out.index.duplicated()].sort_index()


def binance_depth(symbol: str, limit: int = 20) -> dict:
    d = _binance_get("/api/v3/depth", {"symbol": venue_symbol(symbol, "binance"), "limit": limit})
    return {"bids": [(float(p), float(q)) for p, q in d["bids"]],
            "asks": [(float(p), float(q)) for p, q in d["asks"]],
            "ts": pd.Timestamp.now(tz="UTC"), "venue": "binance"}


def binance_agg_trades(symbol: str, limit: int = 1000) -> pd.DataFrame:
    d = _binance_get("/api/v3/aggTrades", {"symbol": venue_symbol(symbol, "binance"), "limit": limit})
    df = pd.DataFrame(d)
    if df.empty:
        return pd.DataFrame(columns=["price", "qty", "side"])
    out = pd.DataFrame({
        "price": df["p"].astype(float),
        "qty": df["q"].astype(float),
        # buyer is maker => aggressor sold
        "side": np.where(df["m"], -1, 1),
    })
    out.index = pd.to_datetime(df["T"].astype("int64"), unit="ms", utc=True)
    return out


def binance_premium_index(symbol: str) -> dict:
    """Mark price + funding from Binance USD-M futures (geo-blocked in many clouds)."""
    d = client().get(BINANCE_FUT + "/fapi/v1/premiumIndex", {"symbol": venue_symbol(symbol, "binance")})
    return {"mark_price": float(d["markPrice"]), "funding_rate": float(d["lastFundingRate"]),
            "next_funding_ms": int(d["nextFundingTime"])}


# --------------------------------------------------------------------------- OKX
def _okx(path: str, params: dict) -> list:
    d = client().get(OKX + path, params)
    if str(d.get("code")) != "0":
        raise SourceUnavailable(f"okx {path}: {d.get('msg')}")
    return d["data"]


def okx_candles(symbol: str, bar: str = "5m", n_bars: int = 300, swap: bool = False) -> pd.DataFrame:
    inst = venue_symbol(symbol, "okx_swap" if swap else "okx")
    rows: list = []
    after = None
    while len(rows) < n_bars:
        params = {"instId": inst, "bar": bar, "limit": 100}
        if after:
            params["after"] = after
        batch = _okx("/api/v5/market/history-candles", params)
        if not batch:
            break
        rows.extend(batch)
        after = batch[-1][0]
        if len(batch) < 100:
            break
    if not rows:
        raise SourceUnavailable("okx candles empty")
    df = pd.DataFrame([r[:6] for r in rows], columns=["t", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True)
    df.index.name = "ts"
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    return out[~out.index.duplicated()].sort_index()


def okx_funding(symbol: str) -> dict:
    d = _okx("/api/v5/public/funding-rate", {"instId": venue_symbol(symbol, "okx_swap")})[0]
    return {"funding_rate": float(d["fundingRate"]), "next_funding_ms": int(d["fundingTime"])}


def okx_funding_history(symbol: str, limit: int = 100) -> pd.Series:
    d = _okx("/api/v5/public/funding-rate-history", {"instId": venue_symbol(symbol, "okx_swap"), "limit": limit})
    s = pd.Series([float(x["fundingRate"]) for x in d],
                  index=pd.to_datetime([int(x["fundingTime"]) for x in d], unit="ms", utc=True))
    return s.sort_index()


def okx_mark_price(symbol: str) -> float:
    d = _okx("/api/v5/public/mark-price", {"instType": "SWAP", "instId": venue_symbol(symbol, "okx_swap")})
    return float(d[0]["markPx"])


def okx_open_interest_history(symbol: str, period: str = "5m") -> pd.Series:
    """OI (USD) history from OKX rubik stats (free, ~ last few days)."""
    d = _okx("/api/v5/rubik/stat/contracts/open-interest-volume", {"ccy": base_asset(symbol), "period": period})
    s = pd.Series([float(r[1]) for r in d], index=pd.to_datetime([int(r[0]) for r in d], unit="ms", utc=True))
    return s.sort_index()


def okx_liquidations(symbol: str, limit: int = 100) -> pd.DataFrame:
    """Recent filled liquidation orders. posSide=long liquidated => forced sell."""
    d = _okx("/api/v5/public/liquidation-orders",
             {"instType": "SWAP", "uly": venue_symbol(symbol, "okx"), "state": "filled", "limit": limit})
    rows = []
    for blk in d:
        for x in blk.get("details", []):
            rows.append((int(x["ts"]), x.get("posSide", ""), float(x["sz"]), float(x.get("bkPx", "nan") or "nan")))
    df = pd.DataFrame(rows, columns=["t", "pos_side", "sz", "px"])
    if df.empty:
        return pd.DataFrame(columns=["pos_side", "sz", "px"])
    df.index = pd.to_datetime(df.pop("t"), unit="ms", utc=True)
    return df.sort_index()


def okx_books(symbol: str, depth: int = 20) -> dict:
    d = _okx("/api/v5/market/books", {"instId": venue_symbol(symbol, "okx"), "sz": depth})[0]
    return {"bids": [(float(b[0]), float(b[1])) for b in d["bids"]],
            "asks": [(float(a[0]), float(a[1])) for a in d["asks"]],
            "ts": pd.Timestamp(int(d["ts"]), unit="ms", tz="UTC"), "venue": "okx"}


# --------------------------------------------------------------------------- Coinbase
def coinbase_candles(symbol: str, granularity_s: int = 300, n_bars: int = 300) -> pd.DataFrame:
    rows: list = []
    end = pd.Timestamp.now(tz="UTC")
    while len(rows) < n_bars:
        start = end - pd.Timedelta(seconds=granularity_s * 300)
        batch = client().get(f"{COINBASE}/products/{venue_symbol(symbol, 'coinbase')}/candles",
                             {"granularity": granularity_s, "start": start.isoformat(), "end": end.isoformat()})
        if not batch:
            break
        rows.extend(batch)
        end = start
    if not rows:
        raise SourceUnavailable("coinbase candles empty")
    df = pd.DataFrame(rows, columns=["t", "low", "high", "open", "close", "volume"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="s", utc=True)
    df.index.name = "ts"
    out = df[["open", "high", "low", "close", "volume"]].astype(float)
    return out[~out.index.duplicated()].sort_index().iloc[-n_bars:]


def coinbase_book(symbol: str) -> dict:
    d = client().get(f"{COINBASE}/products/{venue_symbol(symbol, 'coinbase')}/book", {"level": 2})
    return {"bids": [(float(b[0]), float(b[1])) for b in d["bids"][:20]],
            "asks": [(float(a[0]), float(a[1])) for a in d["asks"][:20]],
            "ts": pd.Timestamp.now(tz="UTC"), "venue": "coinbase"}


# --------------------------------------------------------------------------- Kraken
def kraken_ohlc(symbol: str, interval_min: int = 5) -> pd.DataFrame:
    d = client().get(f"{KRAKEN}/0/public/OHLC", {"pair": venue_symbol(symbol, "kraken"), "interval": interval_min})
    if d.get("error"):
        raise SourceUnavailable(f"kraken: {d['error']}")
    key = next(k for k in d["result"] if k != "last")
    df = pd.DataFrame(d["result"][key], columns=["t", "open", "high", "low", "close", "vwap", "volume", "count"])
    df.index = pd.to_datetime(df["t"].astype("int64"), unit="s", utc=True)
    df.index.name = "ts"
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


# --------------------------------------------------------------------------- Bybit
def bybit_ticker(symbol: str) -> dict:
    d = client().get(f"{BYBIT}/v5/market/tickers", {"category": "linear", "symbol": venue_symbol(symbol, "bybit")})
    t = d["result"]["list"][0]
    return {"mark_price": float(t["markPrice"]), "funding_rate": float(t["fundingRate"]),
            "open_interest": float(t["openInterest"])}
