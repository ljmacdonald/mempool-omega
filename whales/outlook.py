"""Market signals and an outlook ON PROBATION for the coins the followed whales hold (DECISIONS D83).

Three standard signals per coin, worked out once a day from Hyperliquid's public data:
  trend     price above (+1) or below (-1) its 50-day average
  momentum  price higher (+1) or lower (-1) than 14 days ago
  crowding  3-day average funding above +30% a year: too many traders betting up (-1, contrarian);
            below -10% a year: too many betting down (+1); otherwise 0
Outlook: "up" when they add up to +2 or more, "down" at -2 or less, otherwise no clear lean.

Tested before it went on the page (2 years of daily prices and funding, 47 coins, 19,596 calls): the outlook was
right about the direction 50.3% of the time, a coin flip, with no result after costs distinguishable from zero.
So it is shown as "Not proven" and tested live: every call is recorded with the price, judged 7 days later after
costs, and the label only changes to "Held up" after 30+ days of calls that beat a coin flip clearly.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

log = logging.getLogger("omega.whales.outlook")
TREND_DAYS, MOM_DAYS = 50, 14
CROWD_HIGH, CROWD_LOW = 0.30, -0.10     # yearly funding
HOLD_DAYS = 7
COST_RT = 0.0014                        # a round trip, 0.07% a side
MIN_DAYS, MIN_CALLS = 30, 50
BACKTEST = {"coins": 47, "years": 2, "calls": 19596, "hit": 0.503, "up_hit": 0.455, "down_hit": 0.536}
COLS = ["date", "t", "coin", "lean", "score", "price", "whales", "exit_t", "exit_price", "net"]


def signals(closes: list[float], fund3: float | None) -> dict | None:
    """The three signals and the outlook from daily closes (oldest first) and the 3-day average yearly funding."""
    if len(closes) < TREND_DAYS + 1:
        return None
    c = closes[-1]
    trend = 1 if c > sum(closes[-TREND_DAYS:]) / TREND_DAYS else -1
    mom = 1 if c > closes[-1 - MOM_DAYS] else -1
    crowd = 0
    if fund3 is not None and math.isfinite(fund3):
        crowd = -1 if fund3 > CROWD_HIGH else 1 if fund3 < CROWD_LOW else 0
    score = trend + mom + crowd
    return {"trend": trend, "mom": mom, "crowd": crowd, "fund": fund3, "score": score,
            "lean": 1 if score >= 2 else -1 if score <= -2 else 0}


def fetch(post, coin: str, now_ms: int) -> dict | None:
    k = post({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1d", "startTime": now_ms - 75 * 86_400_000, "endTime": now_ms}}) or []
    closes = [float(x["c"]) for x in k if int(x["t"]) + 86_400_000 <= now_ms]      # finished days only
    f = post({"type": "fundingHistory", "coin": coin, "startTime": now_ms - 3 * 86_400_000, "endTime": now_ms}) or []
    rates = [float(x["fundingRate"]) for x in f]
    fund3 = sum(rates) / len(rates) * 24 * 365 if rates else None
    return signals(closes, fund3)


def refresh(post, load, save, coins: list[str], whale_side: dict, mids: dict, now_ms: int) -> dict:
    """Today's signals for the coins (worked out once a day, new coins added as they appear); records each day's
    calls and judges the calls made HOLD_DAYS ago."""
    day = pd.Timestamp(now_ms, unit="ms", tz="UTC").strftime("%Y-%m-%d")
    st = load("outlook.json", {})
    if st.get("date") != day:
        st = {"date": day, "coins": {}}
    calls = load("outlook_calls.json", [])
    made = {(x["date"], x["coin"]) for x in calls}
    for coin in coins:
        if coin in st["coins"] or ":" in coin:
            continue
        try:
            s = fetch(post, coin, now_ms)
        except Exception as e:  # noqa: BLE001
            log.warning("outlook %s: %s", coin, e)
            continue
        st["coins"][coin] = s
        if s and s["lean"] and (day, coin) not in made and mids.get(coin):
            calls.append({"date": day, "t": now_ms, "coin": coin, "lean": s["lean"], "score": s["score"], "price": mids[coin],
                          "whales": whale_side.get(coin, 0), "exit_t": None, "exit_price": None, "net": None})
    for x in calls:
        if x["net"] is None and now_ms - x["t"] >= HOLD_DAYS * 86_400_000 and mids.get(x["coin"]):
            x["exit_t"], x["exit_price"] = now_ms, mids[x["coin"]]
            x["net"] = x["lean"] * (x["exit_price"] / x["price"] - 1) - COST_RT
    save("outlook.json", st)
    save("outlook_calls.json", calls)
    return {"coins": st["coins"], "record": record(calls)}


def record(calls: list[dict]) -> dict:
    """The live record and its label: Held up only after MIN_DAYS of calls, MIN_CALLS judged, beating a coin flip."""
    done = [x for x in calls if x.get("net") is not None]
    out = {"open": sum(1 for x in calls if x.get("net") is None), "judged": len(done), "backtest": BACKTEST, "hold_days": HOLD_DAYS}
    days = sorted({x["date"] for x in done})
    if done:
        out.update({"hit": sum(1 for x in done if x["net"] > 0) / len(done), "avg": sum(x["net"] for x in done) / len(done), "days": len(days)})
        agree = [x for x in done if x["whales"] and x["whales"] == x["lean"]]
        if agree:
            out["agree"] = {"n": len(agree), "hit": sum(1 for x in agree if x["net"] > 0) / len(agree)}
    if len(days) < MIN_DAYS or len(done) < MIN_CALLS:
        return {**out, "level": "warn", "label": "Not proven",
                "text": f"About 50% right in a 2-year test (a coin flip). Live: {len(done)} call(s) judged so far; a verdict needs {MIN_CALLS} over {MIN_DAYS} days."}
    daily = pd.Series({d: sum(x["net"] for x in done if x["date"] == d) / sum(1 for x in done if x["date"] == d) for d in days})
    t = float(daily.mean() / (daily.std(ddof=1) / math.sqrt(len(daily)))) if len(daily) > 2 and daily.std(ddof=1) > 0 else float("nan")
    out["t"] = t
    if out["avg"] <= 0:
        return {**out, "level": "bad", "label": "Failed", "text": "The outlook lost money after costs in live testing: ignore it."}
    if not (t >= 2 and out["hit"] > 0.5):
        return {**out, "level": "warn", "label": "Not proven", "text": "Slightly positive so far, but not clearly better than a coin flip: could be luck."}
    return {**out, "level": "good", "label": "Held up", "text": "Right more often than a coin flip, after costs, over a month or more of live calls."}
