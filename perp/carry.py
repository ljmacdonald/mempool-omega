"""Strategy 2: delta-neutral funding and borrowing capture, GMX v2 against Hyperliquid (D123).

Take the GMX side that the funding/borrowing balance pays, hedge the price on Hyperliquid, collect the difference.
Rules (Multi-strategy spec, strategy 2), hour by hour, using only rates already published:
  net carry   GMX funding received - GMX borrowing - Hyperliquid funding paid (per side), annualised
  enter       net carry, minus the round-trip costs spread over a 72-hour hold, above 15% a year, and positive for
              the last 3 hours
  exit        net carry below 3% a year, or negative for 2 hours running
  fills       at the NEXT hour's prices on both venues (an hourly job can't act faster), so the price gap between
              the two venues at entry and exit is a real gain or loss (the basis)
Positions are sized by the risk rules: margin at most 20% of the sleeve, 2x leverage, so each leg is at most 40%
of the sleeve, shared equally between the markets watched.
"""
from __future__ import annotations

from decimal import Decimal

from perp import costs as CO
from perp.config import load
from perp.money import ZERO, Trade, d, iso

H = Decimal(3600)


def hourly_carry(gmx: dict, hl_rate: Decimal, side: int) -> Decimal | None:
    """Net income per hour as a share of notional for GMX `side` (+1 long) hedged on Hyperliquid (opposite side).
    gmx: {fund_long, fund_short, borrow_long, borrow_short} per second as costs (positive = paid)."""
    k = "long" if side > 0 else "short"
    if gmx.get(f"fund_{k}") is None or gmx.get(f"borrow_{k}") is None or hl_rate is None:
        return None
    gmx_cost = (gmx[f"fund_{k}"] + gmx[f"borrow_{k}"]) * H
    hl_income = hl_rate * (1 if side > 0 else -1)          # positive HL funding: longs pay shorts; the hedge is the other side
    return hl_income - gmx_cost


def round_trip_items(settings: dict, oi: tuple, side: int, notional, exec_fee) -> dict:
    """Entry + exit costs of both legs, itemised (conservative fee, impact at today's balance; holding costs excluded)."""
    ctx = {"settings": settings, "oi_long": oi[0], "oi_short": oi[1], "funding_ps_long": ZERO, "funding_ps_short": ZERO,
           "borrow_ps_long": ZERO, "borrow_ps_short": ZERO, "execution_fee_usd": exec_fee}
    g = CO.gmx_round_trip(ctx, side, notional, 0)
    h = CO.hl_round_trip({"funding_hourly": ZERO}, -side, notional, 0)
    return {k: v for k, v in {**g, **h}.items() if k not in ("funding", "borrow", "hedge_funding")}


def round_trip_share(settings: dict, oi: tuple, side: int, notional, exec_fee) -> Decimal:
    return sum(round_trip_items(settings, oi, side, notional, exec_fee).values(), ZERO) / d(notional)


def backtest(markets: list[dict], start_usd, book: str = "account", cost_mult: Decimal = Decimal(1)) -> dict:
    """markets: [{name, coin, rates: {hour: gmx rates}, hl: {hour: rate}, gmx_px: {hour: close}, hl_px: {hour: close},
    settings, oi, exec_fee}]. Hours where either venue's price is missing can't be traded (fills need both)."""
    P, R = load()["carry"], load()["risk"]
    sleeve = d(start_usd)
    cap = sleeve * d(R["max_margin_share"]) * d(R["max_leverage"]) / max(1, len(markets))
    trades = []
    for m in markets:
        hours = sorted(set(m["rates"]) & set(m["hl"]) & set(m["gmx_px"]) & set(m["hl_px"]))
        pos, hist = None, {1: [], -1: []}
        for i, h in enumerate(hours[:-1]):
            nh = hours[i + 1]
            if nh - h != 3600:
                continue
            c = {s: hourly_carry(m["rates"][h], m["hl"][h], s) for s in (1, -1)}
            for s in (1, -1):
                hist[s] = (hist[s] + [c[s]])[-P["confirm_hours"]:]
            if pos:                                          # accrue this hour, then check the exit rules
                pos["trade"].add_cost("funding", m["rates"][h]["fund_long" if pos["side"] > 0 else "fund_short"] * H * pos["n"])
                pos["trade"].add_cost("borrow", m["rates"][h]["borrow_long" if pos["side"] > 0 else "borrow_short"] * H * pos["n"])
                pos["trade"].add_cost("hedge_funding", -m["hl"][h] * (1 if pos["side"] > 0 else -1) * pos["n"])
                cs = c[pos["side"]]
                pos["neg"] = pos["neg"] + 1 if cs is not None and cs < 0 else 0
                ann = cs * 8760 if cs is not None else None
                if ann is None or ann < d(P["exit_annual"]) or pos["neg"] >= P["flip_hours"]:
                    tr, s, n = pos["trade"], pos["side"], pos["n"]
                    g1, h1 = d(m["gmx_px"][nh]), d(m["hl_px"][nh])
                    tr.gross = s * n * (g1 / pos["g0"] - 1) - s * n * (h1 / pos["h0"] - 1)
                    for k, v in round_trip_items(m["settings"], m["oi"], s, n, m["exec_fee"]).items():
                        tr.add_cost(k, v * cost_mult)
                    tr.closed, tr.reason = iso(nh), "carry below 3% a year" if ann is not None and ann < d(P["exit_annual"]) else "carry negative 2 hours"
                    trades.append(tr)
                    pos = None
                continue
            for s in (1, -1):
                cs = c[s]
                if cs is None or len(hist[s]) < P["confirm_hours"] or any(x is None or x <= 0 for x in hist[s]):
                    continue
                n = cap
                rt = round_trip_share(m["settings"], m["oi"], s, n, m["exec_fee"]) * cost_mult
                net_ann = cs * 8760 - rt / P["hold_hours"] * 8760
                if net_ann > d(P["enter_annual"]):
                    tr = Trade("carry", book, m["name"], s, n, iso(nh))
                    tr.est_costs = {k: str(v * cost_mult) for k, v in round_trip_items(m["settings"], m["oi"], s, n, m["exec_fee"]).items()}
                    pos = {"side": s, "n": n, "trade": tr, "g0": d(m["gmx_px"][nh]), "h0": d(m["hl_px"][nh]), "neg": 0}
                    break
        if pos:                                              # still open at the end: closed at the last prices
            tr, s, n = pos["trade"], pos["side"], pos["n"]
            tr.gross = s * n * (d(m["gmx_px"][hours[-1]]) / pos["g0"] - 1) - s * n * (d(m["hl_px"][hours[-1]]) / pos["h0"] - 1)
            for k, v in round_trip_items(m["settings"], m["oi"], s, n, m["exec_fee"]).items():
                tr.add_cost(k, v * cost_mult)
            tr.closed, tr.reason = iso(hours[-1]), "end of the test"
            trades.append(tr)
    return {"trades": trades}
