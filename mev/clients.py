"""Read-only data sources: Jupiter quotes, Solana RPC fee levels, Jito's tip floor. Nothing here can send a transaction."""
from __future__ import annotations

import time

import requests

from mev import config as K


class Quotes:
    """Jupiter quotes, paced under the free limit (about one request a second), backing off when refused."""

    def __init__(self, pace: float = K.QUOTE_PACE_S, session: requests.Session | None = None):
        self.s, self.pace, self.last, self.calls, self.refused = session or requests.Session(), pace, 0.0, 0, 0

    def quote(self, in_mint: str, out_mint: str, amount: int, dex: str | None = None, paired: bool = False) -> dict | None:
        """{"out": int, "slot": int, "at": epoch seconds, "route": [labels]} or None if there's no route.
        paired: the second leg of a round trip, quoted right after the first (LEG_GAP_S) so both legs see the same
        moment; the pause that pacing skipped is added before the next request, so the average rate is unchanged."""
        params = {"inputMint": in_mint, "outputMint": out_mint, "amount": int(amount), "slippageBps": K.SLIPPAGE_BPS,
                  "restrictIntermediateTokens": "true"}
        if dex:
            params["dexes"] = dex
        for attempt in range(4):
            gap = K.LEG_GAP_S if paired and attempt == 0 else self.pace
            wait = gap - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic() + (self.pace - gap)   # owe the skipped pause to the next request
            self.calls += 1
            try:
                r = self.s.get(f"{K.JUPITER}/quote", params=params, timeout=10)
            except requests.RequestException:
                continue
            if r.status_code == 429:
                self.refused += 1
                time.sleep(min(30, float(r.headers.get("Retry-After") or 2 ** (attempt + 1))))
                continue
            if r.status_code != 200:
                return None
            d = r.json()
            if not d.get("outAmount"):
                return None
            return {"out": int(d["outAmount"]), "slot": d.get("contextSlot"), "at": time.time(),
                    "route": [x["swapInfo"]["label"] for x in d.get("routePlan", [])]}
        return None


def sol_usd(q: Quotes) -> float | None:
    """SOL's price in dollars from a Jupiter quote of 1 SOL into USDC (6 decimals)."""
    r = q.quote(K.SOL, K.TOKENS["USDC"], K.LAMPORTS)
    return r["out"] / 1e6 if r else None


def _rpc(method: str, params: list | None = None, url: str = K.RPC):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}, timeout=15)
    r.raise_for_status()
    return r.json()["result"]


def slot() -> int | None:
    try:
        return int(_rpc("getSlot"))
    except Exception:  # noqa: BLE001
        return None


def priority_fees() -> dict:
    """Priority-fee prices (micro-lamports per compute unit) on busy SOL pools: median and 75th percentile of the
    non-zero ones, never below the assumed floor."""
    try:
        xs = sorted(int(x["prioritizationFee"]) for x in _rpc("getRecentPrioritizationFees", [K.FEE_ACCOUNTS]))
    except Exception:  # noqa: BLE001
        return {"p50": K.PRIORITY_FLOOR, "p75": K.PRIORITY_FLOOR, "slots": 0, "share_paying": None, "floor_used": True}
    nz = [x for x in xs if x > 0]
    pick = lambda q: max(K.PRIORITY_FLOOR, nz[min(len(nz) - 1, int(q * len(nz)))] if nz else 0)  # noqa: E731
    return {"p50": pick(0.5), "p75": pick(0.75), "slots": len(xs),
            "share_paying": (sum(x > 0 for x in xs) / len(xs)) if xs else None,
            "floor_used": not nz or nz[len(nz) // 2] < K.PRIORITY_FLOOR}


def tip_floor() -> dict | None:
    try:
        r = requests.get(K.JITO_TIP_FLOOR, timeout=10)
        r.raise_for_status()
        d = r.json()
        return d[0] if isinstance(d, list) and d else None
    except Exception:  # noqa: BLE001
        return None
