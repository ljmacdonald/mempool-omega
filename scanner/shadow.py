"""Shadow test of a Day model trained on Small coins themselves (DECISIONS D107).

The live Small coins page ranks coins with the Day model trained on the big-exchange coins. The Phase C replay (Jan to
Oct 2026, walk-forward, after realistic costs) found that model no better than random picks on Small coins, while a
model trained on Small coins' own last 90 days ranked them better than random in both the earlier period and the
held-back last 3 months (though still not profitably after costs).

This runs that challenger alongside, without changing anything visitors see: every hour it scores the same coins
with the same ranking rules (no probation, fake-signal checks or bait penalties, so it is the model alone), records
its top 10 in its own track record, and settles them exactly like the live ideas. compare() puts both side by side
over the same period. A switch is only proposed after SWITCH_N finished ideas each, if the challenger beats both the
live model and random picks.
"""
from __future__ import annotations

import json

import pandas as pd

from core.config import state_path
from core.log import get_logger
from scanner import track
from scanner.data import load_all
from scanner.live import big_movers, latest_rows, models_dir
from scanner.model import ScannerModel, build_dataset
from scanner.rank import rank
from scanner.styles import STYLES

log = get_logger("scanner.shadow")
HIST = "suggestions/small_shadow_history.csv"
BOARD = "suggestions/small_shadow.json"
LIVE_HIST = "suggestions/small_history.csv"
STYLE = STYLES["day"]
TRAIN_BARS = 90 * 24
TOP_N = 10
SWITCH_N = 200


def model_dir():
    return models_dir() / "small_shadow"


def train(symbols: list[str]) -> dict:
    """Fit the challenger on the Small coins' own last 90 days of hourly candles (walk-forward checked inside fit)."""
    candles = load_all(sorted(set(symbols) | {"BTCUSDT"}), TRAIN_BARS, STYLE.interval)
    m = ScannerModel(style="day").fit(build_dataset(candles, STYLE), config="small_shadow")
    m.save(model_dir())
    log.info("small shadow model trained: %s", m.info)
    return m.info


def load() -> ScannerModel | None:
    return ScannerModel.load(model_dir(), "day") if ScannerModel.exists(model_dir(), "day") else None


def open_symbols() -> list[str]:
    h = track.load_history(HIST)
    return h.loc[h["status"] == "open", "symbol"].tolist() if len(h) else []


def run(candles: dict[str, pd.DataFrame], universe: pd.DataFrame) -> dict:
    """Score this hour's Small coins with the challenger, record its top 10, settle finished ones, compare."""
    m = load()
    if m is None:                                      # first run before the nightly training: train it now
        train(universe["symbol"].tolist())
        m = load()
    latest = latest_rows(candles, STYLE, list(universe["symbol"]))
    if latest.empty:
        return {}
    qv = dict(zip(universe["symbol"], universe["quoteVolume"]))
    ideas = rank(latest, m.predict(latest), qv, big_movers(), TOP_N, m.win_r, m.loss_r, STYLE)
    track.append(ideas, len(latest), HIST)
    track.resolve(candles, "day", HIST)
    return compare()


def _side(c: pd.DataFrame) -> dict:
    if not len(c):
        return {"n": 0, "avg": None, "random": None, "edge": None, "win": None}
    return {"n": int(len(c)), "avg": float(c["net_ret"].mean()), "random": float(c["baseline_ret"].mean()),
            "edge": float((c["net_ret"] - c["baseline_ret"]).mean()), "win": float((c["net_ret"] > 0).mean())}


def compare() -> dict:
    """Both models' finished Day ideas since the shadow test began, after the same 0.2% fees, against random picks."""
    sh = track.load_history(HIST)
    if sh.empty:
        out = {"since": None, "challenger": _side(sh), "live": _side(sh), "verdict": "early"}
    else:
        since = sh["ts"].min()
        lv = track.load_history(LIVE_HIST)
        lv = lv[(lv["style"] == "day") & (lv["ts"] >= since)] if len(lv) else lv
        ch, li = _side(sh[sh["status"] == "closed"]), _side(lv[lv["status"] == "closed"] if len(lv) else lv)
        if min(ch["n"], li["n"]) < SWITCH_N:
            verdict = "early"
        elif ch["edge"] > max(li["edge"], 0) and ch["avg"] > li["avg"]:
            verdict = "challenger_better"
        else:
            verdict = "no_better"
        out = {"since": since, "challenger": ch, "live": li, "verdict": verdict}
    try:
        info = json.loads((model_dir() / "scanner_day_model.json").read_text()).get("info")
    except (OSError, ValueError):
        info = None
    out.update(switch_n=SWITCH_N, updated=str(pd.Timestamp.now(tz="UTC")), model=info)
    state_path(BOARD).write_text(json.dumps(out, indent=2, default=str))
    return out
