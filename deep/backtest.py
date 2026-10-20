"""Engine B's walk-forward backtest (D122). Every entry is a day all of Engine B's price rules passed (found with data up
to that day only); every exit follows the profile's exit plan day by day. Engine B's thresholds are the
specification's defaults and nothing is tuned on these results, so every window is out of sample. Pure functions.

Execution: buy at the entry day's close plus slippage (base slippage, x the thin-volume multiplier when the order would
exceed 2% of 30-day average volume); pay the fee on every buy and sell. Exits, checked daily in this order: the disaster
stop (25% below the trough), the profit ladder (sell set shares at 2x, 4x, 8x), the trailing stop (40% below the
highest close since entry, 25% when the regime is risk-off), the time stop (270 days unless up 50%). A coin that
stops trading exits at its last close. Size: 0.5% of equity, at most 8 positions, only in allowed regimes.
"""
from __future__ import annotations

import statistics

from deep.profile import load
from hayes import research as R
from hayes import scoring as SC

WINDOWS = (("2019-01-01", "2022-12-31"), ("2023-01-01", "2024-12-31"), ("2025-01-01", "2099-12-31"))


def simulate(c: list[dict], i: int, trough: float, trading: bool, regime_of, P: dict) -> dict:
    """One position bought at the close of c[i]. Returns its multiple after costs and the exit."""
    X, F = P["exits"], P["portfolio"]
    fee = F["fee_per_side"]
    adv = sum(x["v"] for x in c[max(0, i - 29):i + 1]) / min(30, i + 1)
    slip = F["base_slippage_bps"] / 1e4
    size_usd = P["starting_equity_usdc"] * F["sizing"]["size_pct"]
    if adv and size_usd > F["adv_cap_pct"] * adv:
        slip *= F["thin_volume_slippage_multiplier"]
    entry = c[i]["c"] * (1 + slip)
    left, cash = 1.0, 0.0                                  # share of the position still held; proceeds per unit bought
    ladder = list(X["ladder"])
    peak = entry
    disaster = trough * (1 - X["disaster_below_trough_pct"])

    def sell(share: float, price: float) -> None:
        nonlocal left, cash
        cash += share * price * (1 - slip) * (1 - fee) / entry
        left -= share

    reason, day = "open", c[-1]["d"]
    for j in range(i + 1, len(c)):
        x = c[j]
        if x["l"] <= disaster:
            sell(left, min(disaster, x["o"]))
            reason, day = "disaster_stop", x["d"]
            break
        while ladder and x["h"] >= entry * ladder[0][0]:
            m, share = ladder.pop(0)
            sell(min(share, left), entry * m)
        peak = max(peak, x["c"])
        trail = X["trail_pct_risk_off"] if regime_of(x["d"]) == "risk_off" else X["trail_pct"]
        if x["c"] <= peak * (1 - trail) and peak > entry:
            sell(left, x["c"])
            reason, day = "trailing_stop", x["d"]
            break
        if j - i >= X["time_stop"]["days"] and x["c"] < entry * (1 + X["time_stop"]["min_gain"]):
            sell(left, x["c"])
            reason, day = "time_stop", x["d"]
            break
        if left <= 1e-9:
            reason, day = "ladder_done", x["d"]
            break
    else:
        if not trading:
            sell(left, c[-1]["c"])
            reason = "delisted"
        else:
            cash += left * c[-1]["c"] * (1 - slip) * (1 - fee) / entry   # still open: marked at the last close, after exit costs
            left = 0.0
    mult = cash / (1 + fee)
    return {"entry_day": c[i]["d"], "entry": entry, "exit_day": day, "reason": reason, "multiple": mult, "pnl": size_usd * (mult - 1),
            "size": size_usd, "held_days": None}


def trades_for(series: dict[str, list[dict]], trading: set[str], btc_close: dict, regime_of, P: dict | None = None) -> list[dict]:
    """Every day all of Engine B's price rules passed (one per episode), simulated to its exit."""
    P = P or load()
    out = []
    for sym, c in series.items():
        idx = {x["d"]: k for k, x in enumerate(c)}
        for e in R.events_for(sym, c, btc_close, sym in trading, regime_of, P):
            if not e.get("entry"):
                continue
            i = idx[e["entry"]["day"]]
            d = SC.distress(c[max(0, i - 400):i + 1], P)
            out.append({**simulate(c, i, d["L"], sym in trading, regime_of, P), "symbol": sym, "regime": e["entry"]["regime"]})
    return out


def run(trades: list[dict], P: dict | None = None, allowed: tuple[str, ...] | None = None) -> dict:
    """trades: simulated positions with symbol and regime at entry, oldest first. Applies the position limit and the
    regime filter, then reports per window."""
    P = P or load()
    allowed = allowed or tuple(P["portfolio"]["allowed_regimes"])
    cap = P["portfolio"]["max_positions"]
    taken, open_ = [], []
    for t in sorted(trades, key=lambda t: t["entry_day"]):
        if t["regime"] not in allowed:
            continue
        open_ = [o for o in open_ if o["exit_day"] > t["entry_day"]]
        if len(open_) >= cap or any(o["symbol"] == t["symbol"] for o in open_):
            continue
        open_.append(t)
        taken.append(t)
    out = {"allowed_regimes": list(allowed), "trades": len(taken), "windows": []}
    for a, b in WINDOWS:
        w = [t for t in taken if a <= t["entry_day"] <= b]
        out["windows"].append({"from": a, "to": b if b < "2099" else "now", **stats(w)})
    out["all"] = stats(taken)
    prof = [t["pnl"] for t in taken if t["pnl"] > 0]
    by_tok: dict[str, float] = {}
    for t in taken:
        by_tok[t["symbol"]] = by_tok.get(t["symbol"], 0.0) + t["pnl"]
    best = max(by_tok.items(), key=lambda x: x[1], default=(None, 0.0))
    total_profit = sum(v for v in by_tok.values() if v > 0)
    out["top_token"] = {"symbol": best[0], "share_of_profit": best[1] / total_profit if total_profit > 0 else None}
    out["winners"] = sorted((round(t["multiple"], 2) for t in taken), reverse=True)[:20]
    out["profit_count"] = len(prof)
    return out


def stats(w: list[dict]) -> dict:
    if not w:
        return {"n": 0, "expectancy": None}
    m = [t["multiple"] - 1 for t in w]
    wins, losses = [x for x in m if x > 0], [x for x in m if x <= 0]
    return {"n": len(w), "expectancy": statistics.mean(m), "median": statistics.median(m), "hit_rate": len(wins) / len(w),
            "avg_win": statistics.mean(wins) if wins else None, "avg_loss": statistics.mean(losses) if losses else None,
            "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
            "lost_almost_all": sum(1 for x in m if x <= -0.9) / len(w),
            "pnl_usd": sum(t["pnl"] for t in w),
            "exits": {r: sum(1 for t in w if t["reason"] == r) for r in sorted({t["reason"] for t in w})}}


def kill(main: dict, P: dict | None = None) -> dict:
    """The backtest kill criteria: positive expectancy after costs in at least 2 of 3 windows, and no single token
    supplying over 50% of the profit."""
    K = (P or load())["kill"]
    pos = sum(1 for w in main["windows"] if w["n"] and w["expectancy"] > 0)
    k1 = pos >= K["backtest_min_positive_windows"]
    share = main["top_token"]["share_of_profit"]
    k2 = share is not None and share <= K["max_profit_share_one_token"]
    return {"positive_windows": {"pass": k1, "count": pos, "min": K["backtest_min_positive_windows"]},
            "one_token_profit_share": {"pass": k2, "share": share, "symbol": main["top_token"]["symbol"], "max": K["max_profit_share_one_token"]},
            "status": "paper" if k1 and k2 else "halted"}
