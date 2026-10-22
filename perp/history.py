"""History loaders for the Perp DEX lab's tests (D123), cached in .cache/perp and topped up incrementally.

Everything is public: GMX's history service (daily pool-token prices, daily fee and trader-result APRs, hourly
funding and borrowing), Hyperliquid (hourly funding, hourly candles), Binance's public mirror (years of candles).
Each loader returns plain data keyed by time; nothing missing is filled in.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from perp import sources as SRC

CACHE = Path(".cache/perp")
E30 = Decimal(10) ** 30


def _load(name: str, default):
    p = CACHE / f"{name}.json.gz"
    try:
        return json.loads(gzip.decompress(p.read_bytes()))
    except (OSError, ValueError):
        return default


def _save(name: str, d) -> None:
    p = CACHE / f"{name}.json.gz"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(d, separators=(",", ":")).encode()))


def day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


# ------------------------------------------------------------------ GMX markets
def markets() -> list[dict]:
    """Every GMX v2 Arbitrum market with its tokens' symbols and decimals (current listing)."""
    toks = SRC.gmx_tokens()
    out = []
    for m in SRC.gmx_markets():
        ix, lo, sh = (toks.get((m.get(k) or "").lower(), {}) for k in ("indexToken", "longToken", "shortToken"))
        out.append({"name": m["name"], "address": m["marketToken"], "index": ix.get("symbol"), "index_dec": ix.get("decimals"),
                    "long": lo.get("symbol"), "long_addr": m.get("longToken"), "long_dec": lo.get("decimals"),
                    "short": sh.get("symbol"), "short_addr": m.get("shortToken"), "short_dec": sh.get("decimals"),
                    "listed": m.get("isListed"), "since": (m.get("listingDate") or "")[:10], "raw": m})
    return out


def _incremental(name: str, entity: str, fields: str, where: str, tkey: str, order: str) -> list[dict]:
    rows = _load(name, [])
    last = rows[-1][tkey] if rows else 0
    new = SRC.gql_pages(entity, fields, f"{where}, {tkey}_gt: {last}", order)
    rows += new
    if new:
        _save(name, rows)
    return rows


def gm_prices(market: str) -> dict[str, Decimal]:
    """Daily pool-token (GM) price, USD per token: the pool's value including every fee earned and every trader win or loss."""
    rows = _incremental(f"gm_{market.lower()}", "prices", "minPrice maxPrice timestamp",
                        f'type_eq: gm, token_eq: "{market}", isSnapshot_eq: true', "timestamp", "timestamp_ASC")
    return {day(r["timestamp"]): (Decimal(r["minPrice"]) + Decimal(r["maxPrice"])) / 2 / E30 for r in rows}


def token_prices(token: str, decimals: int) -> dict[str, Decimal]:
    rows = _incremental(f"px_{token.lower()}", "prices", "minPrice maxPrice timestamp",
                        f'type_eq: v2, token_eq: "{token}", isSnapshot_eq: true', "timestamp", "timestamp_ASC")
    sc = Decimal(10) ** (30 - decimals)
    return {day(r["timestamp"]): (Decimal(r["minPrice"]) + Decimal(r["maxPrice"])) / 2 / sc for r in rows}


def pool_aprs(market: str) -> dict[str, dict]:
    """Daily fee APR (trading + borrowing fees paid to the pool) and the traders' result as an APR for the pool
    (positive: traders lost to the pool)."""
    a = _incremental(f"apr_{market.lower()}", "aprSnapshots", "aprByFee aprByBorrowingFee snapshotTimestamp",
                     f'address_eq: "{market}"', "snapshotTimestamp", "snapshotTimestamp_ASC")
    p = _incremental(f"pnlapr_{market.lower()}", "pnlAprSnapshots", "apr snapshotTimestamp",
                     f'address_eq: "{market}"', "snapshotTimestamp", "snapshotTimestamp_ASC")
    out: dict[str, dict] = {}
    for r in a:
        out.setdefault(day(r["snapshotTimestamp"]), {})["fee_apr"] = float((Decimal(r["aprByFee"]) + Decimal(r["aprByBorrowingFee"])) / E30)
    for r in p:
        out.setdefault(day(r["snapshotTimestamp"]), {})["pnl_apr"] = float(Decimal(r["apr"]) / E30)
    return out


def gmx_rates(market: str) -> dict[int, dict]:
    """Hourly per-second rates by side, as costs (positive = the position pays): funding and borrowing."""
    f = _incremental(f"fund_{market.lower()}", "fundingRateSnapshots", "fundingFactorPerSecondLong fundingFactorPerSecondShort snapshotTimestamp",
                     f'marketAddress_eq: "{market}"', "snapshotTimestamp", "snapshotTimestamp_ASC")
    b = _incremental(f"borrow_{market.lower()}", "borrowingRateSnapshots", "borrowingFactorPerSecondLong borrowingFactorPerSecondShort snapshotTimestamp",
                     f'address_eq: "{market}"', "snapshotTimestamp", "snapshotTimestamp_ASC")
    out: dict[int, dict] = {}
    for r in f:                                       # the history service reports funding as received: negative = paid
        h = r["snapshotTimestamp"] // 3600 * 3600
        out.setdefault(h, {})["fund_long"] = -Decimal(r["fundingFactorPerSecondLong"]) / E30
        out[h]["fund_short"] = -Decimal(r["fundingFactorPerSecondShort"]) / E30
    for r in b:
        h = r["snapshotTimestamp"] // 3600 * 3600
        out.setdefault(h, {})["borrow_long"] = Decimal(r["borrowingFactorPerSecondLong"]) / E30
        out[h]["borrow_short"] = Decimal(r["borrowingFactorPerSecondShort"]) / E30
    return out


def ffill_hourly(rates: dict[int, dict], max_age: int = 7 * 86400) -> dict[int, dict]:
    """Every hour from the first snapshot to the last, each rate carried forward from its own last published value
    (GMX records a rate when it changes, not every hour; never filled from a later value, never older than 7 days)."""
    if not rates:
        return {}
    keys = sorted(rates)
    last: dict = {}
    seen: dict = {}
    out = {}
    i = 0
    for h in range(keys[0], keys[-1] + 1, 3600):
        while i < len(keys) and keys[i] <= h:
            for k, v in rates[keys[i]].items():
                last[k], seen[k] = v, keys[i]
            i += 1
        out[h] = {k: v for k, v in last.items() if h - seen[k] <= max_age}
    return out


# ------------------------------------------------------------------ Hyperliquid
def hl_funding(coin: str, start_ms: int = 1_672_531_200_000) -> dict[int, Decimal]:
    rows = _load(f"hlfund_{coin}", [])
    t = rows[-1]["time"] + 1 if rows else start_ms
    new = SRC.hl_funding(coin, t)
    if new:
        rows += new
        _save(f"hlfund_{coin}", rows)
    return {r["time"] // 3_600_000 * 3600: Decimal(r["fundingRate"]) for r in rows}


def hl_candles_1h(coin: str) -> dict[int, float]:
    """Hourly closes (Hyperliquid keeps about 5,000 bars), kept and extended in the cache."""
    rows = _load(f"hlc_{coin}", {})
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    start = (max(map(int, rows)) * 1000 + 1) if rows else now - 5000 * 3_600_000
    for c in SRC.hl_candles(coin, "1h", start, now):
        if c["T"] < now:                                 # finished bars only
            rows[str(c["t"] // 1000)] = float(c["c"])
    _save(f"hlc_{coin}", rows)
    return {int(k): v for k, v in rows.items()}


def gmx_candles(symbol: str, period: str) -> dict[int, list]:
    """GMX oracle candles (a few thousand bars served), kept and extended in the cache: t -> [o, h, l, c]."""
    rows = _load(f"gmxc_{symbol}_{period}", {})
    step = {"1m": 60, "15m": 900, "1h": 3600}[period]
    now = int(datetime.now(timezone.utc).timestamp())
    for c in SRC.gmx_candles(symbol, period):
        if c[0] + step <= now:
            rows[str(c[0])] = c[1:5]
    _save(f"gmxc_{symbol}_{period}", rows)
    return {int(k): v for k, v in rows.items()}


# ------------------------------------------------------------------ Binance (long history for the directional test)
def binance(symbol: str, interval: str, start_ms: int = 1_672_531_200_000) -> list[list]:
    name = f"bn_{symbol}_{interval}"
    have = _load(name, [])
    ks = SRC.binance_klines(symbol, interval, start_ms, have)
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    ks = [k for k in ks if k[6] < now]
    if len(ks) != len(have):
        _save(name, ks)
    return ks


# ------------------------------------------------------------------ executed orders (execution-gap study, fee measurement)
ACTION_FIELDS = ("orderKey eventName orderType isLong marketAddress sizeDeltaUsd executionPrice indexTokenPriceMin "
                 "indexTokenPriceMax acceptablePrice priceImpactUsd positionFeeAmount collateralTokenPriceMin "
                 "initialCollateralTokenAddress timestamp")


def trade_actions(since_ts: int) -> list[dict]:
    """Executed and created market orders (increase and decrease) since a time, newest kept in the cache."""
    rows = [r for r in _load("actions", []) if r["timestamp"] >= since_ts]
    last = max((r["timestamp"] for r in rows), default=since_ts)
    new = SRC.gql_pages("tradeActions", ACTION_FIELDS,
                        f'orderType_in: [2, 4], eventName_in: ["OrderCreated", "OrderExecuted"], timestamp_gt: {last}',
                        "timestamp_ASC", page=1000, max_pages=200)
    rows += new
    _save("actions", rows)
    return rows


def execution_fees(n: int = 500) -> list[Decimal]:
    """Execution fees (ETH) paid by the most recent executed market orders."""
    d = SRC.gql(f'{{ orders(limit: {n}, orderBy: updatedAtBlock_DESC, where: {{status_eq: Executed, orderType_in: [2, 4]}}) {{ executionFee }} }}')
    return [Decimal(o["executionFee"]) / Decimal(10) ** 18 for o in (d or {}).get("orders", []) if o.get("executionFee")]
