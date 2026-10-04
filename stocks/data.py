"""Free US stock data, fetched by GitHub Actions (these services don't allow web pages to read them directly).

* Nasdaq screener: price, daily change, volume and market value of every US-listed stock, one request.
* Yahoo Finance chart API: candles (15-minute for 60 days, hourly for 2 years, daily), one stock per request,
  regular trading hours only. Kept in a store (.cache/stocks, GitHub's build cache) and topped up.
* Nasdaq earnings calendar: which companies report results on a given day.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from core.config import REPO_ROOT
from core.log import get_logger

log = get_logger("stocks.data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
      "Accept": "application/json"}
YF = "https://query1.finance.yahoo.com/v8/finance/chart/"
_s = requests.Session()
_s.headers.update(UA)
YF_INTERVAL = {"15m": ("15m", "60d", 15), "1h": ("60m", "730d", 60), "1d": ("1d", "1y", 1440)}


def _num(x) -> float:
    try:
        return float(re.sub(r"[$,%]", "", str(x)))
    except ValueError:
        return float("nan")


def screener() -> pd.DataFrame:
    """Every US-listed stock: symbol, name, price, % change today, volume, market value, sector."""
    rows = _s.get("https://api.nasdaq.com/api/screener/stocks", params={"tableonly": "true", "limit": 10000,
                                                                          "download": "true"}, timeout=60).json()
    df = pd.DataFrame(rows["data"]["rows"])
    df = df[~df["symbol"].str.contains(r"[\^/ .]", regex=True)]
    df = df[~df["name"].fillna("").str.contains(r"\b(?:Warrants?|Rights?|Units?|Notes?|Preferred)\b", regex=True)]   # ordinary shares only
    out = pd.DataFrame({"symbol": df["symbol"].str.strip(), "name": df["name"].fillna("").str.strip().str.replace(r" (Common Stock|Class [A-C] .*|Ordinary Shares.*|American Depositary.*)$", "", regex=True),
                        "price": df["lastsale"].map(_num), "chg_pct": df["pctchange"].map(_num) / 100,
                        "volume": df["volume"].map(_num), "mcap": df["marketCap"].map(_num),
                        "sector": df["sector"].fillna(""), "industry": df["industry"].fillna("")})
    out["dollar_vol"] = out["price"] * out["volume"]
    return out.dropna(subset=["price"]).reset_index(drop=True)


def cache_dir() -> Path:
    d = Path(os.environ.get("OMEGA_STOCK_CACHE", REPO_ROOT / ".cache" / "stocks"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _yahoo(symbol: str, interval: str, rng: str) -> tuple[pd.DataFrame, dict]:
    r = _s.get(YF + symbol, params={"interval": interval, "range": rng, "includePrePost": "false"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    meta = res.get("meta", {})
    ts = res.get("timestamp") or []
    if not ts:
        return pd.DataFrame(), meta
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q.get(k) for k in ("open", "high", "low", "close", "volume")},
                      index=pd.to_datetime(ts, unit="s", utc=True)).astype(float)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df["volume"] = df["volume"].fillna(0.0)
    df["qv"] = df["volume"] * df["close"]
    df["taker_buy_volume"] = df["volume"] * 0.5        # not published for stocks: buy pressure is neutral
    return df[~df.index.duplicated(keep="last")], meta


def candles(symbol: str, interval: str = "15m", n: int = 400) -> pd.DataFrame:
    """Last n CLOSED candles (regular hours), downloading only what's new after the first time."""
    yi, full, minutes = YF_INTERVAL[interval]
    p = cache_dir() / f"{symbol}_{interval}.parquet"
    old = pd.read_parquet(p) if p.exists() else pd.DataFrame()
    rng = full
    if len(old) >= n:
        rng = "5d" if interval == "15m" else "1mo" if interval == "1h" else "3mo"
    df = pd.DataFrame()
    for r in (rng, "1y", "6mo", "3mo", "1mo"):      # recently listed stocks: Yahoo refuses ranges longer than their life
        try:
            df, _ = _yahoo(symbol, yi, r)
            break
        except requests.HTTPError as e:
            if e.response is None or e.response.status_code != 422:
                raise
    if len(old):
        df = pd.concat([old, df])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    now = pd.Timestamp.now(tz="UTC")
    closed = df[df.index + pd.Timedelta(minutes=minutes) <= now]
    if len(closed):
        closed.tail(3000).to_parquet(p)
    return closed.tail(n)


def load_all(symbols: list[str], interval: str, n: int, workers: int = 6) -> dict[str, pd.DataFrame]:
    def one(s):
        try:
            df = candles(s, interval, n)
            return s, df if len(df) >= min(150, n // 2) else None
        except Exception as e:  # noqa: BLE001
            log.warning("candles %s %s: %s", s, interval, e)
            return s, None

    with ThreadPoolExecutor(workers) as ex:
        return {s: df for s, df in ex.map(one, symbols) if df is not None}


def market_status() -> dict:
    """Is the US market open now, and when does today's session start/end (from Yahoo's session data)."""
    try:
        _, meta = _yahoo("SPY", "1d", "5d")
        reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
        start, end = int(reg.get("start", 0)), int(reg.get("end", 0))
        now = pd.Timestamp.now(tz="UTC").timestamp()
        return {"open": start <= now < end, "session_start": start, "session_end": end}
    except Exception as e:  # noqa: BLE001
        log.warning("market status: %s", e)
        return {"open": None, "session_start": 0, "session_end": 0}


def earnings(days: int = 7) -> dict[str, str]:
    """symbol -> next report date (YYYY-MM-DD, with 'before open' / 'after close' when known)."""
    out: dict[str, str] = {}
    day = pd.Timestamp.now(tz="America/New_York").normalize()
    for k in range(days):
        d = (day + pd.Timedelta(days=k)).strftime("%Y-%m-%d")
        try:
            rows = (_s.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": d}, timeout=30)
                    .json().get("data") or {}).get("rows") or []
        except Exception as e:  # noqa: BLE001
            log.warning("earnings %s: %s", d, e)
            continue
        for r in rows:
            when = {"time-pre-market": " before the open", "time-after-hours": " after the close"}.get(r.get("time"), "")
            out.setdefault(r["symbol"], d + when)
    return out
