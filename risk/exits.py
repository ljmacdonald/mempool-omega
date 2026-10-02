"""Exit plan attached to every position. Whichever rule fires first wins.

1. profit target  2. stop loss  3. time stop  4. signal decay  5. trust collapse  6. kill switch
(see docs/EXITS.md for the plain-English version)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from core.config import ExitConfig


@dataclass
class ExitPlan:
    take_profit: float
    stop_loss: float
    max_bars: int
    decay_threshold: float
    trust_floor: float

    def to_dict(self) -> dict:
        return asdict(self)


def make_exit_plan(side: int, entry: float, sigma_bar: float, cfg: ExitConfig, trust_floor: float,
                   edge_bps: float | None = None) -> ExitPlan:
    """Volatility-scaled barriers. The profit target is the larger of the barrier and the model's edge."""
    sh = sigma_bar * np.sqrt(cfg.time_stop_bars)
    tp_dist = cfg.pt_mult * sh
    if edge_bps is not None and np.isfinite(edge_bps):
        tp_dist = max(tp_dist, edge_bps / 1e4)
    sl_dist = cfg.sl_mult * sh
    tp = entry * np.exp(side * tp_dist)
    sl = entry * np.exp(-side * sl_dist)
    return ExitPlan(float(tp), float(sl), cfg.time_stop_bars, cfg.decay_threshold, trust_floor)


def check_exit(side: int, plan: ExitPlan, bar: dict, bars_held: int, signal_p_up: float | None,
               trust: float | None, kill: bool) -> tuple[str | None, float | None]:
    """Evaluate one bar. ``bar`` has open/high/low/close. Returns (reason, exit_price) or (None, None).

    Order of precedence within a bar: kill switch, stop loss (conservative: stop before target when
    both are touched), profit target, trust collapse, signal decay, time stop.
    """
    o, h, lo, c = bar["open"], bar["high"], bar["low"], bar["close"]
    if kill:
        return "kill_switch", o
    if side > 0:
        if o <= plan.stop_loss:
            return "stop_loss", o  # gap through stop
        if lo <= plan.stop_loss:
            return "stop_loss", plan.stop_loss
        if o >= plan.take_profit:
            return "profit_target", o
        if h >= plan.take_profit:
            return "profit_target", plan.take_profit
    else:
        if o >= plan.stop_loss:
            return "stop_loss", o
        if h >= plan.stop_loss:
            return "stop_loss", plan.stop_loss
        if o <= plan.take_profit:
            return "profit_target", o
        if lo <= plan.take_profit:
            return "profit_target", plan.take_profit
    if trust is not None and np.isfinite(trust) and trust < plan.trust_floor:
        return "trust_collapse", c
    if signal_p_up is not None and np.isfinite(signal_p_up):
        score = (signal_p_up - 0.5) * side
        if score < plan.decay_threshold:
            return "signal_decay", c
    if bars_held >= plan.max_bars:
        return "time_stop", c
    return None, None
