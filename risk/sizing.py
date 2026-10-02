"""Position sizing = min(risk-budget size, vol-target size, Kelly size, hard caps)."""
from __future__ import annotations

import numpy as np

from core.config import RiskConfig


def position_notional(equity: float, price: float, stop_distance_frac: float, sigma_bar: float,
                      kelly_frac: float, cfg: RiskConfig, bars_per_year: float = 105_120.0,
                      size_jitter: float = 0.0, rng: np.random.Generator | None = None) -> float:
    """Return notional (quote currency) for a new position.

    * risk budget: lose at most ``risk_per_trade`` of equity if the stop is hit
    * vol targeting: position annualised vol <= vol_target
    * fractional Kelly: cap by kelly_frac of equity
    * hard caps: max_position_frac of equity
    * optional random size jitter (anti-front-running: don't be predictable)
    """
    if equity <= 0 or price <= 0 or stop_distance_frac <= 0 or not np.isfinite(sigma_bar) or sigma_bar <= 0:
        return 0.0
    risk_cap = min(cfg.risk_per_trade, cfg.max_risk_per_trade) * equity / stop_distance_frac
    ann_vol = sigma_bar * np.sqrt(bars_per_year)
    vol_cap = cfg.vol_target_annual / ann_vol * equity
    kelly_cap = kelly_frac * equity * cfg.max_gross_leverage
    hard_cap = cfg.max_position_frac * equity
    n = max(0.0, min(risk_cap, vol_cap, kelly_cap, hard_cap))
    if size_jitter > 0 and n > 0:
        rng = rng or np.random.default_rng()
        n *= float(rng.uniform(1 - size_jitter, 1.0))
    return float(n)
