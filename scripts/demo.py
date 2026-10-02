"""Offline end-to-end demo on SYNTHETIC data (no internet, no secrets).

  python -m scripts.demo            # full demo (~3-5 min), charts -> docs/screenshots/
  python -m scripts.demo --quick    # CI smoke test (~1 min)

Writes everything to .demo_state/ (unless OMEGA_STATE_DIR is set) so the real
paper-trading ledger in state/ is never touched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OMEGA_STATE_DIR", str(ROOT / ".demo_state"))
os.environ["OMEGA_DATA_MODE"] = "synthetic"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-charts", action="store_true")
    a = ap.parse_args()

    import warnings

    warnings.filterwarnings("ignore")
    from backtest.engine import run_backtest, save_report
    from backtest.redteam import run_redteam
    from core.config import get_settings, state_path
    from core.synthetic import SyntheticSpec, generate_market
    from omega.train import train_all
    from paper.trader import run_once

    s = get_settings()
    n = 3200 if a.quick else 6000
    print("== 1/4 train on synthetic history")
    train_all(mode="synthetic", days=7 if a.quick else 14)
    print("== 2/4 walk-forward backtest (with and without ASI)")
    frames = {sym: generate_market(SyntheticSpec(n_bars=n, seed=s.seed), sym) for sym in s.symbols}
    bt = run_backtest(frames, s, n_folds=2 if a.quick else 4, use_asi=True)
    save_report(bt, "backtest_latest")
    naive = run_backtest(frames, s, n_folds=2 if a.quick else 4, use_asi=False, label="naive")
    save_report(naive, "backtest_naive")
    print("== 3/4 red-team attacks")
    attacks = ["spoofing", "wash_trading"] if a.quick else None
    rt = run_redteam(generate_market(SyntheticSpec(n_bars=n, seed=s.seed + 1)), s, attacks=attacks,
                     n_folds=2 if a.quick else 3)
    state_path("reports", "redteam_latest.json").write_text(json.dumps(rt, indent=2, default=str))
    print("== 4/4 paper-trading pass (synthetic live clock)")
    st = run_once(mode="synthetic")

    keys = ("total_return", "sharpe", "max_drawdown", "n_trades", "hit_rate", "profit_factor")
    summary = {"ASI": {k: bt.get(k) for k in keys}, "naive": {k: naive.get(k) for k in keys},
               "redteam": {k: {"naive_bait": v["naive"]["bait_decisions"], "asi_bait": v["asi"]["bait_decisions"],
                               "naive_ret": v["naive"]["total_return"], "asi_ret": v["asi"]["total_return"]}
                           for k, v in rt["attacks"].items()},
               "paper_equity": st["equity"], "note": "SYNTHETIC data with planted alpha - not evidence of real edge"}
    print(json.dumps(summary, indent=2, default=str))
    state_path("reports", "demo_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    if not a.no_charts:
        charts(bt, naive, rt)
    sys.stdout.flush()


def charts(bt: dict, naive: dict, rt: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    out = ROOT / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    ink, muted, grid = "#1f2937", "#6b7280", "#e5e7eb"
    c_asi, c_naive = "#2563eb", "#9ca3af"
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": grid, "axes.labelcolor": muted, "xtick.color": muted,
                         "ytick.color": muted, "axes.spines.top": False, "axes.spines.right": False})

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(bt["_curve"].index, bt["_curve"].values, color=c_asi, lw=1.6, label="Omega + ASI")
    ax.plot(naive["_curve"].index, naive["_curve"].values, color=c_naive, lw=1.2, label="Omega, no ASI")
    ax.set_title("Walk-forward paper equity (synthetic data)", color=ink, loc="left")
    ax.grid(axis="y", color=grid, lw=0.6)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "equity_curve.png", dpi=130)
    plt.close(fig)

    rows = pd.DataFrame({k: {"No ASI": v["naive"]["bait_decisions"], "With ASI": v["asi"]["bait_decisions"]}
                         for k, v in rt["attacks"].items()}).T
    fig, ax = plt.subplots(figsize=(9, 4))
    x = range(len(rows))
    ax.bar([i - 0.2 for i in x], rows["No ASI"], width=0.4, color=c_naive, label="No ASI")
    ax.bar([i + 0.2 for i in x], rows["With ASI"], width=0.4, color=c_asi, label="With ASI")
    ax.set_xticks(list(x), [r.replace("_", " ") for r in rows.index], rotation=15)
    ax.set_ylabel("bait trades taken")
    ax.set_title("Red team: trades taken in the bait direction on attack bars", color=ink, loc="left")
    ax.grid(axis="y", color=grid, lw=0.6)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "redteam.png", dpi=130)
    plt.close(fig)

    tr = bt["_trades"]
    if len(tr):
        fig, ax = plt.subplots(figsize=(7, 3.5))
        vc = tr["exit_reason"].value_counts()
        ax.barh(vc.index[::-1], vc.values[::-1], color=c_asi)
        ax.set_title("Which exit rule closed each trade", color=ink, loc="left")
        ax.grid(axis="x", color=grid, lw=0.6)
        fig.tight_layout()
        fig.savefig(out / "exit_reasons.png", dpi=130)
        plt.close(fig)
    print(f"charts written to {out}")


if __name__ == "__main__":
    main()
