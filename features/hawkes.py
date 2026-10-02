"""Self-exciting (Hawkes) intensity for liquidation cascades.

lambda(t) = mu + sum_i alpha * exp(-beta * (t - t_i))

Two forms:
* ``intensity_from_counts`` - discrete-time recursion on per-bar event counts
  (used as a feature; O(n)).
* ``fit_hawkes`` - Poisson MLE of (mu, alpha, beta) on per-bar counts with a
  stationarity constraint (branching ratio alpha/(1-e^-beta)... kept < 1).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize


@dataclass
class HawkesParams:
    mu: float = 0.1
    alpha: float = 0.5
    beta: float = 1.0

    @property
    def branching_ratio(self) -> float:
        # Discrete-time: expected children per event = alpha * sum_k e^{-beta k}, k>=0
        return self.alpha / (1 - np.exp(-self.beta))


def intensity_from_counts(counts: np.ndarray, p: HawkesParams) -> np.ndarray:
    """Predictable intensity for bar t using events strictly before t."""
    counts = np.nan_to_num(np.asarray(counts, dtype=float))
    lam = np.empty_like(counts)
    excess = 0.0
    decay = np.exp(-p.beta)
    for t in range(len(counts)):
        lam[t] = p.mu + excess
        excess = excess * decay + p.alpha * counts[t]
    return lam


def intensity_at(event_times: np.ndarray, t: float, p: HawkesParams) -> float:
    """Continuous-time intensity at time t given past event times (same units as beta^-1)."""
    past = event_times[event_times < t]
    return float(p.mu + p.alpha * np.exp(-p.beta * (t - past)).sum())


def _nll(theta: np.ndarray, counts: np.ndarray) -> float:
    mu, alpha, beta = np.exp(theta)
    lam = intensity_from_counts(counts, HawkesParams(mu, alpha, beta))
    lam = np.maximum(lam, 1e-9)
    nll = float(np.sum(lam - counts * np.log(lam)))
    br = alpha / (1 - np.exp(-beta))
    if br >= 0.98:
        nll += 1e6 * (br - 0.98 + 1e-3)
    return nll


def fit_hawkes(counts: np.ndarray, init: HawkesParams | None = None) -> HawkesParams:
    counts = np.nan_to_num(np.asarray(counts, dtype=float))
    if counts.sum() < 5:
        return HawkesParams(mu=max(counts.mean(), 1e-3), alpha=0.0 + 1e-6, beta=1.0)
    init = init or HawkesParams(mu=max(counts.mean() * 0.5, 1e-3), alpha=0.3, beta=1.0)
    x0 = np.log([init.mu, init.alpha, init.beta])
    res = minimize(_nll, x0, args=(counts,), method="Nelder-Mead", options={"maxiter": 400, "xatol": 1e-4})
    mu, alpha, beta = np.exp(res.x)
    return HawkesParams(float(mu), float(alpha), float(beta))
