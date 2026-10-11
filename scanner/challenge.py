"""The $100 Small-coin challenge (paper only, DECISIONS D97).

A second running paper account, separate from the DEX one (dex/challenge.py), that trades only the Small coins
page's Day ideas on Binance, the way a person without bots would, so the owner can copy every trade by hand:
* buy: a market order at the close of the hour in which the list was published (about 0-60 minutes after it went
  out), paying the 0.1% fee, half the gap between the buy and sell prices (spread) and a slippage allowance;
* exits: right after buying, a take profit and a safety exit are placed as one OCO order on Binance (ordinary
  exchange orders, no bots). The take profit is a limit order (filled at its price once the price trades above it);
  the safety exit is a stop order (filled at its price minus spread and slippage, or at the opening price if the
  price jumped straight past it). If both are touched in the same hour, the safety exit is assumed. If neither is hit
  within 24 hours, it is sold at that hour's close.

Owner's rules (as for the DEX challenge, D95): grade Moderate or better and a positive expected result after every
cost; no trades while the grade check distrusts grades or the Day speed is on probation (Small coins' own record,
D97); at most 25% of the account in one trade, sized so the safety exit loses at most 2%; at most 2 open and 1 new
trade an hour; no new trades after a 5% loss in a UTC day; a pause 20% below the peak until the owner restarts it.
"""
from __future__ import annotations

import json
import math

import pandas as pd

from core.config import state_path
from core.log import get_logger
from dex.challenge import (
    DAY_LOSS,
    GRADES_OK,
    LOG_KEEP,
    MAX_DRAWDOWN,
    MAX_OPEN,
    MIN_TRADE,
    NEW_PER_RUN,
    PENDING_MAX_H,
    RISK_FRAC,
    START,
    money,
    pct,
    share,
    size_trade,
)
from scanner.model import SL
from scanner.styles import STYLES

log = get_logger("scanner.challenge")
DIR = "suggestions/challenge_small"
STYLE = "day"
FEE = 0.001                  # Binance spot, each way
SLIP = 0.0005                # slippage allowance on market and stop orders, each way
DEFAULT_SPREAD = 0.002       # if the order book can't be read


def _path(name: str):
    p = state_path(DIR, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_account() -> dict:
    p = _path("account.json")
    if p.exists():
        return json.loads(p.read_text())
    return {"start": START, "cash": START, "peak": START, "equity": START, "started_at": None, "day": None,
            "day_start": START, "paused": False, "pause_reason": "", "positions": [], "trades": 0, "venue": "Binance"}


def spread_of(symbol: str, get=None) -> float:
    """Gap between the best sell and buy prices, as a share of the middle price."""
    try:
        if get is None:
            from scanner.data import _binance_get as get
        b = get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        bid, ask = float(b["bidPrice"]), float(b["askPrice"])
        return (ask - bid) / ((ask + bid) / 2) if bid > 0 and ask > bid else DEFAULT_SPREAD
    except Exception as e:  # noqa: BLE001
        log.warning("spread %s: %s", symbol, e)
        return DEFAULT_SPREAD


def round_trip(spread: float) -> float:
    """Fees both ways, the spread once (half on the way in, half on the way out) and slippage both ways."""
    return 2 * FEE + spread + 2 * SLIP


def hhmm(t: pd.Timestamp) -> str:
    return t.strftime("%a %d %b %H:%M UTC")


def run(ideas: list[dict], candles: dict, probation: dict | None = None, quality_check: dict | None = None,
        now: pd.Timestamp | None = None, fetch_candles=None, get_spread=None) -> dict:
    """One hourly step: fill the pending buy, manage open trades, value the account, maybe place one new buy."""
    now = now or pd.Timestamp.now(tz="UTC")
    acc = load_account()
    acc["started_at"] = acc.get("started_at") or str(now)
    steps: list[dict] = []
    get_spread = get_spread or spread_of

    def say(kind: str, text: str, **extra) -> None:
        steps.append({"t": str(now), "kind": kind, "text": text, **extra})

    closed_rows, keep = [], []
    for pos in acc["positions"]:
        df = candles.get(pos["symbol"])
        if (df is None or not len(df)) and fetch_candles:
            try:
                df = fetch_candles(pos["symbol"], "1h", 72)
            except Exception as e:  # noqa: BLE001
                log.warning("challenge candles %s: %s", pos["symbol"], e)
        half = pos["spread"] / 2
        if pos["status"] == "pending":
            bar = pd.Timestamp(pos["fill_bar"])
            if df is not None and len(df) and bar in df.index:
                px = float(df.loc[bar, "close"])
                paid = px * (1 + half + SLIP)                       # market buy: the ask plus slippage
                qty = pos["amount"] * (1 - FEE) / paid
                pos.update({"status": "open", "fill_t": str(bar + pd.Timedelta(hours=1)), "entry": px, "paid": paid,
                            "qty": qty, "last": px, "last_bar": str(bar), "tp": px * (1 + pos["tp_pct"]),
                            "stop": px * (1 + pos["sl_pct"])})
                sell_by = bar + pd.Timedelta(hours=1 + pos["hold_bars"])
                pos["sell_by"] = str(sell_by)
                say("buy", f"Bought {pos['coin']} on Binance for {money(pos['amount'])} at about {paid:.6g} (the price at "
                    f"the end of the hour the list came out, {px:.6g}, plus half the buy-sell gap and slippage; the 0.1% "
                    f"fee comes off the coins). To copy it: right after buying, place one OCO order: take profit (limit) "
                    f"at {pos['tp']:.6g} ({pct(pos['tp_pct'])}) and safety exit (stop) at {pos['stop']:.6g} "
                    f"({pct(pos['sl_pct'])}). If neither is hit, sell by {hhmm(sell_by)}. The round trip costs about "
                    f"{share(round_trip(pos['spread']), 2)}.", coin=pos["coin"])
            elif now - pd.Timestamp(pos["signal_t"]) > pd.Timedelta(hours=PENDING_MAX_H):
                acc["cash"] += pos["amount"]
                say("skip", f"Cancelled the buy of {pos['coin']}: no price for the hour it was meant to fill. The "
                    f"{money(pos['amount'])} went back to cash.", coin=pos["coin"])
                continue
            if pos["status"] == "pending":
                keep.append(pos)
                continue
        exit_px, why, exit_bar, raw = None, None, None, None
        if df is not None and len(df):
            fill = pd.Timestamp(pos["fill_t"]) - pd.Timedelta(hours=1)
            for t, b in df[df.index > pd.Timestamp(pos["last_bar"])].iterrows():
                held = int(round((t - fill) / pd.Timedelta(hours=1)))
                pos["last"], pos["last_bar"] = float(b["close"]), str(t)
                if b["open"] <= pos["stop"]:
                    raw, why = float(b["open"]), "the price jumped straight past the safety exit"
                elif b["low"] <= pos["stop"]:
                    raw, why = pos["stop"], "safety exit (stop order) hit"
                elif b["high"] > pos["tp"]:
                    raw, why = pos["tp"], "take profit (limit order) filled"
                elif held >= pos["hold_bars"]:
                    raw, why = float(b["close"]), "24 hours up: sold at the hour's close"
                if raw is not None:                         # a limit order gets its price; market and stop orders don't
                    exit_px = raw if "limit" in why else raw * (1 - half - SLIP)
                    exit_bar = t
                    break
        if exit_px is not None:
            gross = pos["qty"] * exit_px
            received = gross * (1 - FEE)
            net = received - pos["amount"]
            acc["cash"] += received
            acc["trades"] = int(acc.get("trades", 0)) + 1
            closed_rows.append({"opened": pos["fill_t"], "closed": str(exit_bar + pd.Timedelta(hours=1)), "coin": pos["coin"],
                                "symbol": pos["symbol"], "grade": pos["grade"], "score": pos["score"],
                                "amount": round(pos["amount"], 4), "entry": pos["entry"], "exit": round(exit_px, 10),
                                "received": round(received, 4), "net": round(net, 4), "ret": round(net / pos["amount"], 5),
                                "why": why, "sell_fee": round(gross * FEE, 4),
                                "sell_spread_slippage": round(pos["qty"] * (raw - exit_px), 4)})
            say("sell", f"Sold {pos['coin']} at {exit_px:.6g} ({why}). Got back {money(received)} for the "
                f"{money(pos['amount'])} put in: {money(net)} ({pct(net / pos['amount'])}) after every cost.",
                coin=pos["coin"], net=round(net, 4))
            continue
        pos["value"] = pos["qty"] * pos["last"] * (1 - FEE - half - SLIP)
        keep.append(pos)

    invested = sum(p.get("value", 0.0) if p["status"] == "open" else p["amount"] for p in keep)
    equity = acc["cash"] + invested
    day = str(now.date())
    if acc.get("day") != day:
        acc["day"], acc["day_start"] = day, equity
    acc["peak"] = max(float(acc.get("peak", START)), equity)
    if not acc.get("paused") and equity < (1 - MAX_DRAWDOWN) * acc["peak"]:
        acc["paused"], acc["pause_reason"] = True, (f"The account fell {pct(equity / acc['peak'] - 1)} below its peak of "
                                                    f"{money(acc['peak'])}. Trading is paused until the owner restarts it.")
        say("pause", acc["pause_reason"])

    blockers = []
    if acc.get("paused"):
        blockers.append("trading is paused (" + acc.get("pause_reason", "") + ")")
    if equity < (1 - DAY_LOSS) * acc["day_start"]:
        blockers.append(f"today's loss limit is hit ({pct(equity / acc['day_start'] - 1)} since the start of the day)")
    if len(keep) >= MAX_OPEN:
        blockers.append(f"{MAX_OPEN} trades are already open")
    prob = (probation or {}).get(STYLE) or {}
    if prob.get("active"):
        blockers.append(f"the Day speed is on probation: its last {prob.get('n', 50)} ideas did "
                        f"{pct(prob.get('avg_vs_random', 0), 2)} per idea against random coins, i.e. no better than "
                        f"picking at random")
    if (quality_check or {}).get("reliable") is False:
        blockers.append("the grade check says grades can't be trusted right now")
    held = {p["symbol"] for p in keep}
    best, why_not = [], {}
    for d in ideas or []:
        if d["grade"] not in GRADES_OK:
            why_not[d["coin"]] = f"graded {d['grade']}"
            continue
        if d["symbol"] in held:
            why_not[d["coin"]] = "already held"
            continue
        if blockers:
            continue
        spread = get_spread(d["symbol"])
        cost = round_trip(spread)
        stop_dist = -float(d["safety_exit_pct"])
        amount, _ = size_trade(equity, acc["cash"], stop_dist, lambda a, cost=cost: cost)
        # the page already counts the 0.2% fees; take off the spread and slippage as well
        r_real = float(d["expected_r"]) - (cost - 2 * FEE) / (SL * float(d["risk_unit"]))
        if amount < MIN_TRADE:
            why_not[d["coin"]] = f"the safe size would be only {money(amount)}, below Binance's minimum order"
            continue
        if not (r_real > 0) or not math.isfinite(r_real):
            why_not[d["coin"]] = f"after every cost (about {share(cost, 2)} for the round trip) the expected result is negative"
            continue
        best.append((r_real, d, amount, cost, spread, stop_dist))
    best.sort(key=lambda x: -x[0])
    seen = len(ideas or [])
    if blockers:
        say("check", f"Looked at {seen} Small-coin Day ideas. No new trade: " + "; ".join(blockers) + ".")
    elif not best:
        top = sorted(ideas or [], key=lambda d: -d["score"])[:3]
        detail = "; ".join(f"{d['coin']} (score {d['score']:.1f}): {why_not.get(d['coin'], 'not usable')}" for d in top)
        say("check", f"Looked at {seen} Small-coin Day ideas. Nothing worth the risk, so I stayed in cash. "
            + (f"Best ones: {detail}." if detail else "The list was empty."))
    else:
        for r_real, d, amount, cost, spread, stop_dist in best[:NEW_PER_RUN]:
            acc["cash"] -= amount
            pos = {"id": f"{d['symbol']}|{now.isoformat()}", "coin": d["coin"], "symbol": d["symbol"], "chain": "binance",
                   "status": "pending", "signal_t": str(now), "fill_bar": str(now.floor("h")), "amount": amount,
                   "tp_pct": float(d["take_profit_pct"]), "sl_pct": float(d["safety_exit_pct"]),
                   "hold_bars": int(STYLES[STYLE].horizon_bars), "score": d["score"], "grade": d["grade"],
                   "chance": d["chance_beats_market"], "exp_r": round(r_real, 3), "spread": spread}
            keep.append(pos)
            say("order", f"Decided to buy {d['coin']} on Binance: grade {d['grade']}, score {d['score']:.1f}, "
                f"{round(d['chance_beats_market'] * 100)}% chance of beating the market, and still positive after every "
                f"cost (about {share(cost, 2)} for the round trip: 0.1% fee each way, the {share(spread, 2)} buy-sell gap "
                f"and slippage). Putting in {money(amount)}: {share(amount / equity, 0)} of the account, sized so that "
                f"hitting the safety exit ({pct(d['safety_exit_pct'])}) loses about {money(amount * (stop_dist + cost))}, at "
                f"most {share(RISK_FRAC, 0)} of the account. To copy it: buy {d['coin']} now with the same share of your "
                f"account (price now about {float(d['price_now']):.6g}); the exact take profit and safety exit follow "
                f"once the hour closes.", coin=d["coin"])
    acc["positions"] = keep
    acc["equity"] = acc["cash"] + sum(p.get("value", 0.0) if p["status"] == "open" else p["amount"] for p in keep)
    acc["updated"] = str(now)
    _save(acc, steps, closed_rows, now)
    return acc


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
