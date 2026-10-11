"""The $100 Small-coin challenge (D97): Binance orders a person can place by hand, every cost, the owner's rules."""
import json

import pandas as pd
import pytest

from core import config
from scanner import challenge as C

T0 = pd.Timestamp("2026-10-11T10:10:00Z")


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def idea(grade="Moderate", exp_r=0.4):
    return {"symbol": "ABCUSDT", "coin": "ABC", "score": 6.8, "grade": grade, "chance_beats_market": 0.62,
            "expected_r": exp_r, "risk_unit": 0.06, "price_now": 1.0, "take_profit_pct": 0.12, "safety_exit_pct": -0.06}


def bars(rows):
    """rows: (open, high, low, close) per hour from 10:00."""
    idx = pd.date_range("2026-10-11T10:00:00Z", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def step(now, ideas=None, df=None, prob=None, check=None):
    return C.run(ideas or [], {"ABCUSDT": df} if df is not None else {}, probation=prob, quality_check=check, now=now,
                 get_spread=lambda s: 0.002)


def trade(state):
    return pd.read_csv(state / "suggestions/challenge_small/trades.csv").iloc[0]


def test_take_profit_fills_at_its_limit_price_after_fees(state):
    acc = step(T0, [idea()])
    assert acc["positions"][0]["status"] == "pending"
    acc = step(T0 + pd.Timedelta(hours=3), [], bars([(1, 1, 1, 1.0), (1.0, 1.05, 0.99, 1.04), (1.04, 1.15, 1.03, 1.1)]))
    t = trade(state)
    assert t["why"].startswith("take profit") and t["exit"] == pytest.approx(1.12)
    assert 0.10 < t["ret"] < 0.12 and t["sell_spread_slippage"] == 0          # fees and the buy-side gap only
    log = json.loads((state / "suggestions/challenge_small/log.json").read_text())
    buy = next(x for x in log if x["kind"] == "buy")["text"]
    assert "OCO" in buy and "sell by" in buy                                  # how to copy it by hand


def test_stop_order_fills_at_the_stop_or_at_the_open_after_a_gap(state):
    step(T0, [idea()])
    step(T0 + pd.Timedelta(hours=3), [], bars([(1, 1, 1, 1.0), (0.99, 1.0, 0.93, 0.95)]))
    t = trade(state)
    assert t["why"].startswith("safety exit") and t["exit"] == pytest.approx(0.94 * (1 - 0.001 - 0.0005))
    assert -0.08 < t["ret"] < -0.06


def test_gap_past_the_stop_sells_at_the_open(state):
    step(T0, [idea()])
    acc = step(T0 + pd.Timedelta(hours=3), [], bars([(1, 1, 1, 1.0), (0.80, 0.82, 0.78, 0.80)]))
    t = trade(state)
    assert "jumped" in t["why"] and t["ret"] < -0.19 and acc["equity"] > 94      # sizing kept the damage to about 5%


def test_same_hour_touching_both_assumes_the_safety_exit_and_time_limit_sells_at_the_close(state):
    step(T0, [idea()])
    step(T0 + pd.Timedelta(hours=3), [], bars([(1, 1, 1, 1.0), (1.0, 1.2, 0.9, 1.0)]))
    assert trade(state)["why"].startswith("safety exit")


def test_time_limit(state):
    step(T0, [idea()])
    rows = [(1, 1, 1, 1.0)] + [(1.0, 1.01, 0.99, 1.0)] * 25
    acc = step(T0 + pd.Timedelta(hours=27), [], bars(rows))
    assert trade(state)["why"].startswith("24 hours") and acc["trades"] == 1


@pytest.mark.parametrize("kw,grade,needle", [
    ({"prob": {"day": {"active": True, "n": 50, "avg_vs_random": -0.0004}}}, "Moderate", "probation"),
    ({"check": {"reliable": False}}, "Moderate", "can't be trusted"),
    ({}, "Weak", "graded Weak"),
])
def test_stays_in_cash_when_the_rules_say_no(state, kw, grade, needle):
    acc = step(T0, [idea(grade)], **kw)
    assert not acc["positions"] and acc["cash"] == 100
    log = json.loads((state / "suggestions/challenge_small/log.json").read_text())
    assert needle in log[0]["text"]


def test_spread_and_slippage_can_turn_a_thin_edge_negative(state):
    acc = C.run([idea(exp_r=0.01)], {}, now=T0, get_spread=lambda s: 0.02)
    assert not acc["positions"]
