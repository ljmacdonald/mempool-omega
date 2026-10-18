"""The hedged $100 Small-coin account (D111): the challenge's trades, each with a Bitcoin short."""
import csv
import json

import numpy as np
import pandas as pd
import pytest

from core import config as core_config
from scanner import hedged as H

T0 = pd.Timestamp("2026-10-12 00:00:00+00:00")


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "STATE_DIR", tmp_path)
    (tmp_path / H.SRC).mkdir(parents=True)
    return tmp_path


def series(rets, start=T0 - pd.Timedelta(hours=300), p0=100.0):
    idx = pd.date_range(start, periods=len(rets), freq="1h", tz="UTC")
    return pd.DataFrame({"close": p0 * np.exp(np.cumsum(rets))}, index=idx)


def test_beta_is_measured_before_the_trade_and_capped():
    rng = np.random.default_rng(1)
    b = rng.normal(0, 0.01, 400)
    btc = series(b)
    assert H.beta(series(2 * b), btc, T0) == pytest.approx(2.0, abs=0.01)
    assert H.beta(series(5 * b), btc, T0) == H.BETA_CAP[1]
    assert H.beta(series(rng.normal(0, 0.01, 400)), btc, T0) == H.BETA_CAP[0]     # unrelated coin: the floor
    late = series(np.r_[b[:300], 50 * b[300:]])                                     # what happens after doesn't count
    assert H.beta(late, btc, T0) == pytest.approx(1.0, abs=0.01)
    assert H.beta(None, btc, T0) == 1.0


def test_a_short_gains_when_bitcoin_falls_and_pays_its_costs():
    fees = 50 * 2 * (H.PERP_FEE + H.PERP_HALF_SPREAD)
    assert H.hedge_result(50, 100, 98, 0.0003) == pytest.approx(50 * 0.02 + 50 * 0.0003 - fees)
    assert H.hedge_result(50, 100, 102, -0.0001) == pytest.approx(-50 * 0.02 - 50 * 0.0001 - fees)


def test_account_adds_the_hedges_to_the_challenges_own_value(state):
    rng = np.random.default_rng(2)
    b = rng.normal(0, 0.005, 360)
    b[310:] = -0.002                                          # Bitcoin drifts down after the trade
    btc = series(b)
    coin = series(1.5 * b + rng.normal(0, 0.001, 360))
    src = state / H.SRC
    opened, closed = T0 + pd.Timedelta(hours=10), T0 + pd.Timedelta(hours=30)
    with (src / "trades.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["opened", "closed", "symbol", "amount", "net", "ret"])
        w.writeheader()
        w.writerow({"opened": str(opened), "closed": str(closed), "symbol": "ABCUSDT", "amount": 20, "net": -1.0, "ret": -0.05})
    (src / "account.json").write_text(json.dumps({"start": 100.0, "equity": 99.0, "started_at": str(T0),
                                                  "positions": [{"status": "open", "symbol": "ABCUSDT", "amount": 10,
                                                                 "fill_t": str(T0 + pd.Timedelta(hours=40))}]}))
    out = H.run({"BTCUSDT": btc, "ABCUSDT": coin}, funding_hist=[{"t": (opened + pd.Timedelta(hours=8)).value // 10**6, "rate": 0.0001}],
                now=T0 + pd.Timedelta(hours=58))
    led = json.loads((state / H.DIR / "hedges.json").read_text())
    c = led[f"ABCUSDT|{opened}"]
    assert c["status"] == "closed" and 1.3 < c["beta"] < 1.7 and c["size"] == pytest.approx(c["beta"] * 20, abs=0.02)
    assert c["funding"] == pytest.approx(0.0001) and c["btc_ret"] < 0 and c["result"] > 0
    o = led[f"ABCUSDT|{T0 + pd.Timedelta(hours=40)}"]
    assert o["status"] == "open" and "mark" in o
    assert out["equity"] == pytest.approx(99.0 + c["result"] + o["mark"]) and out["hedges_closed"] == 1
    again = H.run({"BTCUSDT": btc, "ABCUSDT": coin}, funding_hist=[], now=T0 + pd.Timedelta(hours=58))
    assert json.loads((state / H.DIR / "hedges.json").read_text())[f"ABCUSDT|{opened}"]["result"] == c["result"]   # settled once
    assert again["hedges_closed"] == 1
