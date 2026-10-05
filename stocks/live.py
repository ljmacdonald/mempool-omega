"""US stocks scan (GitHub Actions, every 15 minutes while the market is open).

Writes the page's snapshot (top candidates per list and speed, chart candles, biggest movers, market hours) to
.cache/stocks_out/snapshot.json, which the workflow publishes on the `data` branch (no history, so the repository
doesn't grow). The track record (state/stocks/) is updated about once an hour.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.config import REPO_ROOT, state_path
from core.log import get_logger
from dex.live import clean
from scanner.defence import declutter_exits
from scanner.features import coin_features
from scanner.model import PT, SL, ScannerModel, risk_unit
from scanner.rank import grade, score_from_r
from scanner.styles import STOCK_STYLES
from stocks import rank as R
from stocks.data import candles, earnings, load_all, market_status, screener

log = get_logger("stocks.live")
LISTS = {"large": "Large US stocks", "volatile": "High-volatility stocks"}
TOP_KEEP = 15
LIVE_BARS = {"stk_today": 400, "stk_days": 400}
CHART_BARS = {"stk_today": 60, "stk_days": 70}


def out_path():
    d = REPO_ROOT / ".cache" / "stocks_out"
    d.mkdir(parents=True, exist_ok=True)
    return d / "snapshot.json"


def _load_json(name: str, default):
    try:
        return json.loads(state_path("stocks", name).read_text())
    except (OSError, ValueError):
        return default


def load_models() -> dict:
    from scanner.live import models_dir

    return {k: ScannerModel.load(models_dir(), k) for k in STOCK_STYLES if ScannerModel.exists(models_dir(), k)}


def earnings_cached() -> dict:
    e = _load_json("earnings.json", {})
    now = pd.Timestamp.now(tz="UTC")
    if not e.get("at") or now - pd.Timestamp(e["at"]) > pd.Timedelta(hours=12):
        e = {"at": str(now), "dates": earnings(8)}
        state_path("stocks", "earnings.json").write_text(json.dumps(e, indent=1))
    return e["dates"]


def in_window(report: str | None, key: str, today: pd.Timestamp) -> str | None:
    if not report:
        return None
    d = pd.Timestamp(report[:10])
    days = (d - today.tz_localize(None).normalize()).days
    return report if (key == "stk_days" and 0 <= days <= 5) or (key == "stk_today" and days == 0) else None


def score_list(uni: pd.DataFrame, key: str, model: ScannerModel, cs: dict, spy: pd.DataFrame, earn: dict,
               market: dict) -> list[dict]:
    st = STOCK_STYLES[key]
    today_ny = pd.Timestamp.now(tz="America/New_York")
    today = today_ny.strftime("%Y-%m-%d")
    rows = []
    info = uni.set_index("symbol")
    for sym in uni["symbol"]:
        df = cs.get(sym)
        if df is None or len(df) < 150:
            continue
        f = coin_features(df, spy).iloc[-1]
        p = float(model.predict(f.to_frame().T)[0])
        ru = float(risk_unit(df, st).iloc[-1])
        close = float(df["close"].iloc[-1])
        rep = in_window(earn.get(sym), key, today_ny)
        pen, warn = R.flags(f, close, st, rep, today)
        dv = float(info.at[sym, "dollar_vol"])
        cost = R.cost_rt(dv)
        r = p * model.win_r - (1 - p) * model.loss_r - cost / (SL * ru) - pen
        stop, target = declutter_exits(close, close * (1 - SL * ru), close * (1 + PT * ru),
                                       float(df["low"].iloc[-24:].min()), ru, SL)
        sc = score_from_r(r)
        rows.append({
            "symbol": sym, "name": info.at[sym, "name"], "sector": info.at[sym, "sector"], "style": key,
            "p": round(p, 4), "risk_unit": round(ru, 5), "flag_penalty": round(pen, 4), "expected_r": round(r, 4),
            "score": round(sc, 2), "grade": grade(sc), "price": close, "candle_time": str(df.index[-1]),
            "take_profit_pct": target / close - 1, "safety_exit_pct": stop / close - 1, "dollar_vol": dv,
            "mcap": float(info.at[sym, "mcap"]), "chg_pct": float(info.at[sym, "chg_pct"]),
            "spread": R.spread(dv), "why": R.reasons(f, st), "warnings": warn, "earnings": earn.get(sym),
            "win_r": model.win_r, "loss_r": model.loss_r,
            "pre_ret": float(f["ret_24b"]) if np.isfinite(f["ret_24b"]) else None,
            "vol_surge": float(f["volume_surge"]) if np.isfinite(f["volume_surge"]) else None,
            "chart": {"t": [int(x.timestamp()) for x in df.index[-CHART_BARS[key]:]],
                      **{k: [round(float(v), 4) for v in df[k].iloc[-CHART_BARS[key]:]] for k in ("open", "high", "low", "close")}},
        })
    rows.sort(key=lambda d: -d["score"])
    return rows


def movers(scr: pd.DataFrame, lists: dict, swing: dict) -> list[dict]:
    s = scr[(scr["dollar_vol"] >= 5e6) & (scr["price"] >= 1)].sort_values("chg_pct", ascending=False).head(25)
    where = {}
    for lk, by_style in lists.items():
        for key, rows in by_style.items():
            for i, d in enumerate(rows):
                where.setdefault(d["symbol"], {})[f"{lk}:{key}"] = {"place": i + 1, "score": d["score"],
                                                                    "warn": (d["warnings"] or [None])[0]}
    out = []
    for _, r in s.iterrows():
        why = None
        if r["symbol"] not in where:
            if r["price"] < R.VOL_MIN_PRICE:
                why = "Price under $3: not scanned (penny stocks are too easy to push around)."
            elif r["mcap"] < R.VOL_MIN_MCAP:
                why = "Company worth under $300M: not scanned (too small and easy to push around)."
            elif r["dollar_vol"] < R.VOL_MIN_DOLLAR_VOL:
                why = "Trades under $10M a day: not scanned."
            elif r["mcap"] < R.LARGE_MIN_MCAP and (swing.get(r["symbol"]) or {}).get("up_pct", 0) < R.MIN_SWING:
                why = "Doesn't swing 20%+ in a week often enough for the high-volatility list."
            else:
                why = "Outside the most-traded stocks scanned this time."
        out.append({"symbol": r["symbol"], "name": r["name"], "chg_pct": float(r["chg_pct"]), "price": float(r["price"]),
                    "dollar_vol": float(r["dollar_vol"]), "in": where.get(r["symbol"], {}), "why": why})
    return out


def run(track_now: bool | None = None) -> dict:
    from scanner import track

    scr = screener()
    market = market_status()
    swing = _load_json("swing.json", {})
    large, vol = R.universes(scr, swing)
    earn = earnings_cached()
    models = load_models()
    lists: dict = {}
    track_now = bool(market.get("open")) if track_now is None else track_now
    for key, st in STOCK_STYLES.items():
        m = models.get(key)
        if m is None:
            log.warning("no model for %s yet", key)
            continue
        syms = sorted(set(large["symbol"]) | set(vol["symbol"]) | {"SPY"})
        cs = load_all(syms, st.interval, LIVE_BARS[key])
        spy = cs.get("SPY")
        for lk, uni in (("large", large), ("volatile", vol)):
            rows = score_list(uni, key, m, cs, spy, earn, market)
            lists.setdefault(lk, {})[key] = rows[:TOP_KEEP]
            if track_now and rows:
                hist = f"stocks/history_{lk}.csv"
                ideas = [{"ts": d["candle_time"], "style": key, "rank": i + 1, "symbol": d["symbol"], "score": d["score"],
                          "grade": d["grade"], "chance_beats_market": d["p"], "risk_unit": d["risk_unit"],
                          "price_now": d["price"], "take_profit_pct": d["take_profit_pct"],
                          "safety_exit_pct": d["safety_exit_pct"], "pre_ret": d["pre_ret"], "vol_surge": d["vol_surge"],
                          "cost_rt": R.cost_rt(d["dollar_vol"])} for i, d in enumerate(rows[:5])]
                track.append(ideas, len(rows), hist, min_gap_minutes=45)
                h = track.load_history(hist)
                need = set(h.loc[h["status"] == "open", "symbol"]) - set(cs) if len(h) else set()
                extra = {s: candles(s, st.interval, LIVE_BARS[key]) for s in list(need)[:40]}
                track.resolve({**cs, **extra}, key, hist)
                track.scoreboard(hist, f"stocks/scoreboard_{lk}.json", STOCK_STYLES)
    payload = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "market": market,
               "styles": {k: {"label": s.label, "hold_minutes": s.horizon_minutes, "interval": s.interval,
                              "hold_text": "2 hours" if k == "stk_today" else "3 trading days",
                              "bar_minutes": s.bar_minutes, "min_risk": s.min_risk, "max_risk": s.max_risk}
                          for k, s in STOCK_STYLES.items()},
               "models": {k: {"win_r": m.win_r, "loss_r": m.loss_r, "info": m.info} for k, m in models.items()},
               "lists": lists, "list_labels": LISTS, "counts": {"large": len(large), "volatile": len(vol)},
               "movers": movers(scr, lists, swing), "sec_fee": R.SEC_FEE}
    out_path().write_text(json.dumps(clean(payload), separators=(",", ":"), default=str, allow_nan=False))
    log.info("stocks: %s", {lk: {k: [d["symbol"] for d in v[:5]] for k, v in by.items()} for lk, by in lists.items()})
    return payload

