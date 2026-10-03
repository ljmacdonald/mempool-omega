"""Track record: every suggestion is replayed against what the price actually did afterwards,
separately for each trading speed. Same rules as the idea itself (buy at the next candle's open,
take-profit / safety exit / time limit, 0.2 % fees). "Random pick" baseline = the same rules applied
to EVERY coin scanned at that moment.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.config import state_path
from scanner.model import risk_unit, simulate_idea
from scanner.styles import STYLES

HIST = "suggestions/history.csv"


def load_history() -> pd.DataFrame:
    p = state_path(HIST)
    if not p.exists():
        return pd.DataFrame()
    h = pd.read_csv(p)
    if "style" not in h:
        h["style"] = "day"          # rows made before trading speeds existed
    h["style"] = h["style"].fillna("day")
    return h


def append(ideas: list[dict], universe_size: int) -> None:
    rows = [{"ts": d["ts"], "style": d["style"], "rank": d["rank"], "symbol": d["symbol"], "score": d["score"],
             "grade": d["grade"], "chance_beats_market": d["chance_beats_market"], "risk_unit": d["risk_unit"],
             "price_at_idea": d["price_now"], "universe_size": universe_size, "status": "open", "outcome": "",
             "net_ret": np.nan, "hours": np.nan, "baseline_ret": np.nan} for d in ideas]
    h = load_history()
    new = pd.DataFrame(rows)
    if len(h):
        key = ["ts", "symbol", "style"]
        new = new[~new.set_index(key).index.isin(h.set_index(key).index)]
        h = pd.concat([h, new], ignore_index=True)
    else:
        h = new
    h.to_csv(state_path(HIST), index=False)


def resolve(candles: dict[str, pd.DataFrame], style_key: str) -> int:
    """Settle this speed's open suggestions whose outcome is now known."""
    h = load_history()
    if h.empty:
        return 0
    style = STYLES[style_key]
    settled = 0
    cache: dict[str, float] = {}
    for i, row in h[(h["status"] == "open") & (h["style"] == style_key)].iterrows():
        df = candles.get(row["symbol"])
        ts = pd.Timestamp(row["ts"])
        if df is None or ts not in df.index:
            if pd.Timestamp.now(tz="UTC") - ts > pd.Timedelta(minutes=style.horizon_minutes) * 6 + pd.Timedelta(days=2):
                h.loc[i, ["status", "outcome"]] = ["expired", "no_data"]
            continue
        t = df.index.get_loc(ts)
        res = simulate_idea(df, t, float(row["risk_unit"]), style.horizon_bars)
        if res is None:
            continue
        net, outcome, bars = res
        if row["ts"] not in cache:
            cache[row["ts"]] = _baseline(candles, ts, style)
        h.loc[i, ["status", "outcome", "net_ret", "hours", "baseline_ret"]] = [
            "closed", outcome, net, bars * style.bar_minutes / 60, cache[row["ts"]]]
        settled += 1
    h.to_csv(state_path(HIST), index=False)
    return settled


def _baseline(candles: dict[str, pd.DataFrame], ts: pd.Timestamp, style) -> float:
    rets = []
    for df in candles.values():
        if ts in df.index:
            t = df.index.get_loc(ts)
            res = simulate_idea(df, t, float(risk_unit(df, style).iloc[t]), style.horizon_bars)
            if res:
                rets.append(res[0])
    return float(np.mean(rets)) if rets else np.nan


def _summary(c: pd.DataFrame) -> dict:
    if not len(c):
        return {"closed": 0}
    return {"closed": int(len(c)), "win_rate": float((c["net_ret"] > 0).mean()),
            "avg_return_per_idea": float(c["net_ret"].mean()),
            "random_pick_avg_return": float(c["baseline_ret"].mean()),
            "if_100usd_each_total_pnl": float(100 * c["net_ret"].sum()),
            "outcomes": c["outcome"].value_counts().to_dict()}


def scoreboard() -> dict:
    h = load_history()
    if h.empty:
        return {"closed": 0}
    c = h[h["status"] == "closed"]
    out = {"total_suggestions": int(len(h)), "still_open": int((h["status"] == "open").sum()), **_summary(c),
           "by_style": {k: _summary(c[c["style"] == k]) for k in STYLES},
           "by_grade": {g: _summary(x) for g, x in c.groupby("grade")} if len(c) else {}}
    state_path("suggestions/scoreboard.json").write_text(json.dumps(out, indent=2, default=str))
    return out
