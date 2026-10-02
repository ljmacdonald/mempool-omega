"""Anomaly & integrity statistics used by the ASI layer.

* Benford's law conformity of trade sizes (wash trading -> round / repeated sizes).
* CUSUM change-point detection.
* Hurst exponent (R/S) - is a series trending (H>0.5) or mean-reverting (H<0.5)?
* Isolation-Forest row anomaly score over the feature vector (data poisoning / spoof bursts).
* Volume-without-impact wash suspicion.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

BENFORD = np.log10(1 + 1 / np.arange(1, 10))


def first_digits(x: np.ndarray) -> np.ndarray:
    x = np.abs(np.asarray(x, dtype=float))
    x = x[(x > 0) & np.isfinite(x)]
    if len(x) == 0:
        return np.array([], dtype=int)
    return (x / 10 ** np.floor(np.log10(x))).astype(int).clip(1, 9)


def benford_mad(values: np.ndarray) -> float:
    d = first_digits(values)
    if len(d) < 50:
        return np.nan
    obs = np.bincount(d, minlength=10)[1:] / len(d)
    return float(np.mean(np.abs(obs - BENFORD)))


def benford_score(values: np.ndarray) -> float:
    """1.0 = conforms to Benford (organic), 0.0 = strongly non-conforming (Nigrini MAD thresholds)."""
    mad = benford_mad(values)
    if np.isnan(mad):
        return 0.7  # not enough data: neutral-ish prior
    return float(np.clip(1 - (mad - 0.006) / (0.022 - 0.006), 0, 1))


def round_size_share(values: np.ndarray, tol: float = 1e-9) -> float:
    """Share of trades on 'round' sizes (multiples of 0.1 for small, of 1 for larger) - wash tell."""
    v = np.asarray(values, dtype=float)
    v = v[v > 0]
    if len(v) == 0:
        return 0.0
    r = np.isclose(np.mod(v * 10, 1), 0, atol=tol) | np.isclose(np.mod(v, 1), 0, atol=tol)
    return float(r.mean())


def cusum_score(x: pd.Series, k: float = 0.5, h: float = 5.0, lookback: int = 12) -> float:
    """Two-sided CUSUM on standardised x; returns 1 if a change point was flagged in the last ``lookback``."""
    x = pd.Series(x).dropna()
    if len(x) < 30:
        return 0.0
    z = (x - x.median()) / (1.4826 * (x - x.median()).abs().median() + 1e-12)
    sp = sn = 0.0
    flagged = -1
    for i, v in enumerate(z.to_numpy()):
        sp = max(0.0, sp + v - k)
        sn = max(0.0, sn - v - k)
        if sp > h or sn > h:
            flagged = i
            sp = sn = 0.0
    return 1.0 if flagged >= len(z) - lookback else 0.0


def hurst_rs(x: np.ndarray, min_chunk: int = 8) -> float:
    """Rescaled-range Hurst exponent of an increments series."""
    x = np.asarray(pd.Series(x).dropna(), dtype=float)
    n = len(x)
    if n < 4 * min_chunk:
        return np.nan
    sizes = np.unique(np.floor(np.logspace(np.log10(min_chunk), np.log10(n // 2), 8)).astype(int))
    rs = []
    for s in sizes:
        k = n // s
        chunks = x[: k * s].reshape(k, s)
        dev = np.cumsum(chunks - chunks.mean(axis=1, keepdims=True), axis=1)
        r = dev.max(axis=1) - dev.min(axis=1)
        sd = chunks.std(axis=1)
        ok = sd > 0
        if ok.any():
            rs.append((s, np.mean(r[ok] / sd[ok])))
    if len(rs) < 3:
        return np.nan
    s, v = np.log([a for a, _ in rs]), np.log([b for _, b in rs])
    return float(np.polyfit(s, v, 1)[0])


def wash_suspicion(volume_z: pd.Series, ret_bps: pd.Series, vol_bps: pd.Series) -> pd.Series:
    """High volume with no price impact -> wash trading suspicion in [0,1]."""
    vz = volume_z.fillna(0)
    impact = (ret_bps.abs() / (vol_bps.abs() + 1e-9)).fillna(1)
    s = 1 / (1 + np.exp(-(vz - 2.5) * 2)) * np.clip(1 - impact, 0, 1)
    return s.clip(0, 1)


class RowAnomalyDetector:
    """IsolationForest over the (median-imputed) feature vector. score in [0,1], 1 = very anomalous."""

    def __init__(self, contamination: float = 0.02, seed: int = 0):
        self.model = IsolationForest(n_estimators=150, contamination=contamination, random_state=seed)
        self.cols: list[str] = []
        self.medians: pd.Series | None = None
        self._lo = self._hi = 0.0

    def fit(self, X: pd.DataFrame) -> RowAnomalyDetector:
        self.cols = [c for c in X.columns if X[c].notna().mean() > 0.5]
        self.medians = X[self.cols].median()
        Xi = X[self.cols].fillna(self.medians).to_numpy()
        self.model.fit(Xi)
        raw = -self.model.score_samples(Xi)
        self._lo, self._hi = np.percentile(raw, 50), np.percentile(raw, 99.5)
        return self

    def score(self, X: pd.DataFrame) -> pd.Series:
        if not self.cols:
            return pd.Series(0.0, index=X.index)
        Xi = X.reindex(columns=self.cols).fillna(self.medians).to_numpy()
        raw = -self.model.score_samples(Xi)
        return pd.Series(np.clip((raw - self._lo) / (self._hi - self._lo + 1e-12), 0, 1), index=X.index)
