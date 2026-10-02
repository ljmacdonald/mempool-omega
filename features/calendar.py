"""Signal 10: time & event alpha (funding timestamps, unlocks, macro calendar)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

CAL_PATH = Path(__file__).with_name("calendar_events.yaml")


@lru_cache(maxsize=1)
def load_events(path: str | None = None) -> pd.DataFrame:
    p = Path(path) if path else CAL_PATH
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except OSError:
        data = {}
    ev = pd.DataFrame(data.get("events", []))
    if ev.empty:
        return pd.DataFrame(columns=["name", "ts", "impact", "kind"])
    ev["ts"] = pd.to_datetime(ev["ts"], utc=True)
    return ev.sort_values("ts").reset_index(drop=True)


def minutes_to_funding(idx: pd.DatetimeIndex, period_h: int = 8) -> np.ndarray:
    mod = (idx.hour * 60 + idx.minute) % (period_h * 60)
    return ((period_h * 60 - mod) % (period_h * 60)).to_numpy(dtype=float)


def event_proximity(idx: pd.DatetimeIndex, events: pd.DataFrame | None = None, scale_h: float = 6.0) -> np.ndarray:
    """max_e impact_e * exp(-|t - t_e| / scale) using only scheduled (known-in-advance) events."""
    events = load_events() if events is None else events
    out = np.zeros(len(idx))
    if events.empty:
        return out
    t = idx.asi8 / 3.6e12  # hours
    for _, e in events.iterrows():
        dt = np.abs(t - e["ts"].value / 3.6e12)
        out = np.maximum(out, float(e["impact"]) * np.exp(-dt / scale_h))
    return out
