"""Omega Core model: LightGBM primary (direction) + LightGBM meta-labeler (take / skip).

Training recipe (all without look-ahead):
 1. Triple-barrier labels with symmetric barriers -> primary target (up first?).
 2. Purged K-fold -> OUT-OF-SAMPLE primary probabilities for every training row.
 3. Meta labels: would taking side=sign(p-0.5) with the LIVE exit plan have been
    profitable after round-trip costs?  Meta model learns when to trust the primary.
 4. Calibrate win/loss sizes (in sigma units) for the expected-edge formula.
 5. Data-poisoning defence: rows flagged anomalous by IsolationForest are down-weighted.

Models are stored as LightGBM *text* files + JSON (no pickle) under state/models/.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from asi.anomaly import RowAnomalyDetector
from asi.trust import TrustContext, compute_trust, signal_trust, trust_gate
from backtest.cv import purged_kfold
from core.config import ExitConfig, Settings
from core.log import get_logger
from features.engine import FEATURE_NAMES
from omega.cost import CostModel
from omega.labels import triple_barrier

log = get_logger("omega.model")

PRIMARY_PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=80,
                      feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
                      verbose=-1, deterministic=True, force_row_wise=True, num_threads=2)
META_PARAMS = dict(PRIMARY_PARAMS, num_leaves=7, min_data_in_leaf=60, learning_rate=0.05)
META_CONTEXT = ["rv_ratio", "spread_bps", "hawkes_liq_long", "hawkes_liq_short", "funding_proximity",
                "volume_z", "event_proximity", "hour_sin", "hour_cos", "ret_12"]


@dataclass
class Calibration:
    win_sigma: float = 1.0     # mean winning return, in sigma_h units
    loss_sigma: float = 1.0    # mean losing |return|, in sigma_h units
    base_rate: float = 0.5
    min_conf: float = 0.02


@dataclass
class OmegaModel:
    primary: lgb.Booster | None = None
    meta: lgb.Booster | None = None
    features: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    calib: Calibration = field(default_factory=Calibration)
    horizon: int = 12
    info: dict = field(default_factory=dict)
    last_contrib: pd.DataFrame | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def sigma_h(sigma_bar: pd.Series, horizon: int) -> pd.Series:
        return sigma_bar * np.sqrt(horizon)

    def meta_frame(self, X: pd.DataFrame, p: np.ndarray, strust: pd.Series) -> pd.DataFrame:
        m = X.reindex(columns=META_CONTEXT).copy()
        m["p_up"] = p
        m["conf"] = np.abs(p - 0.5)
        m["side"] = np.sign(p - 0.5)
        # NB: trust is deliberately NOT a meta feature - ASI stays an independent, auditable layer.
        return m

    # ------------------------------------------------------------------ training
    def fit(self, X: pd.DataFrame, raw: pd.DataFrame, sigma_bar: pd.Series, settings: Settings,
            n_rounds: int = 250, k: int = 5) -> OmegaModel:
        ex: ExitConfig = settings.exits
        H = self.horizon = ex.time_stop_bars
        X = X[self.features]
        lab = triple_barrier(raw, sigma_bar, H, pt=1.0, sl=1.0)
        y = lab["label_long"]
        ok = y.notna() & sigma_bar.notna()
        Xo, yo, rawo, sig = X[ok], y[ok].astype(int), raw[ok], sigma_bar[ok]
        n = len(Xo)
        if n < 300:
            raise ValueError(f"too few labelled rows to train: {n}")

        det = RowAnomalyDetector(seed=settings.seed).fit(Xo)
        anom = det.score(Xo)
        w = (1 - 0.8 * anom).clip(0.1, 1).to_numpy()

        # 2) purged OOS primary predictions
        oos = np.full(n, np.nan)
        contrib = np.zeros((n, len(self.features) + 1))
        for tr, te in purged_kfold(n, k=k, horizon=H, embargo=H):
            ds = lgb.Dataset(Xo.iloc[tr], yo.iloc[tr], weight=w[tr], free_raw_data=False)
            bst = lgb.train({**PRIMARY_PARAMS, "seed": settings.seed}, ds, num_boost_round=n_rounds)
            contrib[te] = bst.predict(Xo.iloc[te], pred_contrib=True)
        trust, _ = compute_trust(Xo, TrustContext(row_anomaly=anom))
        oos, strust = self._trust_adjust(contrib, trust)
        auc = _auc(yo.to_numpy(), oos)

        # 3) meta labels with the live exit plan, net of costs
        side = np.sign(oos - 0.5)
        side[np.abs(oos - 0.5) < self.calib.min_conf] = 0
        tb = triple_barrier(rawo, sig, H, pt=ex.pt_mult, sl=ex.sl_mult, side=side)
        cost_bps = CostModel(settings.costs).expected_round_trip_bps()
        net = tb["ret"] * 1e4 - cost_bps
        mf = self.meta_frame(Xo, oos, strust)
        mmask = (side != 0) & net.notna().to_numpy()
        ym = (net[mmask] > 0).astype(int)
        if mmask.sum() < 100 or ym.nunique() < 2:
            raise ValueError("too few meta-label rows")
        self.meta = lgb.train({**META_PARAMS, "seed": settings.seed},
                              lgb.Dataset(mf[mmask], ym, weight=w[mmask]), num_boost_round=150)

        # 4) calibration in sigma units (gross of cost)
        sh = (self.sigma_h(sig, H) * 1e4)[mmask]
        r = (tb["ret"] * 1e4)[mmask]
        wins, losses = (r[r > 0] / sh[r > 0]), (-r[r <= 0] / sh[r <= 0])
        self.calib = Calibration(win_sigma=float(wins.mean()) if len(wins) else 1.0,
                                 loss_sigma=float(losses.mean()) if len(losses) else 1.0,
                                 base_rate=float(ym.mean()), min_conf=self.calib.min_conf)

        # 5) final primary on everything
        self.primary = lgb.train({**PRIMARY_PARAMS, "seed": settings.seed},
                                 lgb.Dataset(Xo, yo, weight=w), num_boost_round=n_rounds)
        imp = pd.Series(self.primary.feature_importance("gain"), index=self.features).sort_values(ascending=False)
        self.info = {
            "n_train": int(n), "oos_auc": auc, "meta_rows": int(mmask.sum()), "meta_base_rate": float(ym.mean()),
            "cost_bps_round_trip": cost_bps, "train_start": str(Xo.index[0]), "train_end": str(Xo.index[-1]),
            "data_mode": raw.attrs.get("data_mode", "unknown"),
            "top_features": {k_: round(float(v), 2) for k_, v in imp.head(12).items()},
        }
        log.info("trained: n=%d oos_auc=%.3f meta_rows=%d", n, auc, mmask.sum())
        return self

    # ------------------------------------------------------------------ trust-weighted inference
    def _trust_adjust(self, contrib_full: np.ndarray, trust: pd.DataFrame) -> tuple[np.ndarray, pd.Series]:
        """ASI defence #1: scale each feature's SHAP contribution by its trust gate, then re-sigmoid.
        With all trusts high this equals the raw model output exactly."""
        trust = trust.reindex(columns=self.features)
        c = pd.DataFrame(contrib_full[:, :-1], index=trust.index, columns=self.features)
        bias = contrib_full[:, -1]
        logit = bias + (c * trust_gate(trust)).sum(axis=1).to_numpy()
        p = 1 / (1 + np.exp(-logit))
        st = signal_trust(trust, c, pd.Series(np.sign(p - 0.5), index=trust.index))
        self.last_contrib = c
        return p, st

    # ------------------------------------------------------------------ inference
    def predict(self, X: pd.DataFrame, trust: pd.DataFrame) -> pd.DataFrame:
        if self.primary is None or self.meta is None:
            raise RuntimeError("model not trained/loaded")
        X = X.reindex(columns=self.features)
        p, st = self._trust_adjust(self.primary.predict(X, pred_contrib=True), trust)
        mp = self.meta.predict(self.meta_frame(X, p, st))
        out = pd.DataFrame({"p_up": p, "side": np.sign(p - 0.5), "meta_p": mp, "signal_trust": st}, index=X.index)
        out.loc[np.abs(p - 0.5) < self.calib.min_conf, "side"] = 0
        return out

    # ------------------------------------------------------------------ persistence
    def save(self, directory: Path, tag: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        self.primary.save_model(str(directory / f"{tag}_primary.txt"))
        self.meta.save_model(str(directory / f"{tag}_meta.txt"))
        meta = {"features": self.features, "calib": asdict(self.calib), "horizon": self.horizon, "info": self.info}
        p = directory / f"{tag}_model.json"
        p.write_text(json.dumps(meta, indent=2, default=str))
        return p

    @classmethod
    def load(cls, directory: Path, tag: str) -> OmegaModel:
        meta = json.loads((directory / f"{tag}_model.json").read_text())
        return cls(primary=lgb.Booster(model_file=str(directory / f"{tag}_primary.txt")),
                   meta=lgb.Booster(model_file=str(directory / f"{tag}_meta.txt")),
                   features=meta["features"], calib=Calibration(**meta["calib"]), horizon=meta["horizon"],
                   info=meta.get("info", {}))


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score

    m = np.isfinite(p)
    if m.sum() < 10 or len(np.unique(y[m])) < 2:
        return float("nan")
    return float(roc_auc_score(y[m], p[m]))
