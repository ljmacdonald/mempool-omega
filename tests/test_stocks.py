"""US stocks: which stocks are scanned, costs, warnings (earnings, penny stocks, chasing), sell-by windows."""
import numpy as np
import pandas as pd

from scanner.styles import STOCK_STYLES, get_style
from stocks import rank as R
from stocks.live import in_window


def scr(rows):
    df = pd.DataFrame(rows, columns=["symbol", "name", "price", "chg_pct", "volume", "mcap", "sector", "industry"])
    df["dollar_vol"] = df["price"] * df["volume"]
    return df


def test_universes_split_large_and_volatile_and_apply_the_swing_filter():
    s = scr([["BIG", "Big", 100, 0.01, 5e6, 50e9, "", ""], ["MID", "Mid", 20, 0.05, 2e6, 2e9, "", ""],
             ["CALM", "Calm", 20, 0.0, 2e6, 2e9, "", ""], ["PENNY", "Penny", 2, 0.5, 9e7, 1e9, "", ""],
             ["TINY", "Tiny", 10, 0.2, 5e6, 1e8, "", ""], ["THIN", "Thin", 10, 0.1, 1e5, 1e9, "", ""]])
    large, vol = R.universes(s, {"MID": {"up_pct": 0.3}, "CALM": {"up_pct": 0.05}})
    assert list(large["symbol"]) == ["BIG"]
    assert list(vol["symbol"]) == ["MID"]          # calm, penny, tiny and thin stocks are all excluded


def test_costs_grow_for_thin_stocks_and_currency_fees():
    assert R.cost_rt(5e9) < R.cost_rt(30e6) < R.cost_rt(5e6)
    assert abs(R.cost_rt(5e9, fx=0.01) - R.cost_rt(5e9) - 0.02) < 1e-12


def _f(**kw):
    base = {"ret_24b": 0.0, "ret_1b": 0.0, "rsi_14": 50.0, "btc_ret_24b": 0.0}
    base.update(kw)
    return pd.Series(base)


def test_warnings_for_chasing_penny_stocks_and_results_days():
    st = STOCK_STYLES["stk_days"]
    pen, w = R.flags(_f(ret_24b=np.log(1.2)), 50, st, None, "2026-10-05")
    assert pen > 0 and "Already up" in w[0]
    pen, w = R.flags(_f(), 3.5, st, None, "2026-10-05")
    assert pen > 0 and "penny" in w[0]
    pen, w = R.flags(_f(), 50, st, "2026-10-07 after the close", "2026-10-05")
    assert pen >= 0.3 and "results" in w[0]
    pen, w = R.flags(_f(), 50, STOCK_STYLES["stk_today"], "2026-10-05 after the close", "2026-10-05")
    assert pen == 0 and w            # sold before the close: a warning, no penalty


def test_results_inside_the_holding_window_only():
    now = pd.Timestamp("2026-10-05 11:00", tz="America/New_York")
    assert in_window("2026-10-07 after the close", "stk_days", now)
    assert in_window("2026-10-20", "stk_days", now) is None
    assert in_window("2026-10-05 before the open", "stk_today", now)
    assert in_window("2026-10-06", "stk_today", now) is None


def test_stock_speeds_are_registered():
    assert get_style("stk_today").interval == "15m" and get_style("stk_days").horizon_bars == 21
