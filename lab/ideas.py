"""Trade ideas from the Strategy lab: fresh entries of the re-tested strategies, ranked and graded like every other
page. Each idea is judged by how that strategy did on that market in the nightly re-test (lab/run.py backtest):

* chance it makes money: the market's hit rate after costs, pulled towards a coin flip when there are few trades;
* expected result per $1 put at risk: the average trade after costs (pulled towards zero when there are few trades)
  divided by the average losing trade, or by the distance to the safety exit when the strategy has one;
* minus penalties: the strategy failed (or is unproven) in the re-test, the price already ran away from the entry,
  high-impact news before the trade usually ends, and probation when its live ideas do worse than random entries;
* plus the jumpiness adjustment learned from the live record (scanner/quality.py), as on every page;
* -> the same score out of 10 and grade words as the other pages, switched off automatically if the grades stop
  telling the truth.

Every idea is recorded when it first appears and followed to the strategy's own exit, so the grades are checked.
"""
from __future__ import annotations

import pandas as pd

from core.config import state_path
from fx import checks as C
from lab import strategies as L
from scanner.quality import adjust_r, quality
from scanner.rank import grade as grade_of
from scanner.rank import score_from_r

FRESH = {"5m": 6, "1h": 3, "1d": 1}       # an entry counts as a new idea for this many candles
K_HIT, K_AVG = 20, 30                      # shrinkage: trades' worth of "coin flip" and "zero" added to the history
LEVEL_PEN = {"good": 0.0, "warn": 0.10, "bad": 0.30}
EVIDENCE = {"good": "Held up in tests", "warn": "Not proven in tests", "bad": "Failed in tests"}
MIN_PROVEN = 30
HIST = "lab/ideas.csv"
HIST_COLS = ["id", "strategy", "sym", "label", "side", "t_in", "seen_at", "entry", "stop", "grade", "score", "prob", "exp_r",
             "risk_unit", "sl_pct", "status", "net", "base"]


def news_pair(sym: str) -> str:
    """A 'pair' whose currencies are the ones whose news moves this market (fx.checks.news_in)."""
    return sym[:6] if sym.endswith("=X") else "USDUSD"


def judge(idea: dict, m: dict | None, level: str, price_now: float, events: list, now: pd.Timestamp, hold_h: float,
          vol: dict | None, probation: dict | None) -> dict:
    m = m or {}
    n = int(m.get("n") or 0)
    hit, avg = m.get("hit"), m.get("avg")
    wins = (hit or 0.0) * n
    prob = (wins + 0.5 * K_HIT) / (n + K_HIT)
    exp_ret = (avg or 0.0) * n / (n + K_AVG)
    loss = abs(m.get("avg_loss") or 0.0) or 0.02
    stop = idea.get("stop")
    sl = abs(idea["entry"] - stop) / idea["entry"] if stop else None
    ru = sl if sl and sl > 0 else loss
    exp_r = exp_ret / ru
    warnings, pen = [], LEVEL_PEN[level]
    moved = idea["side"] * (price_now / idea["entry"] - 1)
    avg_win = m.get("avg_win") or 0.0
    late = avg_win > 0 and moved > 0.5 * avg_win
    if late:
        pen += 0.10
        warnings.append(f"The price has already moved {moved:+.2%} in the trade's favour since the strategy entered; its average "
                        f"winning trade makes {avg_win:+.2%}, so much of the usual gain may be gone.")
    if stop and idea["side"] * (price_now - stop) <= 0:
        warnings.append("The price is already past the safety exit: the strategy's trade is effectively over.")
        pen += 1.0
    end = now + pd.Timedelta(hours=max(hold_h, 1.0))
    inside = C.news_in(events, news_pair(idea["sym"]), now, end)
    if inside:
        pen += 0.25
        e = inside[0]
        warnings.append(f"High-impact news before this trade usually ends: {e['title']} ({e['country']}) at "
                        f"{e['at'].strftime('%a %H:%M')} UTC. Prices can jump straight through stops.")
    if level == "bad":
        warnings.append("This strategy failed the re-test (see below): shown so you can see what it's doing, not as a trade to take.")
    if probation and probation.get("active"):
        pen += probation.get("penalty", 0.1)
        warnings.append("On probation: this strategy's live ideas have done worse than random entries.")
    adj = adjust_r(exp_r, ru, vol) - pen
    sc = score_from_r(adj)
    return {"prob": prob, "exp_ret": exp_ret, "exp_r": exp_r, "exp_r_adj": adj, "score": sc, "grade": grade_of(sc),
            "evidence": EVIDENCE[level], "risk_unit": ru, "sl_pct": sl, "moved": moved, "late": late, "warnings": warnings,
            "hist_n": n, "avg_win": m.get("avg_win"), "avg_loss": m.get("avg_loss"), "hold_h": hold_h}


def fresh(trades: list[dict], n_bars: int, tf: str) -> list[dict]:
    return [t for t in trades if t.get("open") and t["i_in"] >= n_bars - FRESH[tf]]


def rsi2_forming(daily: pd.DataFrame, five: pd.DataFrame | None, now: pd.Timestamp) -> dict | None:
    """During the New York session: would today's close give a Connors RSI(2) buy if the price stayed where it is?
    Uses the latest finished 5-minute candle as a stand-in for today's close."""
    if five is None or not len(five) or daily is None or len(daily) < 201:
        return None
    ny_now = now.tz_convert("America/New_York")
    last = five.index[-1].tz_convert("America/New_York")
    if last.date() != ny_now.date() or last.date() == daily.index[-1].tz_convert("America/New_York").date():
        return None
    px = float(five["close"].iloc[-1])
    c = pd.concat([daily["close"], pd.Series([px], index=[five.index[-1]])])
    r2 = float(L.rsi(c, 2).iloc[-1])
    above = px > float(c.rolling(200).mean().iloc[-1])
    return {"price": px, "rsi2": r2, "above200": above, "asof": int((five.index[-1] + pd.Timedelta(minutes=5)).value // 10**6),
            "signal": bool(above and r2 < 10)}


# ---------------------------------------------------------------------------------------------- track record
def track(found: list[dict], done_by_key: dict, now: pd.Timestamp) -> pd.DataFrame:
    """Record new ideas; fill in results when the strategy closes the trade (matched by strategy, market, entry time)."""
    p = state_path(HIST)
    h = pd.read_csv(p, dtype={"id": str}) if p.exists() else pd.DataFrame(columns=HIST_COLS)
    h = h.reindex(columns=HIST_COLS)
    h["grade"] = h["grade"].astype(object)
    h["status"] = h["status"].astype(object)
    known = set(h["id"])
    new = [x for x in found if x["id"] not in known]
    if new:
        h = pd.concat([h, pd.DataFrame([{**{c: x.get(c) for c in HIST_COLS}, "status": "open", "seen_at": str(now)} for x in new])],
                      ignore_index=True)
    for i, row in h[h["status"] == "open"].iterrows():
        d = done_by_key.get((row["strategy"], row["sym"], int(row["t_in"])))
        if d is not None:
            h.loc[i, ["status", "net", "base"]] = ["closed", d["net"], d["base"]]
    h.to_csv(p, index=False)
    return h


def quality_frame(h: pd.DataFrame) -> pd.DataFrame:
    d = h[(h["status"] == "closed") & h["net"].notna() & h["grade"].notna()].copy()
    d["net_ret"] = d["net"].astype(float)
    d["sl_pct"] = -d["sl_pct"].astype(float)
    d["risk_unit"] = d["risk_unit"].astype(float)
    d["baseline_ret"] = d["base"].astype(float)
    d["symbol"] = d["strategy"]
    return d


def self_check(h: pd.DataFrame, names: dict) -> tuple[dict, dict]:
    """The grade check and jumpiness adjustment over all the lab's ideas (with a table per strategy), and probation
    per strategy when its finished live ideas did worse than random entries."""
    d = quality_frame(h.reindex(columns=HIST_COLS))
    qual = quality(d, group=lambda k: names.get(k, k), order=list(names.values()))
    prob = {}
    for k in names:
        f = d[d["strategy"] == k]
        n = len(f)
        avg = float(f["net_ret"].mean()) if n else float("nan")
        base = float(f["baseline_ret"].dropna().mean()) if n and f["baseline_ret"].notna().any() else float("nan")
        prob[k] = {"active": bool(n >= MIN_PROVEN and base == base and avg < base), "n": n, "avg": avg, "random": base, "penalty": 0.1}
    return qual, prob


def record(h: pd.DataFrame, names: dict) -> dict:
    d = h[h["status"] == "closed"].copy()
    out = {}
    for k in names:
        f = d[d["strategy"] == k]
        nets = f["net"].astype(float)
        out[k] = {"n": int(len(f)), "hit": float((nets > 0).mean()) if len(f) else None, "avg": float(nets.mean()) if len(f) else None,
                  "random": float(f["base"].astype(float).mean()) if len(f) and f["base"].notna().any() else None,
                  "open": int((h["strategy"] == k).sum() - len(f))}
    out["recent"] = [{"strategy": r["strategy"], "name": names.get(r["strategy"], r["strategy"]), "label": r["label"], "side": int(r["side"]),
                      "t_in": int(r["t_in"]), "grade": r["grade"], "net": float(r["net"])} for r in d.tail(30).to_dict("records")][::-1]
    return out
