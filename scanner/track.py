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
from scanner.model import COST, risk_unit, simulate_idea
from scanner.styles import STYLES, get_style

HIST = "suggestions/history.csv"


def load_history(hist: str = HIST) -> pd.DataFrame:
    p = state_path(hist)
    if not p.exists():
        return pd.DataFrame()
    h = pd.read_csv(p)
    if "style" not in h:
        h["style"] = "day"          # rows made before trading speeds existed
    h["style"] = h["style"].fillna("day")
    return _normalise(h)


TEXT_COLS = ("ts", "style", "symbol", "grade", "status", "outcome", "failed_checks")
NUM_COLS = ("net_ret", "hours", "baseline_ret", "risk_unit", "score", "tp_pct", "sl_pct", "pre_ret", "vol_surge",
            "integrity_penalty", "cost_rt", "first_hour_runup", "first_hour_runup_base")


def _normalise(h: pd.DataFrame) -> pd.DataFrame:
    """Fix column types after reading CSV. An all-empty column comes back as numbers, and pandas 3 then
    refuses to store words in it (this once stopped ideas from being settled)."""
    for c in TEXT_COLS:
        if c in h:
            h[c] = h[c].astype(object).where(h[c].notna(), "").astype(str)
    for c in NUM_COLS:
        if c in h:
            h[c] = pd.to_numeric(h[c], errors="coerce").astype(float)
    return h


def append(ideas: list[dict], universe_size: int, hist: str = HIST, min_gap_minutes: float = 0) -> None:
    """Add the ideas as open rows. With `min_gap_minutes`, a speed that already has ideas saved less than that long
    before these is skipped, so a job that runs every 15 minutes still records about one set an hour."""
    rows = [{"ts": d["ts"], "style": d["style"], "rank": d["rank"], "symbol": d["symbol"], "score": d["score"],
             "grade": d["grade"], "chance_beats_market": d["chance_beats_market"], "risk_unit": d["risk_unit"],
             "price_at_idea": d["price_now"], "universe_size": universe_size, "status": "open", "outcome": "",
             "net_ret": np.nan, "hours": np.nan, "baseline_ret": np.nan,
             "tp_pct": d.get("take_profit_pct"), "sl_pct": d.get("safety_exit_pct"),
             "pre_ret": d.get("pre_ret"), "vol_surge": d.get("vol_surge"),
             "integrity_penalty": (d.get("integrity") or {}).get("penalty"),
             "failed_checks": "|".join(c["key"] for c in (d.get("integrity") or {}).get("checks", [])
                                       if c["ok"] is False) or "none",
             **({"cost_rt": d["cost_rt"], "pool": d.get("pool", "")} if "cost_rt" in d else {})} for d in ideas]
    if not rows:
        return
    h = load_history(hist)
    new = pd.DataFrame(rows)
    new = _normalise(new)
    if len(h) and min_gap_minutes:
        last = h.groupby("style")["ts"].max().map(lambda x: pd.Timestamp(x))
        new = new[[not (st in last and pd.Timestamp(ts) - last[st] < pd.Timedelta(minutes=min_gap_minutes))
                   for ts, st in zip(new["ts"], new["style"])]]
    if len(h):
        key = ["ts", "symbol", "style"]
        new = new[~new.set_index(key).index.isin(h.set_index(key).index)]
        h = pd.concat([h, new], ignore_index=True)
    else:
        h = new
    h.to_csv(state_path(hist), index=False)


def resolve(candles: dict[str, pd.DataFrame], style_key: str, hist: str = HIST) -> int:
    """Settle this speed's open suggestions whose outcome is now known. DEX rows carry their all-in round-trip
    cost (cost_rt: fees, impact, taxes, gas, front-running at $100); it replaces the CEX fee for the idea AND
    for the random-pick baseline, so both are compared after the same costs."""
    h = load_history(hist)
    if h.empty:
        return 0
    style = get_style(style_key)
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
        tp = row.get("tp_pct")
        sl = row.get("sl_pct")
        res = simulate_idea(df, t, float(row["risk_unit"]), style.horizon_bars,
                            float(tp) if pd.notna(tp) else None, float(sl) if pd.notna(sl) else None)
        if res is None:
            continue
        net, outcome, bars = res
        if row["ts"] not in cache:
            cache[row["ts"]] = _baseline(candles, ts, style)
        extra = float(row["cost_rt"]) - COST if "cost_rt" in h and pd.notna(row.get("cost_rt")) else 0.0
        h.loc[i, ["status", "outcome", "net_ret", "hours", "baseline_ret"]] = [
            "closed", outcome, net - extra, bars * style.bar_minutes / 60, cache[row["ts"]] - extra]
        settled += 1
    h.to_csv(state_path(hist), index=False)
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


def scoreboard(hist: str = HIST, out_path: str = "suggestions/scoreboard.json", styles=None) -> dict:
    styles = styles or STYLES
    h = load_history(hist)
    if h.empty:
        return {"closed": 0}
    c = h[h["status"] == "closed"]
    out = {"total_suggestions": int(len(h)), "still_open": int((h["status"] == "open").sum()), **_summary(c),
           "by_style": {k: _summary(c[c["style"] == k]) for k in styles},
           "by_grade": {g: _summary(x) for g, x in c.groupby("grade")} if len(c) else {}}
    state_path(out_path).write_text(json.dumps(out, indent=2, default=str))
    return out
