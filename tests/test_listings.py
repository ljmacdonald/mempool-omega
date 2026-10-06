"""New listings: the rules use only prices known at the time, exits follow the safety exit, and the filters keep
tokenized stocks and stablecoins out."""
import numpy as np
import pandas as pd

from listings import data as D
from listings import research as RS
from listings import run as RUN


def daily(closes, start="2025-03-03", rng=0.1):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    idx = pd.date_range(start, periods=len(c), freq="1D", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + rng / 2), "low": np.minimum(o, c) * (1 - rng / 2), "close": c, "qv": 1e6}, index=idx)


def test_rules_enter_on_known_prices_only():
    d = daily([10, 12, 8, 5, 5.5, 6, 6, 6, 13, 12] + [12] * 40)
    k, px, t = RS.entry("dip50", d, None)
    assert k == 3 and px == 5 and t == d.index[3] + pd.Timedelta(days=1)            # first close at or below half the high so far
    k, px, _ = RS.entry("breakout", d, None)
    assert k == 8 and px == 13                                                       # first close above the first week's high
    assert RS.entry("day_one", d, None)[1] == 10 and RS.entry("settled", d, None)[0] == 30
    future = d.copy()
    future.iloc[20:, :4] *= 3                                                       # a different future changes nothing earlier
    assert RS.entry("dip50", future, None)[:2] == (3, 5)


def test_hold_uses_the_safety_exit_for_buys_and_shorts():
    d = daily([10, 10, 10, 6.5, 6, 6, 6, 6, 6, 6], rng=0.0)
    j, px, why = RS.hold(d, 0, 10.0, 7, False, 1)
    assert why == "safety exit" and j == 3 and px <= 7.0
    up = daily([10, 10, 14, 15, 15, 15, 15, 15, 15, 15], rng=0.0)
    j, px, why = RS.hold(up, 0, 10.0, 7, False, -1)
    assert why == "safety exit" and px >= 13.0
    assert RS.hold(daily([10] * 5, rng=0.0), 0, 10.0, 30, False, 1) is None         # not finished yet


def test_filters_drop_stocks_and_stablecoins():
    stable = daily([1.0] * 40, rng=0.002)
    assert RS.is_stable(stable)
    coin = daily(list(np.linspace(1, 3, 40)), rng=0.15)
    assert not RS.is_stable(coin)
    # a tokenized stock: big weekday ranges, nearly none at weekends, and a stock-style name
    s = daily([100.0] * 42, start="2025-03-03", rng=0.08)
    we = s.index.dayofweek >= 5
    s.loc[we, "high"] = s.loc[we, "close"] * 1.002
    s.loc[we, "low"] = s.loc[we, "close"] * 0.998
    assert D.stock_like("binance", "DJTB", s) and not D.stock_like("binance", "HYPE", s) and not D.stock_like("binance", "DJTB", coin)
    first = {"AAAB": 1000 * D.DAY_MS + 5, "BBBB": 1000 * D.DAY_MS + 9, "HYPE": 1000 * D.DAY_MS}
    assert D.tokenized_stock_batches(first, {k: k for k in first}) == {"AAAB", "BBBB"}


def test_failed_rules_never_become_ideas_and_judging_is_strict():
    research = {"funding": 0.001, "rules": {r: {"name": r, "text": "", "level": "good" if r.startswith("short") else "bad", "chosen": 7,
                                              "holds": {"7": {"all": {"n": 400, "hit": 0.6, "avg": 0.05, "avg_win": 0.15, "avg_loss": -0.12}, "holder": {"avg": 0.06}}}}
                                          for r in RS.RULES}}
    ev = {"exchange": "binance", "symbol": "NEWUSDT", "base": "NEW", "list_ms": 0}
    row = {"last": 10.0, "vol24": 50e6}
    good = RUN.judge("short_day_one", research, ev, row, 10.0, None, None)
    thin = RUN.judge("short_day_one", research, ev, {**row, "vol24": 1e5}, 10.0, None, None)
    late = RUN.judge("short_day_one", research, ev, {**row, "last": 9.0}, 10.0, None, None)
    assert good["score"] > thin["score"] and good["score"] > late["score"]
    assert any("futures" in w for w in good["warnings"])
