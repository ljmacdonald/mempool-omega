"""Nightly forex training: one model per speed, trained on every major/cross pair and gold/silver in BOTH
directions (the 'sell' side is the inverted price), so the labels are market-neutral by construction.
Exotic pairs are left out of training: managed currencies move in steps set by central banks."""
from __future__ import annotations

import json

from core.config import state_path
from core.log import get_logger
from fx.live import both_sides
from fx.pairs import ALL, EXOTICS, yahoo
from scanner.styles import FX_STYLES
from stocks.data import load_all

log = get_logger("fx.improve")


def train() -> dict:
    from scanner.improve import select_model
    from scanner.live import models_dir
    from scanner.model import build_dataset

    pairs = [p for p in ALL if p not in EXOTICS]
    out = {}
    for key, st in FX_STYLES.items():
        cs = load_all([yahoo(p) for p in pairs], st.interval, st.train_bars)
        data = build_dataset(both_sides(cs, pairs), st, btc_symbol="__none__")
        m, sel = select_model(key, data)
        m.save(models_dir())
        out[key] = {"rows": int(len(data)), "instruments": int(data["symbol"].nunique()), "auc": m.info.get("oos_auc"),
                    "winner": sel["winner"], "beats_random": sel["beats_random"],
                    "top5_avg_excess_ret": m.info.get("top5_avg_excess_ret")}
    state_path("fx", "train.json").write_text(json.dumps(out, indent=1, default=str))
    return out
