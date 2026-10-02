"""Kalman filter for a time-varying linear relation  y_t = alpha_t + beta_t * x_t + e_t.

Used for (a) cross-venue fair-value residuals (Binance vs Coinbase) and
(b) dynamic cross-asset hedge ratios (e.g. ETH beta to BTC) in risk.
State follows a random walk with process noise ``delta``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class KalmanBeta:
    def __init__(self, delta: float = 1e-5, obs_var: float = 1e-3):
        self.delta = delta
        self.R = obs_var
        self.theta = np.zeros(2)
        self.P = np.eye(2)
        self.Q = delta / (1 - delta) * np.eye(2)

    def update(self, x: float, y: float) -> tuple[float, float, float]:
        """Returns (alpha, beta, standardised innovation)."""
        H = np.array([1.0, x])
        self.P = self.P + self.Q
        y_hat = H @ self.theta
        S = H @ self.P @ H + self.R
        K = self.P @ H / S
        err = y - y_hat
        self.theta = self.theta + K * err
        self.P = self.P - np.outer(K, H) @ self.P
        # adapt observation noise slowly (robust to regime shifts)
        self.R = 0.99 * self.R + 0.01 * min(err**2, 25 * self.R)
        return float(self.theta[0]), float(self.theta[1]), float(err / np.sqrt(S))


def kalman_filter(y: pd.Series, x: pd.Series, delta: float = 1e-5) -> pd.DataFrame:
    # initial noise from the first 100 points only (using the whole series would leak the future)
    init = float(np.nanvar(y.diff().iloc[:100])) if len(y) > 2 else 0.0
    kf = KalmanBeta(delta=delta, obs_var=init if np.isfinite(init) and init > 0 else 1e-6)
    rows = []
    for xv, yv in zip(x.to_numpy(), y.to_numpy()):
        if np.isnan(xv) or np.isnan(yv):
            rows.append((np.nan, np.nan, np.nan))
            continue
        rows.append(kf.update(xv, yv))
    return pd.DataFrame(rows, index=y.index, columns=["alpha", "beta", "innov_z"])


def hedge_beta(asset_ret: pd.Series, bench_ret: pd.Series, delta: float = 1e-4) -> float:
    """Latest dynamic beta of asset returns to benchmark returns."""
    df = kalman_filter(asset_ret.fillna(0), bench_ret.fillna(0), delta=delta)
    b = df["beta"].dropna()
    return float(b.iloc[-1]) if len(b) else 1.0
