"""Pooled (all coins together) LightGBM, one per trading speed, estimating the chance a BUY idea
does better than the typical coin over the same time window.

Idea definition (same for training, ranking, the trade monitor and the scoreboard):
  entry  = next candle's open
  target = entry * (1 + 2 * risk_unit)   take-profit   (reward)
  stop   = entry * (1 - 1 * risk_unit)   safety exit   (risk)   -> 2:1 reward-to-risk
  time   = sell at the close once the speed's time limit is reached
  risk_unit = candle volatility * sqrt(time limit in candles), clipped to the speed's range
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from core.log import get_logger
from scanner.features import FEATURES, bar_sigma, coin_features
from scanner.styles import STYLES, Style

log = get_logger("scanner.model")
PT, SL = 2.0, 1.0
COST = 0.002            # 0.1 % fee each way (spot taker)
WARMUP = 200
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=2.0, verbose=-1, deterministic=True,
              force_row_wise=True, num_threads=2, seed=7)


def risk_unit(df: pd.DataFrame, style: Style = STYLES["day"]) -> pd.Series:
    return (bar_sigma(df) * np.sqrt(style.horizon_bars)).clip(style.min_risk, style.max_risk)


def _exit_scan(o, h, lo, c, t: int, ru: float, last: int, tp_pct: float | None = None,
               sl_pct: float | None = None):
    entry = o[t + 1]
    tgt = entry * (1 + (PT * ru if tp_pct is None else tp_pct))
    stp = entry * (1 + (-SL * ru if sl_pct is None else sl_pct))
    for k in range(t + 1, last + 1):
        if lo[k] <= stp:                       # stop first if both touched in one candle (pessimistic)
            px = min(stp, o[k]) if k > t + 1 else stp   # a gap down through the stop fills at the open
            return px / entry - 1 - COST, "safety_exit", k - t
        if h[k] >= tgt:
            return tgt / entry - 1 - COST, "take_profit", k - t
    return None


def _sim(o, h, lo, c, t: int, ru: float, horizon: int) -> tuple[float, str, int] | None:
    if t + 1 >= len(o) or not np.isfinite(ru) or t + horizon >= len(o):
        return None
    res = _exit_scan(o, h, lo, c, t, ru, t + horizon)
    if res:
        return res
    return c[t + horizon] / o[t + 1] - 1 - COST, "time_limit", horizon


def simulate_idea(df: pd.DataFrame, t: int, ru: float, horizon: int = 24, tp_pct: float | None = None,
                  sl_pct: float | None = None) -> tuple[float, str, int] | None:
    """Outcome of a BUY idea created at the close of candle t: (net return, outcome, candles held), or None if
    the time limit has not passed yet and neither exit was hit. Exits default to 2:1 x risk_unit, or are given
    as fractions of the entry price (tp_pct > 0, sl_pct < 0)."""
    o, h, lo, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    if t + 1 >= len(o) or not np.isfinite(ru):
        return None
    if t + horizon < len(o):
        res = _exit_scan(o, h, lo, c, t, ru, t + horizon, tp_pct, sl_pct)
        return res or (c[t + horizon] / o[t + 1] - 1 - COST, "time_limit", horizon)
    return _exit_scan(o, h, lo, c, t, ru, len(o) - 1, tp_pct, sl_pct)


def build_dataset(candles: dict[str, pd.DataFrame], style: Style = STYLES["day"],
                  btc_symbol: str = "BTCUSDT") -> pd.DataFrame:
    btc = candles.get(btc_symbol)
    rows = []
    for sym, df in candles.items():
        f = coin_features(df, btc)
        ru = risk_unit(df, style).to_numpy()
        rets = np.full(len(df), np.nan)
        hit = np.full(len(df), np.nan)
        arrs = [df[k].to_numpy() for k in ("open", "high", "low", "close")]
        for t in range(WARMUP, len(df) - style.horizon_bars - 1):
            out = _sim(*arrs, t, ru[t], style.horizon_bars)
            if out:
                rets[t] = out[0]
                hit[t] = float(out[1] == "take_profit")
        f = f.assign(symbol=sym, risk_unit=ru, net_ret=rets, hit_target=hit)
        rows.append(f.iloc[WARMUP:])
    data = pd.concat(rows).dropna(subset=["net_ret"])
    # Label = did this coin do BETTER THAN THE TYPICAL COIN over the same window? Comparing with the
    # same-moment average removes "the whole market went up" luck and most of the survivorship bias
    # (coins are picked by today's volume, which favours recent winners).
    data["market_ret"] = data.groupby(level=0)["net_ret"].transform("mean")
    data["excess"] = data["net_ret"] - data["market_ret"]
    data["win"] = (data["excess"] > 0).astype(int)
    return data[data.groupby(level=0)["net_ret"].transform("size") >= 5]


@dataclass
class ScannerModel:
    booster: lgb.Booster | None = None
    style: str = "day"
    avg_win: float = 0.03
    avg_loss: float = 0.02
    base_rate: float = 0.4
    win_r: float = 1.0          # mean winning result in units of the amount risked (stop distance)
    loss_r: float = 1.0         # mean losing result in the same units
    info: dict = field(default_factory=dict)

    def fit(self, data: pd.DataFrame, n_rounds: int = 300, folds: int = 4, horizon_td: pd.Timedelta | None = None,
            params: dict | None = None, config: str = "base") -> ScannerModel:
        params = {**PARAMS, **(params or {})}
        horizon_td = horizon_td if horizon_td is not None else pd.Timedelta(minutes=STYLES[self.style].horizon_minutes)
        data = data.sort_index()
        ts = data.index.unique().sort_values()
        bounds = np.linspace(0, len(ts), folds + 1).astype(int)
        oos = pd.Series(np.nan, index=range(len(data)))
        for i in range(1, folds):  # walk-forward: train on the past only, purge one time limit before the test
            test_start, test_end = ts[bounds[i]], ts[min(bounds[i + 1], len(ts) - 1)]
            tr = data.index < test_start - horizon_td
            te = (data.index >= test_start) & (data.index <= test_end)
            if tr.sum() < 1000 or te.sum() == 0:
                continue
            b = lgb.train(params, lgb.Dataset(data.loc[tr, FEATURES], data.loc[tr, "win"]), n_rounds)
            oos[np.where(te)[0]] = b.predict(data.loc[te, FEATURES])
        m = oos.notna().to_numpy()
        from sklearn.metrics import roc_auc_score

        auc = float(roc_auc_score(data["win"].to_numpy()[m], oos[m])) if m.sum() > 100 else float("nan")
        oos_df = data.loc[m].assign(p=oos[m].to_numpy())
        top = oos_df.groupby(oos_df.index).apply(lambda g: g.nlargest(5, "p")).reset_index(drop=True) \
            if len(oos_df) else oos_df
        self.booster = lgb.train(params, lgb.Dataset(data[FEATURES], data["win"]), n_rounds)
        w, lz = data.loc[data.net_ret > 0, "net_ret"], -data.loc[data.net_ret <= 0, "net_ret"]
        self.avg_win, self.avg_loss = float(w.mean()), float(lz.mean())
        self.base_rate = float(data["win"].mean())
        r = data["excess"] / (SL * data["risk_unit"])      # out/under-performance per $1 risked
        self.win_r, self.loss_r = float(r[r > 0].mean()), float(-r[r <= 0].mean())
        self.info = {
            "style": self.style, "config": config, "trained_rows": int(len(data)), "coins": int(data["symbol"].nunique()),
            "oos_auc": auc,
            "top5_beat_market_rate": float((top["excess"] > 0).mean()) if len(top) else None,
            "top5_avg_excess_ret": float(top["excess"].mean()) if len(top) else None,
            "all_ideas_win_rate": float((oos_df["net_ret"] > 0).mean()) if len(oos_df) else None,
            "all_ideas_avg_net_ret": float(oos_df["net_ret"].mean()) if len(oos_df) else None,
            "top5_win_rate": float((top["net_ret"] > 0).mean()) if len(top) else None,
            "top5_avg_net_ret": float(top["net_ret"].mean()) if len(top) else None,
            "start": str(data.index.min()), "end": str(data.index.max()),
            "caveat": "Coins are chosen by TODAY's trading volume, which favours coins that recently rose "
                      "(survivorship bias), so these past-data numbers look better than reality. "
                      "Judge the scanner by the live track record instead.",
        }
        log.info("scanner model %s: %s", self.style, self.info)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.booster.predict(X[FEATURES])

    def save(self, d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
        self.booster.save_model(str(d / f"scanner_{self.style}_model.txt"))
        meta = {k: v for k, v in asdict(self).items() if k != "booster"}
        (d / f"scanner_{self.style}_model.json").write_text(json.dumps(meta, indent=2, default=str))

    @classmethod
    def exists(cls, d: Path, style: str) -> bool:
        return (d / f"scanner_{style}_model.txt").exists()

    @classmethod
    def load(cls, d: Path, style: str = "day") -> ScannerModel:
        meta = json.loads((d / f"scanner_{style}_model.json").read_text())
        return cls(booster=lgb.Booster(model_file=str(d / f"scanner_{style}_model.txt")), **meta)
