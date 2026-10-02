"""Data-quality scoring: staleness, gaps, cross-venue sanity, source health."""
from __future__ import annotations

import numpy as np
import pandas as pd

CRITICAL = ("cex_primary",)


def data_quality(raw: pd.DataFrame, health: dict, bar_minutes: int = 5) -> dict:
    out: dict = {}
    if raw.empty:
        return {"score": 0.0, "reason": "empty frame"}
    now = pd.Timestamp.now(tz="UTC")
    stale_min = (now - raw.index[-1]).total_seconds() / 60 - bar_minutes
    synthetic = raw.attrs.get("data_mode") == "synthetic"
    staleness = 1.0 if synthetic else float(np.clip(1 - max(stale_min - 2 * bar_minutes, 0) / 30, 0, 1))
    expected = pd.date_range(raw.index[0], raw.index[-1], freq=f"{bar_minutes}min")
    gaps = 1 - len(raw.index.unique()) / max(len(expected), 1)
    div = []
    for c in ("close_coinbase", "close_okx", "close_kraken"):
        if c in raw and pd.notna(raw[c].iloc[-1]):
            div.append(abs(raw[c].iloc[-1] / raw["close"].iloc[-1] - 1))
    max_div = max(div) if div else 0.0
    venue_ok = float(np.clip(1 - max(max_div - 0.003, 0) / 0.02, 0, 1))
    srcs = [v.get("ok", False) for v in health.values()] or [True]
    src_ok = float(np.mean(srcs))
    critical_ok = all(health.get(k, {"ok": True}).get("ok", False) for k in CRITICAL)
    score = staleness * (1 - min(gaps * 5, 1)) * venue_ok * (0.5 + 0.5 * src_ok) * (1.0 if critical_ok else 0.0)
    out.update(score=float(score), staleness_min=float(stale_min), gap_frac=float(gaps),
               max_venue_divergence=float(max_div), sources_ok=src_ok, critical_ok=critical_ok)
    return out
