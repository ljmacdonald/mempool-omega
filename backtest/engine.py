"""Walk-forward back-test using the SAME TradingEngine as the paper trader.

For each symbol: purged walk-forward folds -> a fresh OmegaModel per fold ->
out-of-sample predictions on the test chunk only. The OOS predictions of all
symbols are then replayed bar-by-bar through ``paper.engine.TradingEngine``
(maker-first fills, latency, costs, exits, kill switch, correlation caps).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from asi.anomaly import RowAnomalyDetector
from backtest.cv import walk_forward
from backtest.metrics import summarize
from core.config import Settings, get_settings, state_path
from core.log import get_logger
from omega.model import Calibration, OmegaModel
from omega.pipeline import bar_notional, prepare, score
from paper.engine import TradingEngine

log = get_logger("backtest")


@dataclass
class OOS:
    raw: pd.DataFrame
    preds: pd.DataFrame
    sigma: pd.Series
    bar_notional: pd.Series
    calib: Calibration
    fold_info: list


def oos_predictions(raw: pd.DataFrame, s: Settings, n_folds: int = 4, min_train: int | None = None,
                    use_asi: bool = True, test_raw: pd.DataFrame | None = None) -> OOS:
    """Train on ``raw`` folds; predict on the same rows of ``test_raw`` (defaults to raw).
    ``test_raw`` lets red-team runs train on clean history but test on attacked data."""
    X, sigma = prepare(raw)
    test_raw = raw if test_raw is None else test_raw
    Xt, sigt = (X, sigma) if test_raw is raw else prepare(test_raw)
    n = len(raw)
    min_train = min_train or max(1500, n // 3)
    H = s.exits.time_stop_bars
    preds, infos, calibs = [], [], []
    for k, (tr, te) in enumerate(walk_forward(n, n_folds, min_train, horizon=H, embargo=H)):
        m = OmegaModel().fit(X.iloc[tr], raw.iloc[tr], sigma.iloc[tr], s)
        det = RowAnomalyDetector(seed=s.seed).fit(X.iloc[tr])
        # score with full history so rolling trust stats are warm, then keep the test chunk
        lo = max(0, te[0] - 300)
        sc = score(m, test_raw.iloc[lo:te[-1] + 1], Xt.iloc[lo:te[-1] + 1], sigt.iloc[lo:te[-1] + 1], det,
                   use_asi=use_asi)
        preds.append(sc.preds.iloc[te[0] - lo:])
        calibs.append(m.calib)
        infos.append({"fold": k, **m.info})
        log.info("fold %d: train=%d test=%d auc=%.3f", k, len(tr), len(te), m.info.get("oos_auc", float("nan")))
    p = pd.concat(preds)
    cal = Calibration(win_sigma=float(np.mean([c.win_sigma for c in calibs])),
                      loss_sigma=float(np.mean([c.loss_sigma for c in calibs])),
                      base_rate=float(np.mean([c.base_rate for c in calibs])))
    return OOS(test_raw.loc[p.index], p, sigt.loc[p.index], bar_notional(test_raw).loc[p.index], cal, infos)


def replay(oos: dict[str, OOS], s: Settings, use_asi: bool = True, seed: int = 0) -> tuple[TradingEngine, pd.Series]:
    """Replay OOS predictions through the trading engine on a common clock."""
    calib = next(iter(oos.values())).calib
    s2 = Settings()
    s2.symbols, s2.bar = s.symbols, s.bar
    s2.risk, s2.exits, s2.costs = s.risk, s.exits, s.costs
    if not use_asi:  # naive variant: no trust floor, no manipulation premium (trust forced to 1)
        from copy import deepcopy

        s2.risk = deepcopy(s.risk)
        s2.risk.min_trust = 0.0
        s2.risk.trust_collapse = 0.0
    eng = TradingEngine(s2, calib, seed=seed)
    clock = sorted(set().union(*[set(o.preds.index) for o in oos.values()]))
    eq = []
    for ts in clock:
        trusts = []
        for o in oos.values():
            if ts in o.preds.index:
                trusts.append(o.preds.at[ts, "signal_trust"])
        systemic = float(np.mean(trusts)) if (trusts and use_asi) else None
        for sym, o in oos.items():
            if ts not in o.preds.index:
                continue
            row = o.raw.loc[ts]
            bar = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            pr = o.preds.loc[ts]
            pred = {"p_up": float(pr["p_up"]), "side": int(pr["side"]), "meta_p": float(pr["meta_p"]),
                    "signal_trust": float(pr["signal_trust"])}
            fr = float(row.get("funding_rate", np.nan))
            eng.on_bar(sym, ts, bar, pred, float(o.sigma.loc[ts]), 1.0, float(o.bar_notional.loc[ts]),
                       fr if np.isfinite(fr) else 0.0, allow_entry=True, systemic_trust=systemic)
        eq.append((ts, eng.equity()))
    curve = pd.Series([e for _, e in eq], index=pd.DatetimeIndex([t for t, _ in eq]), name="equity")
    return eng, curve


def run_backtest(frames: dict[str, pd.DataFrame], s: Settings | None = None, n_folds: int = 4,
                 use_asi: bool = True, label: str = "walkforward") -> dict:
    s = s or get_settings()
    oos = {sym: oos_predictions(raw, s, n_folds=n_folds, use_asi=use_asi) for sym, raw in frames.items()}
    eng, curve = replay(oos, s, use_asi=use_asi, seed=s.seed)
    trades = pd.DataFrame(eng.trades)
    rep = summarize(curve, trades, s.bar_minutes)
    rep.update({"label": label, "use_asi": use_asi, "symbols": list(frames),
                "data_mode": next(iter(frames.values())).attrs.get("data_mode", "unknown"),
                "folds": {sym: o.fold_info for sym, o in oos.items()},
                "signals_evaluated": len(eng.signals),
                "signals_taken": int(sum(1 for x in eng.signals if x["taken"]))})
    rep["_curve"], rep["_trades"], rep["_signals"] = curve, trades, pd.DataFrame(eng.signals)
    return rep


def save_report(rep: dict, name: str) -> None:
    out = {k: v for k, v in rep.items() if not k.startswith("_")}
    state_path("reports", f"{name}.json").write_text(json.dumps(out, indent=2, default=str))
    if "_curve" in rep:
        rep["_curve"].to_frame().to_csv(state_path("reports", f"{name}_equity.csv"))
    if "_trades" in rep and len(rep["_trades"]):
        rep["_trades"].to_csv(state_path("reports", f"{name}_trades.csv"), index=False)


def main() -> None:
    ap = argparse.ArgumentParser(description="Walk-forward back-test (cost-aware, purged)")
    ap.add_argument("--mode", default=None, help="auto | live | synthetic")
    ap.add_argument("--bars", type=int, default=None)
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--no-asi", action="store_true")
    a = ap.parse_args()
    from ingest.history import load_universe

    s = get_settings()
    bars = a.bars or s.train_days * 24 * 60 // s.bar_minutes
    frames, _ = load_universe(n_bars=bars, mode=a.mode, live_extras=False)
    rep = run_backtest(frames, s, a.folds, use_asi=not a.no_asi)
    save_report(rep, "backtest_latest")
    print(json.dumps({k: v for k, v in rep.items() if not k.startswith("_") and k != "folds"}, indent=2, default=str))


if __name__ == "__main__":
    main()
