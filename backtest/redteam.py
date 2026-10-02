"""Red-team evaluation: does ASI reduce the damage of manipulation?

For each attack in ``asi.redteam.ATTACKS``:
  * train on CLEAN history (walk-forward), test on ATTACKED data (attacks only in the test region)
  * replay a NAIVE strategy (no trust floor / premium) and the ASI-gated strategy
  * report bait trades taken (entries within 2 bars after an attack) and their P&L
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

from asi.redteam import ATTACKS, apply_attack
from backtest.engine import oos_predictions, replay
from backtest.metrics import summarize
from core.config import Settings, get_settings, state_path
from core.log import get_logger
from core.synthetic import SyntheticSpec, generate_market

log = get_logger("redteam")


def _bait_stats(signals: pd.DataFrame, trades: pd.DataFrame, attacked: pd.DataFrame) -> dict:
    """Bait decision = a trade TAKEN on an attack bar in the bait direction.
    Bait P&L = net P&L of the resulting positions (filled on the next bar)."""
    baits = {attacked.index[t]: d for t, d in attacked.attrs.get("baits", [])}
    if signals.empty or not baits:
        return {"bait_decisions": 0, "bait_follow_rate": 0.0, "bait_pnl": 0.0}
    sig = signals.assign(ts=pd.to_datetime(signals["ts"], utc=True))
    on_attack = sig[sig["ts"].isin(list(baits))]
    taken = on_attack[on_attack["taken"]]
    follow = taken[taken["side"] == taken["ts"].map(baits)]
    pnl = 0.0
    if len(trades) and len(follow):
        nxt = {attacked.index[attacked.index.get_loc(t) + 1]: baits[t] for t in follow["ts"]
               if attacked.index.get_loc(t) + 1 < len(attacked)}
        et = pd.to_datetime(trades["entry_ts"], utc=True)
        m = [nxt.get(e) == sd for e, sd in zip(et, trades["side"])]
        pnl = float(trades.loc[m, "net_pnl"].sum())
    return {"bait_decisions": int(len(follow)), "bait_follow_rate": float(len(follow) / max(len(on_attack), 1)),
            "bait_pnl": pnl}


def run_redteam(raw: pd.DataFrame | None = None, s: Settings | None = None, attacks: list[str] | None = None,
                rate: float = 0.03, n_folds: int = 3, symbol: str = "BTCUSDT") -> dict:
    s = s or get_settings()
    raw = raw if raw is not None else generate_market(SyntheticSpec(n_bars=6000, seed=s.seed), symbol)
    results = {}
    start_frac = 1 / 3
    for name in attacks or list(ATTACKS):
        attacked, pos = apply_attack(raw, name, rate=rate, seed=s.seed, start_frac=start_frac)
        row = {}
        for use_asi in (False, True):
            oos = oos_predictions(raw, s, n_folds=n_folds, use_asi=use_asi, test_raw=attacked)
            eng, curve = replay({symbol: oos}, s, use_asi=use_asi, seed=s.seed)
            trades = pd.DataFrame(eng.trades)
            m = summarize(curve, trades, s.bar_minutes)
            row["asi" if use_asi else "naive"] = {
                "total_return": m.get("total_return", 0.0), "max_drawdown": m.get("max_drawdown", 0.0),
                "n_trades": m.get("n_trades", 0), **_bait_stats(pd.DataFrame(eng.signals), trades, attacked)}
        n, a = row["naive"], row["asi"]
        row["n_attacks"] = len(pos)
        row["bait_decisions_avoided"] = n["bait_decisions"] - a["bait_decisions"]
        row["bait_loss_avoided"] = a["bait_pnl"] - n["bait_pnl"]
        results[name] = row
        log.info("%-22s naive bait=%d pnl=%.0f ret=%.2f%% | asi bait=%d pnl=%.0f ret=%.2f%%", name,
                 n["bait_decisions"], n["bait_pnl"], 100 * n["total_return"], a["bait_decisions"], a["bait_pnl"],
                 100 * a["total_return"])
    return {"data_mode": raw.attrs.get("data_mode", "unknown"), "rate": rate, "attacks": results}


def main() -> None:
    ap = argparse.ArgumentParser(description="Adversarial red-team back-test")
    ap.add_argument("--rate", type=float, default=0.05)
    ap.add_argument("--attacks", default="")
    a = ap.parse_args()
    rep = run_redteam(attacks=[x for x in a.attacks.split(",") if x] or None, rate=a.rate)
    state_path("reports", "redteam_latest.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps(rep, indent=2, default=str))


if __name__ == "__main__":
    main()
