"""Nightly self-improvement, driven only by evidence.

1. MODEL SELECTION (champion / challengers). For each trading speed, several model settings are trained on
   the older 75 % of the data and scored on the most recent 25 % they never saw. The score is what matters
   to users: how much the top-5 picks beat the average coin, after fees. The winning settings are retrained
   on all the data and become tomorrow's model. If no setting beats a random pick, the speed is flagged.
2. SELF-TUNING CHECKS. Each fake-signal check's penalty is re-estimated from our own track record: checks
   whose failures really preceded worse results get heavier; checks that didn't matter get lighter. Bounded,
   and blended with the original value until there is enough evidence.
3. PROBATION. A speed whose last 50 settled ideas did worse than random picks gets lower scores and a visible
   "on probation" label until it recovers.
4. A plain-English log of every change: state/reports/improvements.json (shown on the website).
"""
from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from core.config import state_path
from core.log import get_logger
from scanner.features import FEATURES
from scanner.integrity import PENALTY
from scanner.model import PARAMS, ScannerModel
from scanner.styles import STYLES, get_style

log = get_logger("scanner.improve")

CONFIGS = {
    "base": {"params": {}, "rounds": 300, "recent": 1.0},
    "simpler": {"params": {"num_leaves": 7, "min_data_in_leaf": 300}, "rounds": 200, "recent": 1.0},
    "richer": {"params": {"num_leaves": 31, "min_data_in_leaf": 100}, "rounds": 400, "recent": 1.0},
    "recent-only": {"params": {}, "rounds": 300, "recent": 0.5},   # only the newest half of the history
}
HOLDOUT = 0.25
MIN_EDGE_TO_SWITCH = 0.0005   # a challenger must beat the standard setting by 0.05 % per idea (avoid chasing noise)
CHECK_PRIOR_N = 50          # evidence needed before a learned penalty dominates the original
CHECK_BOUNDS = (0.02, 0.30)
PROBATION_N = 50
PROBATION_PENALTY = 0.10


def _top5_excess(df: pd.DataFrame, p: np.ndarray) -> tuple[float, float]:
    d = df.assign(p=p)
    top = d.groupby(level=0, group_keys=False).apply(lambda g: g.nlargest(5, "p"))
    return float(top["excess"].mean()), float((top["excess"] > 0).mean())


def select_model(style_key: str, data: pd.DataFrame) -> tuple[ScannerModel, dict]:
    """Try each setting on unseen recent data, keep the best, retrain it on everything."""
    st = get_style(style_key)
    data = data.sort_index()
    ts = data.index.unique().sort_values()
    split = ts[int(len(ts) * (1 - HOLDOUT))]
    purge = pd.Timedelta(minutes=st.horizon_minutes)
    train, test = data[data.index < split - purge], data[data.index >= split]
    results = {}
    for name, cfg in CONFIGS.items():
        tr = train
        if cfg["recent"] < 1:
            tts = tr.index.unique().sort_values()
            tr = tr[tr.index >= tts[int(len(tts) * (1 - cfg["recent"]))]]
        if len(tr) < 2000 or len(test) < 500:
            continue
        b = lgb.train({**PARAMS, **cfg["params"]}, lgb.Dataset(tr[FEATURES], tr["win"]), cfg["rounds"])
        exc, beat = _top5_excess(test, b.predict(test[FEATURES]))
        results[name] = {"top5_excess": round(exc, 5), "top5_beat_market": round(beat, 3), "train_rows": int(len(tr))}
    winner = "base"
    if results:
        best = max(results, key=lambda k: results[k]["top5_excess"])
        base_score = results.get("base", {}).get("top5_excess", -1.0)
        if results[best]["top5_excess"] >= base_score + MIN_EDGE_TO_SWITCH:
            winner = best
    cfg = CONFIGS[winner]
    full = data
    if cfg["recent"] < 1:
        ats = data.index.unique().sort_values()
        full = data[data.index >= ats[int(len(ats) * (1 - cfg["recent"]))]]
    m = ScannerModel(style=style_key).fit(full, n_rounds=cfg["rounds"], params=cfg["params"], config=winner)
    sel = {"winner": winner, "tested_on": f"{split} onwards (never seen in training)", "results": results,
           "beats_random": bool(results and results[winner]["top5_excess"] > 0)}
    m.info["selection"] = sel
    log.info("model selection %s: %s", style_key, sel)
    return m, sel


def tune_check_penalties(h: pd.DataFrame) -> dict:
    """Learn each check's penalty from settled ideas: how much worse (vs random picks, per $ risked) ideas
    that failed the check did, compared with ideas that passed."""
    out = {}
    if h.empty or "failed_checks" not in h:
        return {k: {"penalty": v, "n_failed": 0, "learned": None} for k, v in PENALTY.items() if k != "venues_none"}
    c = h[(h["status"] == "closed")].dropna(subset=["net_ret", "baseline_ret", "risk_unit"])
    c = c[c["failed_checks"].astype(str).str.len() > 0]       # rows made before checks were recorded are skipped
    sets = c["failed_checks"].astype(str).str.split("|")
    rel = (c["net_ret"] - c["baseline_ret"]) / c["risk_unit"].clip(lower=1e-4)
    for key, prior in PENALTY.items():
        if key == "venues_none":
            continue
        failed = sets.apply(lambda xs, k=key: k in xs)
        nf, np_ = int(failed.sum()), int((~failed).sum())
        if nf < 5 or np_ < 5:
            out[key] = {"penalty": prior, "n_failed": nf, "learned": None}
            continue
        learned = float(np.clip(rel[~failed].mean() - rel[failed].mean(), 0, CHECK_BOUNDS[1]))
        w = nf / (nf + CHECK_PRIOR_N)
        pen = float(np.clip((1 - w) * prior + w * learned, *CHECK_BOUNDS))
        out[key] = {"penalty": round(pen, 4), "n_failed": nf, "learned": round(learned, 4)}
    return out


def probation(h: pd.DataFrame) -> dict:
    out = {}
    for key in STYLES:
        c = h[(h.get("status") == "closed") & (h.get("style") == key)].dropna(subset=["net_ret", "baseline_ret"]) \
            if len(h) else h
        last = c.tail(PROBATION_N)
        excess = float((last["net_ret"] - last["baseline_ret"]).mean()) if len(last) else 0.0
        active = len(last) >= PROBATION_N and excess < 0
        out[key] = {"active": bool(active), "n": int(len(last)), "avg_vs_random": round(excess, 5),
                    "penalty": PROBATION_PENALTY if active else 0.0}
    return out


def _notes(selection: dict, checks: dict, prob: dict, bait: dict) -> list[str]:
    notes = []
    for k, sel in selection.items():
        label = get_style(k).label.split(":")[0]
        r = sel["results"].get(sel["winner"], {})
        if r:
            notes.append(f"{label}: tested {len(sel['results'])} model settings on the most recent data they had "
                         f"never seen. Kept/chose '{sel['winner']}' (a challenger must beat the standard setting by "
                         f"0.05% per idea to replace it): its top-5 picks beat the average coin by "
                         f"{r['top5_excess']:+.2%} per idea there ({r['top5_beat_market']:.0%} of the time)."
                         + ("" if sel["beats_random"] else " None of the settings beat random picks, so treat "
                            "this speed with extra caution."))
    for key, v in checks.items():
        if v["learned"] is not None and abs(v["penalty"] - PENALTY[key]) > 0.01:
            notes.append(f"Fake-signal check '{key}': after {v['n_failed']} flagged ideas, its weight moved from "
                         f"{PENALTY[key]:.2f} to {v['penalty']:.2f} based on how flagged ideas actually did.")
    for k, p in prob.items():
        if p["active"]:
            notes.append(f"{STYLES[k].label.split(':')[0]} is on probation: its last {p['n']} ideas did "
                         f"{p['avg_vs_random']:+.2%} vs random picks. Its scores are lowered until it recovers.")
    if bait.get("note"):
        notes.append("Bait monitor: " + bait["note"])
    if not any(v["learned"] is not None for v in checks.values()):
        notes.append("Fake-signal check weights unchanged: not enough checked ideas with recorded check results yet.")
    return notes


def nightly(selection: dict) -> dict:
    """Run after model selection: tune checks, set probation, merge with the bait monitor, log changes."""
    from scanner.defence import update_adaptive
    from scanner.track import load_history

    h = load_history()
    bait = update_adaptive()
    checks = tune_check_penalties(h)
    prob = probation(h)
    from scanner.run import SMALL_HIST  # Small coins: their own record (D97)
    adaptive = {**bait, "check_penalties": {k: v["penalty"] for k, v in checks.items()}, "probation": prob,
                "probation_small": probation(load_history(SMALL_HIST))}
    state_path("web", "adaptive.json").write_text(json.dumps(adaptive, indent=1))
    entry = {"date": str(pd.Timestamp.now(tz="UTC"))[:16], "notes": _notes(selection, checks, prob, bait),
             "model_selection": selection, "check_penalties": checks, "probation": prob}
    p = state_path("reports", "improvements.json")
    try:
        log_ = json.loads(p.read_text())
    except (OSError, ValueError):
        log_ = []
    log_ = [entry] + log_[:59]
    p.write_text(json.dumps(log_, indent=1, default=str))
    return entry
