"""Purged K-fold with embargo and walk-forward splits (no leakage across label horizons)."""
from __future__ import annotations

from collections.abc import Iterator

import numpy as np


def purged_kfold(n: int, k: int = 5, horizon: int = 12, embargo: int = 12) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Contiguous test folds; training drops ``horizon`` bars before each test fold (labels overlap)
    and ``embargo`` bars after it (serial correlation)."""
    bounds = np.linspace(0, n, k + 1).astype(int)
    idx = np.arange(n)
    for i in range(k):
        a, b = bounds[i], bounds[i + 1]
        test = idx[a:b]
        train_mask = np.ones(n, bool)
        train_mask[max(0, a - horizon): min(n, b + embargo)] = False
        yield idx[train_mask], test


def walk_forward(n: int, n_folds: int = 5, min_train: int = 1000, horizon: int = 12,
                 embargo: int = 12, rolling: int | None = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Expanding (or rolling, if ``rolling`` set) train window followed by a test chunk; purged at the boundary."""
    if n <= min_train + horizon + embargo + 10:
        raise ValueError(f"not enough bars for walk-forward: n={n}, min_train={min_train}")
    test_len = (n - min_train) // n_folds
    for i in range(n_folds):
        test_start = min_train + i * test_len
        test_end = n if i == n_folds - 1 else test_start + test_len
        train_end = test_start - horizon - embargo
        train_start = 0 if rolling is None else max(0, train_end - rolling)
        yield np.arange(train_start, train_end), np.arange(test_start, test_end)
