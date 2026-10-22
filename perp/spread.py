"""Strategy 3: cross-venue basis and spread, GMX v2 against Hyperliquid (D123).

When the same coin's price differs enough between GMX (oracle price, plus GMX's price impact at our size) and
Hyperliquid, buy the cheap one, sell the dear one, close both when the gap narrows.
Rules (Multi-strategy spec, strategy 3), applied on hourly closes (an hourly free job can't act on 10-second data,
which is why the page also MEASURES how often and how long such gaps appear):
  spread      GMX price / Hyperliquid price - 1, z-score against its own trailing 7 days (past data only)
  enter       |z| > 2.5 and |spread| > 2 x the round-trip costs at our size; fill at the NEXT hour's prices
  exit        |z| < 0.5, or after 24 hours, or a stop at |z| > 4 against us
"""
from __future__ import annotations

import statistics
from decimal import Decimal

from perp.carry import round_trip_items
from perp.config import load
from perp.money import ZERO, Trade, d, iso

H = Decimal(3600)


def zscores(spreads: list[tuple[int, float]], window: int) -> dict[int, tuple[float, float, float]]:
    """hour -> (spread, mean, sd) from the `window` hours BEFORE it (the current hour isn't in its own baseline)."""
    out = {}
    for i in range(window, len(spreads)):
        past = [s for _, s in spreads[i - window:i]]
        sd = statistics.pstdev(past)
        if sd > 0:
            out[spreads[i][0]] = (spreads[i][1], statistics.fmean(past), sd)
    return out


def backtest(markets: list[dict], start_usd, book: str = "account", cost_mult: Decimal = Decimal(1)) -> dict:
    """markets: [{name, gmx_px: {hour: close}, hl_px: {hour: close}, rates: {hour: gmx rates}, hl: {hour: funding},
    settings, oi, exec_fee}]."""
    P, R = load()["spread"], load()["risk"]
    cap = d(start_usd) * d(R["max_margin_share"]) * d(R["max_leverage"]) / max(1, len(markets))
    trades, seen = [], []
    for m in markets:
        hours = sorted(set(m["gmx_px"]) & set(m["hl_px"]))
        sp = [(h, m["gmx_px"][h] / m["hl_px"][h] - 1) for h in hours]
        z = zscores(sp, P["window_days"] * 24)
        zh = sorted(z)
        pos = None
        rt_share = float(sum(round_trip_items(m["settings"], m["oi"], 1, cap, m["exec_fee"]).values(), ZERO) / cap) * float(cost_mult)
        for i, h in enumerate(zh[:-1]):
            nh = zh[i + 1]
            if nh - h != 3600:
                continue
            s, mu, sd = z[h]
            zz = (s - mu) / sd
            if pos:
                side = pos["side"]                       # +1: long GMX / short HL (GMX was cheap)
                for k_src, k_dst in (("fund_long" if side > 0 else "fund_short", "funding"),
                                     ("borrow_long" if side > 0 else "borrow_short", "borrow")):
                    v = m["rates"].get(h, {}).get(k_src)
                    if v is not None:
                        pos["trade"].add_cost(k_dst, v * H * pos["n"])
                if m["hl"].get(h) is not None:
                    pos["trade"].add_cost("hedge_funding", -m["hl"][h] * side * pos["n"])
                against = (zz > P["stop_z"]) if side < 0 else (zz < -P["stop_z"])
                done = abs(zz) < P["exit_z"] or (nh - pos["t0"]) >= P["max_hours"] * 3600 or against
                if done:
                    tr, n = pos["trade"], pos["n"]
                    g1, h1 = d(m["gmx_px"][nh]), d(m["hl_px"][nh])
                    tr.gross = side * n * (g1 / pos["g0"] - 1) - side * n * (h1 / pos["h0"] - 1)
                    for k, v in round_trip_items(m["settings"], m["oi"], side, n, m["exec_fee"]).items():
                        tr.add_cost(k, v * cost_mult)
                    tr.closed = iso(nh)
                    tr.reason = "stop: gap widened (z beyond 4)" if against else "gap closed" if abs(zz) < P["exit_z"] else "24 hours"
                    trades.append(tr)
                    pos = None
                continue
            if abs(zz) > P["enter_z"]:
                ok = abs(s) > P["cost_multiple"] * rt_share
                seen.append({"market": m["name"], "hour": h, "spread": s, "z": zz, "costs": rt_share, "taken": ok})
                if ok and nh in m["gmx_px"] and nh in m["hl_px"]:
                    side = -1 if s > 0 else 1
                    tr = Trade("spread", book, m["name"], side, cap, iso(nh))
                    tr.est_costs = {k: str(v * cost_mult) for k, v in round_trip_items(m["settings"], m["oi"], side, cap, m["exec_fee"]).items()}
                    pos = {"side": side, "n": cap, "trade": tr, "g0": d(m["gmx_px"][nh]), "h0": d(m["hl_px"][nh]), "t0": nh}
    return {"trades": trades, "seen": seen}


def gap_stats(markets: list[dict]) -> dict:
    """How often the GMX-Hyperliquid gap exceeds 2x round-trip costs at a $1,000 position, and for how long."""
    out = {}
    for m in markets:
        hours = sorted(set(m["gmx_px"]) & set(m["hl_px"]))
        if not hours:
            continue
        rt = float(sum(round_trip_items(m["settings"], m["oi"], 1, Decimal(1000), m["exec_fee"]).values(), ZERO) / 1000)
        sp = [abs(m["gmx_px"][h] / m["hl_px"][h] - 1) for h in hours]
        wide = [x > 2 * rt for x in sp]
        runs, cur = [], 0
        for w in wide:
            cur = cur + 1 if w else (runs.append(cur) or 0) if cur else 0
        if cur:
            runs.append(cur)
        out[m["name"]] = {"hours": len(hours), "median_gap": statistics.median(sp), "p95_gap": sorted(sp)[int(0.95 * len(sp))],
                          "cost_share_1000": rt, "hours_over_2x_costs": sum(wide), "episodes": len(runs),
                          "longest_hours": max(runs, default=0)}
    return out
