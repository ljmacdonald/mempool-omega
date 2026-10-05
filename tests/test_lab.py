"""Strategy lab: the copied strategies follow their published rules and never use prices from the future."""
import numpy as np
import pandas as pd

from lab import run as R
from lab import strategies as L


def frame(close, start="2024-01-01", freq="1D", spread=0.5):
    c = np.asarray(close, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq=freq, tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c, "volume": 1.0}, index=idx)


def walk(n=1500, seed=1):
    rng = np.random.default_rng(seed)
    return frame(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n))), spread=0.2)


def test_no_lookahead_trades_do_not_change_when_future_prices_change():
    df = walk()
    for fn in (L.rsi2, L.turtle, L.golden_cross, L.supertrend, L.bband_rsi, L.strategy001):
        a = [t for t in fn(df.iloc[:1000]) if not t.get("open")]
        fut = df.copy()
        fut.iloc[1000:, :4] *= 1.5            # a different future
        b = [t for t in fn(fut) if t["i_out"] < 1000 and not t.get("open")]
        assert [(t["i_in"], t["i_out"], round(t["exit"], 8)) for t in a if t["i_out"] < 999] == \
               [(t["i_in"], t["i_out"], round(t["exit"], 8)) for t in b if t["i_out"] < 999], fn.__name__


def test_rsi2_buys_a_dip_in_an_uptrend_and_sells_above_the_5_day_average():
    c = list(np.linspace(100, 200, 260)) + [190, 180, 185, 195, 205]
    tr = L.rsi2(frame(c))
    assert tr and tr[0]["i_in"] == 260 and tr[0]["i_out"] == 263            # bought on the sharp drop, sold above the 5-day average
    assert tr[0]["entry"] == 190 and tr[0]["exit"] == 195


def test_orb_follows_the_first_candle_and_stops_at_its_other_end():
    idx = pd.date_range("2026-03-02 14:30", periods=78, freq="5min", tz="UTC")       # 9:30 New York (winter)
    o = np.full(78, 100.0)
    o[0], c0 = 100.0, 101.0                                                                # up first candle: buy
    df = pd.DataFrame({"open": o, "high": o + 0.5, "low": o - 0.5, "close": o, "volume": 1.0}, index=idx)
    df.iloc[0, df.columns.get_loc("close")] = c0
    df.iloc[0, df.columns.get_loc("low")] = 99.0
    df.iloc[1, df.columns.get_loc("open")] = 101.0
    df.iloc[5, df.columns.get_loc("low")] = 98.0                                        # falls through the stop
    t = L.orb(df)[0]
    assert t["side"] == 1 and t["entry"] == 101.0 and t["stop"] == 99.0 and t["reason"] == "stop loss" and t["r"] == -1.0


def test_freqtrade_take_profit_table_and_stop():
    c = [100] * 5 + [100, 103, 106] + [100] * 5
    df = frame(c, freq="5min", spread=0.0)
    enter = np.zeros(len(c), bool)
    enter[4] = True
    t = L.freqtrade_sim(df, enter, np.zeros(len(c), bool), {"0": 0.05}, -0.10, 5)[0]
    assert t["i_in"] == 5 and t["reason"] == "take profit" and abs(t["exit"] - 105) < 1e-9


def test_supertrend_matches_published_definition_on_a_trend():
    up = frame(np.linspace(100, 200, 120))
    d = L.supertrend_dir(up, 10, 3)
    assert d[-1] == "up" and d[5] is None


def test_verdicts_are_strict():
    good = {"n": 400, "avg": 0.004, "random": 0.001, "edge": 0.003, "t_edge": 4.0}
    assert R.verdict(good, {**good, "n": 100})[0] == "good"
    assert R.verdict({**good, "n": 10}, good)[0] == "warn"
    assert R.verdict({**good, "avg": -0.001}, good)[0] == "bad"
    assert R.verdict({**good, "edge": -0.001, "t_edge": -1}, good)[0] == "bad"          # only rode the market up
    assert R.verdict(good, {**good, "edge": 0.0001, "t_edge": 0.1})[0] == "warn"        # faded after publication
