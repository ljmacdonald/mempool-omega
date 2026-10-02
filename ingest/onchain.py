"""On-chain & mempool adapters using only free/public endpoints.

* mempool.space  - Bitcoin mempool size/fees (no key).
* Ethereum RPC   - public JSON-RPC (default: publicnode). Fee history (gas
  auction urgency), pending/latest block DEX router calls (DEX intent),
  USDT/USDC Transfer logs (stablecoin mints, burns, exchange netflow).
* Solana RPC     - recent prioritisation fees (congestion/urgency).
* Etherscan      - optional (free key) token transfers.
* The Graph      - optional (free key) Uniswap v3 swaps.

Caveat (documented in ASI threat model): private order-flow (Flashbots,
MEV-Share, builders' private pools) never reaches the public mempool. A
public-mempool DEX-intent signal therefore has a structural blind spot and
gets a lower trust prior.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.config import get_settings
from core.log import get_logger
from ingest.http import SourceUnavailable, client

log = get_logger("ingest.onchain")

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64
STABLECOINS = {
    "USDT": ("0xdac17f958d2ee523a2206206994597c13d831ec7", 6),
    "USDC": ("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", 6),
}
# 4-byte selectors of common DEX router calls -> direction w.r.t. ETH
#  "eth_in"  : user pays ETH for tokens  -> ETH sell pressure
#  "eth_out" : user sells tokens for ETH -> ETH buy pressure
ROUTER_SELECTORS = {
    "0x7ff36ab5": "eth_in",   # swapExactETHForTokens
    "0xfb3bdb41": "eth_in",   # swapETHForExactTokens
    "0xb6f9de95": "eth_in",   # swapExactETHForTokensSupportingFeeOnTransferTokens
    "0x18cbafe5": "eth_out",  # swapExactTokensForETH
    "0x4a25d94a": "eth_out",  # swapTokensForExactETH
    "0x791ac947": "eth_out",  # swapExactTokensForETHSupportingFeeOnTransferTokens
    "0x3593564c": "universal",  # UniversalRouter.execute (direction from msg.value)
    "0x24856bc3": "universal",
    "0x5ae401dc": "universal",  # multicall (v3 router)
    "0xac9650d8": "universal",
}


# --------------------------------------------------------------------------- mempool.space
def btc_mempool_stats() -> dict:
    m = client().get("https://mempool.space/api/mempool")
    f = client().get("https://mempool.space/api/v1/fees/recommended")
    return {"btc_mempool_vsize": float(m["vsize"]), "btc_mempool_count": float(m["count"]),
            "btc_fee_fast": float(f["fastestFee"]), "btc_fee_hour": float(f["hourFee"])}


def btc_recent_txs() -> pd.DataFrame:
    d = client().get("https://mempool.space/api/mempool/recent")
    return pd.DataFrame(d)


# --------------------------------------------------------------------------- Ethereum JSON-RPC
def _rpc(method: str, params: list, url: str | None = None):
    url = url or get_settings().eth_rpc
    d = client().post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if "error" in d:
        raise SourceUnavailable(f"rpc {method}: {d['error']}")
    return d["result"]


def eth_gas_urgency(blocks: int = 20) -> dict:
    fh = _rpc("eth_feeHistory", [hex(blocks), "latest", [50, 90]])
    base = [int(x, 16) / 1e9 for x in fh["baseFeePerGas"]]
    prio50 = [int(r[0], 16) / 1e9 for r in fh["reward"]]
    prio90 = [int(r[1], 16) / 1e9 for r in fh["reward"]]
    return {"base_fee_gwei": base[-1], "base_fee_trend": base[-1] / (np.mean(base) + 1e-9) - 1,
            "priority_fee_gwei": float(np.median(prio50)), "priority_fee_p90": float(np.median(prio90)),
            "gas_used_ratio": float(np.mean(fh["gasUsedRatio"]))}


@dataclass
class DexIntent:
    buy: float = 0.0       # gas-weighted ETH buy pressure
    sell: float = 0.0      # gas-weighted ETH sell pressure
    n_txs: int = 0
    block_tag: str = "pending"
    from_addrs: list[str] = field(default_factory=list)


def eth_dex_intent() -> DexIntent:
    """Gas-weighted net DEX pressure from the pending block (fallback: latest)."""
    blk = None
    tag = "pending"
    for tag in ("pending", "latest"):
        try:
            blk = _rpc("eth_getBlockByNumber", [tag, True])
            if blk and blk.get("transactions"):
                break
        except SourceUnavailable:
            continue
    if not blk:
        raise SourceUnavailable("no block")
    base_fee = int(blk.get("baseFeePerGas", "0x0"), 16)
    out = DexIntent(block_tag=tag)
    for tx in blk["transactions"]:
        sel = (tx.get("input") or "0x")[:10]
        kind = ROUTER_SELECTORS.get(sel)
        if not kind:
            continue
        value_eth = int(tx.get("value", "0x0"), 16) / 1e18
        if kind == "universal":
            kind = "eth_in" if value_eth > 0 else "eth_out"
        tip = int(tx.get("maxPriorityFeePerGas") or tx.get("gasPrice") or "0x0", 16)
        gas_price = int(tx.get("gasPrice") or "0x0", 16)
        urgency = 1.0 + max(tip, gas_price - base_fee, 0) / 1e9  # gwei above base fee
        w = urgency * (1.0 + np.log1p(value_eth))
        if kind == "eth_in":
            out.sell += w
        else:
            out.buy += w
        out.n_txs += 1
        out.from_addrs.append(tx.get("from", ""))
    return out


def stablecoin_flows(n_blocks: int = 40, exchange_wallets: set[str] | None = None) -> dict:
    """USDT/USDC mints, burns and exchange in/outflows over the last ``n_blocks``."""
    from asi.wallets import load_known_wallets

    exchange_wallets = exchange_wallets or {a.lower() for a in load_known_wallets()["exchange"]}
    head = int(_rpc("eth_blockNumber", []), 16)
    res = {"stable_mint": 0.0, "stable_burn": 0.0, "stable_exch_inflow": 0.0, "stable_exch_outflow": 0.0}
    transfers = []
    for _name, (addr, dec) in STABLECOINS.items():
        logs = _rpc("eth_getLogs", [{"fromBlock": hex(head - n_blocks), "toBlock": hex(head),
                                     "address": addr, "topics": [TRANSFER_TOPIC]}])
        for lg in logs:
            if len(lg["topics"]) < 3:
                continue
            frm = "0x" + lg["topics"][1][-40:]
            to = "0x" + lg["topics"][2][-40:]
            val = int(lg["data"], 16) / 10**dec if lg["data"] not in ("0x", "") else 0.0
            transfers.append((frm, to, val))
            if lg["topics"][1] == ZERO_TOPIC:
                res["stable_mint"] += val
            elif lg["topics"][2] == ZERO_TOPIC:
                res["stable_burn"] += val
            frm_ex, to_ex = frm in exchange_wallets, to in exchange_wallets
            if to_ex and not frm_ex:
                res["stable_exch_inflow"] += val
            elif frm_ex and not to_ex:
                res["stable_exch_outflow"] += val
    res["transfers"] = pd.DataFrame(transfers, columns=["from", "to", "value"])
    return res


# --------------------------------------------------------------------------- Solana
def sol_priority_fees() -> dict:
    url = get_settings().sol_rpc
    d = client().post(url, json={"jsonrpc": "2.0", "id": 1, "method": "getRecentPrioritizationFees", "params": []})
    fees = [x["prioritizationFee"] for x in d.get("result", [])]
    return {"sol_priority_fee_median": float(np.median(fees)) if fees else np.nan}


# --------------------------------------------------------------------------- Etherscan (optional key)
def etherscan_token_transfers(address: str, contract: str, key: str | None = None) -> pd.DataFrame:
    key = key or get_settings().etherscan_key
    if not key:
        raise SourceUnavailable("ETHERSCAN_API_KEY not set (optional)")
    d = client().get("https://api.etherscan.io/v2/api", {
        "chainid": 1, "module": "account", "action": "tokentx", "address": address,
        "contractaddress": contract, "page": 1, "offset": 200, "sort": "desc", "apikey": key})
    rows = d.get("result") or []
    if not isinstance(rows, list):
        raise SourceUnavailable(f"etherscan: {rows}")
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["value"] = df["value"].astype(float) / 10 ** df["tokenDecimal"].astype(int)
    return df[["from", "to", "value", "timeStamp"]]


# --------------------------------------------------------------------------- The Graph (optional key)
UNISWAP_V3_SUBGRAPH = "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"


def thegraph_uniswap_swaps(first: int = 100, key: str | None = None) -> pd.DataFrame:
    key = key or get_settings().thegraph_key
    if not key:
        raise SourceUnavailable("THEGRAPH_API_KEY not set (optional)")
    q = {"query": f"{{ swaps(first: {int(first)}, orderBy: timestamp, orderDirection: desc) "
                  "{ timestamp amountUSD amount0 amount1 token0 { symbol } token1 { symbol } origin } }"}
    d = client().post(f"https://gateway.thegraph.com/api/{key}/subgraphs/id/{UNISWAP_V3_SUBGRAPH}", json=q)
    return pd.json_normalize(d.get("data", {}).get("swaps", []))
