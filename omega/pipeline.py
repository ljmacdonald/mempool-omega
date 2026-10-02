"""Glue: raw frame -> features -> sigma -> trust -> model predictions (shared by train/backtest/paper)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from asi.anomaly import RowAnomalyDetector, benford_score
from asi.trust import TrustContext, compute_trust
from asi.wallets import graph_cleanliness
from features.engine import build_features, realized_vol
from features.hawkes import HawkesParams
from omega.model import OmegaModel


@dataclass
class Scored:
    X: pd.DataFrame
    sigma: pd.Series
    trust: pd.DataFrame
    preds: pd.DataFrame
    bar_notional: pd.Series


def prepare(raw: pd.DataFrame, hawkes: HawkesParams | None = None) -> tuple[pd.DataFrame, pd.Series]:
    return build_features(raw, hawkes), realized_vol(raw)


def bar_notional(raw: pd.DataFrame) -> pd.Series:
    return (raw["volume"] * raw["close"]).rolling(96, min_periods=1).median().fillna(1e6)


def score(model: OmegaModel, raw: pd.DataFrame, X: pd.DataFrame | None = None, sigma: pd.Series | None = None,
          detector: RowAnomalyDetector | None = None, trade_sizes: np.ndarray | None = None,
          transfers: pd.DataFrame | None = None, use_asi: bool = True) -> Scored:
    if X is None or sigma is None:
        X, sigma = prepare(raw)
    if use_asi:
        if detector is None:
            detector = RowAnomalyDetector().fit(X.iloc[:-1] if len(X) > 50 else X)
        ctx = TrustContext(row_anomaly=detector.score(X),
                           benford=benford_score(trade_sizes) if trade_sizes is not None else 0.8,
                           graph_cleanliness=graph_cleanliness(transfers))
        trust, _ = compute_trust(X, ctx, raw=raw)
    else:
        trust = pd.DataFrame(1.0, index=X.index, columns=X.columns)
    preds = model.predict(X, trust)
    if not use_asi:
        preds["signal_trust"] = 1.0
    return Scored(X, sigma, trust, preds, bar_notional(raw))
