import numpy as np
import pandas as pd

from backtest.cv import purged_kfold, walk_forward
from core.config import CostConfig, ExitConfig, RiskConfig
from omega.cost import CostModel
from omega.kalman import kalman_filter
from omega.labels import triple_barrier
from omega.model import Calibration, OmegaModel
from omega.signal import decide
from risk.exits import check_exit, make_exit_plan
from risk.limits import KillSwitch, correlation_ok
from risk.sizing import position_notional


def test_purged_kfold_no_overlap():
    for tr, te in purged_kfold(1000, k=5, horizon=12, embargo=12):
        assert len(set(tr) & set(te)) == 0
        assert not any((te.min() - 12 <= x <= te.max() + 12) for x in tr)


def test_walk_forward_is_forward():
    for tr, te in walk_forward(3000, 4, 1000, 12, 12):
        assert tr.max() + 24 <= te.min()


def test_triple_barrier_basic():
    idx = pd.date_range("2026-01-01", periods=30, freq="5min", tz="UTC")
    c = np.r_[np.full(5, 100.0), np.linspace(100, 110, 25)]
    raw = pd.DataFrame({"open": c, "high": c + 0.01, "low": c - 0.01, "close": c}, index=idx)
    lab = triple_barrier(raw, pd.Series(0.01, index=idx), horizon=5, pt=1, sl=1)
    assert lab["label_long"].iloc[5] == 1.0


def test_kalman_tracks_beta():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(size=3000)).cumsum()
    y = 2.0 * x + rng.normal(0, 0.1, 3000)
    kf = kalman_filter(y, x, delta=1e-4)
    assert abs(kf["beta"].iloc[-1] - 2.0) < 0.1


def test_cost_model_positive():
    c = CostModel(CostConfig())
    assert c.expected_round_trip_bps() > CostConfig().taker_fee_bps
    assert c.slippage_bps(1e6, 1e6, maker=False) > c.slippage_bps(1e3, 1e6, maker=False)


def test_decide_requires_edge_over_cost_and_premium():
    cal = Calibration(win_sigma=1.2, loss_sigma=1.0)
    d = decide(0.7, 0.75, 1, 80, trust=0.9, cost_bps=10, calib=cal, min_trust=0.45, kelly_fraction=0.25)
    assert d.take
    d2 = decide(0.7, 0.75, 1, 80, trust=0.2, cost_bps=10, calib=cal, min_trust=0.45, kelly_fraction=0.25)
    assert not d2.take and "trust" in d2.reason
    d3 = decide(0.7, 0.75, 1, 5, trust=0.9, cost_bps=10, calib=cal, min_trust=0.45, kelly_fraction=0.25)
    assert not d3.take and "edge" in d3.reason


def test_sizing_respects_risk_budget():
    cfg = RiskConfig()
    n = position_notional(100_000, 50_000, stop_distance_frac=0.01, sigma_bar=0.002, kelly_frac=1.0, cfg=cfg)
    assert n * 0.01 <= 100_000 * cfg.risk_per_trade + 1e-6
    assert n <= cfg.max_position_frac * 100_000


def test_exits_priority():
    plan = make_exit_plan(1, 100.0, 0.005, ExitConfig(), trust_floor=0.3)
    assert check_exit(1, plan, {"open": 100, "high": 100.1, "low": 90, "close": 99}, 1, 0.6, 0.9, False)[0] == "stop_loss"
    assert check_exit(1, plan, {"open": 100, "high": 200, "low": 99.9, "close": 101}, 1, 0.6, 0.9, False)[0] == "profit_target"
    assert check_exit(1, plan, {"open": 100, "high": 100.1, "low": 99.9, "close": 100}, 1, 0.6, 0.1, False)[0] == "trust_collapse"
    assert check_exit(1, plan, {"open": 100, "high": 100.1, "low": 99.9, "close": 100}, 1, 0.3, 0.9, False)[0] == "signal_decay"
    assert check_exit(1, plan, {"open": 100, "high": 100.1, "low": 99.9, "close": 100}, 99, 0.6, 0.9, False)[0] == "time_stop"
    assert check_exit(1, plan, {"open": 100, "high": 100.1, "low": 99.9, "close": 100}, 1, 0.6, 0.9, True)[0] == "kill_switch"


def test_kill_switch_daily_drawdown_and_reset():
    k = KillSwitch()
    cfg = RiskConfig()
    t = pd.Timestamp("2026-01-01 10:00", tz="UTC")
    k.new_day(t, 100_000)
    assert k.evaluate(t, 96_000, cfg, data_quality=1.0)
    k.new_day(t + pd.Timedelta(days=1), 96_000)
    assert not k.active


def test_correlation_cap():
    cfg = RiskConfig(max_correlated_positions=1)
    assert not correlation_ok({"BTCUSDT": 1}, "ETHUSDT", 1, cfg)
    assert correlation_ok({"BTCUSDT": 1}, "ETHUSDT", -1, cfg)


def test_model_fit_save_load_roundtrip(trained, tmp_path):
    m, X, sigma = trained
    from asi.trust import compute_trust

    trust, _ = compute_trust(X.iloc[-200:])
    p1 = m.predict(X.iloc[-200:], trust)
    m.save(tmp_path, "T")
    m2 = OmegaModel.load(tmp_path, "T")
    p2 = m2.predict(X.iloc[-200:], trust)
    np.testing.assert_allclose(p1["p_up"], p2["p_up"])
    assert set(p1.columns) >= {"p_up", "side", "meta_p", "signal_trust"}
    assert m.info["n_train"] > 0
