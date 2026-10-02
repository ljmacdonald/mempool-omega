from backtest.engine import run_backtest
from backtest.redteam import run_redteam
from core.synthetic import SyntheticSpec, generate_market


def test_walkforward_backtest_runs(settings):
    frames = {"BTCUSDT": generate_market(SyntheticSpec(n_bars=3200, seed=5), "BTCUSDT")}
    rep = run_backtest(frames, settings, n_folds=2)
    assert rep["data_mode"] == "synthetic"
    assert "sharpe" in rep and rep["final_equity"] > 0
    assert len(rep["folds"]["BTCUSDT"]) == 2


def test_redteam_runs(settings):
    raw = generate_market(SyntheticSpec(n_bars=3200, seed=6), "BTCUSDT")
    rep = run_redteam(raw, settings, attacks=["spoofing"], n_folds=2)
    r = rep["attacks"]["spoofing"]
    assert {"naive", "asi", "n_attacks"} <= set(r)
