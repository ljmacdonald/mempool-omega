"""Free public DEX data: GeckoTerminal (pool lists, hourly candles) and DexScreener (live pool details).
No keys. Rate limits respected through ingest.http (GeckoTerminal: 30 calls/min)."""
from __future__ import annotations

import pandas as pd

from core.log import get_logger
from dex.chains import CHAINS, norm
from ingest.http import PoliteClient

log = get_logger("dex.data")
GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"
_http = PoliteClient(timeout=30, retries=6)


def http() -> PoliteClient:
    return _http


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def gt_pools(chain: str, sort: str = "h24_volume_usd_desc", pages: int = 5) -> list[dict]:
    """Busiest pools on a network (GeckoTerminal), with base/quote token addresses."""
    net = CHAINS[chain]["gt"]
    out = []
    for page in range(1, pages + 1):
        try:
            d = _http.get(f"{GT}/networks/{net}/pools", {"page": page, "sort": sort, "include": "base_token,quote_token"})
        except Exception as e:  # noqa: BLE001
            log.warning("geckoterminal %s page %s: %s", chain, page, e)
            break
        toks = {t["id"]: t["attributes"] for t in d.get("included", [])}
        for p in d.get("data", []):
            out.append(pool_record(chain, p, toks))
        if len(d.get("data", [])) < 20:
            break
    return out


def pool_record(chain: str, p: dict, toks: dict) -> dict:
    a, rel = p["attributes"], p["relationships"]
    b = toks.get(rel["base_token"]["data"]["id"], {})
    q = toks.get(rel["quote_token"]["data"]["id"], {})
    tx = a.get("transactions") or {}
    vol = a.get("volume_usd") or {}
    return {"chain": chain, "pool": norm(chain, a["address"]), "dex": rel["dex"]["data"]["id"], "name": a.get("name"),
            "base_symbol": b.get("symbol") or (a.get("name") or "?").split(" / ")[0],
            "base_name": b.get("name") or "", "token": norm(chain, b.get("address") or rel["base_token"]["data"]["id"].split("_", 1)[-1]),
            "quote_symbol": q.get("symbol"), "quote": norm(chain, q.get("address") or rel["quote_token"]["data"]["id"].split("_", 1)[-1]),
            "price": _f(a.get("base_token_price_usd")), "quote_price": _f(a.get("quote_token_price_usd")),
            "reserve_usd": _f(a.get("reserve_in_usd")), "created": a.get("pool_created_at"),
            "fee_pct": _f(a.get("pool_fee_percentage")),
            "tx_h24": {k: int(v or 0) for k, v in (tx.get("h24") or {}).items()},
            "tx_h1": {k: int(v or 0) for k, v in (tx.get("h1") or {}).items()},
            "vol_h24": _f(vol.get("h24"))}


def gt_multi(chain: str, pools: list[str]) -> dict[str, dict]:
    """Fresh stats (price, money in the pool, buyers/sellers) for up to 30 pools per request."""
    net = CHAINS[chain]["gt"]
    out: dict[str, dict] = {}
    for i in range(0, len(pools), 30):
        chunk = pools[i:i + 30]
        try:
            d = _http.get(f"{GT}/networks/{net}/pools/multi/{','.join(chunk)}", {"include": "base_token,quote_token"})
        except Exception as e:  # noqa: BLE001
            log.warning("geckoterminal multi %s: %s", chain, e)
            continue
        toks = {t["id"]: t["attributes"] for t in d.get("included", [])}
        for p in d.get("data", []):
            r = pool_record(chain, p, toks)
            out[r["pool"]] = r
    return out


def gt_ohlcv(chain: str, pool: str, n: int = 300, timeframe: str = "hour", aggregate: int = 1) -> pd.DataFrame:
    """CLOSED candles in USD, oldest first. Columns match scanner candles (taker volume unknown on DEX data,
    so buy pressure is set neutral)."""
    net = CHAINS[chain]["gt"]
    rows: list = []
    before = None
    while len(rows) < n:
        params = {"aggregate": aggregate, "limit": min(1000, n - len(rows) + 1), "currency": "usd"}
        if before:
            params["before_timestamp"] = before
        d = _http.get(f"{GT}/networks/{net}/pools/{pool}/ohlcv/{timeframe}", params)
        batch = (d.get("data") or {}).get("attributes", {}).get("ohlcv_list") or []
        if not batch:
            break
        rows = batch + rows if before else batch
        before = min(int(r[0]) for r in batch)
        if len(batch) < params["limit"]:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "qv"]).drop_duplicates("t").sort_values("t")
    step = {"minute": 60, "hour": 3600, "day": 86400}[timeframe] * aggregate
    df = df[df["t"] + step <= pd.Timestamp.now(tz="UTC").timestamp()]          # closed candles only
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    df = df.drop(columns="t").astype(float)
    df["volume"] = df["qv"] / df["close"].where(df["close"] > 0)
    df["taker_buy_volume"] = df["volume"] * 0.5
    return df.tail(n)


def ds_pairs(chain: str, tokens: list[str]) -> dict[str, list[dict]]:
    """DexScreener pools for up to 30 tokens per call. Returns token -> list of pairs."""
    out: dict[str, list[dict]] = {}
    ds = CHAINS[chain]["ds"]
    for i in range(0, len(tokens), 30):
        chunk = tokens[i:i + 30]
        try:
            pairs = _http.get(f"{DS}/tokens/v1/{ds}/{','.join(chunk)}")
        except Exception as e:  # noqa: BLE001
            log.warning("dexscreener %s: %s", chain, e)
            continue
        for p in pairs or []:
            out.setdefault(norm(chain, (p.get("baseToken") or {}).get("address")), []).append(p)
    return out


def ds_boosted() -> set[tuple[str, str]]:
    """Tokens currently paying DexScreener for promotion ("boosts"). Paid promotion is a classic way to pull in
    buyers for a planned dump, so it counts against a token."""
    out = set()
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1"):
        try:
            for b in _http.get(DS + path) or []:
                out.add((b.get("chainId"), b.get("tokenAddress")))
        except Exception as e:  # noqa: BLE001
            log.warning("dexscreener boosts: %s", e)
    return {(c, a if c == "solana" else (a or "").lower()) for c, a in out}


def gas_usd(chain: str, native_price: float) -> float:
    c = CHAINS[chain]
    if "gas_usd" in c:
        return float(c["gas_usd"])
    try:
        r = _http.post(c["rpc"], json={"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []})
        return int(r["result"], 16) * c["gas_units"] / 1e18 * native_price
    except Exception as e:  # noqa: BLE001
        log.warning("gas %s: %s", chain, e)
        return {"ethereum": 1.5, "bsc": 0.1}.get(chain, 0.1)
