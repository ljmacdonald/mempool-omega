"""The $100 USDC paper account of the Accumulation lab (D119). PAPER ONLY: no wallet, no keys, no orders.

Every amount is in USDC. Token prices from the pools are in US dollars; they are converted 1:1 to USDC only while
USDC trades within USDC_DEPEG of a dollar (checked on Binance), otherwise new trades are blocked.

A watchlist entry never opens a trade by itself. A candidate is eligible only if all of these hold:
  * its state is Accumulation detected or Persistent accumulation, it isn't already repricing, its data is fresh;
  * there is a defensible invalidation level: the lowest daily low of the last 14 days, 2% lower, between
    STOP_RANGE below the entry (else: not eligible under this risk model; no stop is invented);
  * reward to risk at least MIN_RR (target = entry + MIN_RR x the distance to the stop) after costs;
  * size: planned loss RISK_PER_TRADE of equity, capped at MAX_POSITION of equity and by cash; total planned open
    risk at most MAX_OPEN_RISK; costs (pool fee each way, an estimate of price impact, the network fee) under a
    quarter of the planned gain.
Exits: the stop or the target (checked every hour; a gap through the stop fills at the worse price), the state
turning to distribution, weakening, disqualified or archived, or MAX_HOLD_D days. No averaging down, no leverage.
"""
from __future__ import annotations

from datetime import datetime

from accum import config as K


def new_account() -> dict:
    return {"start": K.PAPER_START, "cash": K.PAPER_START, "positions": [], "trades": [], "decisions": [], "live_trading": False,
            "rules": {"risk_per_trade": K.RISK_PER_TRADE, "max_open_risk": K.MAX_OPEN_RISK, "max_position": K.MAX_POSITION,
                      "min_rr": K.MIN_RR, "dex_fee": K.DEX_FEE, "max_hold_d": K.MAX_HOLD_D}}


def impact(notional: float, reserve: float | None) -> float | None:
    """Price impact of a swap of `notional` against a constant-product pool holding `reserve` (both sides, USD)."""
    if not reserve or reserve <= 0:
        return None
    half = reserve / 2
    return notional / (half + notional)


def equity(acc: dict, prices: dict[str, float | None]) -> float:
    return acc["cash"] + sum(p["qty"] * (prices.get(p["key"]) or p["entry"]) for p in acc["positions"])


def open_risk(acc: dict) -> float:
    return sum(p["qty"] * (p["entry"] - p["stop"]) for p in acc["positions"])


def plan(c: dict, acc: dict, prices: dict, usdc_ok: bool, now: datetime) -> dict:
    """A trade plan for candidate c, or the reason it isn't eligible. c: key, chain, symbol, state, price, price_at,
    low14, reserve, repricing."""
    why = None
    px = c.get("price")
    if c.get("state") not in ("accumulation_detected", "persistent_accumulation"):
        why = "state is not an accumulation state"
    elif c.get("repricing"):
        why = "already repricing (up 30%+ in 14 days)"
    elif not usdc_ok:
        why = "USDC is off its dollar peg or its price is unavailable: no new trades"
    elif not px or not c.get("price_at") or (now - c["price_at"]).total_seconds() / 60 > K.PRICE_STALE_MIN:
        why = "price missing or stale"
    elif any(p["key"] == c["key"] for p in acc["positions"]):
        why = "already holding it"
    elif not c.get("low14"):
        why = "no 14-day low: no defensible invalidation level"
    if why:
        return {"eligible": False, "why": why}
    stop = c["low14"] * 0.98
    dist = (px - stop) / px
    if not (K.STOP_RANGE[0] <= dist <= K.STOP_RANGE[1]):
        return {"eligible": False, "why": f"invalidation {dist:.0%} below the price: outside {K.STOP_RANGE[0]:.0%}-{K.STOP_RANGE[1]:.0%}, no defensible stop"}
    eq = equity(acc, prices)
    qty = K.RISK_PER_TRADE * eq / (px - stop)
    notional = min(qty * px, K.MAX_POSITION * eq, acc["cash"] * 0.95)
    qty = notional / px
    risk = qty * (px - stop)
    if open_risk(acc) + risk > K.MAX_OPEN_RISK * eq + 1e-9:
        return {"eligible": False, "why": "total planned open risk would pass 4% of the account"}
    if notional < 1:
        return {"eligible": False, "why": "less than $1 of cash available"}
    imp = impact(notional, c.get("reserve"))
    if imp is None:
        return {"eligible": False, "why": "liquidity unknown: price impact can't be estimated"}
    target = px + K.MIN_RR * (px - stop)
    costs = notional * 2 * (K.DEX_FEE + imp) + 2 * K.NETWORK_FEE_USD.get(c.get("chain"), 0.3)
    gain = qty * (target - px)
    if costs > 0.25 * gain:
        return {"eligible": False, "why": f"costs ${costs:.2f} would eat more than a quarter of the planned ${gain:.2f} gain"}
    return {"eligible": True, "entry": px, "stop": stop, "target": target, "qty": qty, "notional": notional, "risk": risk,
            "impact": imp, "costs_est": costs, "rr": K.MIN_RR}


def buy(acc: dict, c: dict, p: dict, now: datetime) -> dict:
    fill = p["entry"] * (1 + p["impact"])                       # pay the price impact
    fee = p["notional"] * K.DEX_FEE + K.NETWORK_FEE_USD.get(c.get("chain"), 0.3)
    qty = p["notional"] / fill
    acc["cash"] -= p["notional"] + fee
    pos = {"key": c["key"], "symbol": c.get("symbol"), "chain": c.get("chain"), "opened": now.isoformat(), "entry": fill, "qty": qty,
           "stop": p["stop"], "target": p["target"], "cost": p["notional"] + fee, "fees": fee, "route": c.get("route"),
           "why": c.get("why")}
    acc["positions"].append(pos)
    acc["decisions"].append({"t": now.isoformat(), "key": c["key"], "symbol": c.get("symbol"), "action": "open", "plan": p,
                             "rules": acc["rules"]})
    return pos


def sell(acc: dict, pos: dict, px: float, reserve: float | None, reason: str, now: datetime) -> dict:
    gross = pos["qty"] * px
    imp = impact(gross, reserve) or 0.01
    fill = px * (1 - imp)
    fee = gross * K.DEX_FEE + K.NETWORK_FEE_USD.get(pos.get("chain"), 0.3)
    proceeds = pos["qty"] * fill - fee
    acc["cash"] += proceeds
    acc["positions"] = [p for p in acc["positions"] if p is not pos]
    tr = {**{k: pos[k] for k in ("key", "symbol", "chain", "opened", "entry", "qty", "stop", "target", "route")}, "closed": now.isoformat(),
          "exit": fill, "reason": reason, "fees": pos["fees"] + fee, "pnl": proceeds - pos["cost"], "ret": proceeds / pos["cost"] - 1}
    acc["trades"].append(tr)
    acc["decisions"].append({"t": now.isoformat(), "key": pos["key"], "symbol": pos.get("symbol"), "action": "close", "reason": reason})
    return tr


def manage(acc: dict, cands: dict[str, dict], prices: dict, lows: dict, reserves: dict, now: datetime) -> list[dict]:
    """Exits first: stop (at the worse of stop and price), target, state, time."""
    done = []
    for pos in list(acc["positions"]):
        px = prices.get(pos["key"])
        lo = lows.get(pos["key"])
        st = (cands.get(pos["key"]) or {}).get("state")
        held = (now - datetime.fromisoformat(pos["opened"])).total_seconds() / 86400
        if px is None:
            continue
        if (lo is not None and lo <= pos["stop"]) or px <= pos["stop"]:
            done.append(sell(acc, pos, min(px, pos["stop"]), reserves.get(pos["key"]), "safety exit (invalidation level hit)", now))
        elif px >= pos["target"]:
            done.append(sell(acc, pos, pos["target"], reserves.get(pos["key"]), "target reached", now))
        elif st in ("distribution_detected", "accumulation_weakening", "disqualified", "archived"):
            done.append(sell(acc, pos, px, reserves.get(pos["key"]), f"state changed: {st.replace('_', ' ')}", now))
        elif held >= K.MAX_HOLD_D:
            done.append(sell(acc, pos, px, reserves.get(pos["key"]), f"held {K.MAX_HOLD_D} days", now))
    return done


def mark(acc: dict, prices: dict) -> dict:
    eq = equity(acc, prices)
    unreal = sum(p["qty"] * ((prices.get(p["key"]) or p["entry"]) - p["entry"]) for p in acc["positions"])
    real = sum(t["pnl"] for t in acc["trades"])
    return {"equity": eq, "cash": acc["cash"], "realized": real, "unrealized": unreal, "open_risk": open_risk(acc),
            "exposure": eq - acc["cash"], "fees": sum(t["fees"] for t in acc["trades"]) + sum(p["fees"] for p in acc["positions"])}

