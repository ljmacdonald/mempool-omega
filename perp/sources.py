"""Read-only data adapters for the Perp DEX lab (D123). Public endpoints, no keys, nothing here can sign or send.

  GMX REST (arbitrum-api.gmxinfra.io): markets with open interest, liquidity, funding and borrowing rates; tickers
      (oracle prices); token decimals; candles.
  GMX history service (gmx.squids.live, the GraphQL index GMX's own app uses): market settings (fee factors,
      price-impact factors and exponents), hourly funding and borrowing snapshots since 2023, daily pool-token (GM)
      prices, every executed order with its oracle price, execution price, price impact and fees.
  Hyperliquid info API: funding history, candles, order book, market context (hedge venue; data only).
  Binance public data mirror (data-api.binance.vision): long candle history for the directional backtest.

Every value keeps its source and the time it refers to. Missing values stay None, never 0. GMX fixed-point values
are converted with Decimal: USD values have 30 decimals; a token price has 30 - token decimals.
"""
from __future__ import annotations

import json
import logging
import time
from decimal import Decimal, getcontext

import requests

getcontext().prec = 50
log = logging.getLogger("perp.sources")

GMX = "https://arbitrum-api.gmxinfra.io"
SQUID = "https://gmx.squids.live/gmx-synthetics-arbitrum:prod/api/graphql"
HL = "https://api.hyperliquid.xyz/info"
BINANCE = "https://data-api.binance.vision/api/v3/klines"
S = requests.Session()
S.headers["User-Agent"] = "mempool-omega research (paper trading, read-only)"
E30 = Decimal(10) ** 30
HEALTH: dict[str, dict] = {k: {"calls": 0, "errors": 0, "last_ok": None, "last_error": None}
                           for k in ("gmx", "gmx_history", "hyperliquid", "binance")}


def _note(src: str, ok: bool, err: str | None = None) -> None:
    h = HEALTH[src]
    h["calls"] += 1
    if ok:
        h["last_ok"] = time.time()
    else:
        h["errors"] += 1
        h["last_error"] = (err or "")[:200]


def _req(src: str, method: str, url: str, tries: int = 4, pace: float = 0.0, **kw):
    err = None
    for i in range(tries):
        if pace:
            time.sleep(pace)
        try:
            r = S.request(method, url, timeout=60, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                time.sleep(min(30, 2 ** (i + 1)))
                continue
            if r.status_code != 200:
                _note(src, False, f"HTTP {r.status_code}")
                return None
            d = r.json()
            _note(src, True)
            return d
        except (requests.RequestException, ValueError) as e:
            err = str(e)
            time.sleep(min(30, 2 ** (i + 1)))
    _note(src, False, err)
    log.warning("%s %s failed: %s", src, url, err)
    return None


# ------------------------------------------------------------------ conversions
def usd(raw) -> Decimal | None:
    return None if raw is None else Decimal(str(raw)) / E30


def factor(raw) -> Decimal | None:
    return None if raw is None else Decimal(str(raw)) / E30


def token_price(raw, decimals: int) -> Decimal | None:
    return None if raw is None else Decimal(str(raw)) / (Decimal(10) ** (30 - decimals))


# ------------------------------------------------------------------ GMX REST
def gmx_markets() -> list[dict]:
    d = _req("gmx", "GET", f"{GMX}/markets/info")
    return (d or {}).get("markets", [])


def gmx_tickers() -> list[dict]:
    return _req("gmx", "GET", f"{GMX}/prices/tickers") or []


def gmx_tokens() -> dict[str, dict]:
    d = _req("gmx", "GET", f"{GMX}/tokens") or {}
    return {t["address"].lower(): t for t in d.get("tokens", [])}


def gmx_candles(symbol: str, period: str = "1h", limit: int = 10000) -> list[list]:
    """[[t, o, h, l, c], ...] oldest first (GMX serves at most a few thousand bars)."""
    d = _req("gmx", "GET", f"{GMX}/prices/candles", params={"tokenSymbol": symbol, "period": period, "limit": limit}) or {}
    return sorted(d.get("candles", []), key=lambda c: c[0])


# ------------------------------------------------------------------ GMX history service (GraphQL)
def gql(query: str) -> dict | None:
    d = _req("gmx_history", "POST", SQUID, pace=0.15, data=json.dumps({"query": query}),
             headers={"Content-Type": "application/json"})
    if not d or d.get("errors"):
        if d and d.get("errors"):
            _note("gmx_history", False, str(d["errors"][0].get("message")))
        return None
    return d.get("data")


def gql_pages(entity: str, fields: str, where: str, order: str, page: int = 5000, max_pages: int = 40) -> list[dict]:
    """All rows of an entity, `page` at a time (ordered so the offset is stable)."""
    out = []
    for i in range(max_pages):
        d = gql(f"{{ {entity}(limit: {page}, offset: {i * page}, orderBy: {order}, where: {{{where}}}) {{ {fields} }} }}")
        if d is None:
            break
        rows = d.get(entity) or []
        out += rows
        if len(rows) < page:
            break
    return out


MARKET_FIELDS = ("positionFeeFactorForPositiveImpact positionFeeFactorForNegativeImpact positionImpactFactorPositive "
                 "positionImpactFactorNegative positionImpactExponentFactorPositive positionImpactExponentFactorNegative "
                 "maxPositionImpactFactorNegative swapFeeFactorForPositiveImpact swapFeeFactorForNegativeImpact "
                 "longOpenInterestUsd shortOpenInterestUsd poolValue marketTokenSupply longsPayShorts "
                 "fundingFactorPerSecond borrowingFactorPerSecondForLongs borrowingFactorPerSecondForShorts "
                 "longPoolAmount shortPoolAmount isDisabled longOpenInterestInTokens shortOpenInterestInTokens "
                 "useOpenInterestInTokensForBalance indexTokenAddress")


def gmx_market_settings(address: str) -> dict | None:
    d = gql(f'{{ marketInfoById(id: "{address}") {{ {MARKET_FIELDS} }} }}')
    return (d or {}).get("marketInfoById")


def hl_post(body: dict):
    return _req("hyperliquid", "POST", HL, pace=0.1, data=json.dumps(body), headers={"Content-Type": "application/json"})


def hl_funding(coin: str, start_ms: int, end_ms: int | None = None) -> list[dict]:
    """Hourly funding [{time, fundingRate}], paged (500 a call)."""
    out, t = [], start_ms
    while True:
        part = hl_post({"type": "fundingHistory", "coin": coin, "startTime": t, **({"endTime": end_ms} if end_ms else {})}) or []
        out += part
        if len(part) < 500:
            break
        t = part[-1]["time"] + 1
    return out


def hl_candles(coin: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    return hl_post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}}) or []


def hl_book(coin: str) -> dict | None:
    return hl_post({"type": "l2Book", "coin": coin})


def hl_contexts() -> dict[str, dict]:
    d = hl_post({"type": "metaAndAssetCtxs"})
    if not d:
        return {}
    return {u["name"]: {**c, "szDecimals": u.get("szDecimals")} for u, c in zip(d[0]["universe"], d[1])}


def binance_klines(symbol: str, interval: str, start_ms: int, have: list | None = None) -> list[list]:
    ks = list(have or [])
    t = ks[-1][0] + 1 if ks else start_ms
    while True:
        part = _req("binance", "GET", BINANCE, pace=0.05, params={"symbol": symbol, "interval": interval, "startTime": t, "limit": 1000}) or []
        ks += [[x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]] for x in part if not ks or x[0] > ks[-1][0]]
        if len(part) < 1000:
            break
        t = part[-1][0] + 1
    return ks
