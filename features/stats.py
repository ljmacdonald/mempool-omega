"""Small, robust statistics helpers shared across features and ASI."""
from __future__ import annotations

import numpy as np
import pandas as pd


def robust_z(s: pd.Series, window: int = 288, min_periods: int = 30, clip: float = 8.0) -> pd.Series:
    """Rolling median/MAD z-score (resistant to single spoofed outliers)."""
    med = s.rolling(window, min_periods=min_periods).median()
    mad = (s - med).abs().rolling(window, min_periods=min_periods).median()
    z = (s - med) / (1.4826 * mad.replace(0, np.nan))
    return z.clip(-clip, clip)


def safe_div(a, b, eps: float = 1e-12):
    return a / (np.abs(b) + eps) * np.sign(b + eps)


def log_ret(s: pd.Series, n: int = 1) -> pd.Series:
    return np.log(s).diff(n)
