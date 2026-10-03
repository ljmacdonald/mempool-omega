"""Defences against a manipulator who knows how this scanner works (docs/ADVERSARY.md).

1. Stop-hunt-aware exits: safety exits and take-profits are moved off the levels everyone watches
   (round numbers, just under the recent low) where stop-hunters aim. Mirrored in site/engine.js.
2. Bait monitor: every night, look at how our OWN past ideas did, split by the patterns a manipulator
   would use as bait (a big run-up before the idea, a big volume burst). If those ideas reverse after we
   publish them, someone is using the scanner against its users, and that pattern gets penalised
   automatically (state/web/adaptive.json, used by the server and the website).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd

from core.config import state_path

ROUND_BAND = 0.002     # within 0.2 % of a round number counts as "on" it
ROUND_SHIFT = 0.003    # move a stop 0.3 % below the round number
LOW_BAND = (0.002, 0.004)
LOW_SHIFT = 0.004


def round_step(price: float) -> float:
    """'Round' price levels people watch: multiples of half the leading decimal (e.g. 0.0115, 0.012, 1.5, 2)."""
    return 0.5 * 10 ** math.floor(math.log10(price))


def declutter_exits(entry: float, stop: float, target: float, low_recent: float, ru: float,
                    sl_mult: float = 1.0) -> tuple[float, float]:
    """Return (stop, target) moved away from crowded levels. The stop never moves more than 25 % of the
    planned risk further away; the target only ever moves closer (just below a round number)."""
    floor = entry * (1 - sl_mult * ru * 1.25)
    s = stop
    step = round_step(s)
    rn = round(s / step) * step
    if rn > 0 and abs(s / rn - 1) <= ROUND_BAND:
        s = rn * (1 - ROUND_SHIFT)
    if np.isfinite(low_recent) and low_recent > 0 and low_recent * (1 - LOW_BAND[0]) <= s <= low_recent * (1 + LOW_BAND[1]):
        s = low_recent * (1 - LOW_SHIFT)
    s = max(s, floor)
    g = target
    gstep = round_step(g)
    grn = math.ceil(g / gstep) * gstep
    if grn > entry and 0 <= grn / g - 1 <= ROUND_BAND:   # sellers queue AT round numbers: take profit just below
        g = grn * (1 - ROUND_BAND)
    return float(s), float(g)


# ------------------------------------------------------------------------------------- bait monitor
NEUTRAL = {"runup": {"cut": None, "penalty": 0.0}, "surge": {"cut": None, "penalty": 0.0}, "n": 0,
           "note": "Not enough settled ideas yet (needs 60). No pattern is penalised."}


def load_adaptive() -> dict:
    p = state_path("web", "adaptive.json")
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return dict(NEUTRAL)


def _pattern(c: pd.DataFrame, col: str, min_n: int) -> dict:
    x = c.dropna(subset=[col])
    if len(x) < 2 * min_n:
        return {"cut": None, "penalty": 0.0, "n": int(len(x))}
    cut = float(x[col].quantile(2 / 3))
    hi, lo = x[x[col] >= cut], x[x[col] < cut]
    rel_hi = (hi["net_ret"] - hi["baseline_ret"]).mean()
    rel_lo = (lo["net_ret"] - lo["baseline_ret"]).mean()
    gap = float(rel_hi - rel_lo)
    # penalty (in units of money risked) grows with how much worse the bait-pattern ideas did
    pen = float(np.clip(-gap * 10, 0, 0.3)) if len(hi) >= min_n and gap < -0.005 else 0.0
    return {"cut": cut, "penalty": round(pen, 4), "n": int(len(x)), "gap": round(gap, 5)}


def update_adaptive(min_n: int = 30) -> dict:
    from scanner.track import load_history

    h = load_history()
    c = h[(h.get("status") == "closed")] if len(h) else h
    if len(c) == 0 or "pre_ret" not in c:
        out = dict(NEUTRAL)
    else:
        c = c.dropna(subset=["net_ret", "baseline_ret"])
        out = {"runup": _pattern(c, "pre_ret", min_n), "surge": _pattern(c, "vol_surge", min_n), "n": int(len(c))}
        active = [k for k in ("runup", "surge") if out[k]["penalty"] > 0]
        out["note"] = ("Ideas with these patterns did worse after being suggested, a sign of baiting: "
                       + ", ".join(active)) if active else "No sign that our ideas are being used as bait."
    out["updated"] = str(pd.Timestamp.now(tz="UTC"))
    state_path("web", "adaptive.json").write_text(json.dumps(out, indent=1))
    return out


def adaptive_penalty(f: dict, adaptive: dict) -> tuple[float, list[str]]:
    pen, warn = 0.0, []
    ru, su = adaptive.get("runup", {}), adaptive.get("surge", {})
    if ru.get("penalty", 0) > 0 and ru.get("cut") is not None and f.get("ret_24b", 0) >= ru["cut"]:
        pen += ru["penalty"]
        warn.append("In our own track record, coins that had already run up like this tended to drop right after "
                    "being suggested (a sign someone sells into followers). Score lowered.")
    if su.get("penalty", 0) > 0 and su.get("cut") is not None and f.get("volume_surge", 0) >= su["cut"]:
        pen += su["penalty"]
        warn.append("In our own track record, ideas with a volume burst like this tended to reverse after being "
                    "suggested. Score lowered.")
    return pen, warn
