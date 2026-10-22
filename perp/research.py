"""The Perp DEX lab's history tests (D123), run once a day: every strategy on the $100 account and on the $10,000
reference account, with costs as estimated, with costs doubled (gate 5) and, for diagnosis only, with no costs;
the benchmarks; the execution-gap study; and the gates. Thresholds are fixed in config.yaml before any result and
nothing is tuned here, so every period is out of sample; results are also split by period, market and side.
"""
from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timezone
from decimal import Decimal

from perp import carry as CA
from perp import costs as CO
from perp import directional as DI
from perp import execgap as XG
from perp import gates as GT
from perp import history as HI
from perp import live as LV
from perp import lp as LP
from perp import report as RP
from perp import sources as SRC
from perp import spread as SP
from perp.config import fingerprint, load

log = logging.getLogger("perp.research")
E30 = Decimal(10) ** 30


def _books():
    A = load()["account"]
    return (("account", A["start_usd"]), ("reference", A["reference_usd"]))


def _sleeve(size, key) -> Decimal:
    return Decimal(str(size)) * Decimal(str(load()["account"]["sleeves"][key]))


def market_inputs(markets, exec_fee) -> list[dict]:
    """The in-scope GMX markets with their settings, today's balance, hourly rates and both venues' hourly prices."""
    out = []
    tick = {t["tokenSymbol"]: t for t in SRC.gmx_tickers()}
    for coin, m in LV.main_markets(markets).items():
        raw = SRC.gmx_market_settings(m["address"]) or {}
        if not raw:
            continue
        t = tick.get(m["index"])
        px = (Decimal(t["minPrice"]) + Decimal(t["maxPrice"])) / 2 / Decimal(10) ** (30 - m["index_dec"]) if t else None
        sc = Decimal(10) ** m["index_dec"]
        oi = ((Decimal(raw["longOpenInterestInTokens"]) / sc * px, Decimal(raw["shortOpenInterestInTokens"]) / sc * px)
              if raw.get("useOpenInterestInTokensForBalance") and px else
              (Decimal(raw["longOpenInterestUsd"]) / E30, Decimal(raw["shortOpenInterestUsd"]) / E30))
        hcoin = LP.hl_coin(m["index"])
        rates = HI.ffill_hourly(HI.gmx_rates(m["address"]))
        gmx1h = HI.gmx_candles(m["index"], "1h")
        out.append({"name": m["name"], "coin": coin, "hl_coin": hcoin, "settings": CO.gmx_settings(raw), "oi": oi,
                    "exec_fee": exec_fee, "rates": rates, "hl": HI.hl_funding(hcoin),
                    "gmx_px": {t: v[3] for t, v in gmx1h.items()}, "hl_px": HI.hl_candles_1h(hcoin)})
    return out


def _rate_at(rates: dict, max_age: int = 7 * 86400):
    """ts -> each rate's last published value at or before ts. GMX's history records a rate when it changes, not
    every hour, so each field carries forward from its own last snapshot (never from a later one), for up to 7 days."""
    import bisect
    series = {}
    for k in ("fund_long", "fund_short", "borrow_long", "borrow_short"):
        pts = sorted((h, v[k]) for h, v in rates.items() if v.get(k) is not None)
        series[k] = ([h for h, _ in pts], [v for _, v in pts])

    def f(ts: int) -> dict:
        out = {}
        for k, (hs, vs) in series.items():
            i = bisect.bisect_right(hs, ts) - 1
            if i >= 0 and ts - hs[i] <= max_age:
                out[k] = vs[i]
        return out
    return f


def _bars(kl: list[list]) -> list[list]:
    return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), k[6], float(k[7])] for k in kl]


def _gate(summary: dict, complete: float, unit: str = "trades") -> dict:
    return GT.evaluate({"complete": complete, "nets": summary.get("nets") or [], "drawdown": summary.get("max_drawdown"),
                        "stressed_mean": summary.get("stressed_mean"), "shadow": None}, unit)


def run(now: float | None = None) -> dict:
    now = now or time.time()
    t0 = time.monotonic()
    markets = HI.markets()
    tick = {t["tokenSymbol"]: t for t in SRC.gmx_tickers()}
    eth = tick.get("ETH")
    eth_usd = (Decimal(eth["minPrice"]) + Decimal(eth["maxPrice"])) / 2 / Decimal(10) ** 12
    fees = HI.execution_fees(500)
    exec_fee = Decimal(str(statistics.median(fees))) * eth_usd
    out = {"generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(), "config": fingerprint(), "paper_only": True,
           "execution_fee_usd": float(exec_fee), "strategies": {}}

    # ---------------- strategy 1: pools
    pools, bad, total = [], 0, 0
    for m in markets:
        gm = HI.gm_prices(m["address"])
        if len(gm) < load()["lp"]["min_history_days"]:
            continue
        total += len(gm)
        gm, b = LP.clean(gm)
        bad += len(b)
        raw = SRC.gmx_market_settings(m["address"]) or {}
        st = CO.gmx_settings(raw) if raw else {}
        tok = HI.token_prices(m["long_addr"], m["long_dec"]) if m.get("long_addr") and m.get("long_dec") is not None else {}
        pools.append({"name": m["name"], "address": m["address"], "gm": gm, "apr": HI.pool_aprs(m["address"]),
                      "tok": LP.clean(tok)[0] if tok else {}, "coin": LP.hl_coin(m["long"]),
                      "swap_fee": st.get("swap_fee_neg") or Decimal("0.0007"), "exec_fee": exec_fee})
    hlc = SRC.hl_contexts()
    fund = {c: HI.hl_funding(c) for c in {p["coin"] for p in pools if p["coin"]} if c in hlc}
    for p in pools:
        p["hl_funding"] = fund.get(p["coin"])
    complete = 1 - bad / total if total else 0.0
    for hedge in (False, True):
        key = "lp_hedged" if hedge else "lp"
        res = {}
        for book, size in _books():
            sl = _sleeve(size, "lp")
            r = LP.backtest(pools, sl, hedge, book)
            r2 = LP.backtest(pools, sl, hedge, book, Decimal(2))
            r0 = LP.backtest(pools, sl, hedge, book, Decimal(0))
            daily = r["daily"]
            s = {**LP.summary(r, sl), **{k: v for k, v in RP.summarize(r["trades"], sl, r2["trades"]).items()
                                         if k in ("waterfall", "cost_drift", "by_market", "sample", "hit_rate", "profit_factor")}}
            s["nets"] = daily
            s["stressed_mean"] = statistics.fmean(r2["daily"]) if r2["daily"] else None
            s["cost_blind"] = LP.summary(r0, sl)
            s["gates"] = _gate(s, complete, "days")
            s.pop("nets")
            res[book] = s
        mains = LV.main_markets(markets)
        big = [mains[c]["name"] for c in ("ETH", "BTC") if c in mains]
        res["benchmark_hold"] = {"pools": big, **LP.passive(pools, big, 10000)}
        out["strategies"][key] = res
    log.info("pools done (%.0fs)", time.monotonic() - t0)

    # ---------------- strategies 2 and 3: carry and spread
    mk = market_inputs(markets, exec_fee)
    gmx_hours = [len(m["gmx_px"]) for m in mk]
    comp = []
    for m in mk:                                       # hours with every input, over the span where GMX prices exist
        if m["gmx_px"]:
            a, b = min(m["gmx_px"]), max(m["gmx_px"])
            want = (b - a) // 3600 + 1
            have = len([h for h in m["gmx_px"] if h in m["hl_px"] and h in m["rates"] and h in m["hl"]])
            comp.append(have / want)
    hedge_complete = min(comp) if comp else 0.0
    for key, mod in (("carry", CA), ("spread", SP)):
        res = {}
        for book, size in _books():
            sl = _sleeve(size, key)
            r = mod.backtest(mk, sl, book)
            r2 = mod.backtest(mk, sl, book, Decimal(2))
            r0 = mod.backtest(mk, sl, book, Decimal(0))
            s = RP.summarize(r["trades"], sl, r2["trades"])
            s["cost_blind"] = {k: v for k, v in RP.summarize(r0["trades"], sl).items() if k in ("n", "mean", "total_return")}
            s["gates"] = _gate(s, hedge_complete)
            s.pop("nets")
            fixed = 2 * exec_fee + Decimal("0.04")
            cap = sl * Decimal(str(load()["risk"]["max_margin_share"])) * Decimal(str(load()["risk"]["max_leverage"])) / max(1, len(mk))
            if not r["trades"] and cap and fixed / cap > Decimal("0.05"):
                s["gates"]["status"] = "ineligible"
                s["ineligible"] = (f"Each position can be at most ${float(cap):,.2f} per market here, and the fixed GMX execution fees alone are "
                                   f"${float(fixed):.2f} a round trip ({float(fixed / cap):.0%}): no carry or gap can pay that.")
            if key == "spread":
                s["opportunities"] = {"seen": len(r["seen"]), "taken": sum(1 for x in r["seen"] if x["taken"])}
            res[book] = s
        if key == "spread":
            res["gaps"] = SP.gap_stats(mk)
        res["history_hours"] = gmx_hours
        res["data_complete"] = hedge_complete
        out["strategies"][key] = res
    log.info("carry and spread done (%.0fs)", time.monotonic() - t0)

    # ---------------- strategy 4: directional (4a trend on 1 h, 4b mean reversion on 15 min)
    P = load()["directional"]
    for key in ("trend", "reversion", "trend_long_only", "trend_short_only"):
        res = {}
        for book, size in _books():
            sl = _sleeve(size, "directional") / 2
            trades, trades2, trades0, rej = [], [], [], []
            for m in mk:
                sym = f"{m['coin']}USDT"
                b1 = _bars(HI.binance(sym, "1h", 1_688_169_600_000))           # from July 2023 (GMX v2 history)
                if not b1:
                    continue
                mkt = {**m, "rate_at": _rate_at(m["rates"])}
                if key.startswith("trend"):
                    sig = DI.signals_trend(b1, P)
                    if key.endswith("long_only"):
                        sig = [x for x in sig if x[1] > 0]
                    elif key.endswith("short_only"):
                        sig = [x for x in sig if x[1] < 0]
                    bars = b1
                else:
                    b15 = _bars(HI.binance(sym, "15m", 1_704_067_200_000))     # from January 2024
                    sig = DI.signals_reversion(b15, b1, P)
                    bars = b15
                full = [h for h, v in m["rates"].items() if len(v) == 4]     # every rate published (funding history starts first)
                first = min(full) if full else None
                sig = [x for x in sig if first is not None and bars[x[0]][0] // 1000 >= first]   # from the market's GMX listing
                for mult, acc in ((Decimal(1), trades), (Decimal(2), trades2), (Decimal(0), trades0)):
                    t_, r_ = DI.run_trades(bars, sig, mkt, sl, key, book, mult)
                    acc += t_
                    if mult == 1:
                        rej += r_
            trades, trades2, trades0 = DI.limit_group(trades), DI.limit_group(trades2), DI.limit_group(trades0)
            s = RP.summarize(trades, sl, trades2)
            s["cost_blind"] = {k: v for k, v in RP.summarize(trades0, sl).items() if k in ("n", "mean", "total_return")}
            s["rejected"] = len(rej)
            s["rejected_why"] = {w: sum(1 for x in rej if x["why"] == w) for w in {x["why"] for x in rej}}
            missing = sum(1 for x in rej if x["why"].startswith("missing") or x["why"].startswith("not tradeable"))
            s["gates"] = _gate(s, 1 - missing / (len(trades) + missing) if trades or missing else 1.0)
            if not trades and any("risk budget" in x["why"] for x in rej):
                s["gates"]["status"] = "ineligible"
                s["ineligible"] = "Every signal's fixed costs alone exceed the 0.5% risk budget at this account size: it can never trade here."
            s.pop("nets")
            res[book] = s
        out["strategies"][key] = res
    log.info("directional done (%.0fs)", time.monotonic() - t0)

    # ---------------- execution-gap study (real GMX orders, last 7 days)
    acts = HI.trade_actions(int(now) - 7 * 86400)
    byaddr = {m["address"].lower(): m for m in markets}
    import collections
    top = collections.Counter((byaddr.get((a.get("marketAddress") or "").lower()) or {}).get("index") for a in acts)
    minute = {s: HI.gmx_candles(s, "1m") for s, _ in top.most_common(8) if s}
    out["execgap"] = XG.analyse(acts, byaddr, minute)
    out["data"] = {"pool_price_completeness": complete, "pools": len(pools), "markets": [m["name"] for m in mk],
                   "sources": {k: {kk: vv for kk, vv in v.items()} for k, v in SRC.HEALTH.items()}}
    out["seconds"] = round(time.monotonic() - t0)
    return out
