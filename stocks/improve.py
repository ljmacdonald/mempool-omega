"""Nightly (after the US close): swing statistics for the high-volatility list, earnings calendar, and the
two stock models (champion/challenger selection, as for crypto)."""
from __future__ import annotations

import json

import pandas as pd

from core.config import state_path
from core.log import get_logger
from scanner.features import big_mover_stats
from scanner.styles import STOCK_STYLES
from stocks import rank as R
from stocks.data import candles, earnings, load_all, screener

log = get_logger("stocks.improve")


def swing_stats(scr: pd.DataFrame) -> dict:
    """How often each mid-size stock rose / fell 20%+ within a week over the last year (daily candles)."""
    s = scr[(scr["mcap"] >= R.VOL_MIN_MCAP) & (scr["mcap"] < R.LARGE_MIN_MCAP) & (scr["price"] >= R.VOL_MIN_PRICE)
            & (scr["dollar_vol"] >= R.VOL_MIN_DOLLAR_VOL)].sort_values("dollar_vol", ascending=False).head(R.VOL_N)
    out = {}
    for sym in s["symbol"]:
        try:
            d = candles(sym, "1d", 260)
            if len(d) > 30:
                out[sym] = big_mover_stats(d.iloc[-250:])
        except Exception as e:  # noqa: BLE001
            log.warning("daily %s: %s", sym, e)
    state_path("stocks", "swing.json").write_text(json.dumps(out, indent=1))
    return out


def train() -> dict:
    from scanner.improve import select_model
    from scanner.live import models_dir
    from scanner.model import build_dataset

    scr = screener()
    swing = swing_stats(scr)
    state_path("stocks", "earnings.json").write_text(json.dumps({"at": str(pd.Timestamp.now(tz="UTC")),
                                                                 "dates": earnings(8)}, indent=1))
    large, vol = R.universes(scr, swing)
    syms = sorted(set(large["symbol"]) | set(vol["symbol"]) | {"SPY"})
    out = {}
    for key, st in STOCK_STYLES.items():
        cs = load_all(syms, st.interval, st.train_bars)
        data = build_dataset(cs, st, btc_symbol="SPY")
        if key == "stk_today":
            # "Today" ideas are sold before the close: drop training rows whose window would run past 4 pm
            et = data.index.tz_convert("America/New_York")
            data = data[(et.hour * 60 + et.minute) <= 14 * 60]
        m, sel = select_model(key, data)
        m.save(models_dir())
        out[key] = {"rows": int(len(data)), "stocks": int(data["symbol"].nunique()), "auc": m.info.get("oos_auc"),
                    "winner": sel["winner"], "top5_avg_excess_ret": m.info.get("top5_avg_excess_ret")}
    state_path("stocks", "train.json").write_text(json.dumps(out, indent=1, default=str))
    return out
