"""Strategy 1: GM pool liquidity ("be the house"), D123.

A GM pool token's price already contains every trading and borrowing fee the pool earned, every trader win or loss
against it, and the pool's token moves, so its daily price history is an exact record of what holding it paid.

Rules (Multi-strategy spec, strategy 1), using only data published by each day:
  rank      trailing 7- and 30-day fee APR + trailing 30-day traders' result for the pool (positive: traders lost)
  hold      the top 3 (at most 50% of the sleeve in one pool); a held pool stays until an exit rule fires or it
            falls out of the top 6 (switching costs a deposit and a withdrawal fee each time)
  exit      trailing 7-day pool return negative, or traders net winning against it 3 days running
  hedged    optional: short the pool's volatile-token exposure on Hyperliquid (size from a 30-day regression of the
            pool's daily returns on that token's), rebalanced when the hedge is 5%+ off; pays Hyperliquid fees,
            assumed slippage and the real hourly funding
Not available historically (forward only, stated on the page): utilisation and open-interest imbalance, and the
6-hourly re-ranking (the free history is daily).
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from decimal import Decimal

from perp import gates as GT
from perp.config import load
from perp.money import ZERO, Trade, d

HLMAP = {"WETH": "ETH", "WBTC": "BTC", "WBTC.b": "BTC", "BTC": "BTC", "ETH": "ETH"}


def hl_coin(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = symbol.replace(".b", "")
    return HLMAP.get(s, s[1:] if s.startswith("W") and len(s) > 3 else s)


def _beta(gm_r: list[float], tok_r: list[float]) -> float | None:
    if len(gm_r) < 20 or len(gm_r) != len(tok_r):
        return None
    v = statistics.pvariance(tok_r)
    if v <= 0:
        return None
    mg, mt = statistics.fmean(gm_r), statistics.fmean(tok_r)
    return sum((a - mg) * (b - mt) for a, b in zip(gm_r, tok_r)) / len(gm_r) / v


def clean(gm: dict) -> tuple[dict, list[str]]:
    """ANEE gate 1 for pool prices. A price at or below zero is a bad observation. A move of more than 50% in a day
    is a break: if the next two prices confirm the new level it is a level change (the series restarts from it and no
    return is ever counted across the jump), otherwise a one-off glitch. Bad days are excluded and counted; the
    first price after a break starts a new segment (returns use only consecutive good days of one segment)."""
    ks = sorted(gm)
    good, bad, last = {}, [], None
    for n, k in enumerate(ks):
        v = gm[k]
        if v is None or v <= 0:
            bad.append(k)
            continue
        if last is not None and abs(v / last - 1) > Decimal("0.5"):
            nxt = [gm[x] for x in ks[n + 1:n + 3] if gm[x] and gm[x] > 0]
            bad.append(k)
            if len(nxt) == 2 and all(abs(x / v - 1) < Decimal("0.1") for x in nxt):
                last = None                       # confirmed level change: restart from the next day
            continue
        good[k], last = v, v
    return good, bad


def score(p: dict, days: list[str], i: int) -> float | None:
    """Ranking score on day days[i], from that day's and earlier APR snapshots only."""
    a = [p["apr"].get(days[j], {}) for j in range(max(0, i - 29), i + 1)]
    f7 = [x["fee_apr"] for x in a[-7:] if "fee_apr" in x]
    f30 = [x["fee_apr"] for x in a if "fee_apr" in x]
    pn = [x["pnl_apr"] for x in a if "pnl_apr" in x]
    if len(f7) < 5 or len(f30) < 20 or len(pn) < 20:
        return None
    return (statistics.fmean(f7) + statistics.fmean(f30)) / 2 + statistics.fmean(pn)


def blocked(p: dict, days: list[str], i: int) -> str | None:
    """An exit rule firing on day days[i] (or None)."""
    t = days[i]
    if i < 7 or days[i - 7] not in p["gm"] or t not in p["gm"]:
        return "not enough history"
    if p["gm"][t] / p["gm"][days[i - 7]] - 1 < 0:
        return "trailing 7-day pool return negative"
    pn = [p["apr"].get(days[j], {}).get("pnl_apr") for j in range(i - 2, i + 1)]
    if all(x is not None and x < 0 for x in pn):
        return "traders net winning against the pool 3 days running"
    return None


def backtest(pools: list[dict], start_usd, hedge: bool = False, book: str = "account", cost_mult: Decimal = Decimal(1)) -> dict:
    """pools: [{name, address, gm: {day: price}, apr: {day: {fee_apr, pnl_apr}}, tok: {day: price} (long token),
    hl_funding: {hour_ts: rate}, swap_fee: Decimal, exec_fee: Decimal}]. Daily simulation of the sleeve."""
    P, C = load()["lp"], load()["costs"]
    gas = d(C["arbitrum_tx_usd"]) * cost_mult
    hl_fee = (d(C["hyperliquid"]["taker"]) + Decimal("0.0002")) * cost_mult   # documented taker fee + assumed 2 bp slippage
    days = sorted({k for p in pools for k in p["gm"]})
    idx = {k: i for i, k in enumerate(days)}
    cash, held = d(start_usd), {}                      # held: name -> {value, hedge, trade}
    trades, equity, daily = [], [], []
    too_small = 0                                      # deposits skipped: fixed fees above the allowed share (D124)
    byname = {p["name"]: p for p in pools}

    def close(name, t, why):
        nonlocal cash
        h = held.pop(name)
        p = byname[name]
        tr = h["trade"]
        fee = h["value"] * p["swap_fee"] * cost_mult
        tr.add_cost("withdraw_fee", fee)
        tr.add_cost("execution_fee", p["exec_fee"] * cost_mult)
        tr.add_cost("gas", gas)
        if h["hedge"]:
            tr.add_cost("hedge_fee", abs(h["hedge"]) * hl_fee)
        tr.closed, tr.reason = t, why
        cash += h["value"] - fee - p["exec_fee"] * cost_mult - gas - (abs(h["hedge"]) * hl_fee if h["hedge"] else ZERO) + h["hedge_pnl"]
        trades.append(tr)

    for t in days[30:-1]:
        nxt = days[idx[t] + 1]
        # 1) decide with data up to t
        ranked = sorted(((s, p["name"]) for p in pools if t in p["gm"] and nxt in p["gm"]
                         and (s := score(p, days, idx[t])) is not None and not blocked(p, days, idx[t])), reverse=True)
        top = [n for _, n in ranked[:P["top_n"]]]
        keep = {n for _, n in ranked[:P["top_n"] * 2]}
        for n in list(held):
            why = blocked(byname[n], days, idx[t]) if t in byname[n]["gm"] else "no price"
            if why or n not in keep:
                close(n, t, why or "fell out of the top 6")
        total = cash + sum(h["value"] + h["hedge_pnl"] for h in held.values())
        want = [n for n in top if n not in held][: max(0, P["top_n"] - len(held))]
        for n in want:
            p = byname[n]
            alloc = min(total * d(P["max_pool_share_of_sleeve"]), total / P["top_n"], cash)
            fixed = p["exec_fee"] * cost_mult + gas
            if alloc <= fixed * 2 or 2 * fixed > alloc * d(P["max_fixed_cost_share"]):
                too_small += 1
                continue
            fee = (alloc - fixed) * p["swap_fee"] * cost_mult
            value = alloc - fixed - fee
            tr = Trade("lp_hedged" if hedge else "lp", book, n, 0, value, t)
            tr.add_cost("deposit_fee", fee)
            tr.add_cost("execution_fee", p["exec_fee"] * cost_mult)
            tr.add_cost("gas", gas)
            tr.est_costs = {"deposit_fee": str(fee), "withdraw_fee": str(value * p["swap_fee"]), "execution_fee": str(2 * p["exec_fee"])}
            cash -= alloc
            held[n] = {"value": value, "hedge": ZERO, "hedge_pnl": ZERO, "trade": tr}
        # 2) hedge (sized with past data only)
        if hedge:
            for n, h in held.items():
                p = byname[n]
                i = idx[t]
                rs = [(float(p["gm"][days[j]] / p["gm"][days[j - 1]] - 1), float(p["tok"][days[j]] / p["tok"][days[j - 1]] - 1))
                      for j in range(i - 29, i + 1) if all(days[k] in p["gm"] and days[k] in p["tok"] for k in (j, j - 1))]
                b = _beta([a for a, _ in rs], [b for _, b in rs]) if p.get("tok") else None
                target = -d(round(b, 4)) * h["value"] if b is not None and b > 0 and p.get("hl_funding") is not None else ZERO
                if h["hedge"] == 0 and target == 0:
                    continue
                if h["hedge"] == 0 or abs(target - h["hedge"]) > abs(h["hedge"]) * Decimal("0.05"):
                    chg = abs(target - h["hedge"])
                    h["trade"].add_cost("hedge_fee", chg * hl_fee)
                    h["hedge_pnl"] -= chg * hl_fee
                    h["hedge"] = target
        # 3) mark to the next day
        for n in [n for n in held if nxt not in byname[n]["gm"] or t not in byname[n]["gm"]]:
            close(n, t, "pool price data break (bad observation): closed at the last good price")
        for n, h in held.items():
            p = byname[n]
            r = p["gm"][nxt] / p["gm"][t] - 1
            g = h["value"] * r
            h["value"] += g
            h["trade"].gross += g
            if h["hedge"]:
                tr_ = p["tok"].get(nxt), p["tok"].get(t)
                if tr_[0] and tr_[1]:
                    hp = h["hedge"] * (tr_[0] / tr_[1] - 1)          # the short's price result
                    h["hedge_pnl"] += hp
                    h["trade"].gross += hp
                t0 = int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp())
                fund = sum((p["hl_funding"].get(t0 + 3600 * k, ZERO) for k in range(24)), ZERO)
                cost = -abs(h["hedge"]) * fund                 # positive funding: longs pay shorts, so the short receives
                h["trade"].add_cost("hedge_funding", cost)
                h["hedge_pnl"] -= cost
        eq = cash + sum(h["value"] + h["hedge_pnl"] for h in held.values())
        daily.append(float(eq / (equity[-1][1] if equity else d(start_usd)) - 1))
        equity.append((nxt, eq))
    for n in list(held):
        close(n, days[-1], "end of the test")
    return {"trades": trades, "equity": [(t, str(e)) for t, e in equity], "daily": daily, "too_small": too_small}


def passive(pools: list[dict], names: list[str], start_usd) -> dict:
    """Benchmark: buy the named pools in equal parts on the first common day and hold, one deposit and one
    withdrawal fee each (no rotation, no hedge)."""
    ps = [p for p in pools if p["name"] in names]
    days = sorted(set.intersection(*(set(p["gm"]) for p in ps))) if ps else []
    if len(days) < 2:
        return {"days": 0}
    part = d(start_usd) / len(ps)
    eq = []
    for t in days:
        v = sum((part * (1 - p["swap_fee"]) * p["gm"][t] / p["gm"][days[0]] for p in ps), ZERO)
        eq.append((t, v))
    fin = eq[-1][1] * (1 - ps[0]["swap_fee"])
    vals = [float(v) for _, v in eq]
    return {"from": days[0], "days": len(days), "total_return": float(fin / d(start_usd) - 1),
            "annual_return": float(fin / d(start_usd)) ** (365 / len(days)) - 1, "max_drawdown": GT.max_drawdown(vals)}


def summary(res: dict, start_usd) -> dict:
    eq = [float(e) for _, e in res["equity"]]
    daily = res["daily"]
    held_days = sum(1 for x in daily if x != 0)
    out = {"days": len(daily), "held_days": held_days, "final": eq[-1] if eq else None,
           "total_return": (eq[-1] / float(start_usd) - 1) if eq else None,
           "annual_return": ((eq[-1] / float(start_usd)) ** (365 / len(daily)) - 1) if eq and daily else None,
           "max_drawdown": GT.max_drawdown(eq), "trades": len(res["trades"]),
           "by_year": {}}
    for y in sorted({t[:4] for t, _ in res["equity"]}):
        ys = [float(e) for t, e in res["equity"] if t[:4] == y]
        prev = [float(e) for t, e in res["equity"] if t[:4] < y]
        base = prev[-1] if prev else float(start_usd)
        out["by_year"][y] = ys[-1] / base - 1
    return out
