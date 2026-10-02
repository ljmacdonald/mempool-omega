"""Track record: every suggestion is replayed against what the price actually did afterwards.

Same rules as the idea itself: buy at the next hour's open, sell at take-profit or safety exit,
or after 24 hours, minus 0.2 % fees. "Random pick" baseline = the same rules applied to EVERY coin
the scanner looked at that hour, so you can see whether the top 5 beat picking at random.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.config import state_path
from scanner.model import risk_unit, simulate_idea

HIST = "suggestions/history.csv"


def load_history() -> pd.DataFrame:
    p = state_path(HIST)
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def append(ideas: list[dict], universe_size: int) -> None:
    rows = [{"ts": d["ts"], "rank": d["rank"], "symbol": d["symbol"], "score": d["score"], "grade": d["grade"],
             "chance_of_profit": d["chance_of_profit"], "risk_unit": d["risk_unit"], "price_at_idea": d["price_now"],
             "universe_size": universe_size, "status": "open", "outcome": "", "net_ret": np.nan, "hours": np.nan,
             "baseline_ret": np.nan} for d in ideas]
    h = load_history()
    new = pd.DataFrame(rows)
    if len(h):
        new = new[~new.set_index(["ts", "symbol"]).index.isin(h.set_index(["ts", "symbol"]).index)]
        h = pd.concat([h, new], ignore_index=True)
    else:
        h = new
    h.to_csv(state_path(HIST), index=False)


def resolve(candles: dict[str, pd.DataFrame]) -> int:
    """Settle open suggestions whose outcome is now known. Returns how many were settled."""
    h = load_history()
    if h.empty:
        return 0
    settled = 0
    baseline_cache: dict[str, float] = {}
    for i, row in h[h["status"] == "open"].iterrows():
        df = candles.get(row["symbol"])
        ts = pd.Timestamp(row["ts"])
        if df is None or ts not in df.index:
            if pd.Timestamp.now(tz="UTC") - ts > pd.Timedelta(days=9):
                h.loc[i, ["status", "outcome"]] = ["expired", "no_data"]
            continue
        t = df.index.get_loc(ts)
        res = simulate_idea(df, t, float(row["risk_unit"]))
        if res is None:
            continue
        net, outcome, hours = res
        if row["ts"] not in baseline_cache:
            baseline_cache[row["ts"]] = _baseline(candles, ts)
        h.loc[i, ["status", "outcome", "net_ret", "hours", "baseline_ret"]] = [
            "closed", outcome, net, hours, baseline_cache[row["ts"]]]
        settled += 1
    h.to_csv(state_path(HIST), index=False)
    return settled


def _baseline(candles: dict[str, pd.DataFrame], ts: pd.Timestamp) -> float:
    rets = []
    for df in candles.values():
        if ts in df.index:
            ru = risk_unit(df)
            t = df.index.get_loc(ts)
            res = simulate_idea(df, t, float(ru.iloc[t]))
            if res and res[1] != "time_limit" or (res and res[2] == 24):
                rets.append(res[0])
    return float(np.mean(rets)) if rets else np.nan


def scoreboard() -> dict:
    h = load_history()
    if h.empty:
        return {"closed": 0}
    c = h[h["status"] == "closed"]
    out = {"total_suggestions": int(len(h)), "closed": int(len(c)), "still_open": int((h["status"] == "open").sum())}
    if len(c):
        out.update({
            "win_rate": float((c["net_ret"] > 0).mean()),
            "avg_return_per_idea": float(c["net_ret"].mean()),
            "random_pick_avg_return": float(c["baseline_ret"].mean()),
            "if_100usd_each_total_pnl": float(100 * c["net_ret"].sum()),
            "outcomes": c["outcome"].value_counts().to_dict(),
            "by_grade": {g: {"n": int(len(x)), "win_rate": float((x["net_ret"] > 0).mean()),
                             "avg_return": float(x["net_ret"].mean())} for g, x in c.groupby("grade")},
            "rank1_avg_return": float(c.loc[c["rank"] == 1, "net_ret"].mean()) if (c["rank"] == 1).any() else None,
        })
    state_path("suggestions/scoreboard.json").write_text(json.dumps(out, indent=2, default=str))
    return out
