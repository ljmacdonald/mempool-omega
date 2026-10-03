import numpy as np
import pandas as pd

from scanner.features import FEATURES, big_mover_stats, coin_features
from scanner.model import ScannerModel, build_dataset, risk_unit, simulate_idea
from scanner.rank import grade, market_mood, rank, risk_level


def _candles(n=700, seed=0, drift=0.0, vol=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = 10 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, n)))
    v = rng.gamma(3, 100, n)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v, "qv": v * c,
                         "taker_buy_volume": v * rng.uniform(0.4, 0.6, n)}, index=idx)


def test_features_causal():
    df = _candles()
    f1 = coin_features(df)
    df2 = df.copy()
    df2.iloc[500:, :] *= 1.5
    f2 = coin_features(df2)
    pd.testing.assert_frame_equal(f1.iloc[:500], f2.iloc[:500])
    assert list(f1.columns) == FEATURES


def test_simulate_idea_exits():
    idx = pd.date_range("2026-01-01", periods=40, freq="1h", tz="UTC")
    flat = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0}, index=idx)
    up = flat.copy()
    up.iloc[5:, up.columns.get_loc("high")] = 110
    assert simulate_idea(up, 2, 0.02)[1] == "take_profit"
    down = flat.copy()
    down.iloc[5:, down.columns.get_loc("low")] = 90
    assert simulate_idea(down, 2, 0.02)[1] == "safety_exit"
    r = simulate_idea(flat, 2, 0.02)
    assert r[1] == "time_limit" and r[0] < 0  # flat price loses the fees
    assert simulate_idea(flat, 30, 0.02) is None  # not finished yet


def test_train_rank_end_to_end(tmp_path):
    candles = {f"C{i}USDT": _candles(seed=i, drift=0.0005 * (i % 3 - 1)) for i in range(8)}
    candles["BTCUSDT"] = _candles(seed=99)
    data = build_dataset(candles)
    m = ScannerModel().fit(data, n_rounds=40, folds=3)
    m.save(tmp_path)
    m2 = ScannerModel.load(tmp_path)
    rows = []
    for s, df in candles.items():
        f = coin_features(df, candles["BTCUSDT"]).iloc[-1].copy()
        f["risk_unit"], f["close"], f["ts"] = float(risk_unit(df).iloc[-1]), float(df["close"].iloc[-1]), df.index[-1]
        rows.append(f.rename(s))
    latest = pd.DataFrame(rows)
    p = m2.predict(latest)
    ideas = rank(latest, p, {s: 5e7 for s in candles}, {}, 5, m2.win_r, m2.loss_r)
    assert [d["rank"] for d in ideas] == [1, 2, 3, 4, 5]
    assert ideas[0]["score"] >= ideas[-1]["score"]
    d = ideas[0]
    assert d["take_profit"] > d["price_now"] > d["safety_exit"] and d["why"]
    assert market_mood(p, m2.win_r, m2.loss_r)["label"] in {"Favourable", "Mixed", "Unfavourable"}


def test_labels():
    assert grade(7) == "Strong" and grade(4) == "Avoid - watch only"
    assert risk_level(0.02) == "Low" and risk_level(0.15) == "Very high"


def test_big_mover_stats():
    idx = pd.date_range("2026-01-01", periods=60, freq="1D", tz="UTC")
    c = np.linspace(1, 3, 60)
    daily = pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c}, index=idx)
    s = big_mover_stats(daily)
    assert s["up_pct"] > 0 and s["down_pct"] == 0


def test_styles_and_durations():
    from scanner.styles import STYLES, human_duration

    assert STYLES["quick"].horizon_minutes == 60 and STYLES["short"].horizon_minutes == 240
    assert STYLES["day"].hold_text == "24 hours" and human_duration(30) == "30 minutes"


def test_trade_monitor_advice():
    from scanner.live import advise, manual_trade

    now = pd.Timestamp("2026-01-01 12:00", tz="UTC")
    t = manual_trade("SOLUSDT", "short", 100.0, now, 2.0)   # stop 98, target 104, sell by 16:00
    assert round(t["safety_exit"], 6) == 98 and round(t["take_profit"], 6) == 104
    assert advise(t, 101, now + pd.Timedelta(minutes=30))["action"] == "HOLD"
    assert advise(t, 104.5, now)["action"] == "TAKE PROFIT NOW"
    assert advise(t, 97.9, now)["action"].startswith("EXIT NOW")
    assert advise(t, 101, now + pd.Timedelta(hours=5))["action"].startswith("TIME")
    assert advise(t, 101, now, score=3.0)["action"] == "CONSIDER LEAVING EARLY"


def test_quick_style_dataset_labels_vs_market():
    from scanner.styles import STYLES

    candles = {f"C{i}USDT": _candles(n=500, seed=i, vol=0.004) for i in range(6)}
    data = build_dataset(candles, STYLES["quick"])
    assert {"excess", "market_ret", "win"} <= set(data.columns)
    assert abs(data.groupby(level=0)["excess"].mean()).max() < 1e-9   # excess is relative to the same-moment average
