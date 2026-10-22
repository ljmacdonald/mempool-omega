"""The live paper engine (D123): every hour, each strategy acts on the live snapshot through the same cost model,
the independent risk gate and the kill switches, on two books: the $100 account and a $10,000 reference account
(the same rules at a size where fixed costs matter less, to separate edge from small-account friction).

Every candidate is logged with its decision (ACCEPT / REJECT / NO_SIGNAL), the reasons, the cost estimates and the
data it was decided on (state/perp/decisions/DAY.jsonl), accepted or not. Fills are at the live oracle price (GMX)
and mid price (Hyperliquid); impact, fees, execution fees and slippage are charged as cost lines. Nothing is ever
signed or sent.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from perp import costs as CO
from perp import directional as DI
from perp import history as HI
from perp import lp as LP
from perp.carry import hourly_carry
from perp.config import fingerprint, load
from perp.money import ZERO, Trade, d, iso

STATE = Path("state/perp")
STRATS = ("lp", "carry", "spread", "trend", "reversion")
SLEEVE_OF = {"lp": "lp", "carry": "carry", "spread": "spread", "trend": "directional", "reversion": "directional"}
H = Decimal(3600)


# ------------------------------------------------------------------ state
def new_state(now: float) -> dict:
    A = load()["account"]
    books = {}
    for book, size in (("account", A["start_usd"]), ("reference", A["reference_usd"])):
        books[book] = {}
        for s in STRATS:
            share = Decimal(str(A["sleeves"][SLEEVE_OF[s]])) / (2 if SLEEVE_OF[s] == "directional" else 1)
            start = d(size) * share
            books[book][s] = {"start": str(start), "cash": str(start), "open": [], "closed": [], "equity": [],
                              "peak": str(start), "day": None, "day_start": str(start), "paused": None}
    return {"version": 1, "started": iso(now), "config": fingerprint(), "books": books, "carry_hist": {}, "acted": {},
            "lp_day": None, "kills": []}


def load_state(now: float) -> dict:
    try:
        return json.loads((STATE / "paper.json").read_text())
    except (OSError, ValueError):
        return new_state(now)


def save_state(st: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "paper.json").write_text(json.dumps(st, indent=1, default=str))


def log_decisions(rows: list[dict], now: float, st: dict) -> int:
    """Appends to the day's decision log: every ACCEPT, CLOSE and FLATTEN in full, and a REJECT or NO_SIGNAL whenever
    its reason changes for that strategy, book, market and side (an unchanged hourly repeat is counted, not re-written).
    Returns the number of rows written."""
    p = STATE / "decisions" / f"{iso(now)[:10]}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    last = st.setdefault("last_why", {})
    n = 0
    with p.open("a") as f:
        for r in rows:
            if r["decision"] in ("REJECT", "NO_SIGNAL"):
                k = f"{r['book']}|{r['strategy']}|{r.get('market')}|{r.get('side')}"
                if last.get(k) == r["why"]:
                    st.setdefault("repeats", {})[k] = st.get("repeats", {}).get(k, 0) + 1
                    continue
                last[k] = r["why"]
            f.write(json.dumps(r, default=str) + "\n")
            n += 1
    return n


# ------------------------------------------------------------------ positions
def _trade(pos: dict) -> Trade:
    t = Trade(pos["strategy"], pos["book"], pos["market"], pos["side"], d(pos["n"]), pos["opened"])
    t.costs = {k: d(v) for k, v in pos.get("costs", {}).items()}
    t.est_costs = pos.get("est", {})
    return t


def _mark(pos: dict, snap: dict) -> Decimal:
    """Unrealised gross result of an open position at live prices."""
    c = snap["contexts"].get(pos["market"])
    if pos["strategy"] == "lp":
        px = pos.get("gm_now")
        return d(pos["n"]) * (d(px) / d(pos["entry"]) - 1) if px else ZERO
    if not c or c["oracle"] is None:
        return ZERO
    g = d(pos["side"]) * d(pos["n"]) * (c["oracle"] / d(pos["entry"]) - 1)
    if pos.get("hedge_entry") and c.get("hl") and c["hl"]["mid"]:
        g -= d(pos["side"]) * d(pos["n"]) * (c["hl"]["mid"] / d(pos["hedge_entry"]) - 1)
    return g


def equity(acc: dict, snap: dict) -> Decimal:
    return d(acc["cash"]) + sum((d(p["n"]) * (1 if p["strategy"] == "lp" else 0) + _mark(p, snap)
                                 - sum((d(v) for v in p.get("costs", {}).values()), ZERO) for p in acc["open"]), ZERO)


def close(acc: dict, pos: dict, snap: dict, now: float, why: str) -> dict:
    tr = _trade(pos)
    tr.gross = _mark(pos, snap)
    c = snap["contexts"].get(pos["market"])
    if pos["strategy"] == "lp":
        fee = (d(pos["n"]) + tr.gross) * d(pos.get("swap_fee", "0.0007"))
        tr.add_cost("withdraw_fee", fee)
        tr.add_cost("execution_fee", d(snap["exec_fee_usd"] or 0))
        tr.add_cost("gas", d(load()["costs"]["arbitrum_tx_usd"]))
    elif c:
        items = CO.gmx_round_trip({**c, "funding_ps_long": ZERO, "funding_ps_short": ZERO, "borrow_ps_long": ZERO,
                                    "borrow_ps_short": ZERO}, pos["side"], d(pos["n"]), 0)
        for k in ("close_fee", "execution_fee", "gas"):
            tr.add_cost(k, items[k] / (2 if k in ("execution_fee", "gas") else 1))
        if pos.get("hedge_entry"):
            tr.add_cost("hedge_fee", d(pos["n"]) * d(load()["costs"]["hyperliquid"]["taker"]))
    tr.closed, tr.reason = iso(now), why
    acc["cash"] = str(d(acc["cash"]) + (d(pos["n"]) if pos["strategy"] == "lp" else ZERO) + tr.net)
    acc["open"].remove(pos)
    acc["closed"].append(tr.to_json())
    return tr.to_json()


def open_pos(acc: dict, book: str, strat: str, market: str, side: int, n: Decimal, entry, now: float, est: dict,
             first_costs: dict, extra: dict | None = None) -> dict:
    pos = {"strategy": strat, "book": book, "market": market, "side": side, "n": str(n), "opened": iso(now), "t_open": now,
           "entry": str(entry), "est": {k: str(v) for k, v in est.items()}, "costs": {k: str(v) for k, v in first_costs.items()},
           "accrued_at": now, **(extra or {})}
    acc["open"].append(pos)
    if strat == "lp":                                   # the pool tokens leave cash; their costs stay on the position's cost
        acc["cash"] = str(d(acc["cash"]) - n)           # stack, so equity and the closing net count them exactly once
    return pos


def accrue(pos: dict, snap: dict, now: float) -> None:
    """Holding costs since the last accrual at the current published rates (funding, borrowing, hedge funding)."""
    c = snap["contexts"].get(pos["market"])
    if pos["strategy"] == "lp" or not c:
        return
    dt = Decimal(str(max(0.0, now - pos["accrued_at"])))
    k = "long" if pos["side"] > 0 else "short"
    add = {"funding": c.get(f"funding_ps_{k}"), "borrow": c.get(f"borrow_ps_{k}")}
    for name, rate in add.items():
        if rate is not None:
            pos["costs"][name] = str(d(pos["costs"].get(name, 0)) + d(pos["n"]) * rate * dt)
    if pos.get("hedge_entry") and c.get("hl") and c["hl"]["funding_hourly"] is not None:
        pos["costs"]["hedge_funding"] = str(d(pos["costs"].get("hedge_funding", 0))
                                            - c["hl"]["funding_hourly"] * d(pos["side"]) * d(pos["n"]) * dt / H)
    pos["accrued_at"] = now


# ------------------------------------------------------------------ the independent risk gate
def risk_block(acc: dict, snap: dict, now: float, margin_new: Decimal, sleeve_start: Decimal) -> str | None:
    """Reasons a NEW position may not open. Strategies can't change these limits."""
    R = load()["risk"]
    if snap["kill"]:
        return "kill switch: " + "; ".join(snap["kill"])
    if acc.get("paused"):
        return f"paused: {acc['paused']}"
    eq = equity(acc, snap)
    if acc["day_start"] and eq < d(acc["day_start"]) * (1 - d(R["max_daily_loss"])):
        return "daily loss limit (2%) reached: no new positions until tomorrow"
    used = sum((d(p["n"]) / d(R["max_leverage"]) for p in acc["open"] if p["strategy"] != "lp"), ZERO)
    if used + margin_new > sleeve_start * d(R["max_margin_share"]):
        return "margin limit (20% of the sleeve) reached"
    return None


def capacity_ok(n, liquidity) -> bool:
    """AT-04: a position may be at most 1% of the market's available liquidity (unknown liquidity: not allowed)."""
    return liquidity is not None and d(n) <= d(liquidity) * d(load()["risk"]["max_pool_share"])


def group_ok(acc: dict, side: int) -> bool:
    """AT-09: the coins traded move together, so a directional strategy holds at most one position per side."""
    return not any(p["side"] == side for p in acc["open"])


def liquidation_ok(lev: Decimal) -> bool:
    """With 2x leverage the price must move about 50% to liquidate: above the 25% minimum distance."""
    return (1 / lev) - Decimal("0.01") >= d(load()["risk"]["min_liquidation_distance"])


# ------------------------------------------------------------------ strategies (live)
def step(st: dict, snap: dict, now: float, lp_inputs: dict | None, bars: dict) -> list[dict]:
    """One hourly pass over every strategy and book. Returns the decision log rows."""
    R = load()["risk"]
    log = []
    day = iso(now)[:10]
    for book, accs in st["books"].items():
        for strat, acc in accs.items():
            if acc["day"] != day:                       # new day: reset the daily-loss reference
                acc["day"], acc["day_start"] = day, str(equity(acc, snap))
            for pos in list(acc["open"]):
                accrue(pos, snap, now)
            eq = equity(acc, snap)
            acc["peak"] = str(max(d(acc["peak"]), eq))
            if eq < d(acc["peak"]) * (1 - d(R["max_drawdown"])) and not acc["paused"]:
                acc["paused"] = f"drawdown beyond 10% on {day}"
                for pos in list(acc["open"]):
                    log.append({"t": iso(now), "book": book, "strategy": strat, "decision": "FLATTEN", "why": acc["paused"],
                                "trade": close(acc, pos, snap, now, "drawdown limit: flattened")})
    # kill switches: flatten everything on a depeg; flatten a market whose oracle is stale
    for book, accs in st["books"].items():
        for strat, acc in accs.items():
            for pos in list(acc["open"]):
                q = snap["quality"].get(pos["market"], {})
                if snap["kill"] or (pos["strategy"] != "lp" and not q.get("tradeable", True)):
                    why = "; ".join(snap["kill"] or q.get("reasons", []))
                    log.append({"t": iso(now), "book": book, "strategy": strat, "decision": "FLATTEN", "why": why,
                                "trade": close(acc, pos, snap, now, "kill switch: " + why)})
    if snap["kill"]:
        st["kills"].append({"t": iso(now), "why": snap["kill"]})
    log += _carry(st, snap, now)
    log += _spread(st, snap, now, bars)
    log += _directional(st, snap, now, bars)
    if lp_inputs is not None:
        log += _lp(st, snap, now, lp_inputs)
    for accs in st["books"].values():
        for acc in accs.values():
            acc["equity"] = (acc["equity"] + [[iso(now), str(equity(acc, snap))]])[-24 * 120:]
    return log


def _row(now, book, strat, market, decision, why, **kw) -> dict:
    return {"t": iso(now), "book": book, "strategy": strat, "market": market, "decision": decision, "why": why, **kw}


def _carry(st, snap, now) -> list[dict]:
    P, log = load()["carry"], []
    for coin, c in snap["contexts"].items():
        q = snap["quality"][coin]
        if not q["hedgeable"]:
            continue
        gmx = {"fund_long": c["funding_ps_long"], "fund_short": c["funding_ps_short"], "borrow_long": c["borrow_ps_long"],
               "borrow_short": c["borrow_ps_short"]}
        h = st["carry_hist"].setdefault(coin, [])
        h.append([now, {s: str(hourly_carry(gmx, c["hl"]["funding_hourly"], s)) for s in (1, -1)}])
        st["carry_hist"][coin] = h = [x for x in h if now - x[0] <= 4 * 3600][-6:]
        for book, accs in st["books"].items():
            acc = accs["carry"]
            mine = [p for p in acc["open"] if p["market"] == coin]
            for pos in mine:
                cs = hourly_carry(gmx, c["hl"]["funding_hourly"], pos["side"])
                ann = cs * 8760
                pos["neg"] = pos.get("neg", 0) + 1 if cs < 0 else 0
                if ann < d(P["exit_annual"]) or pos["neg"] >= P["flip_hours"]:
                    log.append(_row(now, book, "carry", coin, "CLOSE", f"carry {float(ann):.1%} a year",
                                    trade=close(acc, pos, snap, now, "carry fell below 3% a year" if ann >= 0 else "carry negative")))
            if mine:
                continue
            start = d(acc["start"])
            n = start * d(load()["risk"]["max_margin_share"]) * d(load()["risk"]["max_leverage"]) / len(snap["contexts"])
            for s in (1, -1):
                recent = [d(x[1][str(s)]) for x in h if x[1].get(str(s)) not in (None, "None")]
                cs = hourly_carry(gmx, c["hl"]["funding_hourly"], s)
                try:
                    g = CO.gmx_round_trip({**c, "funding_ps_long": ZERO, "funding_ps_short": ZERO, "borrow_ps_long": ZERO,
                                           "borrow_ps_short": ZERO}, s, n, 0)
                    hh = CO.hl_round_trip({"funding_hourly": ZERO, "book": c["hl"]["book"], "mid": c["hl"]["mid"]}, -s, n, 0)
                except CO.NotTradeable as e:
                    log.append(_row(now, book, "carry", coin, "REJECT", f"not tradeable: {e}", side=s))
                    continue
                items = {k: v for k, v in {**g, **hh}.items() if k not in ("funding", "borrow", "hedge_funding")}
                rt = sum(items.values(), ZERO)
                net_ann = cs * 8760 - rt / n / P["hold_hours"] * 8760 if n > 0 else None
                est = {"carry_annual": float(cs * 8760), "net_after_costs_annual": float(net_ann) if net_ann is not None else None,
                       "round_trip_usd": str(rt)}
                if len(recent) < P["confirm_hours"] or any(x <= 0 for x in recent[-P["confirm_hours"]:]):
                    log.append(_row(now, book, "carry", coin, "NO_SIGNAL", "carry not positive for 3 hours yet", side=s, **est))
                    continue
                if net_ann is None or net_ann <= d(P["enter_annual"]):
                    log.append(_row(now, book, "carry", coin, "REJECT", "carry after costs below 15% a year", side=s, **est))
                    continue
                blk = risk_block(acc, snap, now, 2 * n / d(load()["risk"]["max_leverage"]), start)
                if blk:
                    log.append(_row(now, book, "carry", coin, "REJECT", blk, side=s, **est))
                    continue
                open_pos(acc, book, "carry", coin, s, n, c["oracle"], now, items,
                         {k: v for k, v in items.items() if k in ("open_fee",)} | {"execution_fee": items["execution_fee"] / 2,
                                                                                 "gas": items["gas"] / 2,
                                                                                 "hedge_fee": items["hedge_fee"] / 2,
                                                                                 "hedge_slippage": items["hedge_slippage"],
                                                                                 "price_impact": items["price_impact"]},
                         {"hedge_entry": str(c["hl"]["mid"])})
                log.append(_row(now, book, "carry", coin, "ACCEPT", "carry after costs above 15% a year", side=s, notional=str(n), **est))
                break
    return log


def _spread(st, snap, now, bars) -> list[dict]:
    import statistics
    P, log = load()["spread"], []
    for coin, c in snap["contexts"].items():
        if not snap["quality"][coin]["hedgeable"]:
            continue
        g, hl = bars.get(("gmx", coin), {}), bars.get(("hl", coin), {})
        past = [g[t][3] / hl[t] - 1 for t in sorted(set(g) & set(hl))[-P["window_days"] * 24:]]
        if len(past) < P["window_days"] * 24 * 0.9:
            continue
        mu, sd = statistics.fmean(past), statistics.pstdev(past)
        s = float(c["oracle"] / c["hl"]["mid"] - 1)
        z = (s - mu) / sd if sd > 0 else 0.0
        for book, accs in st["books"].items():
            acc = accs["spread"]
            start = d(acc["start"])
            n = start * d(load()["risk"]["max_margin_share"]) * d(load()["risk"]["max_leverage"]) / len(snap["contexts"])
            for pos in [p for p in acc["open"] if p["market"] == coin]:
                against = z > P["stop_z"] if pos["side"] < 0 else z < -P["stop_z"]
                if abs(z) < P["exit_z"] or now - pos["t_open"] >= P["max_hours"] * 3600 or against:
                    why = "stop" if against else "gap closed" if abs(z) < P["exit_z"] else "24 hours"
                    log.append(_row(now, book, "spread", coin, "CLOSE", why, z=z, trade=close(acc, pos, snap, now, why)))
            if any(p["market"] == coin for p in acc["open"]) or abs(z) <= P["enter_z"]:
                continue
            side = -1 if s > mu else 1
            try:
                gi = CO.gmx_round_trip({**c, "funding_ps_long": ZERO, "funding_ps_short": ZERO, "borrow_ps_long": ZERO,
                                        "borrow_ps_short": ZERO}, side, n, 0)
                hi = CO.hl_round_trip({"funding_hourly": ZERO, "book": c["hl"]["book"], "mid": c["hl"]["mid"]}, -side, n, 0)
            except CO.NotTradeable as e:
                log.append(_row(now, book, "spread", coin, "REJECT", f"not tradeable: {e}", z=z))
                continue
            items = {k: v for k, v in {**gi, **hi}.items() if k not in ("funding", "borrow", "hedge_funding")}
            share = float(sum(items.values(), ZERO) / n) if n else 1.0
            if abs(s) <= P["cost_multiple"] * share:
                log.append(_row(now, book, "spread", coin, "REJECT", "gap smaller than 2x the round-trip costs", z=z, spread=s, costs=share))
                continue
            blk = risk_block(acc, snap, now, 2 * n / d(load()["risk"]["max_leverage"]), start)
            if blk:
                log.append(_row(now, book, "spread", coin, "REJECT", blk, z=z))
                continue
            open_pos(acc, book, "spread", coin, side, n, c["oracle"], now, items,
                     {"open_fee": items["open_fee"], "price_impact": items["price_impact"], "execution_fee": items["execution_fee"] / 2,
                      "gas": items["gas"] / 2, "hedge_fee": items["hedge_fee"] / 2, "hedge_slippage": items["hedge_slippage"]},
                     {"hedge_entry": str(c["hl"]["mid"])})
            log.append(_row(now, book, "spread", coin, "ACCEPT", f"gap z {z:.1f}", z=z, spread=s, costs=share, notional=str(n)))
    return log


def _directional(st, snap, now, bars) -> list[dict]:
    P, R, log = load()["directional"], load()["risk"], []
    for coin, c in snap["contexts"].items():
        if not snap["quality"][coin]["tradeable"]:
            continue
        for label, key in (("trend", ("bn1h", coin)), ("reversion", ("bn15m", coin))):
            b = bars.get(key) or []
            if len(b) < P["atr_floor_window"] + 2 and label == "trend":
                continue
            if label == "trend":
                sig = DI.signals_trend(b, P)
            else:
                sig = DI.signals_reversion(b, bars.get(("bn1h", coin)) or [], P)
            last = sig[-1] if sig else None
            fresh = last is not None and last[0] == len(b) - 1
            a = DI.atr([x[2] for x in b], [x[3] for x in b], [x[4] for x in b], P["atr"])[-1] if b else None
            for book, accs in st["books"].items():
                acc = accs[label]
                for pos in [p for p in acc["open"] if p["market"] == coin]:
                    since = [x for x in b if x[0] / 1000 > pos.get("checked", pos["t_open"])]
                    stop, tp = d(pos["stop"]), d(pos["target"])
                    hit = None
                    for x in since:
                        lo, hi_, cl = d(x[3]), d(x[2]), d(x[4])
                        if (pos["side"] > 0 and lo <= stop) or (pos["side"] < 0 and hi_ >= stop):
                            hit = ("stop", stop)
                            break
                        if (pos["side"] > 0 and hi_ >= tp) or (pos["side"] < 0 and lo <= tp):
                            hit = ("take profit", tp)
                            break
                        best = max(d(pos["best"]), cl) if pos["side"] > 0 else min(d(pos["best"]), cl)
                        pos["best"] = str(best)
                        trail = best - pos["side"] * d(P["trail_atr"]) * d(pos["atr"])
                        if (pos["side"] > 0 and best > d(pos["entry"]) and trail > stop) or (pos["side"] < 0 and best < d(pos["entry"]) and trail < stop):
                            stop = trail
                            pos["stop"] = str(stop)
                    if since:
                        pos["checked"] = since[-1][0] / 1000
                    if hit:
                        c2 = {**snap, "contexts": {**snap["contexts"], coin: {**c, "oracle": hit[1]}}}
                        log.append(_row(now, book, label, coin, "CLOSE", hit[0], trade=close(acc, pos, c2, now, hit[0])))
                if not fresh or any(p["market"] == coin for p in acc["open"]) or a is None:
                    continue
                if st["acted"].get(f"{book}:{label}:{coin}") == b[-1][0]:
                    continue
                st["acted"][f"{book}:{label}:{coin}"] = b[-1][0]
                side = last[1]
                tot = (c["oi_long"] or 0) + (c["oi_short"] or 0)
                crowd = (c["oi_long"] if side > 0 else c["oi_short"]) / tot if tot else None
                if crowd is not None and crowd > d(P["oi_crowded"]):
                    log.append(_row(now, book, label, coin, "REJECT", f"contrarian filter: this side holds {float(crowd):.0%} of open interest", side=side))
                    continue
                start = d(acc["start"])
                try:
                    probe = CO.estimate_costs("gmx", c, side, Decimal(1000), 24)
                except CO.NotTradeable as e:
                    log.append(_row(now, book, label, coin, "REJECT", f"not tradeable: {e}", side=side))
                    continue
                fixed = probe["items"]["execution_fee"] + probe["items"]["gas"]
                stop_dist = d(P["stop_atr"]) * d(a)
                loss_share = stop_dist / c["oracle"] + (probe["base"] - fixed) / 1000
                budget = start * d(R["max_loss_per_position"])
                n = min((budget - fixed) / loss_share if budget > fixed else ZERO, start * d(R["max_margin_share"]) * d(R["max_leverage"]))
                if n < 10:
                    log.append(_row(now, book, label, coin, "REJECT", "costs alone exceed the 0.5% risk budget at this account size",
                                    side=side, budget=str(budget), fixed_costs=str(fixed)))
                    continue
                if not capacity_ok(n, c["liquidity_long" if side > 0 else "liquidity_short"]):
                    log.append(_row(now, book, label, coin, "REJECT", "more than 1% of the market's available liquidity (or unknown)", side=side))
                    continue
                if not group_ok(acc, side):
                    log.append(_row(now, book, label, coin, "REJECT", "already one position this side in the correlated group (AT-09)", side=side))
                    continue
                blk = risk_block(acc, snap, now, n / d(R["max_leverage"]), start)
                if blk or not liquidation_ok(d(R["max_leverage"])):
                    log.append(_row(now, book, label, coin, "REJECT", blk or "liquidation too close", side=side))
                    continue
                items = CO.gmx_round_trip(c, side, n, 24)
                entry = c["oracle"]
                open_pos(acc, book, label, coin, side, n, entry, now, items,
                         {"open_fee": items["open_fee"], "price_impact": items["price_impact"], "execution_fee": items["execution_fee"] / 2,
                          "gas": items["gas"] / 2},
                         {"stop": str(entry - side * stop_dist), "target": str(entry + side * d(P["take_atr"]) * d(a)),
                          "best": str(entry), "atr": str(a), "checked": now})
                log.append(_row(now, book, label, coin, "ACCEPT", "signal", side=side, notional=str(n), costs_base=str(probe["base"])))
    return log


def _lp(st, snap, now, inp) -> list[dict]:
    """Once a day, after the day's pool data is published: rank, exit, enter (same rules as the backtest)."""
    P, log = load()["lp"], []
    pools, days = inp["pools"], inp["days"]
    i = len(days) - 1
    gm_now = inp.get("gm_now", {})
    for accs in st["books"].values():
        acc = accs["lp"]
        for pos in acc["open"]:
            if gm_now.get(pos["market"]):
                pos["gm_now"] = str(gm_now[pos["market"]])
    if st["lp_day"] == days[-1]:
        return log
    st["lp_day"] = days[-1]
    ranked = sorted(((s, p["name"]) for p in pools if (s := LP.score(p, days, i)) is not None and not LP.blocked(p, days, i)), reverse=True)
    top, keep = [n for _, n in ranked[:P["top_n"]]], {n for _, n in ranked[:P["top_n"] * 2]}
    byname = {p["name"]: p for p in pools}
    for book, accs in st["books"].items():
        acc = accs["lp"]
        for pos in list(acc["open"]):
            why = LP.blocked(byname[pos["market"]], days, i) if pos["market"] in byname else "no longer listed"
            if why or pos["market"] not in keep:
                log.append(_row(now, book, "lp", pos["market"], "CLOSE", why or "fell out of the top 6",
                                trade=close(acc, pos, snap, now, why or "fell out of the top 6")))
        start = d(acc["start"])
        for n_ in top:
            if any(p["market"] == n_ for p in acc["open"]) or len(acc["open"]) >= P["top_n"]:
                continue
            if not gm_now.get(n_):
                log.append(_row(now, book, "lp", n_, "REJECT", "no live pool price"))
                continue
            alloc = min(start * d(P["max_pool_share_of_sleeve"]), start / P["top_n"], d(acc["cash"]))
            xf, gas = d(snap["exec_fee_usd"] or 0), d(load()["costs"]["arbitrum_tx_usd"])
            if snap["exec_fee_usd"] is None:
                log.append(_row(now, book, "lp", n_, "REJECT", "execution fee unknown"))
                continue
            if alloc <= 2 * (xf + gas):
                log.append(_row(now, book, "lp", n_, "REJECT", "allocation too small for the fixed deposit costs"))
                continue
            blk = risk_block(acc, snap, now, ZERO, start)
            if blk:
                log.append(_row(now, book, "lp", n_, "REJECT", blk))
                continue
            swap_fee = byname[n_]["swap_fee"]
            fee = (alloc - xf - gas) * swap_fee
            value = alloc - xf - gas - fee
            open_pos(acc, book, "lp", n_, 0, value, gm_now[n_], now,
                     {"deposit_fee": fee, "withdraw_fee": value * swap_fee, "execution_fee": 2 * xf},
                     {"deposit_fee": fee, "execution_fee": xf, "gas": gas}, {"swap_fee": str(swap_fee), "gm_now": str(gm_now[n_])})
            log.append(_row(now, book, "lp", n_, "ACCEPT", "top-ranked pool", notional=str(value)))
    return log


def shadow_stats(st: dict, book: str = "account") -> dict:
    """Gate 6 inputs per strategy: days running, closed trades, share of trades whose booked costs were within the
    pre-registered tolerance (25%, or 1 cent) of the estimate made at entry."""
    out = {}
    days = (datetime.now(timezone.utc) - datetime.fromisoformat(st["started"])).days
    for strat, acc in st["books"][book].items():
        ok = n = 0
        for t in acc["closed"]:
            est = sum((d(v) for v in t.get("est_costs", {}).values()), ZERO)
            act = sum((d(v) for v in t.get("costs", {}).values()), ZERO)
            if t.get("est_costs"):
                n += 1
                ok += abs(act - est) <= max(abs(est) * Decimal("0.25"), Decimal("0.01"))
        out[strat] = {"days": days, "trades": len(acc["closed"]), "cost_ok": ok / n if n else None}
    return out


def lp_live_inputs(snap: dict) -> dict:
    """Daily pool data for the live LP step (history through the latest published day) and live pool prices."""
    from perp.sources import gmx_market_settings
    pools, gm_now = [], {}
    for m in snap["markets"]:
        if not m.get("listed"):
            continue
        gm = HI.gm_prices(m["address"])
        if len(gm) < load()["lp"]["min_history_days"]:
            continue
        pools.append({"name": m["name"], "address": m["address"], "gm": gm, "apr": HI.pool_aprs(m["address"]),
                      "swap_fee": None})
    days = sorted({k for p in pools for k in p["gm"]})
    i = len(days) - 1
    ranked = sorted(((s, p["name"]) for p in pools if (s := LP.score(p, days, i)) is not None), reverse=True)[:12]
    names = {n for _, n in ranked}
    for p in pools:
        if p["name"] in names:
            raw = gmx_market_settings(p["address"]) or {}
            st_ = CO.gmx_settings(raw) if raw else {}
            p["swap_fee"] = st_.get("swap_fee_neg") or Decimal("0.0007")
            if raw.get("poolValue") and raw.get("marketTokenSupply"):
                gm_now[p["name"]] = (Decimal(raw["poolValue"]) / Decimal(10) ** 30) / (Decimal(raw["marketTokenSupply"]) / Decimal(10) ** 18)
        else:
            p["swap_fee"] = Decimal("0.0007")
    return {"pools": pools, "days": days, "gm_now": gm_now}
