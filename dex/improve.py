"""Nightly DEX training and self-improvement.

* Models: the same champion/challenger selection as the main scanner (scanner/improve.py), trained on hourly
  DEX candles. To limit survivorship bias (tokens that rugged disappear from today's lists), pools seen in
  earlier runs are kept in the training set, crashes included.
* Bait monitor: did our own ideas with a big run-up or volume burst drop right after being suggested?
* Probation: a speed whose last 50 settled ideas did worse than random picks gets lower scores.
* Sniper / front-running cost: how far our ideas typically ran up in the first hour after publication,
  compared with the other candidates at the same time. That excess is what front-runners take from people
  who follow the list, so it becomes the "sniper" cost used in every profit calculation.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pandas as pd

from core.config import state_path
from core.log import get_logger
from dex.chains import CHAINS
from scanner.defence import NEUTRAL, _pattern
from scanner.styles import DEX_STYLES

log = get_logger("dex.improve")
HIST = "dex/history.csv"
SNIPER_BOUNDS = (0.002, 0.03)
PRIOR_N = 50


def load_adaptive() -> dict:
    try:
        return json.loads(state_path("dex", "adaptive.json").read_text())
    except (OSError, ValueError):
        return dict(NEUTRAL)


def training_candles() -> tuple[dict, dict]:
    """Hourly candles for training, grouped by network, read from the candle store that the hourly runs keep
    topped up (no re-downloading). It includes pools that later crashed or were rugged. Networks with few stored
    pools are topped up from the candidate list within a time budget."""
    from dex import cache
    from dex.live import _load, universe

    u = _load("universe.json", {})
    if not u.get("cands"):
        universe()
        u = _load("universe.json", {})
    refs_pool = {ch: r["pool"] for ch, r in (u.get("refs") or {}).items()}
    by_chain: dict[str, dict] = {k: {} for k in CHAINS}
    refs: dict[str, pd.DataFrame] = {}
    for key, df in cache.all_cached().items():
        chain, pool = key.split(":", 1)
        if chain not in by_chain:
            continue
        if refs_pool.get(chain) == pool:
            refs[chain] = df
        elif len(df) >= 400:
            by_chain[chain][key] = df
    start, budget = time.time(), 60 * float(os.environ.get("OMEGA_DEX_TRAIN_MINUTES", "25"))
    for n, chain in enumerate(CHAINS, 1):
        deadline = start + budget * n / len(CHAINS)          # each network gets its share of the time
        if chain in refs_pool and chain not in refs:
            try:
                refs[chain] = cache.candles(chain, refs_pool[chain], 2000)
            except Exception as e:  # noqa: BLE001
                log.warning("reference %s: %s", chain, e)
        for c in [c for c in u.get("cands", []) if c["chain"] == chain]:
            if len(by_chain[chain]) >= 25 or time.time() > deadline:
                break
            key = f"{chain}:{c['pool']}"
            if key in by_chain[chain]:
                continue
            try:
                df = cache.candles(chain, c["pool"], 2000)
                if len(df) >= 400:
                    by_chain[chain][key] = df
            except Exception as e:  # noqa: BLE001
                log.warning("training candles %s: %s", key, e)
        log.info("training candles %s: %d pools", chain, len(by_chain[chain]))
    return by_chain, refs


def train() -> dict:
    from scanner.improve import select_model
    from scanner.live import models_dir
    from scanner.model import build_dataset

    by_chain, refs = training_candles()
    out = {}
    for key, st in DEX_STYLES.items():
        parts = []
        for chain, candles in by_chain.items():
            if len(candles) < 3:
                continue
            cs = dict(candles)
            if chain in refs:
                cs["REF"] = refs[chain]
            try:
                parts.append(build_dataset(cs, st, btc_symbol="REF"))
            except Exception as e:  # noqa: BLE001
                log.warning("dataset %s %s: %s", key, chain, e)
        if not parts:
            continue
        data = pd.concat(parts).sort_index()
        m, sel = select_model(key, data)
        m.save(models_dir())
        out[key] = {"selection": sel, "rows": int(len(data)), "pools": int(data["symbol"].nunique()),
                    "auc": m.info.get("oos_auc"), "top5_avg_excess_ret": m.info.get("top5_avg_excess_ret")}
    state_path("dex", "train.json").write_text(json.dumps(out, indent=1, default=str))
    out["self_improvement"] = nightly()
    return out


def sniper_costs(h: pd.DataFrame, candles_note: dict | None = None) -> dict:
    """Learned per-network front-running cost, blended with the prior until there is enough evidence."""
    out = {}
    for chain, c in CHAINS.items():
        x = h[(h.get("status") == "closed") & h["symbol"].astype(str).str.startswith(chain + ":")] if len(h) else h
        x = x.dropna(subset=["first_hour_runup", "first_hour_runup_base"]) if "first_hour_runup" in x else x.iloc[0:0]
        n = len(x)
        if n:
            learned = float(np.clip((x["first_hour_runup"] - x["first_hour_runup_base"]).mean(), 0, SNIPER_BOUNDS[1]))
            w = n / (n + PRIOR_N)
            v = (1 - w) * c["sniper"] + w * learned
        else:
            v = c["sniper"]
        out[chain] = round(float(np.clip(v, *SNIPER_BOUNDS)), 5)
    return out


def probation(h: pd.DataFrame, n_last: int = 50, penalty: float = 0.10) -> dict:
    out = {}
    for key in DEX_STYLES:
        c = h[(h.get("status") == "closed") & (h.get("style") == key)].dropna(subset=["net_ret", "baseline_ret"]) \
            if len(h) else h
        last = c.tail(n_last)
        excess = float((last["net_ret"] - last["baseline_ret"]).mean()) if len(last) else 0.0
        active = len(last) >= n_last and excess < 0
        out[key] = {"active": bool(active), "n": int(len(last)), "avg_vs_random": round(excess, 5),
                    "penalty": penalty if active else 0.0}
    return out


def nightly() -> dict:
    from scanner.track import load_history

    h = load_history(HIST)
    c = h[h["status"] == "closed"].dropna(subset=["net_ret", "baseline_ret"]) if len(h) else h
    if len(c) and "pre_ret" in c:
        bait = {"runup": _pattern(c, "pre_ret", 30), "surge": _pattern(c, "vol_surge", 30), "n": int(len(c))}
        active = [k for k in ("runup", "surge") if bait[k]["penalty"] > 0]
        bait["note"] = ("DEX ideas with these patterns did worse after being suggested: " + ", ".join(active)) \
            if active else "No sign that our DEX ideas are being used as bait."
    else:
        bait = dict(NEUTRAL)
    # front-running is measured after every published list, so it reads the archive with repeats (D93)
    every = load_history("dex/history_all.csv")
    adaptive = {**bait, "probation": probation(h), "sniper": sniper_costs(every if len(every) else h),
                "updated": str(pd.Timestamp.now(tz="UTC"))}
    state_path("dex", "adaptive.json").write_text(json.dumps(adaptive, indent=1))
    return adaptive
