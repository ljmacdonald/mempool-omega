"""Live PAPER trader. One invocation = one pass (GitHub Actions runs it hourly).

Each pass:
  1. load persisted engine state + models (train on the fly if none exist yet)
  2. pull fresh public data for every symbol (+ optional time-boxed WebSocket capture)
  3. compute features, data quality, ASI trust, model predictions
  4. replay every bar closed since the last pass through the engine (so stops / targets /
     time stops that happened between hourly runs are honoured on the real price path);
     new entries are only considered on the LATEST bar (no retroactive trading)
  5. append ledger, send alerts, save state + status.json

Usage:  python -m paper.trader --once [--mode auto|live|synthetic] [--capture 45] [--loop-minutes 0]
"""
from __future__ import annotations

import argparse
import json
import time
import traceback

import numpy as np
import pandas as pd

from alerts.telegram import format_event, send
from asi.anomaly import RowAnomalyDetector, hurst_rs
from asi.quality import data_quality
from core.config import get_settings, state_path
from core.log import get_logger
from core.synthetic import synthetic_trade_sizes
from ingest.history import load_frame
from omega.model import OmegaModel
from omega.pipeline import prepare, score
from paper import ledger
from paper.engine import TradingEngine
from paper.testnet import maybe_mirror

log = get_logger("paper.trader")
STATE_FILE = "paper_state.json"
MAX_CATCHUP_BARS = 36


def _load_models(symbols: list[str], mode: str | None) -> dict[str, OmegaModel]:
    d = state_path("models", "x").parent
    models = {}
    missing = [s for s in symbols if not (d / f"{s}_model.json").exists()]
    if missing:
        log.warning("no trained model for %s - training now (first run)", missing)
        from omega.train import train_all

        train_all(mode=mode, days=None)
    for s in symbols:
        models[s] = OmegaModel.load(d, s)
    return models


def _trade_sizes(symbol: str, mode: str) -> np.ndarray | None:
    if mode == "synthetic":
        return synthetic_trade_sizes(1000, seed=int(time.time()) // 3600)
    try:
        from ingest.cex import binance_agg_trades

        return binance_agg_trades(symbol, 1000)["qty"].to_numpy()
    except Exception:  # noqa: BLE001
        return None


def run_once(mode: str | None = None, capture_seconds: float = 0.0) -> dict:
    s = get_settings()
    mode = mode or s.data_mode
    models = _load_models(s.symbols, mode)
    calib = next(iter(models.values())).calib
    prev_state = TradingEngine.load_state(state_path(STATE_FILE))
    eng = TradingEngine(s, calib, seed=int(time.time()) % 2**31, state=prev_state)
    status: dict = {"run_at": str(pd.Timestamp.now(tz="UTC")), "symbols": {}, "mode": mode}

    scored = {}
    for sym in s.symbols:
        raw, health = load_frame(sym, n_bars=s.history_bars, mode=mode, capture_seconds=capture_seconds)
        X, sigma = prepare(raw, None)
        det = RowAnomalyDetector(seed=s.seed).fit(X.iloc[:-1])
        transfers = pd.DataFrame(raw.attrs.get("transfers", []), columns=["from", "to", "value"]) \
            if raw.attrs.get("transfers") else None
        sc = score(models[sym], raw, X, sigma, det, _trade_sizes(sym, raw.attrs.get("data_mode", mode)), transfers)
        dq = data_quality(raw, health, s.bar_minutes)
        scored[sym] = (raw, sc, dq, health)

    latest_trust = [sc.preds["signal_trust"].iloc[-1] for _, sc, _, _ in scored.values()]
    systemic = float(np.mean(latest_trust)) if latest_trust else None

    for sym, (raw, sc, dq, health) in scored.items():
        last_done = eng.state.last_bar.get(sym)
        if last_done:
            new_idx = raw.index[raw.index > pd.Timestamp(last_done)][-MAX_CATCHUP_BARS:]
        else:
            new_idx = raw.index[-1:]
        for ts in new_idx:
            row = raw.loc[ts]
            pr = sc.preds.loc[ts]
            is_last = ts == raw.index[-1]
            fr = float(row["funding_rate"]) if pd.notna(row["funding_rate"]) else 0.0
            eng.on_bar(sym, ts, {k: float(row[k]) for k in ("open", "high", "low", "close")},
                       {"p_up": float(pr["p_up"]), "side": int(pr["side"]), "meta_p": float(pr["meta_p"]),
                        "signal_trust": float(pr["signal_trust"])},
                       float(sc.sigma.loc[ts]), dq["score"] if is_last else 1.0, float(sc.bar_notional.loc[ts]),
                       fr, allow_entry=is_last, systemic_trust=systemic if is_last else None)
        last_trust = float(sc.preds["signal_trust"].iloc[-1])
        if sym in eng.state.positions and last_trust < s.risk.min_trust:
            eng.events.append({"ts": str(raw.index[-1]), "kind": "trust_drop", "symbol": sym, "trust": last_trust})
        top_trust = sc.trust.iloc[-1].dropna().sort_values()
        status["symbols"][sym] = {
            "last_bar": str(raw.index[-1]), "close": float(raw["close"].iloc[-1]),
            "data_mode": raw.attrs.get("data_mode", mode), "data_quality": dq,
            "p_up": float(sc.preds["p_up"].iloc[-1]), "meta_p": float(sc.preds["meta_p"].iloc[-1]),
            "signal_trust": last_trust, "bars_processed": len(new_idx),
            "least_trusted_features": {k: round(float(v), 3) for k, v in top_trust.head(5).items()},
            "most_trusted_features": {k: round(float(v), 3) for k, v in top_trust.tail(5).items()},
            "hurst_returns_96": hurst_rs(np.log(raw["close"]).diff().dropna().to_numpy()[-96:]),
            "sources": {k: v.get("ok") for k, v in health.items()},
        }

    eq = eng.equity()
    now = str(pd.Timestamp.now(tz="UTC"))
    ledger.append("trades", eng.trades)
    ledger.append("signals", eng.signals)
    ledger.append("events", eng.events)
    ledger.append("equity", [{"ts": now, "equity": eq, "cash": eng.state.cash, "gross": eng.gross(),
                              "n_positions": len(eng.state.positions), "kill_active": eng.kill.active,
                              "systemic_trust": systemic, "mode": mode}])
    for ev in eng.events:
        kind, text = format_event(ev)
        send(kind, text)
        maybe_mirror(ev)
    eng.save(state_path(STATE_FILE))
    status.update(equity=eq, cash=eng.state.cash, positions=eng.state.positions, pending=eng.state.pending,
                  kill_switch=eng.kill.to_dict(), systemic_trust=systemic, new_trades=len(eng.trades),
                  new_events=len(eng.events), decisions=eng.signals,
                  model_info={k: m.info for k, m in models.items()})
    state_path("status.json").write_text(json.dumps(status, indent=2, default=str))
    log.info("pass done: equity=%.2f positions=%d events=%d", eq, len(eng.state.positions), len(eng.events))
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description="Mempool Omega PAPER trader (no real funds)")
    ap.add_argument("--once", action="store_true", help="single pass (default)")
    ap.add_argument("--mode", default=None, help="auto | live | synthetic")
    ap.add_argument("--capture", type=float, default=0.0, help="seconds of WebSocket book capture per symbol")
    ap.add_argument("--loop-minutes", type=float, default=0.0, help="keep running passes for N minutes")
    a = ap.parse_args()
    s = get_settings()
    assert not s.live_trading, "live trading is not supported"
    deadline = time.time() + a.loop_minutes * 60
    while True:
        try:
            st = run_once(a.mode, a.capture)
            print(json.dumps({k: st[k] for k in ("run_at", "equity", "systemic_trust", "new_trades", "new_events")},
                             default=str))
        except Exception as e:  # noqa: BLE001
            log.error("paper pass failed: %s", e)
            send("error", f"paper pass failed: {e}\n{traceback.format_exc()[-600:]}")
            raise
        if time.time() + s.bar_minutes * 60 > deadline:
            break
        time.sleep(s.bar_minutes * 60)


if __name__ == "__main__":
    main()
