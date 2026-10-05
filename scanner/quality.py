"""Are the grades telling the truth? Checked from the track record every hour, for every page.

* Results are judged in R (the result divided by the distance to the safety exit), so a jumpy coin's big swings
  count the same as a calm coin's small ones, per $1 put at risk.
* grade_check: over enough finished ideas, do the high grades (Strong, Moderate) beat the low ones (Weak, Avoid) in R,
  and does a higher score go with a better result? If not, the pages say so and stop using the grade words.
* vol_adjust: does a coin's jumpiness (its risk unit) predict worse results than its score expected? The slope of R on
  the (log) risk unit, shrunk towards zero when there are few ideas, becomes an adjustment to every expected R, so a
  volatile coin doesn't rank high just because it moves a lot.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

HIGH, LOW = ("Strong", "Moderate"), ("Weak", "Avoid - watch only")
GRADE_ORDER = ["Strong", "Moderate", "Weak", "Avoid - watch only"]
MIN_EACH = 20          # finished ideas needed on each side before the grades are judged
SHRINK_N = 100         # the volatility slope counts fully only with many more ideas than this
MAX_SLOPE = 0.3


def outcome_r(c: pd.DataFrame) -> pd.Series:
    """Each finished idea's result per $1 put at risk."""
    sl = c["sl_pct"].abs() if "sl_pct" in c else pd.Series(np.nan, index=c.index)
    ru = c["risk_unit"].abs() if "risk_unit" in c else pd.Series(np.nan, index=c.index)
    stop = sl.where(sl > 0, ru)
    return (c["net_ret"] / stop).where(stop > 0)


def grade_table(c: pd.DataFrame) -> list[dict]:
    if not len(c):
        return []
    c = c.assign(R=outcome_r(c))
    out = []
    for g in GRADE_ORDER:
        x = c[c["grade"] == g]
        if len(x):
            out.append({"grade": g, "ideas": int(len(x)), "win_rate": float((x["net_ret"] > 0).mean()),
                        "avg_return": float(x["net_ret"].mean()), "avg_r": float(x["R"].mean()),
                        "random_avg_return": float(x["baseline_ret"].mean()) if "baseline_ret" in x else None})
    return out


def grade_check(c: pd.DataFrame) -> dict:
    """reliable: True (high grades did better), False (they didn't), None (not enough finished ideas yet)."""
    if not len(c):
        return {"reliable": None, "high_n": 0, "low_n": 0, "reason": "No finished ideas yet."}
    c = c.assign(R=outcome_r(c)).dropna(subset=["R"])
    hi, lo = c[c["grade"].isin(HIGH)], c[c["grade"].isin(LOW)]
    out = {"high_n": int(len(hi)), "low_n": int(len(lo)),
           "high_avg_r": float(hi["R"].mean()) if len(hi) else None, "low_avg_r": float(lo["R"].mean()) if len(lo) else None}
    corr = float(c[["score", "R"]].rank().corr().iloc[0, 1]) if "score" in c and len(c) >= 10 else None
    out["score_result_corr"] = None if corr is None or math.isnan(corr) else corr
    if len(hi) < MIN_EACH or len(lo) < MIN_EACH:
        out.update(reliable=None, reason=f"Not enough finished ideas yet to judge the grades ({len(hi)} high-grade and "
                                         f"{len(lo)} low-grade; {MIN_EACH} of each are needed).")
    elif out["high_avg_r"] > out["low_avg_r"] and (out["score_result_corr"] or 0) > 0:
        out.update(reliable=True, reason="Higher grades did better than lower grades, per $1 risked.")
    else:
        out.update(reliable=False, reason=f"Higher grades did no better than lower ones: Strong/Moderate averaged "
                                          f"{out['high_avg_r']:+.2f}R per idea and Weak/Avoid {out['low_avg_r']:+.2f}R "
                                          f"({len(hi)} and {len(lo)} finished ideas).")
    return out


def vol_adjust(c: pd.DataFrame) -> dict:
    """Slope of R on the standardised log risk unit, shrunk by n / (n + SHRINK_N) and capped."""
    out = {"n": 0, "mean": 0.0, "sd": 1.0, "slope": 0.0}
    if not len(c) or "risk_unit" not in c:
        return out
    c = c.assign(R=outcome_r(c)).dropna(subset=["R"])
    c = c[c["risk_unit"] > 0]
    if len(c) < 30:
        return {**out, "n": int(len(c))}
    x = np.log(c["risk_unit"].to_numpy(float))
    m, sd = float(x.mean()), float(x.std())
    if not sd > 0:
        return {**out, "n": int(len(c))}
    z = (x - m) / sd
    r = c["R"].to_numpy(float).clip(-5, 5)
    b = float(np.polyfit(z, r, 1)[0])
    n = len(c)
    return {"n": int(n), "mean": m, "sd": sd, "slope": float(np.clip(b * n / (n + SHRINK_N), -MAX_SLOPE, MAX_SLOPE)),
            "raw_slope": b}


def adjust_r(r: float, risk_unit: float, adj: dict | None) -> float:
    """Expected R after the learned volatility adjustment (no change without enough history)."""
    if not adj or not adj.get("slope") or not risk_unit or risk_unit <= 0:
        return r
    return r + adj["slope"] * (math.log(risk_unit) - adj["mean"]) / adj["sd"]


def quality(c: pd.DataFrame) -> dict:
    return {"by_grade": grade_table(c), "check": grade_check(c), "vol": vol_adjust(c)}


def load(rel_path: str) -> dict | None:
    """The `quality` block of a scoreboard written by scanner.track.scoreboard."""
    import json

    from core.config import state_path
    p = state_path(rel_path)
    try:
        return json.loads(p.read_text()).get("quality") if p.exists() else None
    except Exception:  # noqa: BLE001
        return None
