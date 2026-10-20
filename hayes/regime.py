"""The liquidity regime (Engine A's master switch for new entries). Realised data only, never forecasts.

Net USD liquidity (Hayes): NL = WALCL - RRPONTSYD - WTREGEN, all in millions of dollars (FRED reports WALCL and
WTREGEN in millions, RRPONTSYD in billions). Each value is used only from its publication date onward (observation
date + publication lag), so no past day ever sees a number that hadn't been published yet.

Three confirmations: net liquidity's 13-week change is positive; total stablecoin supply grew over 30 days; Bitcoin's
daily close is above its 200-day average. 3 = risk_on, 2 = neutral, 0-1 or any stale input = risk_off.
"""
from __future__ import annotations

import bisect
from datetime import date, timedelta

from hayes.profile import load

VERSION = 1
UNIT_TO_MILLIONS = {"WALCL": 1.0, "WTREGEN": 1.0, "RRPONTSYD": 1000.0}


class Series:
    """A published series: values usable only from their publication date."""

    def __init__(self, obs: list[tuple[str, float]], lag_days: int, scale: float = 1.0):
        rows = sorted((date.fromisoformat(d) + timedelta(days=lag_days), date.fromisoformat(d), v * scale) for d, v in obs if v is not None)
        self.pub = [r[0] for r in rows]
        self.obs = [r[1] for r in rows]
        self.val = [r[2] for r in rows]

    def as_of(self, day: date) -> tuple[float | None, date | None]:
        """The latest value published on or before `day`, and its observation date."""
        i = bisect.bisect_right(self.pub, day) - 1
        return (self.val[i], self.obs[i]) if i >= 0 else (None, None)


def net_liquidity(walcl: Series, rrp: Series, tga: Series, day: date) -> tuple[float | None, dict]:
    vals = {k: s.as_of(day) for k, s in (("WALCL", walcl), ("RRPONTSYD", rrp), ("WTREGEN", tga))}
    if any(v is None for v, _ in vals.values()):
        return None, vals
    return vals["WALCL"][0] - vals["RRPONTSYD"][0] - vals["WTREGEN"][0], vals


def classify(day: date, walcl: Series, rrp: Series, tga: Series, stables: Series, btc: dict[str, float], profile: dict | None = None) -> dict:
    """The regime on `day` with every input and its date (pure: same inputs, same answer)."""
    P = (profile or load())["regime"]
    nl, vals = net_liquidity(walcl, rrp, tga, day)
    nl_then, _ = net_liquidity(walcl, rrp, tga, day - timedelta(days=P["nl_change_days"]))
    st_now, st_obs = stables.as_of(day)
    st_then, _ = stables.as_of(day - timedelta(days=P["stablecoin_change_days"]))
    closes = [btc[d] for d in sorted(btc) if d <= day.isoformat()][-P["btc_sma_days"]:]
    btc_now = closes[-1] if closes else None
    btc_day = max((d for d in btc if d <= day.isoformat()), default=None)
    sma = sum(closes) / len(closes) if len(closes) == P["btc_sma_days"] else None
    stale = []
    for k, (_v, obs) in vals.items():
        if obs is None or (day - obs).days > P["max_age_days"][k]:
            stale.append(k)
    if st_obs is None or (day - st_obs).days > P["max_age_days"]["stablecoins"]:
        stale.append("stablecoins")
    if btc_day is None or (day - date.fromisoformat(btc_day)).days > P["max_age_days"]["btc"] or sma is None:
        stale.append("btc")
    c1 = nl is not None and nl_then is not None and nl - nl_then > 0
    c2 = st_now is not None and st_then is not None and st_now > st_then
    c3 = btc_now is not None and sma is not None and btc_now > sma
    n = sum((c1, c2, c3))
    state = "risk_off" if stale or n <= 1 else "risk_on" if n == 3 else "neutral"
    return {"day": day.isoformat(), "state": state, "confirmations": n, "stale": stale, "version": VERSION,
            "inputs": {"net_liquidity": nl, "net_liquidity_13w_ago": nl_then, "nl_rising": c1, "stablecoins": st_now,
                       "stablecoins_30d_ago": st_then, "stables_rising": c2, "btc": btc_now, "btc_sma200": sma, "btc_above": c3,
                       "dates": {k: (obs.isoformat() if obs else None) for k, (v, obs) in vals.items()} |
                                {"stablecoins": st_obs.isoformat() if st_obs else None, "btc": btc_day}}}
