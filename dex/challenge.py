"""The $100 DEX challenge (paper only, DECISIONS D95).

One running paper account that starts with $100 and trades only the DEX page's own hourly ideas, the way a person
without bots would: they see the list when it is published, buy within that hour, check once an hour and sell by hand.
Goal, in this order: don't lose the money, then grow it. Staying in cash is always allowed and is the default.

What a real person would pay, every time (dex/costs.py at the trade's real size, so gas weighs more on small trades):
pool fee, price impact, sandwich bots (MEV), front-runners / snipers who buy a published list first, token taxes and
network fees, both ways. Fills are never better than the market was:
* buy: at the close of the hour in which the list was published (about 0-60 minutes after it went out), plus the
  front-running cost on top;
* sell: DEXes have no stop orders, so exits are checked once an hour, at each hourly close: at or above the take
  profit, at or below the safety exit, or when the time limit is up, the coin is sold at that close. A crash through
  the safety exit is therefore sold wherever the price was, and a drained pool gives back almost nothing (the sale is
  priced against the pool's money at the time of the sale).

Owner's rules (D95): grade Moderate or better and a positive expected result after every cost at the real size;
grades must not be switched off by the grade check, and the speed must not be on probation; at most 25% of the account
in one trade and at most 2% of it lost if the safety exit is hit; at most 2 trades open and 1 new trade an hour; no
new trades after a 5% loss in a day (UTC); everything pauses 20% below the peak until the owner restarts it; the
standard list only. The $100 keeps running from day to day.
"""
from __future__ import annotations

import json
import math

import pandas as pd

from core.config import state_path
from core.log import get_logger
from dex.costs import dex_cost, mev_share
from scanner.model import SL
from scanner.styles import DEX_STYLES

log = get_logger("dex.challenge")
START = 100.0
MAX_POS_FRAC = 0.25          # of the account in one trade
RISK_FRAC = 0.02             # of the account lost if the safety exit is hit (sizing)
MAX_OPEN = 2
NEW_PER_RUN = 1
DAY_LOSS = 0.05              # no new trades for the rest of the UTC day
MAX_DRAWDOWN = 0.20          # pause everything until the owner restarts it
MIN_TRADE = 5.0              # smaller trades are all fees
GRADES_OK = ("Strong", "Moderate")
PENDING_MAX_H = 4            # a buy whose hour never shows up in the price data is cancelled
LOG_KEEP = 1500
DIR = "dex/challenge"


def _path(name: str):
    p = state_path(DIR, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_account() -> dict:
    p = _path("account.json")
    if p.exists():
        return json.loads(p.read_text())
    return {"start": START, "cash": START, "peak": START, "equity": START, "started_at": None, "day": None,
            "day_start": START, "paused": False, "pause_reason": "", "positions": [], "trades": 0}


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def pct(x: float, d: int = 1) -> str:
    return f"{x * 100:+.{d}f}%"


def share(x: float, d: int = 1) -> str:
    """A size or a cost, without a sign."""
    return f"{x * 100:.{d}f}%"


def sell_value(coins: float, px: float, liq: float, pool_fee: float, sell_tax: float, gas: float, mev: float) -> dict:
    """What selling the coins now pays out, priced against the pool's money NOW (the sell half of dex_cost)."""
    q = max(liq / 2, 1.0)
    gross = coins * px
    y = gross * (1 - sell_tax)
    z = y * (1 - pool_fee)
    simp = z / (q + z)
    ms = mev * mev_share(z, pool_fee, q)
    recv = max(z * (1 - simp) * (1 - ms) - gas, 0.0)
    return {"received": recv, "gross": gross, "costs": {"tax": gross * sell_tax, "fee": y * pool_fee, "impact": z * simp,
                                                         "mev": z * (1 - simp) * ms, "gas": min(gas, z)}}


def size_trade(equity: float, cash: float, stop_dist: float, cost_at) -> tuple[float, float]:
    """(amount, round-trip cost) so that hitting the safety exit loses at most RISK_FRAC of the account, capped at
    MAX_POS_FRAC and the cash. The cost depends on the amount (gas is fixed), so it is solved in a few steps."""
    amount = min(MAX_POS_FRAC * equity, cash)
    cost = cost_at(amount) if amount > 0 else 1.0
    for _ in range(6):
        amount = min(RISK_FRAC * equity / max(stop_dist + cost, 1e-9), MAX_POS_FRAC * equity, cash)
        cost = cost_at(amount) if amount > 0 else 1.0
    return amount, cost


def _liq_now(pos: dict, cands_by_token: dict, pools: dict) -> float:
    c = cands_by_token.get((pos["chain"], pos["token"]))
    if c and c.get("liq_real"):
        return float(c["liq_real"])
    p = pools.get(pos["pool"])
    if p and p.get("reserve_usd") and pos.get("reserve0"):
        return float(pos["liq0"]) * float(p["reserve_usd"]) / float(pos["reserve0"])
    return float(pos.get("liq_last") or pos["liq0"])


def run(ideas: dict, cands: list[dict], candles: dict, chains: dict, adaptive: dict, now: pd.Timestamp | None = None,
        fetch_candles=None, fetch_pools=None, quality_check: dict | None = None) -> dict:
    """One hourly step: fill pending buys, manage open trades, value the account, maybe place one new buy."""
    now = now or pd.Timestamp.now(tz="UTC")
    acc = load_account()
    acc["started_at"] = acc.get("started_at") or str(now)
    steps: list[dict] = []

    def say(kind: str, text: str, **extra) -> None:
        steps.append({"t": str(now), "kind": kind, "text": text, **extra})

    by_token = {(c["chain"], c["token"]): c for c in cands}
    pools: dict = {}
    if fetch_pools:
        need: dict[str, list[str]] = {}
        for p in acc["positions"]:
            if (p["chain"], p["token"]) not in by_token:
                need.setdefault(p["chain"], []).append(p["pool"])
        for chain, ps in need.items():
            try:
                pools.update(fetch_pools(chain, ps))
            except Exception as e:  # noqa: BLE001
                log.warning("challenge pools %s: %s", chain, e)
    closed_rows: list[dict] = []
    keep: list[dict] = []
    for pos in acc["positions"]:
        ch = chains.get(pos["chain"]) or {}
        gas, mev, sniper = float(ch.get("gas_usd", 1.0)), float(ch.get("mev", 0.0)), float(ch.get("sniper", 0.0))
        df = candles.get(f"{pos['chain']}:{pos['pool']}")
        if (df is None or not len(df)) and fetch_candles:
            try:
                df = fetch_candles(pos["chain"], pos["pool"], 72)
            except Exception as e:  # noqa: BLE001
                log.warning("challenge candles %s: %s", pos["coin"], e)
        liq = _liq_now(pos, by_token, pools)
        pos["liq_last"] = liq
        if pos["status"] == "pending":
            bar = pd.Timestamp(pos["fill_bar"])
            if df is not None and len(df) and bar in df.index:
                px = float(df.loc[bar, "close"])
                c = dex_cost(pos["amount"], px, liq, pos["fee"], pos["buy_tax"], pos["sell_tax"], gas, mev, sniper)
                buy_costs = {k: v for k, v in c["at"](px)["costs"].items() if k in ("sniper",)}
                pos.update({"status": "open", "fill_t": str(bar + pd.Timedelta(hours=1)), "entry": px, "coins": c["coins"],
                            "last": px, "last_bar": str(bar), "rt_cost": c["round_trip"], "buy_costs": buy_costs,
                            "tp": px * (1 + pos["tp_pct"]), "stop": px * (1 + pos["sl_pct"])})
                say("buy", f"Bought {pos['coin']} ({ch.get('name', pos['chain'])}) for {money(pos['amount'])} at {px:.6g}, "
                    f"the price at the end of the hour the list came out. After every cost (fees, price impact, sandwich "
                    f"bots, front-runners, taxes, network fees both ways) the round trip costs about {share(c['round_trip'])}, "
                    f"so it must rise about {pct(c['break_even'] / px - 1)} just to break even. Take profit at "
                    f"{pos['tp']:.6g} ({pct(pos['tp_pct'])}), safety exit at {pos['stop']:.6g} ({pct(pos['sl_pct'])}), "
                    f"time limit {pos['hold_bars']} h.", coin=pos["coin"])
            elif now - pd.Timestamp(pos["signal_t"]) > pd.Timedelta(hours=PENDING_MAX_H):
                acc["cash"] += pos["amount"]
                say("skip", f"Cancelled the buy of {pos['coin']}: no price for the hour it was meant to fill. The "
                    f"{money(pos['amount'])} went back to cash.", coin=pos["coin"])
                continue
            if pos["status"] == "pending":
                keep.append(pos)
                continue
        # open: check each closed hour after the fill (and after the last check), like a person looking once an hour
        exit_bar, why = None, None
        if df is not None and len(df):
            later = df[df.index > pd.Timestamp(pos["last_bar"])]
            fill = pd.Timestamp(pos["fill_t"]) - pd.Timedelta(hours=1)
            for t, b in later.iterrows():
                held = int(round((t - fill) / pd.Timedelta(hours=1)))
                pos["last"], pos["last_bar"] = float(b["close"]), str(t)
                if b["close"] >= pos["tp"]:
                    exit_bar, why = t, "take profit reached"
                elif b["close"] <= pos["stop"]:
                    exit_bar, why = t, ("crashed through the safety exit" if b["close"] < pos["stop"] * 0.95
                                        else "safety exit reached")
                elif held >= pos["hold_bars"]:
                    exit_bar, why = t, "time limit reached"
                if exit_bar is not None:
                    break
        if exit_bar is None and liq < 0.1 * float(pos["liq0"]):
            exit_bar, why = pd.Timestamp(pos["last_bar"]), "the pool's money was pulled (rug-pull)"
        if exit_bar is not None:
            s = sell_value(pos["coins"], pos["last"], liq, pos["fee"], pos["sell_tax"], gas, mev)
            net = s["received"] - pos["amount"]
            acc["cash"] += s["received"]
            acc["trades"] = int(acc.get("trades", 0)) + 1
            costs = {k: round(v, 4) for k, v in s["costs"].items()}
            costs["sniper_on_buy"] = round(float(pos.get("buy_costs", {}).get("sniper", 0.0)), 4)
            closed_rows.append({"opened": pos["fill_t"], "closed": str(exit_bar + pd.Timedelta(hours=1)), "coin": pos["coin"],
                                "chain": pos["chain"], "pool": pos["pool"], "style": pos["style"], "grade": pos["grade"],
                                "score": pos["score"], "amount": round(pos["amount"], 4), "entry": pos["entry"],
                                "exit": pos["last"], "received": round(s["received"], 4), "net": round(net, 4),
                                "ret": round(net / pos["amount"], 5), "why": why, "rt_cost_est": round(pos["rt_cost"], 5),
                                **{f"sell_{k}": v for k, v in costs.items()}})
            say("sell", f"Sold {pos['coin']} at {pos['last']:.6g} ({why}). Got back {money(s['received'])} for the "
                f"{money(pos['amount'])} put in: {money(net)} ({pct(net / pos['amount'])}) after every cost. Selling cost "
                f"{money(sum(s['costs'].values()))} (pool fee {money(s['costs']['fee'])}, price impact "
                f"{money(s['costs']['impact'])}, sandwich bots {money(s['costs']['mev'])}, tax {money(s['costs']['tax'])}, "
                f"network fee {money(s['costs']['gas'])}).", coin=pos["coin"], net=round(net, 4))
            continue
        pos["value"] = sell_value(pos["coins"], pos["last"], liq, pos["fee"], pos["sell_tax"], gas, mev)["received"]
        keep.append(pos)
    acc["positions"] = keep

    # value the account at what it would get if it sold everything now
    invested = sum(p.get("value", 0.0) if p["status"] == "open" else p["amount"] for p in keep)
    equity = acc["cash"] + invested
    acc["equity"] = equity
    day = str(now.date())
    if acc.get("day") != day:
        acc["day"], acc["day_start"] = day, equity
    acc["peak"] = max(float(acc.get("peak", START)), equity)
    if not acc.get("paused") and equity < (1 - MAX_DRAWDOWN) * acc["peak"]:
        acc["paused"], acc["pause_reason"] = True, (f"The account fell {pct(equity / acc['peak'] - 1)} below its peak of "
                                                    f"{money(acc['peak'])}. Trading is paused until the owner restarts it.")
        say("pause", acc["pause_reason"])

    # new buys
    blockers = []
    if acc.get("paused"):
        blockers.append("trading is paused (" + acc.get("pause_reason", "") + ")")
    if equity < (1 - DAY_LOSS) * acc["day_start"]:
        blockers.append(f"today's loss limit is hit ({pct(equity / acc['day_start'] - 1)} since the start of the day)")
    if len(keep) >= MAX_OPEN:
        blockers.append(f"{MAX_OPEN} trades are already open")
    reliable = (quality_check or {}).get("reliable")
    seen, best, why_not = 0, [], {}
    held = {(p["chain"], p["token"]) for p in keep}
    for style, lst in (ideas or {}).items():
        st = DEX_STYLES.get(style)
        prob = ((adaptive or {}).get("probation") or {}).get(style) or {}
        for d in lst:
            seen += 1
            c = by_token.get((d["chain"], d["symbol"].split(":", 1)[1]))
            s = (c or {}).get("styles", {}).get(style)
            if not c or not s or st is None:
                continue
            if d["grade"] not in GRADES_OK:
                why_not[d["coin"]] = f"graded {d['grade']}"
                continue
            if reliable is False:
                why_not[d["coin"]] = "the grade check says grades can't be trusted right now"
                continue
            if prob.get("active"):
                why_not[d["coin"]] = "its speed is on probation (recent ideas did worse than random picks)"
                continue
            if (d["chain"], c["token"]) in held:
                why_not[d["coin"]] = "already held"
                continue
            ch = chains.get(d["chain"]) or {}
            gas, mev, sniper = float(ch.get("gas_usd", 1.0)), float(ch.get("mev", 0.0)), float(ch.get("sniper", 0.0))
            bt, stx = (c["facts"].get("buy_tax") or 0.0), (c["facts"].get("sell_tax") or 0.0)

            def cost_at(a, c=c, gas=gas, mev=mev, sniper=sniper, bt=bt, stx=stx):
                return dex_cost(a, c["close"], c["liq_real"], c["pool_fee"], bt, stx, gas, mev, sniper)["round_trip"]

            stop_dist = -float(d["safety_exit_pct"])
            amount, cost = size_trade(equity, acc["cash"], stop_dist, cost_at)
            # the page's expected result, with its $100 cost swapped for the cost at this trade's real size
            r_real = float(d["expected_r"]) + (float(d["cost_rt"]) - cost) / (SL * float(d["risk_unit"]))
            if amount < MIN_TRADE:
                why_not[d["coin"]] = f"the safe size would be only {money(amount)}, too small to cover the fees"
                continue
            if not (r_real > 0) or not math.isfinite(r_real):
                why_not[d["coin"]] = f"after every cost at {money(amount)} the expected result is negative"
                continue
            best.append((r_real, d, c, style, amount, cost))
    best.sort(key=lambda x: -x[0])
    if blockers:
        say("check", f"Looked at {seen} ideas on the DEX list. No new trade: " + "; ".join(blockers) + ".")
    elif not best:
        top = sorted((d for lst in (ideas or {}).values() for d in lst), key=lambda d: -d["score"])[:3]
        detail = "; ".join(f"{d['coin']} (score {d['score']:.1f}): {why_not.get(d['coin'], 'not usable')}" for d in top)
        say("check", f"Looked at {seen} ideas on the DEX list. Nothing worth the risk, so I stayed in cash. "
            + (f"Best ones: {detail}." if detail else "The list was empty."))
    else:
        for r_real, d, c, style, amount, cost in best[:NEW_PER_RUN]:
            bar = now.floor("h")
            acc["cash"] -= amount
            pos = {"id": f"{d['symbol']}|{now.isoformat()}", "coin": d["coin"], "chain": d["chain"], "pool": d["pool"],
                   "token": c["token"], "style": style, "status": "pending", "signal_t": str(now), "fill_bar": str(bar),
                   "amount": amount, "tp_pct": float(d["take_profit_pct"]), "sl_pct": float(d["safety_exit_pct"]),
                   "hold_bars": int(DEX_STYLES[style].horizon_bars), "score": d["score"], "grade": d["grade"],
                   "chance": d["chance_beats_market"], "exp_r": round(r_real, 3), "liq0": float(c["liq_real"]),
                   "reserve0": float(c.get("reserve_usd") or 0.0), "fee": float(c["pool_fee"]),
                   "buy_tax": float(c["facts"].get("buy_tax") or 0.0), "sell_tax": float(c["facts"].get("sell_tax") or 0.0)}
            keep.append(pos)
            say("order", f"Decided to buy {d['coin']} ({(chains.get(d['chain']) or {}).get('name', d['chain'])}): grade "
                f"{d['grade']}, score {d['score']:.1f}, {round(d['chance_beats_market'] * 100)}% chance of beating the "
                f"market, and still positive after every cost at this size (about {share(cost)} for the round trip). "
                f"Putting in {money(amount)}: {share(amount / equity, 0)} of the account, sized so that hitting the safety exit "
                f"({pct(d['safety_exit_pct'])}) loses about {money(amount * (stop_dist_of(d) + cost))}, at most "
                f"{share(RISK_FRAC, 0)} of the account. It is bought at the end of this hour, like a person acting on the "
                f"list, and the front-running cost is added on top.", coin=d["coin"])
    acc["positions"] = keep
    acc["equity"] = acc["cash"] + sum(p.get("value", 0.0) if p["status"] == "open" else p["amount"] for p in keep)
    acc["updated"] = str(now)
    _save(acc, steps, closed_rows, now)
    return acc


def stop_dist_of(d: dict) -> float:
    return -float(d["safety_exit_pct"])


def _save(acc: dict, steps: list[dict], closed_rows: list[dict], now: pd.Timestamp) -> None:
    _path("account.json").write_text(json.dumps(acc, indent=1, default=str))
    lp = _path("log.json")
    old = json.loads(lp.read_text()) if lp.exists() else []
    lp.write_text(json.dumps((steps[::-1] + old)[:LOG_KEEP], indent=0, default=str))
    if closed_rows:
        tp = _path("trades.csv")
        new = pd.DataFrame(closed_rows)
        (pd.concat([pd.read_csv(tp), new], ignore_index=True) if tp.exists() else new).to_csv(tp, index=False)
    ep = _path("equity.csv")
    row = pd.DataFrame([{"t": str(now), "equity": round(acc["equity"], 4), "cash": round(acc["cash"], 4),
                         "open": sum(1 for p in acc["positions"] if p["status"] == "open")}])
    (pd.concat([pd.read_csv(ep), row], ignore_index=True) if ep.exists() else row).to_csv(ep, index=False)
