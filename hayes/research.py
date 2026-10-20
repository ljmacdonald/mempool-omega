"""Engine A's event study (phase 5) on every Binance USDT coin since 2019, delisted coins included. Price and volume
only: past supply, fees and unlock schedules aren't available for free, so those gates can't be applied to history
(stated in the report). Every number comes from data up to the day in question.

Events: the first day a coin's trough sits 75-90% below its 365-day high (recovered at most 60%, trough 14-180 days
old, candles complete), with 18+ months of history and $5M+ average daily volume. Each event is measured from that
first touch, and from the first day each sleeve's confirmation passed (no perpetuals in this history: the
confirmation is the price-and-volume part, and each threshold is 5 points higher, as the specification says).
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

import pandas as pd

from hayes import regime as RG
from hayes import scoring as SC
from hayes.profile import load

STABLES = {"USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USDE", "PYUSD", "EUR", "EURI", "AEUR", "USD1", "XUSD", "BFUSD", "UST", "USDS", "PAX", "SUSD"}
WRAPPED = {"WBTC", "WBETH", "BETH", "STETH", "WSTETH", "BNSOL", "PAXG", "XAUT"}
HORIZONS = (30, 90, 180)


def eligible_symbol(base: str) -> bool:
    b = base.upper()
    return b not in STABLES and b not in WRAPPED and not b.endswith(("UP", "DOWN", "BULL", "BEAR")) and "USD" not in b


def forward(c: list[dict], i: int, last_trading: bool) -> dict:
    """Returns from the close of day i; a coin that stopped trading before the horizon exits at its last close."""
    p0 = c[i]["c"]
    out = {"delisted": {}, "ret": {}}
    for h in HORIZONS:
        j = i + h
        if j < len(c):
            out["ret"][h] = c[j]["c"] / p0 - 1
            out["delisted"][h] = False
        elif not last_trading:
            out["ret"][h] = c[-1]["c"] / p0 - 1
            out["delisted"][h] = True
        else:
            out["ret"][h] = None                               # the horizon hasn't come yet
    seg = c[i + 1:i + 366]
    out["max_mult"] = max((x["h"] for x in seg), default=p0) / p0
    low180 = min((x["l"] for x in c[i + 1:i + 181]), default=p0) / p0
    out["fell_50"], out["fell_80"] = low180 <= 0.5, low180 <= 0.2
    out["complete_365"] = len(seg) >= 365 or not last_trading
    return out


def entry_rules(P: dict) -> dict:
    """The ways of entering an event, from the profile: Engine A's two sleeves, or Engine B's single entry rule. No
    perpetuals in this history: the confirmation's price-and-volume part, with the profile's higher no-perps threshold."""
    if "sleeves" in P:
        add = P["confirmation"]["no_perps_threshold_add"]
        return {k: {"min": P["sleeves"][k]["min_confirmation"] + add, "req": P["sleeves"][k]["required_components"], "base_days": 0}
                for k in ("asymmetric", "confirmed")}
    C = P["confirmation"]
    return {"entry": {"min": C["no_perps_min_confirmation"], "req": C["required_components"], "base_days": C["min_base_days"]}}


def events_for(sym: str, c: list[dict], btc_close: dict, trading: bool, regime_of, P: dict | None = None) -> list[dict]:
    P = P or load()
    hist_d = int(P["universe"]["min_history_months"] * 30.4)
    if len(c) < hist_d:
        return []
    df = pd.DataFrame(c)
    roll = df["h"].rolling(P["distress"]["lookback_days"], min_periods=1).max()
    adv = df["v"].rolling(30).mean()
    ceiling = (1 + P["distress"]["band"][1]) * (1 + P["distress"]["recovery_cap"])     # the highest price a passing day can have
    cand = [i for i in range(hist_d, len(c)) if df["c"].iat[i] <= ceiling * roll.iat[i] and (adv.iat[i] or 0) >= P["universe"]["liquidity"]["min_adv_usd"]]
    out, seen = [], set()
    rules = entry_rules(P)
    span = P["distress"]["trough_age_days"][1]
    for i in cand:
        if c[i]["d"] < "2019-01-01":
            continue
        d = SC.distress(c[max(0, i - 400):i + 1], P)
        if not d["pass"] or d["H_day"] in seen:
            continue
        seen.add(d["H_day"])
        ev = {"symbol": sym, "touch": c[i]["d"], "H_day": d["H_day"], "trough_dd": d["trough_dd"], "regime_touch": regime_of(c[i]["d"]),
              "touch_fwd": forward(c, i, trading), "btc_touch": btc_fwd(btc_close, c[i]["d"])}
        pending = dict(rules)
        for j in range(i, min(len(c), i + span + 1)):           # the first day each entry rule passed, within the episode
            if not pending:
                break
            dj = SC.distress(c[max(0, j - 400):j + 1], P)
            if dj.get("H_day") != d["H_day"] or not dj.get("L"):
                break
            if not dj["pass"]:
                continue
            cf = SC.confirmation(c[max(0, j - 400):j + 1], btc_close, dj["L_day"], dj["L"], None, P)
            for name, r in list(pending.items()):
                if cf["score"] >= r["min"] and all(cf["components"].get(x) for x in r["req"]) and dj["trough_age"] >= r["base_days"]:
                    ev[name] = {"day": c[j]["d"], "score": cf["score"], "regime": regime_of(c[j]["d"]), "fwd": forward(c, j, trading),
                                "btc": btc_fwd(btc_close, c[j]["d"])}
                    pending.pop(name)
        out.append(ev)
    return out


def btc_fwd(btc_close: dict, day: str) -> dict:
    d0 = date.fromisoformat(day)
    p0 = btc_close.get(day)
    out = {}
    for h in HORIZONS:
        p1 = btc_close.get((d0 + timedelta(days=h)).isoformat())
        out[h] = (p1 / p0 - 1) if p0 and p1 else None
    return out


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    f = int(k)
    return xs[f] + (xs[min(f + 1, len(xs) - 1)] - xs[f]) * (k - f)


def summarise(rows: list[dict]) -> dict:
    """rows: [{fwd, btc, regime}] for one way of entering."""
    out = {"n": len(rows)}
    for h in HORIZONS:
        r = [x["fwd"]["ret"][h] for x in rows if x["fwd"]["ret"].get(h) is not None]
        b = [x["btc"].get(h) for x in rows if x["fwd"]["ret"].get(h) is not None and x["btc"].get(h) is not None]
        out[str(h)] = {"n": len(r), "median": statistics.median(r) if r else None, "p10": pct(r, 0.1), "p90": pct(r, 0.9),
                       "btc_median": statistics.median(b) if b else None,
                       "delisted": sum(1 for x in rows if x["fwd"]["delisted"].get(h)) / len(r) if r else None}
    full = [x for x in rows if x["fwd"]["complete_365"]]
    for m in (2, 3, 5, 10):
        out[f"reached_{m}x"] = sum(1 for x in full if x["fwd"]["max_mult"] >= m) / len(full) if full else None
    out["fell_another_50"] = sum(1 for x in rows if x["fwd"]["fell_50"]) / len(rows) if rows else None
    out["fell_another_80"] = sum(1 for x in rows if x["fwd"]["fell_80"]) / len(rows) if rows else None
    out["delisted_or_fell_80"] = sum(1 for x in rows if x["fwd"]["fell_80"] or any(x["fwd"]["delisted"].values())) / len(rows) if rows else None
    out["n_full_year"] = len(full)
    return out


def regime_fn(fred_series: dict, stables: list, btc_close: dict, profile: dict | None = None, full: bool = False):
    """day -> the regime that day (cached), from the inputs as published by then; full=True returns the whole record."""
    Pf = profile or load()
    P = Pf["regime"]["publication_lag_days"]
    w = RG.Series(fred_series["WALCL"], P["WALCL"], RG.UNIT_TO_MILLIONS["WALCL"])
    r = RG.Series(fred_series["RRPONTSYD"], P["RRPONTSYD"], RG.UNIT_TO_MILLIONS["RRPONTSYD"])
    t = RG.Series(fred_series["WTREGEN"], P["WTREGEN"], RG.UNIT_TO_MILLIONS["WTREGEN"])
    s = RG.Series(stables, P["stablecoins"])
    cache: dict[str, dict] = {}

    def regime_of(day: str):
        if day not in cache:
            cache[day] = RG.classify(date.fromisoformat(day), w, r, t, s, btc_close, Pf)
        return cache[day] if full else cache[day]["state"]
    return regime_of


def event_study(series: dict[str, list[dict]], trading: set[str], btc: list[dict], fred_series: dict, stables: list,
                profile: dict | None = None) -> dict:
    """series: symbol -> candles (Binance USDT, daily); trading: symbols still trading today. profile: Engine A's by default."""
    Pf = profile or load()
    btc_close = {x["d"]: x["c"] for x in btc}
    regime_of = regime_fn(fred_series, stables, btc_close, Pf)
    events = []
    for sym, c in series.items():
        events += events_for(sym, c, btc_close, sym in trading, regime_of, Pf)
    out = {"engine": Pf["engine"], "profile_version": Pf["profile_version"], "coins": len(series),
           "delisted_coins": sum(1 for x in series if x not in trading), "events": len(events),
           "events_by_coin": {sym: n for sym in series if (n := sum(1 for e in events if e["symbol"] == sym))},
           "regime_inputs_ok": bool(fred_series["WALCL"] and fred_series["RRPONTSYD"] and fred_series["WTREGEN"] and stables)}
    for way in ("touch", *entry_rules(Pf)):
        rows = [{"fwd": e["touch_fwd"], "btc": e["btc_touch"], "regime": e["regime_touch"]} for e in events] if way == "touch" else \
               [{"fwd": e[way]["fwd"], "btc": e[way]["btc"], "regime": e[way]["regime"]} for e in events if e.get(way)]
        out[way] = {"all": summarise(rows), **{st: summarise([x for x in rows if x["regime"] == st]) for st in ("risk_on", "neutral", "risk_off")}}
    out["samples"] = [{"symbol": e["symbol"], "touch": e["touch"], "trough_dd": e["trough_dd"], "regime": e["regime_touch"],
                       "ret_180": e["touch_fwd"]["ret"].get(180), "max_mult": e["touch_fwd"]["max_mult"],
                       **{f"{k}_day": (e.get(k) or {}).get("day") for k in entry_rules(Pf)}}
                      for e in sorted(events, key=lambda e: e["touch"])[-200:]]
    return out


def kill_a(es: dict, P: dict | None = None) -> dict:
    """Engine A's event-study kill criteria (applied automatically)."""
    K = (P or load())["kill"]
    conf = es["confirmed"]["risk_on"]["180"]
    k1 = conf["median"] is not None and conf["btc_median"] is not None and conf["median"] > conf["btc_median"]
    asym = es["asymmetric"]["all"].get("reached_3x")
    k2 = asym is not None and asym >= K["event_study_min_3x_share"]
    return {"confirmed_beats_btc_risk_on_180d": {"pass": k1, "median": conf["median"], "btc_median": conf["btc_median"], "n": conf["n"]},
            "asymmetric_3x_share": {"pass": k2, "share": asym, "min": K["event_study_min_3x_share"], "n": es["asymmetric"]["all"]["n_full_year"]},
            "status": "paper" if k1 and k2 and es["regime_inputs_ok"] else "halted"}
