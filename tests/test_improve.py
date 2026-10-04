"""Nightly self-improvement: model selection on unseen data, self-tuning check weights, probation."""
import numpy as np
import pandas as pd

from scanner import improve
from scanner.integrity import PENALTY, combine
from scanner.model import build_dataset
from scanner.rank import apply_probation
from scanner.styles import STYLES


def _candles(n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    v = rng.gamma(3, 100, n)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c,
                         "volume": v, "qv": v * c, "taker_buy_volume": v * rng.uniform(0.4, 0.6, n)}, index=idx)


def _history(n, fail_key, fail_hurts=True, style="day"):
    rng = np.random.default_rng(1)
    failed = rng.random(n) < 0.4
    net = np.where(failed & fail_hurts, -0.02, 0.005) + rng.normal(0, 0.003, n)
    return pd.DataFrame({"style": style, "status": "closed", "net_ret": net, "baseline_ret": 0.0, "risk_unit": 0.03,
                         "failed_checks": np.where(failed, fail_key, "none")})


def test_model_selection_uses_unseen_recent_data_and_retrains_winner():
    candles = {f"C{i}USDT": _candles(900, i) for i in range(8)}
    candles["BTCUSDT"] = _candles(900, 99)
    data = build_dataset(candles, STYLES["day"])
    m, sel = improve.select_model("day", data)
    assert sel["winner"] in improve.CONFIGS and sel["results"]
    assert m.info["config"] == sel["winner"] and m.info["selection"] is sel
    # a challenger only replaces the standard setting when clearly better
    if sel["winner"] != "base":
        assert sel["results"][sel["winner"]]["top5_excess"] >= sel["results"]["base"]["top5_excess"] + improve.MIN_EDGE_TO_SWITCH


def test_check_penalty_grows_when_flagged_ideas_really_did_worse():
    out = improve.tune_check_penalties(_history(400, "wash"))
    assert out["wash"]["learned"] > 0 and out["wash"]["penalty"] > PENALTY["wash"]
    assert improve.CHECK_BOUNDS[0] <= out["wash"]["penalty"] <= improve.CHECK_BOUNDS[1]
    assert out["walls"]["learned"] is None and out["walls"]["penalty"] == PENALTY["walls"]   # no evidence: unchanged


def test_check_penalty_shrinks_when_flag_did_not_matter():
    out = improve.tune_check_penalties(_history(400, "venues", fail_hurts=False))
    assert out["venues"]["penalty"] < PENALTY["venues"]


def test_learned_penalties_are_used_when_combining_checks():
    checks = [{"key": "wash", "ok": False}, {"key": "walls", "ok": True}]
    assert combine(checks)["penalty"] == PENALTY["wash"]
    assert combine(checks, {"wash": 0.25})["penalty"] == 0.25


def test_probation_and_its_effect_on_scores():
    bad = _history(80, "none", style="quick").assign(net_ret=-0.01)
    prob = improve.probation(bad)
    assert prob["quick"]["active"] and not prob["day"]["active"]
    ideas = [{"symbol": "A", "expected_r": 0.3, "score": 8.0, "grade": "Strong", "warnings": []}]
    out = apply_probation(ideas, {"probation": prob}, "quick")
    assert out[0]["expected_r"] < 0.3 and "probation" in out[0]["warnings"][-1]
    assert apply_probation([dict(ideas[0], expected_r=0.3, warnings=[])], {"probation": prob}, "day")[0]["expected_r"] == 0.3
