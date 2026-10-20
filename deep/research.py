"""Engine B's validation (D122), in the specification's order: the coverage check first, then the event study, then the
kill criteria, applied automatically. Shares Engine A's event-study code (hayes.research) with Engine B's profile.

Known coins: every base asset Binance (USDT, trading or delisted) or Coinbase (USD/USDC, online or delisted) lists.
Candles: Binance first, then Coinbase for coins Binance never listed. For context the same statistics are reported for
the -75% to -92% range, as the specification asks.
"""
from __future__ import annotations

import copy

from deep import coverage as CV
from deep.profile import load
from hayes import research as R


def context_profile(P: dict) -> dict:
    """Engine B's profile with the distress band moved to -75%..-92% (the context range)."""
    Q = copy.deepcopy(P)
    Q["distress"]["band"] = [-0.92, -0.75]
    return Q


def study(series: dict[str, list[dict]], trading: set[str], known: dict[str, set[str]], btc: list[dict], fred_series: dict,
          stables: list, profile: dict | None = None) -> dict:
    """series: coin -> daily candles (covered coins); trading: coins still trading; known: coin -> sources listing it."""
    P = profile or load()
    es = R.event_study(series, trading, btc, fred_series, stables, P)
    cov = CV.report(known, {c for c, v in series.items() if v}, es["events_by_coin"], P)
    ctx = R.event_study(series, trading, btc, fred_series, stables, context_profile(P))
    return {"coverage": cov, "event_study": es, "context_75_92": ctx, "kill": kill_b(cov, es, P)}


def kill_b(cov: dict, es: dict, P: dict | None = None) -> dict:
    """Engine B's coverage and event-study kill criteria (the backtest ones need a backtest, which runs only if these pass)."""
    K = (P or load())["kill"]
    e = es["entry"]["all"]
    bad, x3 = e.get("delisted_or_fell_80"), e.get("reached_3x")
    k1 = bool(cov["reliable"])
    k2 = bad is not None and bad <= K["max_delisted_or_fell_80"]
    k3 = x3 is not None and x3 >= K["min_3x_share"]
    return {"coverage": {"pass": k1, "ratio": cov["ratio"], "min": cov["min_ratio"]},
            "delisted_or_fell_80": {"pass": k2, "share": bad, "max": K["max_delisted_or_fell_80"], "n": e["n"]},
            "reached_3x": {"pass": k3, "share": x3, "min": K["min_3x_share"], "n": e["n_full_year"]},
            "status": "paper" if k1 and k2 and k3 and es["regime_inputs_ok"] else "halted"}
