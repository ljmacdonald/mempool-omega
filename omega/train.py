"""Nightly retrain (GitHub Actions cron): rolling window -> per-symbol OmegaModel -> state/models/.

Also re-fits the liquidation Hawkes kernel and writes a training report.
Usage:  python -m omega.train [--mode auto|live|synthetic] [--days 30]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import numpy as np
import pandas as pd

from core.config import get_settings, state_path
from core.log import get_logger
from features.hawkes import fit_hawkes
from ingest.history import load_universe
from omega.model import OmegaModel
from omega.pipeline import prepare

log = get_logger("omega.train")


def train_all(mode: str | None = None, days: int | None = None) -> dict:
    s = get_settings()
    days = days or s.train_days
    n_bars = days * 24 * 60 // s.bar_minutes
    frames, health = load_universe(n_bars=n_bars, mode=mode, live_extras=False)
    report = {"trained_at": str(pd.Timestamp.now(tz="UTC")), "bar": s.bar, "days": days, "symbols": {}}
    model_dir = state_path("models", "x").parent
    for sym, raw in frames.items():
        liq = (raw["liq_long_qty"].fillna(0) + raw["liq_short_qty"].fillna(0)).to_numpy()
        hk = fit_hawkes(np.log1p(liq)) if np.nansum(liq) > 0 else None
        X, sigma = prepare(raw, hk)
        m = OmegaModel().fit(X, raw, sigma, s)
        if hk is not None:
            m.info["hawkes"] = asdict(hk)
        m.save(model_dir, sym)
        report["symbols"][sym] = {**m.info, "source_health": health.get(sym, {}),
                                  "n_bars": len(raw), "calib": asdict(m.calib)}
        log.info("saved model %s (auc=%.3f, data_mode=%s)", sym, m.info["oos_auc"], m.info["data_mode"])
    state_path("reports", "train_latest.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Retrain Omega models")
    ap.add_argument("--mode", default=None)
    ap.add_argument("--days", type=int, default=None)
    a = ap.parse_args()
    rep = train_all(a.mode, a.days)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ("oos_auc", "n_train", "data_mode")}
                      for k, v in rep["symbols"].items()}, indent=2))


if __name__ == "__main__":
    main()
