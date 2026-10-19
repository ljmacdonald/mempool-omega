"""Data sources for the Accumulation lab (D119): one polite client per provider, and a registry of what each one is,
what it covers, and how it is doing (last success, last error, calls), shown on the page's Data sources tab.

Rules for every adapter: bounded retries with backoff, explicit checks of the fields used, missing values stay None
(never 0), and source timestamps are kept apart from the time we fetched them. Nothing here can trade.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

import requests

from accum import config as K

REGISTRY = {
    "geckoterminal": {"name": "GeckoTerminal", "auth": "public, no key", "covers": "DEX pools, prices, liquidity, the "
                      "wallet behind each recent swap (Solana, Ethereum)", "history": "last 300 swaps per pool (24 h); "
                      "history is built forward by polling", "limit": "30 requests a minute",
                      "category": "A: verified on-chain swaps"},
    "solana_rpc": {"name": "Solana public network", "auth": "public, no key", "covers": "token supply, a wallet's token "
                   "balance, top-20 holder accounts", "history": "current state only", "limit": "rate-limited; the "
                   "top-holder call is often refused", "category": "B: balances"},
    "etherscan": {"name": "Etherscan (free key)", "auth": "free API key (ETHERSCAN_API_KEY secret)", "covers": "Ethereum token "
                  "transfers and wallet balances (other chains need a paid plan; holder lists are paid)",
                  "history": "full transfer history", "limit": "3 requests a second, 100,000 a day",
                  "category": "B: balances, D: transfers"},
    "binance": {"name": "Binance public market data", "auth": "public, no key", "covers": "exchange candles with taker buy "
                "volume (who hit the ask, not who they are), delisted coins' history", "history": "years",
                "limit": "6,000 weight a minute", "category": "C: exchange trading activity"},
    "coingecko": {"name": "CoinGecko (public)", "auth": "public, no key", "covers": "market cap, supply, all-time high",
                  "history": "current values", "limit": "about 5-15 requests a minute", "category": "market data"},
    "defillama": {"name": "DefiLlama", "auth": "public, no key", "covers": "protocol TVL and fees (DeFi projects only)",
                  "history": "current values here", "limit": "generous", "category": "fundamentals"},
}
_health: dict[str, dict] = {k: {"calls": 0, "errors": 0, "last_ok": None, "last_error": None, "last_error_at": None}
                            for k in REGISTRY}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_note_lock = threading.Lock()


def note(src: str, ok: bool, err: str | None = None) -> None:
    with _note_lock:
        _note(src, ok, err)


def _note(src: str, ok: bool, err: str | None = None) -> None:
    h = _health[src]
    h["calls"] += 1
    if ok:
        h["last_ok"] = now_iso()
    else:
        h["errors"] += 1
        h["last_error"], h["last_error_at"] = (err or "error")[:200], now_iso()


def registry(prev: dict | None = None) -> dict:
    """The registry with this run's health merged into the previous run's (so a source that wasn't called keeps its
    last success)."""
    out = {}
    for k, meta in REGISTRY.items():
        old = (prev or {}).get(k) or {}
        h = _health[k]
        out[k] = {**meta, "connected": k != "etherscan" or bool(etherscan_key()),
                  "last_ok": h["last_ok"] or old.get("last_ok"), "last_error": h["last_error"] or old.get("last_error"),
                  "last_error_at": h["last_error_at"] or old.get("last_error_at"), "calls_last_run": h["calls"],
                  "errors_last_run": h["errors"]}
    return out


class Http:
    def __init__(self, src: str, pace: float = 0.0, session: requests.Session | None = None):
        self.src, self.pace, self.last = src, pace, 0.0
        self.s = session or requests.Session()
        self.lock = threading.Lock()              # several replay threads share one rate limit

    def _wait(self):
        with self.lock:
            w = self.pace - (time.monotonic() - self.last)
            if w > 0:
                time.sleep(w)
            self.last = time.monotonic()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, tries: int = 4, quiet: bool = False):
        return self._call("GET", url, params=params, headers=headers, tries=tries, quiet=quiet)

    def post(self, url: str, body: dict, tries: int = 3):
        return self._call("POST", url, json=body, tries=tries)

    def _call(self, method, url, tries=4, quiet=False, **kw):
        err = None
        for i in range(tries):
            self._wait()
            try:
                r = self.s.request(method, url, timeout=30, **kw)
                if r.status_code == 429 or r.status_code >= 500:
                    err = f"HTTP {r.status_code}"
                    time.sleep(min(30, 2 ** (i + 1)))
                    continue
                if r.status_code != 200:
                    if quiet and r.status_code in (400, 404):      # an expected "no such market", not a failure
                        note(self.src, True)
                    else:
                        note(self.src, False, f"HTTP {r.status_code}: {r.text[:120]}")
                    return None
                d = r.json()
                note(self.src, True)
                return d
            except (requests.RequestException, ValueError) as e:
                err = str(e)
                time.sleep(min(30, 2 ** (i + 1)))
        note(self.src, False, err)
        return None


GT_HDR = {"Accept": "application/json;version=20230302"}
_gt = Http("geckoterminal", K.GT_PACE_S)
_sol = Http("solana_rpc", 0.4)
_es = Http("etherscan", 0.36)                 # the free plan allows 3 requests a second
_bn = Http("binance", 0.1)
_cg = Http("coingecko", 6.5)
_ll = Http("defillama", 0.5)


def f(x) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- GeckoTerminal
def gt_top_pools(chain: str, pages: int = K.POOL_PAGES) -> list[dict]:
    net = K.CHAINS[chain]["gt"]
    out = []
    for page in range(1, pages + 1):
        d = _gt.get(f"{K.GT}/networks/{net}/pools", {"page": page, "sort": "h24_volume_usd_desc", "include": "base_token"}, GT_HDR)
        if not d or "data" not in d:
            break
        toks = {t["id"]: t.get("attributes") or {} for t in d.get("included", [])}
        for p in d["data"]:
            try:
                a, rel = p["attributes"], p["relationships"]
                bid = rel["base_token"]["data"]["id"]
            except (KeyError, TypeError):
                continue
            b = toks.get(bid, {})
            out.append({"chain": chain, "pool": a.get("address"), "token": b.get("address") or bid.split("_", 1)[-1],
                        "symbol": b.get("symbol") or (a.get("name") or "?").split(" / ")[0], "name": b.get("name"),
                        "reserve_usd": f(a.get("reserve_in_usd")), "price_usd": f(a.get("base_token_price_usd")),
                        "created": a.get("pool_created_at"), "vol_h24": f((a.get("volume_usd") or {}).get("h24")),
                        "dex": ((rel.get("dex") or {}).get("data") or {}).get("id")})
    return out


def gt_tokens(chain: str, addrs: list[str]) -> dict[str, dict]:
    """Supply, market cap (CoinGecko's, through GeckoTerminal), price for up to 30 tokens per request."""
    net = K.CHAINS[chain]["gt"]
    out = {}
    for i in range(0, len(addrs), 30):
        d = _gt.get(f"{K.GT}/networks/{net}/tokens/multi/{','.join(addrs[i:i + 30])}", None, GT_HDR)
        for t in (d or {}).get("data", []):
            a = t.get("attributes") or {}
            if not a.get("address"):
                continue
            out[a["address"]] = {"symbol": a.get("symbol"), "name": a.get("name"), "decimals": a.get("decimals"),
                                 "total_supply": f(a.get("normalized_total_supply")), "price_usd": f(a.get("price_usd")),
                                 "market_cap_usd": f(a.get("market_cap_usd")), "fdv_usd": f(a.get("fdv_usd")),
                                 "coingecko_id": a.get("coingecko_coin_id"), "reserve_usd": f(a.get("total_reserve_in_usd"))}
    return out


def gt_trades(chain: str, pool: str, min_usd: float = K.MIN_TRADE_USD) -> list[dict] | None:
    """The pool's last (up to 300) swaps of at least min_usd in the past 24 h, oldest first. None if unavailable."""
    net = K.CHAINS[chain]["gt"]
    d = _gt.get(f"{K.GT}/networks/{net}/pools/{pool}/trades", {"trade_volume_in_usd_greater_than": min_usd}, GT_HDR)
    if d is None or "data" not in d:
        return None
    out = []
    for t in d["data"]:
        a = t.get("attributes") or {}
        if not (a.get("tx_hash") and a.get("tx_from_address") and a.get("kind") in ("buy", "sell") and a.get("block_timestamp")):
            continue
        buy = a["kind"] == "buy"
        qty = f(a.get("to_token_amount") if buy else a.get("from_token_amount"))
        px = f(a.get("price_to_in_usd") if buy else a.get("price_from_in_usd"))
        out.append({"ts": a["block_timestamp"], "tx": a["tx_hash"], "wallet": a["tx_from_address"], "side": a["kind"],
                    "qty": qty, "usd": f(a.get("volume_in_usd")), "price": px, "anomalous": bool(a.get("is_anomalous"))})
    out.sort(key=lambda x: x["ts"])
    return out


def gt_daily(chain: str, pool: str, days: int = 100) -> list[dict] | None:
    """Daily candles (UTC days, USD), oldest first."""
    net = K.CHAINS[chain]["gt"]
    d = _gt.get(f"{K.GT}/networks/{net}/pools/{pool}/ohlcv/day", {"limit": days, "currency": "usd", "token": "base"}, GT_HDR)
    try:
        rows = d["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        return None
    return sorted(({"t": int(r[0]), "o": f(r[1]), "h": f(r[2]), "l": f(r[3]), "c": f(r[4]), "v": f(r[5])} for r in rows),
                  key=lambda x: x["t"])


# ---------------------------------------------------------------- Solana public RPC
def sol(method: str, params: list):
    d = _sol.post(K.SOL_RPC, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not d or "result" not in d:
        if d and "error" in d:
            note("solana_rpc", False, str(d["error"])[:160])
        return None
    return d["result"]


def sol_balance(owner: str, mint: str) -> float | None:
    r = sol("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
    if r is None:
        return None
    tot = 0.0
    for v in r.get("value", []):
        try:
            tot += float(v["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmountString"])
        except (KeyError, TypeError, ValueError):
            return None
    return tot


def sol_top_holders(mint: str) -> list[dict] | None:
    """The 20 largest token accounts and their owners (often refused by the public network: then None)."""
    r = sol("getTokenLargestAccounts", [mint])
    if not r:
        return None
    accs = [(x["address"], f(x.get("uiAmountString"))) for x in r.get("value", [])]
    info = sol("getMultipleAccounts", [[a for a, _ in accs], {"encoding": "jsonParsed"}])
    if not info:
        return None
    out = []
    for (acc, amt), v in zip(accs, info.get("value", [])):
        try:
            owner = v["data"]["parsed"]["info"]["owner"]
        except (KeyError, TypeError):
            owner = None
        out.append({"account": acc, "owner": owner, "amount": amt})
    return out


# ---------------------------------------------------------------- Etherscan (Ethereum only on the free plan)
def etherscan_key() -> str | None:
    return os.environ.get("ETHERSCAN_API_KEY") or None


def es(params: dict):
    key = etherscan_key()
    if not key:
        return None
    for i in range(4):
        d = _es.get(K.ETHERSCAN, {"chainid": 1, **params, "apikey": key})
        if d and "rate limit" in str(d.get("result", "")).lower():     # too fast: wait and try again, not an error
            time.sleep(1 + i)
            continue
        break
    if not d:
        return None
    if str(d.get("status")) != "1" and d.get("message") != "No transactions found":
        note("etherscan", False, str(d.get("result"))[:160])
        return None
    return d.get("result")


def eth_balance(owner: str, token: str, decimals: int) -> float | None:
    r = es({"module": "account", "action": "tokenbalance", "contractaddress": token, "address": owner, "tag": "latest"})
    v = f(r)
    return v / 10 ** int(decimals) if v is not None and decimals is not None else None


def eth_transfers(token: str, decimals: int, n: int = 1000) -> list[dict] | None:
    r = es({"module": "account", "action": "tokentx", "contractaddress": token, "page": 1, "offset": n, "sort": "desc"})
    if r is None:
        return None
    out = []
    for x in r if isinstance(r, list) else []:
        v = f(x.get("value"))
        if v is None or not x.get("hash"):
            continue
        out.append({"ts": datetime.fromtimestamp(int(x["timeStamp"]), tz=timezone.utc).isoformat(), "tx": x["hash"],
                    "from": (x.get("from") or "").lower(), "to": (x.get("to") or "").lower(), "qty": v / 10 ** int(decimals)})
    return out


# ---------------------------------------------------------------- Binance, CoinGecko, DefiLlama
def bn(path: str, params: dict | None = None):
    return _bn.get(f"{K.BINANCE}/{path}", params)


def bn_daily(symbol: str, start_ms: int | None = None, limit: int = 1000, quiet: bool = False) -> list[list] | None:
    p = {"symbol": symbol, "interval": "1d", "limit": limit}
    if start_ms is not None:
        p["startTime"] = start_ms
    return _bn.get(f"{K.BINANCE}/klines", p, quiet=quiet)


def cg_markets(pages: int = 2) -> list[dict]:
    out = []
    for page in range(1, pages + 1):
        d = _cg.get(f"{K.COINGECKO}/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "page": page})
        if not isinstance(d, list):
            break
        out += d
    return out


def llama(path: str, params: dict | None = None):
    return _ll.get(f"{K.LLAMA}/{path}", params)
