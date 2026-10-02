"""Performance metrics (computed on the per-bar equity curve and trade list)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(equity: pd.Series, trades: pd.DataFrame, bar_minutes: int = 5) -> dict:
    if equity.empty:
        return {}
    r = equity.pct_change().fillna(0)
    bpy = 365 * 24 * 60 / bar_minutes
    sd = r.std()
    dd = equity / equity.cummax() - 1
    downside = r[r < 0].std()
    out = {
        "start": str(equity.index[0]), "end": str(equity.index[-1]),
        "final_equity": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1),
        "sharpe": float(r.mean() / sd * np.sqrt(bpy)) if sd > 0 else 0.0,
        "sortino": float(r.mean() / downside * np.sqrt(bpy)) if downside and downside > 0 else 0.0,
        "max_drawdown": float(dd.min()),
        "n_trades": int(len(trades)),
    }
    if len(trades):
        pnl = trades["net_pnl"]
        out.update({
            "hit_rate": float((pnl > 0).mean()),
            "profit_factor": float(pnl[pnl > 0].sum() / max(-pnl[pnl < 0].sum(), 1e-9)),
            "avg_trade_bps": float(trades["ret_bps"].mean()),
            "total_fees": float(trades["fees"].sum()),
            "maker_share": float(trades["maker_entry"].mean()),
            "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
            "avg_bars_held": float(trades["bars_held"].mean()),
        })
    return out
