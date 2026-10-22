"""Results of a trade list (D123): the same numbers for every strategy, every book, every test."""
from __future__ import annotations

import statistics

from perp import gates as GT
from perp.money import ZERO, Trade, cost_drift, d, waterfall


def summarize(trades: list[Trade], start_usd, stressed: list[Trade] | None = None) -> dict:
    """Trades in closing order: per-trade net as a share of the starting capital, equity, drawdown, the cost
    waterfall, dependence on the biggest winners (AT-12), and the same trades with costs doubled (gate 5)."""
    start = d(start_usd)
    ts = sorted(trades, key=lambda t: (t.closed or "", t.opened))
    nets = [float(t.net / start) for t in ts]
    # each trade is sized as a share of the starting capital; run on the CURRENT balance (as a real account would),
    # results compound, so an account can approach zero but never go below it
    eq, e = [], 1.0
    for x in nets:
        e *= max(0.0, 1 + x)
        eq.append(e * float(start))
    e = d(str(eq[-1])) if eq else start
    wins = [t for t in ts if t.net > 0]
    losses = [t for t in ts if t.net <= 0]
    gross_win, gross_loss = sum((t.net for t in wins), ZERO), -sum((t.net for t in losses), ZERO)
    top = sorted(nets, reverse=True)
    out = {"n": len(ts), "final": str(e), "total_return": float(e / start - 1), "mean": statistics.fmean(nets) if nets else None,
           "hit_rate": len(wins) / len(ts) if ts else None,
           "avg_win": float(gross_win / len(wins) / start) if wins else None,
           "avg_loss": float(-gross_loss / len(losses) / start) if losses else None,
           "profit_factor": float(gross_win / gross_loss) if gross_loss > 0 else None,
           "max_drawdown": GT.max_drawdown([float(start)] + eq), "compounded": True,
           "gross": str(sum((t.gross for t in ts), ZERO)), "costs": str(sum((sum(t.costs.values(), ZERO) for t in ts), ZERO)),
           "waterfall": waterfall(ts), "cost_drift": cost_drift(ts),
           "without_top5": statistics.fmean(top[5:]) if len(top) > 5 else None,
           "by_market": {}, "by_side": {}, "by_period": {}}
    for key, f in (("by_market", lambda t: t.market), ("by_side", lambda t: {1: "long", -1: "short", 0: "pool"}[t.side]),
                   ("by_period", lambda t: (t.closed or "")[:7] if (t.closed or "")[:4].isdigit() and "-" in (t.closed or "") else "")):
        groups: dict = {}
        for t in ts:
            groups.setdefault(f(t), []).append(float(t.net / start))
        out[key] = {k: {"n": len(v), "mean": statistics.fmean(v), "total": sum(v)} for k, v in groups.items() if k}
    if stressed is not None:
        sn = [float(t.net / start) for t in stressed]
        out["stressed_mean"] = statistics.fmean(sn) if sn else None
    out["nets"] = nets
    out["sample"] = [t.to_json() for t in ts[-60:]]
    return out
