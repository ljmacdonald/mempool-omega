"""The live snapshot each hourly run decides on (D123): one context per in-scope GMX market, with every input's
source and time, and the data-quality verdict (ANEE section 4.2, stages 1-2). Nothing is filled in: a missing or
stale input makes that market NOT_TRADEABLE with the reason recorded.
"""
from __future__ import annotations

import statistics
import time
from decimal import Decimal

from perp import costs as CO
from perp import history as HI
from perp import sources as SRC
from perp.config import load
from perp.lp import hl_coin

YEAR_S = Decimal(31_536_000)
E30 = Decimal(10) ** 30


def integrity_ok(oracle, reference, limit=Decimal("0.02")) -> bool:
    """AT-01: the oracle and an independent reference price must agree within 2%, or nothing trades on them."""
    return abs(Decimal(oracle) / Decimal(reference) - 1) <= limit


def main_markets(markets: list[dict]) -> dict[str, dict]:
    """The deepest USDC-backed GMX market for each in-scope coin (e.g. ETH/USD [ETH-USDC])."""
    want, out = set(load()["markets"]), {}
    for m in markets:
        ix = (m.get("index") or "").replace(".b", "")
        if ix in want and m.get("short") == "USDC" and m.get("listed"):
            liq = Decimal(m["raw"].get("availableLiquidityLong") or 0)
            if ix not in out or liq > out[ix]["_liq"]:
                out[ix] = {**m, "_liq": liq}
    return out


def snapshot(now: float | None = None) -> dict:
    """Contexts for the cost model and the strategies, the kill-switch inputs, and the quality verdicts."""
    now = now or time.time()
    R = load()["risk"]
    markets = HI.markets()
    mains = main_markets(markets)
    tick = {t["tokenSymbol"]: t for t in SRC.gmx_tickers()}
    hlc = SRC.hl_contexts()
    fees = HI.execution_fees(300)
    eth = tick.get("ETH")
    eth_usd = (Decimal(eth["minPrice"]) + Decimal(eth["maxPrice"])) / 2 / Decimal(10) ** 12 if eth else None
    exec_fee = Decimal(str(statistics.median(fees))) * eth_usd if fees and eth_usd else None
    usdc = tick.get("USDC")
    usdc_px = (Decimal(usdc["minPrice"]) + Decimal(usdc["maxPrice"])) / 2 / Decimal(10) ** 24 if usdc else None
    kill = []
    if usdc_px is None or abs(usdc_px - 1) > Decimal(str(R["depeg"])):
        kill.append(f"USDC price {usdc_px} outside 1% of $1 (or missing)")
    ctxs, quality = {}, {}
    for coin, m in mains.items():
        why = []
        raw = SRC.gmx_market_settings(m["address"]) or {}
        settings = CO.gmx_settings(raw) if raw else {}
        t = tick.get(m["index"])
        oracle = (Decimal(t["minPrice"]) + Decimal(t["maxPrice"])) / 2 / Decimal(10) ** (30 - m["index_dec"]) if t and m.get("index_dec") is not None else None
        age = now - t["timestamp"] if t else None
        if oracle is None:
            why.append("no oracle price")
        elif age > R["stale_oracle_s"]:
            why.append(f"oracle price {int(age)} s old")
        r = m["raw"]

        def ann(k, r=r):                                 # the API gives annual rates, positive = paid
            return (Decimal(r[k]) / E30 / YEAR_S) if r.get(k) is not None else None
        if raw.get("useOpenInterestInTokensForBalance") and raw.get("longOpenInterestInTokens") and oracle is not None:
            sc = Decimal(10) ** m["index_dec"]
            oi = (Decimal(raw["longOpenInterestInTokens"]) / sc * oracle, Decimal(raw["shortOpenInterestInTokens"]) / sc * oracle)
        else:
            oi = (Decimal(r["openInterestLong"]) / E30, Decimal(r["openInterestShort"]) / E30) if r.get("openInterestLong") else (None, None)
        hcoin = hl_coin(m["index"])
        hl = hlc.get(hcoin)
        book = SRC.hl_book(hcoin) if hl else None
        bk = {"bids": [(x["px"], x["sz"]) for x in book["levels"][0]], "asks": [(x["px"], x["sz"]) for x in book["levels"][1]]} if book else None
        ctx = {"market": coin, "name": m["name"], "address": m["address"], "settings": settings, "oracle": oracle, "oracle_age_s": age,
               "oi_long": oi[0], "oi_short": oi[1],
               "liquidity_long": Decimal(r["availableLiquidityLong"]) / E30 if r.get("availableLiquidityLong") else None,
               "liquidity_short": Decimal(r["availableLiquidityShort"]) / E30 if r.get("availableLiquidityShort") else None,
               "funding_ps_long": ann("fundingRateLong"), "funding_ps_short": ann("fundingRateShort"),
               "borrow_ps_long": ann("borrowingRateLong"), "borrow_ps_short": ann("borrowingRateShort"),
               "execution_fee_usd": exec_fee, "hl_coin": hcoin,
               "hl": {"mid": Decimal(hl["midPx"]) if hl and hl.get("midPx") else None,
                      "funding_hourly": Decimal(hl["funding"]) if hl and hl.get("funding") is not None else None,
                      "book": bk} if hl else None,
               "pool_value": Decimal(raw["poolValue"]) / E30 if raw.get("poolValue") else None,
               "gm_price": (Decimal(raw["poolValue"]) / E30) / (Decimal(raw["marketTokenSupply"]) / Decimal(10) ** 18)
               if raw.get("poolValue") and raw.get("marketTokenSupply") else None}
        if not ctx["settings"]:
            why.append("missing market settings")
        for k in ("oi_long", "oi_short", "funding_ps_long", "funding_ps_short", "borrow_ps_long", "borrow_ps_short", "execution_fee_usd"):
            if ctx.get(k) is None:                       # a zero rate is a real value; only None is missing
                why.append(f"missing {k.replace('_ps', '').replace('_', ' ')}")
        if not ctx["hl"] or ctx["hl"]["mid"] is None:
            why.append("no Hyperliquid price (hedge strategies can't trade this market)")
        elif oracle is not None and not integrity_ok(oracle, ctx["hl"]["mid"]):
            why.append(f"price integrity: GMX oracle and Hyperliquid differ by {float(abs(oracle / ctx['hl']['mid'] - 1)):.1%} (AT-01)")
        ctxs[coin] = ctx
        quality[coin] = {"tradeable": not [w for w in why if not w.startswith("no Hyperliquid")], "hedgeable": not why,
                         "reasons": why}
    return {"t": now, "contexts": ctxs, "quality": quality, "kill": kill, "exec_fee_usd": exec_fee, "usdc": usdc_px,
            "markets": markets}
