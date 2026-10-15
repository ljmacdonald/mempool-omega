"""The $100 funding-income account (D108): paper only, no network."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from carry import run as C
from core import config as core_config

T0 = datetime(2026, 10, 15, 7, 30, tzinfo=timezone.utc)
MS = lambda t: int(t.timestamp() * 1000)  # noqa: E731


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "STATE_DIR", tmp_path)
    return tmp_path


def mkt(price=100.0, hist_rate=0.0002, history=None, perp_shift=0.0):
    return {"spot_bid": price - 0.01, "spot_ask": price + 0.01, "perp_bid": price + perp_shift - 0.01,
            "perp_ask": price + perp_shift + 0.01, "rate_now": hist_rate, "next_funding": "",
            "history": history if history is not None else [{"t": MS(T0 - timedelta(hours=8 * i)), "rate": hist_rate} for i in range(1, 22)]}


def markets(**kw):
    return {c: mkt(**kw) for c in C.BASKET}


def test_enters_coins_whose_7_day_funding_clears_the_bar_and_pays_fees(state):
    m = markets()
    m["XRP"] = mkt(hist_rate=0.00001)                     # 21 x 0.001% = 0.021%: under the 0.05% bar
    acc = C.step(C.load(), m, T0)
    assert sorted(acc["positions"]) == ["BTC", "DOGE", "ETH", "SOL"] and acc["cash"] == pytest.approx(20)
    assert 99.8 < acc["equity"] < 100                       # fees and gaps only
    pos = acc["positions"]["BTC"]
    assert pos["qty"] * 100.01 == pytest.approx(10 / 1.001, rel=1e-3) and pos["margin"] < 10


def test_collects_each_funding_payment_once_and_hedges_out_the_price(state):
    acc = C.step(C.load(), markets(), T0)
    t1 = T0 + timedelta(hours=1)
    new = [{"t": MS(t1 - timedelta(minutes=30)), "rate": 0.0003}]
    later = markets(price=150.0)                              # price up 50%: spot gains, futures loses the same
    for c in later:
        later[c]["history"] = markets()[c]["history"] + new
    acc = C.step(acc, later, t1)
    pos = acc["positions"]["BTC"]
    assert pos["funding"] == pytest.approx(pos["qty"] * 150.0 * 0.0003)
    eq1 = acc["equity"]
    assert 99.8 < eq1 - acc["funding_total"] < 100            # price move cancelled out
    acc = C.step(acc, later, t1 + timedelta(hours=1))         # same funding seen again: not paid twice
    assert acc["equity"] == pytest.approx(eq1)
    rows = (state / "carry" / "funding.csv").read_text().strip().splitlines()
    assert len(rows) == 1 + len(C.BASKET)


def test_exits_when_funding_turns_negative_and_on_the_risk_rule(state):
    acc = C.step(C.load(), markets(), T0)
    t1 = T0 + timedelta(hours=1)
    m = markets()
    m["BTC"] = mkt(history=[{"t": MS(t1 - timedelta(hours=8 * i)), "rate": -0.0002} for i in range(21)])
    m["ETH"] = mkt(price=171.0)                               # futures loss 71% of margin: risk rule
    acc = C.step(acc, m, t1)
    assert "BTC" not in acc["positions"] and "ETH" not in acc["positions"]
    trades = (state / "carry" / "trades.csv").read_text()
    assert "7-day funding turned negative" in trades and "risk rule" in trades
    acc = C.step(acc, m, t1 + timedelta(hours=1))             # not reopened within a day of closing
    assert "ETH" not in acc["positions"]
    acc = C.step(acc, m, t1 + timedelta(hours=25))            # ETH's funding is still positive: back in after a day
    assert "ETH" in acc["positions"] and "BTC" not in acc["positions"]


def test_a_coin_whose_prices_fail_keeps_its_last_value(state):
    acc = C.step(C.load(), markets(), T0)
    eq = acc["equity"]
    m = markets()
    del m["SOL"]
    acc = C.step(acc, m, T0 + timedelta(hours=1))
    assert acc["equity"] == pytest.approx(eq, abs=0.01) and "SOL" in acc["positions"]
    v = C.view(acc, m)
    assert v["account"]["equity"] == acc["equity"] and json.dumps(v, default=str)
