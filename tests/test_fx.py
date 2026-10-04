"""Forex: manipulation checks (fixes, stop hunts, off-market quotes, thin hours, news), both directions, exits."""
import numpy as np
import pandas as pd

from fx import checks as C
from fx.live import declutter, invert, market_open, sell_by
from fx.pairs import label, pip, spread

T = C.thresholds(None)


def candles(n=300, start="2026-10-05 00:00", freq="15min", seed=0, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    c = 1.10 * np.exp(np.cumsum(rng.normal(drift, 0.0004, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.0002, "low": np.minimum(o, c) * 0.9998, "close": c,
                         "volume": 0.0, "qv": 0.0, "taker_buy_volume": 0.0}, index=idx)


def test_fix_windows_cover_the_london_4pm_fix_in_summer_and_winter():
    summer = pd.Timestamp("2026-07-06 15:00", tz="UTC")       # 4 pm London (BST)
    winter = pd.Timestamp("2026-12-07 16:00", tz="UTC")       # 4 pm London (GMT)
    assert C.in_fix(summer) == "London 4 pm fix" and C.in_fix(winter) == "London 4 pm fix"
    assert C.in_fix(pd.Timestamp("2026-07-06 11:00", tz="UTC")) is None
    moved = C.avoid_fix(summer)
    assert moved < summer and C.in_fix(moved) is None           # sell-by never lands inside a fix


def test_spike_and_reverse_at_the_fix_is_flagged():
    df = candles(600, start="2026-10-01 00:00")
    a, b = pd.Timestamp("2026-10-05 14:45", tz="UTC"), pd.Timestamp("2026-10-05 15:15", tz="UTC")
    idx = np.where((df.index >= a) & (df.index <= b))[0]
    c = df["close"].to_numpy().copy()
    base = c[idx[0] - 1]
    c[idx] = base * np.array([1.001, 1.002, 1.003][:len(idx)] + [1.003] * (len(idx) - 3))   # pushed through the fix
    c[idx[-1] + 1:idx[-1] + 5] = base                                                         # then given back
    df["close"] = c
    df["open"] = np.r_[c[0], c[:-1]]
    df = df.iloc[:idx[-1] + 6]
    assert not C.fix_spikes(candles(600, start="2026-10-01 00:00").iloc[:idx[-1] + 6], T)     # normal day: nothing
    assert C.fix_spikes(df, T)


def test_stop_hunt_and_failed_breakout_are_detected():
    df = candles(80, seed=1)
    lo, hi = df["low"].iloc[-60:-5].min(), df["high"].iloc[-60:-5].max()
    i = len(df) - 3
    df.iloc[i, df.columns.get_loc("low")] = lo * 0.997          # pushed through the low...
    df.iloc[i, df.columns.get_loc("close")] = lo * 1.0005       # ...and closed back above
    j = len(df) - 2
    df.iloc[j, df.columns.get_loc("high")] = hi * 1.003
    df.iloc[j, df.columns.get_loc("close")] = hi * 0.9995
    h = C.stop_hunt(df, T)
    assert h["swept_low"] and h["failed_high"]


def test_off_market_cross_quote_is_caught():
    last = {"EURUSD": 1.10, "USDJPY": 150.0, "EURJPY": 165.0, "GBPUSD": 1.30, "GBPJPY": 199.0}
    tri = C.triangular(last)
    assert abs(tri["EURJPY"]) < 1e-9 and tri["GBPJPY"] > T["tri_dev"]


def test_thin_market_hours_and_weekend():
    assert C.thin_market(pd.Timestamp("2026-10-04 21:30", tz="UTC"))        # Sunday open
    assert C.thin_market(pd.Timestamp("2026-10-06 21:05", tz="UTC"))        # 5 pm New York changeover
    assert C.thin_market(pd.Timestamp("2026-10-06 12:00", tz="UTC")) is None
    assert not market_open(pd.Timestamp("2026-10-10 12:00", tz="UTC"))      # Saturday
    assert market_open(pd.Timestamp("2026-10-06 12:00", tz="UTC"))


def test_news_inside_the_window_and_managed_currencies():
    ev = [{"title": "Non-Farm Payrolls", "country": "USD", "impact": "High", "date": "2026-10-09T08:30:00-04:00"}]
    s = pd.Timestamp("2026-10-09 10:00", tz="UTC")
    assert C.news_in(ev, "EURUSD", s, s + pd.Timedelta(hours=4)) and not C.news_in(ev, "EURGBP", s, s + pd.Timedelta(hours=4))
    assert "central bank" in C.managed_note("USDNGN") and C.managed_note("EURUSD") is None


def test_sell_side_is_the_inverted_price_and_exits_point_the_right_way():
    df = candles(50)
    inv = invert(df)
    assert np.allclose(inv["close"] * df["close"], 1) and (inv["high"] >= inv["low"]).all()
    px = 1.1249
    tp, sl = declutter("EURUSD", "sell", px, px * 0.99, px * 1.0003, {})
    assert tp < px < sl
    tp, sl = declutter("EURUSD", "buy", 1.1210, 1.1290, 1.1201, {})
    assert abs(sl - (1.1200 - 5 * pip("EURUSD"))) < 1e-9          # stop moved below the round 1.1200 level


def test_no_holds_into_the_weekend_and_costs():
    fri = pd.Timestamp("2026-10-09 18:00", tz="UTC")
    assert sell_by(fri, "fx_days").tz_convert("America/New_York").hour <= 16
    assert spread("EURUSD") < spread("EURGBP") < spread("USDNGN") and label("XAUUSD").startswith("Gold")


def test_secret_limits_only_get_stricter():
    for seed in range(100):
        t = C.thresholds(seed)
        assert t["fix_k"] <= C.BASE["fix_k"] and t["tri_dev"] <= C.BASE["tri_dev"]
        assert t["news_before_min"] >= C.BASE["news_before_min"]
