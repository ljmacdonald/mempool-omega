"""Pooled (all coins together) LightGBM that estimates the chance a BUY idea hits its take-profit
before its safety exit within 24 hours.

Idea definition (same for training, ranking and the scoreboard):
  entry  = next hour's open
  target = entry * (1 + 2 * risk_unit)      (reward)
  stop   = entry * (1 - 1 * risk_unit)      (risk)   -> 2:1 reward-to-risk
  time   = give up after 24 hours
  risk_unit = hourly volatility * sqrt(24), clipped to [1.5 %, 12 %]
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from core.log import get_logger
from scanner.features import FEATURES, coin_features, hourly_sigma

log = get_logger("scanner.model")
HORIZON = 24
PT, SL = 2.0, 1.0
COST = 0.002            # 0.1 % fee each way (spot taker)
MIN_RISK, MAX_RISK = 0.015, 0.12
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=2.0, verbose=-1, deterministic=True,
              force_row_wise=True, num_threads=2, seed=7)


def risk_unit(df: pd.DataFrame) -> pd.Series:
    return (hourly_sigma(df) * np.sqrt(HORIZON)).clip(MIN_RISK, MAX_RISK)


def _sim(o, h, lo, c, t: int, ru: float) -> tuple[float, str, int] | None:
    if t + 1 >= len(o) or not np.isfinite(ru):
        return None
    entry = o[t + 1]
    tgt, stp = entry * (1 + PT * ru), entry * (1 - SL * ru)
    end = t + HORIZON
    if end >= len(o):
        return None
    for k in range(t + 1, end + 1):
        if lo[k] <= stp:                       # stop first if both touched in one hour (pessimistic)
            px = min(stp, o[k]) if k > t + 1 else stp   # a gap down through the stop fills at the open
            return px / entry - 1 - COST, "safety_exit", k - t
        if h[k] >= tgt:
            return tgt / entry - 1 - COST, "take_profit", k - t
    return c[end] / entry - 1 - COST, "time_limit", HORIZON


def simulate_idea(df: pd.DataFrame, t: int, ru: float) -> tuple[float, str, int] | None:
    """Outcome of a BUY idea created at the close of bar t: (net return, outcome, hours held) or None if
    the 24 hours have not fully played out yet and neither exit was hit."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    res = _sim(o, h, lo, c, t, ru)
    if res is not None:
        return res
    # not enough future bars for a full 24h: resolve early only if an exit already fired
    if t + 1 >= len(o):
        return None
    entry = o[t + 1]
    tgt, stp = entry * (1 + PT * ru), entry * (1 - SL * ru)
    for k in range(t + 1, len(o)):
        if lo[k] <= stp:
            px = min(stp, o[k]) if k > t + 1 else stp
            return px / entry - 1 - COST, "safety_exit", k - t
        if h[k] >= tgt:
            return tgt / entry - 1 - COST, "take_profit", k - t
    return None


def build_dataset(candles: dict[str, pd.DataFrame], btc_symbol: str = "BTCUSDT") -> pd.DataFrame:
    btc = candles.get(btc_symbol)
    rows = []
    for sym, df in candles.items():
        f = coin_features(df, btc)
        ru = risk_unit(df).to_numpy()
        rets = np.full(len(df), np.nan)
        hit = np.full(len(df), np.nan)
        arrs = [df[k].to_numpy() for k in ("open", "high", "low", "close")]
        for t in range(200, len(df) - HORIZON - 1):  # need 7d warm-up
            out = _sim(*arrs, t, ru[t])
            if out:
                rets[t] = out[0]
                hit[t] = float(out[1] == "take_profit")
        f = f.assign(symbol=sym, risk_unit=ru, net_ret=rets, hit_target=hit)
        rows.append(f.iloc[200:])
    data = pd.concat(rows).dropna(subset=["net_ret"])
    # label = the idea ended in profit after fees (via take-profit, or still up at the 24 h time limit)
    data["win"] = (data["net_ret"] > 0).astype(int)
    return data


@dataclass
class ScannerModel:
    booster: lgb.Booster | None = None
    avg_win: float = 0.03        # mean net return of winners (fraction)
    avg_loss: float = 0.02       # mean |net return| of losers
    base_rate: float = 0.4
    win_r: float = 1.0          # mean winning result in units of the amount risked (stop distance)
    loss_r: float = 1.0         # mean losing result in the same units
    info: dict = field(default_factory=dict)

    def fit(self, data: pd.DataFrame, n_rounds: int = 300, folds: int = 4) -> ScannerModel:
        data = data.sort_index()
        ts = data.index.unique().sort_values()
        bounds = np.linspace(0, len(ts), folds + 1).astype(int)
        oos = pd.Series(np.nan, index=range(len(data)))
        pos_ts = data.index
        for i in range(1, folds):  # walk-forward: train on past blocks only, purge 24h before test
            test_start, test_end = ts[bounds[i]], ts[min(bounds[i + 1], len(ts) - 1)]
            tr = pos_ts < test_start - pd.Timedelta(hours=HORIZON)
            te = (pos_ts >= test_start) & (pos_ts <= test_end)
            if tr.sum() < 1000 or te.sum() == 0:
                continue
            b = lgb.train(PARAMS, lgb.Dataset(data.loc[tr, FEATURES], data.loc[tr, "win"]), n_rounds)
            oos[np.where(te)[0]] = b.predict(data.loc[te, FEATURES])
        m = oos.notna().to_numpy()
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(data["win"].to_numpy()[m], oos[m])) if m.sum() > 100 else float("nan")
        # how good were the top-ranked OOS ideas vs all ideas? (honest usefulness check)
        oos_df = data.loc[m].assign(p=oos[m].to_numpy(), profit=lambda x: (x["net_ret"] > 0).astype(int))
        top = oos_df.groupby(oos_df.index).apply(lambda g: g.nlargest(5, "p")).reset_index(drop=True)
        self.booster = lgb.train(PARAMS, lgb.Dataset(data[FEATURES], data["win"]), n_rounds)
        w, lz = data.loc[data.net_ret > 0, "net_ret"], -data.loc[data.net_ret <= 0, "net_ret"]
        self.avg_win, self.avg_loss = float(w.mean()), float(lz.mean())
        self.base_rate = float(data["win"].mean())
        r = data["net_ret"] / (SL * data["risk_unit"])
        self.win_r, self.loss_r = float(r[r > 0].mean()), float(-r[r <= 0].mean())
        self.info = {
            "trained_rows": int(len(data)), "coins": int(data["symbol"].nunique()), "oos_auc": auc,
            "all_ideas_target_rate": float(oos_df["hit_target"].mean()),
            "top5_target_rate": float(top["hit_target"].mean()),
            "all_ideas_win_rate": float(oos_df["profit"].mean()), "all_ideas_avg_net_ret": float(oos_df["net_ret"].mean()),
            "top5_win_rate": float(top["profit"].mean()), "top5_avg_net_ret": float(top["net_ret"].mean()),
            "start": str(data.index.min()), "end": str(data.index.max()),
            "caveat": "Coins are chosen by TODAY's trading volume, which favours coins that recently rose "
                      "(survivorship bias), so these past-data numbers look better than reality. "
                      "Judge the scanner by the live track record instead.",
        }
        log.info("scanner model: %s", self.info)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(X[FEATURES])

    def save(self, d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(d / "scanner_model.txt"))
        meta = {k: v for k, v in asdict(self).items() if k != "booster"}
        (d / "scanner_model.json").write_text(json.dumps(meta, indent=2, default=str))

    @classmethod
    def load(cls, d: Path) -> ScannerModel:
        meta = json.loads((d / "scanner_model.json").read_text())
        return cls(booster=lgb.Booster(model_file=str(d / "scanner_model.txt")), **meta)
