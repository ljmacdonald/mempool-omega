"""Free public data on new exchange listings: Binance (first trading day from its own price history), OKX (listing
time published per pair, including pairs scheduled to open) and Gate.io (trading start time per pair). Daily and
hourly candles from each. Everything here is public, keyless and read-only."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from core.config import state_path

log = logging.getLogger("omega.listings")
BV = "https://data-api.binance.vision/api/v3"
OKX = "https://www.okx.com/api/v5"
GATE = "https://api.gateio.ws/api/v4"
_s = requests.Session()
_s.headers.update({"User-Agent": "Mozilla/5.0 (mempool-omega research)", "Accept": "application/json"})
DAY_MS, HOUR_MS = 86_400_000, 3_600_000
LEVERAGED = re.compile(r"\d+[LS]$")                 # Gate's leveraged tokens (BTC5L, NEAR3S...)
STABLE = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "EURI", "AEUR", "USD1", "USDE", "PYUSD", "XUSD", "BFUSD", "RLUSD", "USDS",
          "PAXG", "XAUT", "WBTC", "WBETH", "BNSOL", "STETH"}


def _get(url: str, params: dict | None = None, tries: int = 3):
    for i in range(tries):
        try:
            r = _s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:  # noqa: PERF203
            if i == tries - 1:
                raise
            log.debug("retry %s: %s", url, e)
            time.sleep(1 + i)
    return None


def _frame(rows: list, cols: dict) -> pd.DataFrame:
    """rows -> OHLCV frame on a UTC index; cols maps open/high/low/close/qv to positions, 't' to the time (ms)."""
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "qv"])
    df = pd.DataFrame({k: [float(r[i]) for r in rows] for k, i in cols.items() if k != "t"},
                      index=pd.to_datetime([int(float(r[cols["t"]])) for r in rows], unit="ms", utc=True))
    return df[~df.index.duplicated()].sort_index()


# ---------------------------------------------------------------------------------------------- Binance
def binance_symbols() -> list[dict]:
    """Every USDT spot pair, still trading or not (delisted coins count too, or the test would only see survivors)."""
    info = _get(f"{BV}/exchangeInfo")
    return [{"symbol": s["symbol"], "base": s["baseAsset"], "status": s["status"]} for s in info["symbols"]
            if s["quoteAsset"] == "USDT" and s["baseAsset"] not in STABLE]


def binance_klines(sym: str, interval: str, start_ms: int, limit: int = 1000) -> pd.DataFrame:
    rows = _get(f"{BV}/klines", {"symbol": sym, "interval": interval, "startTime": int(start_ms), "limit": limit}) or []
    return _frame(rows, {"t": 0, "open": 1, "high": 2, "low": 3, "close": 4, "qv": 7})


def binance_first_days(symbols: list[str]) -> dict[str, int]:
    """First daily candle (ms) of each symbol, remembered in state/listings/binance_first.json (it never changes)."""
    p = state_path("listings", "binance_first.json")
    known = json.loads(p.read_text()) if p.exists() else {}
    todo = [s for s in symbols if s not in known]

    def first(s):
        try:
            r = _get(f"{BV}/klines", {"symbol": s, "interval": "1d", "startTime": 0, "limit": 1})
            return s, (int(r[0][0]) if r else None)
        except Exception:  # noqa: BLE001
            return s, None
    with ThreadPoolExecutor(6) as ex:
        for s, f in ex.map(first, todo):
            if f:
                known[s] = f
    p.write_text(json.dumps(known, indent=0, sort_keys=True))
    return known


STOCK_NAME = {"binance": re.compile(r"[A-Z]{1,6}B"), "okx": re.compile(r"X[A-Z]{2,6}"), "gate": re.compile(r"[A-Z]{1,6}X")}


def tokenized_stock_batches(first: dict[str, int], bases: dict[str, str], exchange: str = "binance") -> set[str]:
    """Tokenized stocks and ETFs (Binance ADBEB, DJTB...; OKX XSOXL, XMUU...) are named in a pattern and arrive in
    same-day batches: not crypto listings."""
    by_day: dict[int, list[str]] = {}
    for s, f in first.items():
        if STOCK_NAME[exchange].fullmatch(bases.get(s, "")):
            by_day.setdefault(f // DAY_MS, []).append(s)
    return {s for v in by_day.values() if len(v) >= 2 for s in v}


def quiet_weekends(d: pd.DataFrame) -> bool:
    """Stocks don't trade at weekends, so their tokens barely move then; crypto trades just as much on Saturday.
    Needs two weekends of data."""
    if d is None or d.empty:
        return False
    r = d["high"] / d["low"].clip(lower=1e-12) - 1
    we, wd = r[d.index.dayofweek >= 5], r[d.index.dayofweek < 5]
    return len(we) >= 4 and len(wd) >= 6 and we.median() < 0.4 * wd.median()


def stock_like(exchange: str, base: str, d: pd.DataFrame | None) -> bool:
    return bool(STOCK_NAME.get(exchange) and STOCK_NAME[exchange].fullmatch(base) and quiet_weekends(d))


# ---------------------------------------------------------------------------------------------- OKX
def okx_pairs() -> list[dict]:
    rows = _get(f"{OKX}/public/instruments", {"instType": "SPOT"})["data"]
    return [{"symbol": r["instId"], "base": r["baseCcy"], "list_ms": int(r["listTime"]), "state": r["state"]} for r in rows
            if r["quoteCcy"] == "USDT" and r.get("listTime") and r["baseCcy"] not in STABLE]


def okx_candles(inst: str, bar: str, start_ms: int, n: int) -> pd.DataFrame:
    """n candles from start_ms forwards (OKX pages backwards from `after`, newest first, 100 at a time)."""
    step = DAY_MS if bar.startswith("1D") else HOUR_MS
    end = start_ms + (n + 1) * step
    rows: list = []
    while len(rows) < n:
        got = (_get(f"{OKX}/market/history-candles", {"instId": inst, "bar": bar, "after": int(end), "limit": 100}) or {}).get("data") or []
        got = [g for g in got if int(g[0]) >= start_ms - step]
        if not got:
            break
        rows += got
        end = int(got[-1][0])
        if len(got) < 100:
            break
    return _frame(rows, {"t": 0, "open": 1, "high": 2, "low": 3, "close": 4, "qv": 7})


# ---------------------------------------------------------------------------------------------- Gate.io
def gate_pairs() -> list[dict]:
    rows = _get(f"{GATE}/spot/currency_pairs")
    return [{"symbol": r["id"], "base": r["base"], "list_ms": int(r["buy_start"]) * 1000, "state": r["trade_status"]} for r in rows
            if r["quote"] == "USDT" and int(r.get("buy_start") or 0) > 0 and not LEVERAGED.search(r["base"]) and r["base"] not in STABLE]


def gate_candles(pair: str, interval: str, start_ms: int, n: int) -> pd.DataFrame:
    step = DAY_MS if interval == "1d" else HOUR_MS
    rows = _get(f"{GATE}/spot/candlesticks", {"currency_pair": pair, "interval": interval, "from": int(start_ms // 1000),
                                               "to": int(min(start_ms + n * step, time.time() * 1000) // 1000)}) or []
    # [t (s), quote volume, close, high, low, open, base volume, closed]
    return _frame([[int(r[0]) * 1000, r[5], r[3], r[4], r[2], r[1]] for r in rows], {"t": 0, "open": 1, "high": 2, "low": 3, "close": 4, "qv": 5})


def candles(exchange: str, sym: str, interval: str, start_ms: int, n: int) -> pd.DataFrame:
    if exchange == "binance":
        return binance_klines(sym, interval, start_ms, min(n, 1000))
    if exchange == "okx":
        return okx_candles(sym, "1Dutc" if interval == "1d" else "1H", start_ms, n)
    return gate_candles(sym, interval, start_ms, n)


def basket_daily(start_ms: int) -> pd.DataFrame:
    """Daily closes of established coins: what simply holding big coins did over the same days."""
    out = {}
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT"):
        parts, t = [], start_ms
        while True:
            d = binance_klines(s, "1d", t, 1000)
            if d.empty:
                break
            parts.append(d)
            if len(d) < 1000:
                break
            t = int(d.index[-1].value // 10**6) + DAY_MS
        if parts:
            out[s] = pd.concat(parts)["close"]
    return pd.DataFrame(out)
