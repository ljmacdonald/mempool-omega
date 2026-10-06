"""What really happens after a coin is listed on a big exchange, and does any simple rule make money from it?

Every Binance and OKX USDT listing since 2023 (delisted coins included, tokenized stocks and stablecoins left out).
Five entry rules people commonly use, each held 7 or 30 days with a -30% safety exit, after costs (0.2% fees + 0.3%
slippage on thin new order books), and compared with simply holding established coins (median of BTC, ETH, SOL,
XRP, BNB, DOGE, ADA, LINK) over exactly the same days. For each rule the holding time is chosen on the older listings
(first two thirds of the time) and the rule is judged on the newer third, with lab.run.verdict (made money, beat
the big-coin basket by more than luck, still did on the newer listings).

    python -m listings.research   -> state/listings/research.json
"""
from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core.config import state_path
from lab.run import clean, summarize, verdict
from listings import data as D

log = logging.getLogger("omega.listings")
SINCE = pd.Timestamp("2023-01-01", tz="UTC")
COST = 0.005
STOP = 0.30
FUNDING = 0.001        # shorts via perpetual futures: assumed funding cost per day (new listings can cost more)
HOLDS = (7, 30)
DAYS_NEEDED = 130
RULES = {
    "first_hour": {"name": "Buy in the first hour", "text": "Buy at the close of the first hour of trading."},
    "day_one": {"name": "Buy at the end of day one", "text": "Buy at the close of the first trading day (UTC)."},
    "dip50": {"name": "Buy the 50% dip", "text": "Buy the first daily close at least 50% below the highest price since listing (days 2 to 60)."},
    "breakout": {"name": "Buy the breakout above week one", "text": "Buy the first daily close above the highest price of the first 7 days (days 8 to 90)."},
    "settled": {"name": "Buy after a month", "text": "Buy at the close of day 30, after the listing excitement has settled."},
    "short_first_hour": {"name": "Sell (short) after the first hour", "side": -1,
                         "text": "Sell at the close of the first hour of trading (or sell coins you already hold, e.g. from an airdrop); buy back later. Safety exit if the price rises 30%."},
    "short_day_one": {"name": "Sell (short) at the end of day one", "side": -1,
                      "text": "Sell at the close of the first trading day (or sell coins you already hold); buy back later. Safety exit if the price rises 30%."},
}
SIDE = {k: v.get("side", 1) for k, v in RULES.items()}


def entry(rule: str, d: pd.DataFrame, h: pd.DataFrame | None) -> tuple[int, float, pd.Timestamp] | None:
    """(daily index of the entry day, entry price, entry time) or None. Only prices known at the time are used."""
    hi, c = d["high"].to_numpy(float), d["close"].to_numpy(float)
    n = len(d)
    rule = rule.removeprefix("short_")
    if rule == "first_hour":
        if h is None or h.empty:
            return None
        return 0, float(h["close"].iloc[0]), h.index[0] + pd.Timedelta(hours=1)
    if rule == "day_one":
        return (0, c[0], d.index[0] + pd.Timedelta(days=1)) if n > 1 else None
    if rule == "settled":
        return (30, c[30], d.index[30] + pd.Timedelta(days=1)) if n > 31 else None
    if rule == "dip50":
        for k in range(2, min(61, n)):
            if c[k] <= 0.5 * hi[: k + 1].max():
                return k, c[k], d.index[k] + pd.Timedelta(days=1)
        return None
    if rule == "breakout":
        if n < 9:
            return None
        top = hi[:7].max()
        for k in range(7, min(91, n)):
            if c[k] > top:
                return k, c[k], d.index[k] + pd.Timedelta(days=1)
        return None
    raise ValueError(rule)


def hold(d: pd.DataFrame, k: int, px: float, days: int, delisted: bool, side: int = 1) -> tuple[int, float, str] | None:
    """Exit (daily index, price, reason): safety exit 30% against the trade on a daily low (high for a short), else
    the close after `days` days. A delisted coin that stops trading first is closed at its last price. None: not
    finished yet."""
    o, lo, hi, c = d["open"].to_numpy(float), d["low"].to_numpy(float), d["high"].to_numpy(float), d["close"].to_numpy(float)
    stop = px * (1 - STOP * side)
    for j in range(k + 1, min(k + days, len(d) - 1) + 1):
        if (lo[j] <= stop) if side > 0 else (hi[j] >= stop):
            return j, (min(o[j], stop) if side > 0 else max(o[j], stop)), "safety exit"
        if j == k + days:
            return j, c[j], f"{days} days"
    if delisted and len(d) - 1 > k:
        return len(d) - 1, c[-1], "delisted"
    return None


def basket_return(basket: pd.DataFrame, t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    """Median return of the established coins between two times (daily closes on or before each time)."""
    b = basket[basket.index <= t0]
    e = basket[basket.index <= t1]
    if b.empty or e.empty:
        return float("nan")
    r = e.iloc[-1] / b.iloc[-1] - 1
    return float(np.nanmedian(r.to_numpy(float)))


def trades_for(ev: dict, d: pd.DataFrame, h: pd.DataFrame | None, basket: pd.DataFrame) -> list[dict]:
    out = []
    for rule in RULES:
        e = entry(rule, d, h)
        if not e:
            continue
        k, px, t_in = e
        for days in HOLDS:
            side = SIDE[rule]
            x = hold(d, k, px, days, ev["delisted"], side)
            if not x:
                continue
            j, out_px, why = x
            t_out = d.index[j] + pd.Timedelta(days=1)
            out.append({"rule": rule, "days": days, "exchange": ev["exchange"], "sym": ev["symbol"], "coin": ev["base"],
                        "listed": int(ev["list_ms"]), "t_in": int(t_in.value // 10**6), "t_out": int(t_out.value // 10**6),
                        "side": side, "entry": px, "exit": out_px, "reason": why, "net": side * (out_px / px - 1) - COST - (FUNDING * (t_out - t_in).total_seconds() / 86400 if side < 0 else 0.0),
                        "net_holder": side * (out_px / px - 1) - COST,
                        "base_ret": side * basket_return(basket, t_in, t_out) - COST, "hold_h": (t_out - t_in).total_seconds() / 3600})
    return out


def events() -> list[dict]:
    """Every Binance and OKX USDT listing since SINCE that has traded a few days."""
    now_ms = int(pd.Timestamp.now(tz="UTC").value // 10**6)
    ev = []
    syms = D.binance_symbols()
    first = D.binance_first_days([s["symbol"] for s in syms])
    stocks = D.tokenized_stock_batches(first, {s["symbol"]: s["base"] for s in syms})
    for s in syms:
        f = first.get(s["symbol"])
        if f and f >= SINCE.value // 10**6 and s["symbol"] not in stocks and now_ms - f > 3 * D.DAY_MS:
            ev.append({"exchange": "binance", "symbol": s["symbol"], "base": s["base"], "list_ms": f, "delisted": s["status"] != "TRADING"})
    okx = [s for s in D.okx_pairs() if s["state"] == "live"]
    okx_stocks = D.tokenized_stock_batches({s["symbol"]: s["list_ms"] for s in okx}, {s["symbol"]: s["base"] for s in okx}, "okx")
    for s in okx:
        if s["list_ms"] >= SINCE.value // 10**6 and now_ms - s["list_ms"] > 3 * D.DAY_MS and s["symbol"] not in okx_stocks:
            ev.append({"exchange": "okx", "symbol": s["symbol"], "base": s["base"], "list_ms": s["list_ms"], "delisted": False})
    return ev


def load_event(ev: dict) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    d = D.candles(ev["exchange"], ev["symbol"], "1d", ev["list_ms"] - D.DAY_MS, DAYS_NEEDED)
    h = D.candles(ev["exchange"], ev["symbol"], "1h", ev["list_ms"] - D.HOUR_MS, 48)
    return d, h


CALM_RANGE = 0.04


def is_stable(d: pd.DataFrame) -> bool:
    """Not a crypto listing: stablecoins and tokenized stocks barely move (new coins typically swing 10-20% a day).
    Judged on the typical daily range of the first month."""
    x = d.head(30)
    if len(x) < 3:
        return False
    rng = (x["high"] / x["low"].clip(lower=1e-12) - 1).median()
    return bool(rng < CALM_RANGE)


def facts(evd: list[tuple[dict, pd.DataFrame]]) -> dict:
    """Plain facts about what listings do, for the page."""
    r1, r30, r90, crash, day0 = [], [], [], [], []
    for _, d in evd:
        c, hi = d["close"].to_numpy(float), d["high"].to_numpy(float)
        if len(c) < 2:
            continue
        day0.append(hi[0] / d["open"].iloc[0] - 1)
        if len(c) > 7:
            r1.append(c[7] / c[0] - 1)
        if len(c) > 30:
            r30.append(c[30] / c[0] - 1)
        if len(c) > 90:
            r90.append(c[90] / c[0] - 1)
            crash.append(float(c[1:91].min() <= 0.5 * c[0]))
    med = lambda x: float(np.median(x)) if x else float("nan")  # noqa: E731
    return {"listings": len(evd), "median_7d": med(r1), "median_30d": med(r30), "median_90d": med(r90),
            "share_down_30d": float(np.mean([x < 0 for x in r30])) if r30 else float("nan"),
            "share_halved_90d": float(np.mean(crash)) if crash else float("nan"), "median_day0_high": med(day0)}


def research() -> dict:
    now = pd.Timestamp.now(tz="UTC")
    evs = events()
    log.info("listings research: %d listings", len(evs))
    basket = D.basket_daily(int(SINCE.value // 10**6) - 5 * D.DAY_MS)
    with ThreadPoolExecutor(6) as ex:
        loaded = list(ex.map(lambda e: (e, *_safe_load(e)), evs))
    rows, evd = [], []
    for ev, d, h in loaded:
        if d is None or len(d) < 3 or is_stable(d) or D.stock_like(ev["exchange"], ev["base"], d):
            continue
        evd.append((ev, d))
        rows += trades_for(ev, d, h, basket)
    t0, t1 = SINCE, now
    cut_ms = int((t0 + (t1 - t0) * 2 / 3).value // 10**6)
    out = {"generated_at": str(now), "since": str(SINCE.date()), "cost": COST, "stop": STOP, "funding": FUNDING, "cut": cut_ms, "facts": facts(evd),
           "exchanges": sorted({e["exchange"] for e, _ in evd}), "rules": {}}
    for rule, meta in RULES.items():
        per = {}
        for days in HOLDS:
            rr = [{**r, "base": r["base_ret"], "late": r["listed"] >= cut_ms} for r in rows if r["rule"] == rule and r["days"] == days]
            early, late = [r for r in rr if not r["late"]], [r for r in rr if r["late"]]
            per[days] = {"all": summarize(rr), "early": summarize(early), "late": summarize(late),
                         "holder": summarize([{**r, "net": r["net_holder"]} for r in rr]) if SIDE[rule] < 0 else None,
                         "by_exchange": {x: summarize([r for r in rr if r["exchange"] == x]) for x in out["exchanges"]},
                         "exits": {w: sum(r["reason"] == w for r in rr) for w in ("safety exit", f"{days} days", "delisted")},
                         "recent": [{k: r[k] for k in ("exchange", "coin", "t_in", "t_out", "net", "reason")} for r in sorted(rr, key=lambda r: r["t_in"])[-8:]][::-1]}
        chosen = max(HOLDS, key=lambda dd: per[dd]["early"]["edge"] if per[dd]["early"]["n"] and per[dd]["early"]["edge"] == per[dd]["early"]["edge"] else -9)
        level, text = verdict(per[chosen]["all"], per[chosen]["late"])
        out["rules"][rule] = {**meta, "holds": per, "chosen": chosen, "level": level, "verdict": text}
        a = per[chosen]["all"]
        log.info("rule %s (%dd): n %d, hit %.0f%%, avg %+.2f%% vs basket %+.2f%% -> %s", rule, chosen, a["n"], 100 * (a["hit"] or 0),
                 100 * (a["avg"] or 0), 100 * (a["random"] or 0), level)
    state_path("listings", "research.json").write_text(json.dumps(clean(out), indent=1, allow_nan=False))
    return out


def _safe_load(ev: dict):
    try:
        return load_event(ev)
    except Exception as e:  # noqa: BLE001
        log.warning("listing %s %s: %s", ev["exchange"], ev["symbol"], e)
        return None, None


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    research()
    return 0


if __name__ == "__main__":
    sys.exit(main())
