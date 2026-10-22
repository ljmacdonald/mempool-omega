"""ANEE's six go/no-go gates (D123), applied automatically to each strategy, plus the statistics behind them.

  1 Data integrity    99.5%+ of expected observations present; every accepted trade had all critical fields
  2 Evidence          500+ closed trades (or, for the pool strategy, 500+ days held), reported per strategy
  3 Net profit        positive net expectancy AND a positive lower bound of the 95% block-bootstrap interval
  4 Drawdown          at most 10% in the test
  5 Robustness        still net profitable with every cost doubled
  6 Shadow            30+ days of live paper running, 100+ trades if markets allow, 95%+ of cost estimates within
                      tolerance of what was then charged
All mandatory gates must pass; passing only allows a review. There is no live trading in any case.
"""
from __future__ import annotations

import random
import statistics

from perp.config import load


def block_bootstrap_ci(xs: list[float], block: int | None = None, reps: int | None = None, seed: int = 7) -> tuple:
    """95% interval of the mean, resampling consecutive blocks (keeps the time dependence between trades)."""
    G = load()["gates"]
    block, reps = block or G["bootstrap_block"], reps or G["bootstrap_reps"]
    n = len(xs)
    if n < 2:
        return (None, None)
    block = max(1, min(block, n))
    rnd = random.Random(seed)
    starts = list(range(n - block + 1))
    means = []
    for _ in range(reps):
        sample = []
        while len(sample) < n:
            s = rnd.choice(starts)
            sample += xs[s:s + block]
        means.append(statistics.fmean(sample[:n]))
    means.sort()
    return (means[int(0.025 * reps)], means[int(0.975 * reps) - 1])


def max_drawdown(equity: list[float]) -> float:
    peak, dd = None, 0.0
    for e in equity:
        peak = e if peak is None else max(peak, e)
        if peak and peak > 0:
            dd = max(dd, 1 - e / peak)
    return dd


def evaluate(r: dict, unit: str = "trades") -> dict:
    """r: {complete, n, nets (per trade or per day, as a share of the capital used), drawdown, stressed_mean,
    shadow: {days, trades, cost_ok}}. Returns each gate with pass/fail/incomplete and the overall status."""
    G = load()["gates"]
    nets = r.get("nets") or []
    mean = statistics.fmean(nets) if nets else None
    lo, hi = block_bootstrap_ci(nets)
    sh = r.get("shadow") or {}
    g = {
        "data": {"pass": (r.get("complete") or 0) >= G["data_complete"], "value": r.get("complete"), "need": G["data_complete"]},
        "evidence": {"pass": len(nets) >= G["min_trades"], "value": len(nets), "need": G["min_trades"], "unit": unit},
        "profit": {"pass": bool(mean is not None and mean > 0 and lo is not None and lo > 0), "mean": mean, "ci": [lo, hi]},
        "drawdown": {"pass": r.get("drawdown") is not None and r["drawdown"] <= G["max_drawdown"], "value": r.get("drawdown"),
                     "need": G["max_drawdown"]},
        "robust": {"pass": r.get("stressed_mean") is not None and r["stressed_mean"] > 0, "value": r.get("stressed_mean"),
                   "cost_multiple": G["stress_cost_multiple"]},
        "shadow": {"pass": (sh.get("days") or 0) >= G["shadow_days"] and (sh.get("cost_ok") or 0) >= G["shadow_cost_ok"]
                   and ((sh.get("trades") or 0) >= G["shadow_trades"] or sh.get("few_markets")),
                   "days": sh.get("days") or 0, "trades": sh.get("trades") or 0, "cost_ok": sh.get("cost_ok"),
                   "complete": (sh.get("days") or 0) >= G["shadow_days"]},
    }
    for k in ("evidence", "shadow"):
        g[k]["status"] = "pass" if g[k]["pass"] else "incomplete"
    enough = g["evidence"]["pass"]
    for k in ("data", "profit", "robust"):           # judged only once the evidence gate is met; until then shown as measured
        g[k]["status"] = ("pass" if g[k]["pass"] else "fail") if enough else "incomplete"
    dd = g["drawdown"]                                 # a drawdown beyond the limit fails at any sample size
    dd["status"] = "pass" if dd["pass"] and enough else ("fail" if dd.get("value") is not None and not dd["pass"] else "incomplete")
    if any(v["status"] == "fail" for v in g.values()):
        status = "failed"
    elif all(v["status"] == "pass" for v in g.values()):
        status = "passed: review only"
    else:
        status = "testing"
    return {"gates": g, "status": status, "mean": mean, "ci": [lo, hi]}
