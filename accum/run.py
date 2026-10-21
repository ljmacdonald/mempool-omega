"""The Accumulation lab's engine (D119). Paper only: it reads public data, records evidence, keeps the watchlist,
judges past detections, runs a $100 USDC paper account and publishes a snapshot for the page.

    python -m accum.run              # hourly
    python -m accum.run --backtest   # weekly: distressed-recovery backtest on Binance history (delisted coins too)

Storage: raw swaps for the last RAW_KEEP_D days live in the job cache (.cache/accum); each finished day is written
once to state/accum/days/YYYY-MM-DD.csv (token totals, $1k+ wallet rows, coverage); the watchlist, its status events,
frozen detections, forward results and the paper account live in state/accum/; the page reads
data-branch accum/snapshot.json.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from accum import config as K
from accum import evidence as E
from accum import forward as F
from accum import history as HI
from accum import paper as P
from accum import recovery as R
from accum import sources as S
from accum import stealth as ST
from accum import watch as W
from core.config import state_path

log = logging.getLogger("accum")
CACHE = Path(".cache/accum")
OUT = Path(".cache/accum_out")
DIR = "accum"


# ---------------------------------------------------------------- small storage helpers
def sp(*parts) -> Path:
    p = state_path(DIR, *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def jload(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return default


def jsave(p: Path, d, indent=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=indent, default=str, separators=(",", ":") if indent is None else None))


def append_csv(p: Path, rows: list[dict], fields: list[str]):
    if not rows:
        return
    new = not p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


def read_csv(p: Path) -> list[dict]:
    try:
        with p.open(newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def key_of(chain: str, addr: str) -> str:
    return f"{chain}:{addr}"


def fnum(x):
    return S.f(x)


# ---------------------------------------------------------------- universe
def looks_stable(sym: str, price) -> bool:
    sym = (sym or "").upper()
    return sym in K.STABLE_OR_WRAPPED or "USD" in sym or "EUR" in sym or bool(price and 0.97 <= price <= 1.03)


def build_universe(now: datetime) -> dict:
    """Daily (D120): the 600 largest coins by market cap (CoinGecko), with their contracts. Ethereum coins go to the
    history replay; Solana coins are followed swap by swap (their largest pool). Every coin left out has a reason."""
    coins = S.cg_markets(pages=3)[:600]
    plist = jload(CACHE / "cg_platforms.json", {})
    if not plist or plist.get("_day", "") < (now - timedelta(days=7)).date().isoformat():
        lst = S._cg.get(f"{K.COINGECKO}/coins/list", {"include_platform": "true"})
        if isinstance(lst, list) and lst:
            plist = {"_day": now.date().isoformat(), **{c["id"]: c.get("platforms") or {} for c in lst}}
            jsave(CACHE / "cg_platforms.json", plist)
    eth, sol, excluded = [], {}, []
    for rank, c in enumerate(coins, 1):
        plat = plist.get(c["id"]) or {}
        if looks_stable(c.get("symbol"), c.get("current_price")) or (c.get("symbol") or "").lower() in R.WRAPPED:
            excluded.append({"chain": "-", "symbol": c.get("symbol"), "token": c["id"], "why": "stablecoin, wrapped or staked copy"})
            continue
        base = {"symbol": (c.get("symbol") or "").upper(), "id": c["id"], "supply": c.get("circulating_supply"), "price": c.get("current_price"),
                "mcap": c.get("market_cap"), "rank": rank}
        if plat.get("ethereum"):
            eth.append({**base, "address": plat["ethereum"].lower()})
        if plat.get("solana"):
            sol[plat["solana"]] = base
        if not plat.get("ethereum") and not plat.get("solana"):
            excluded.append({"chain": "-", "symbol": c.get("symbol"), "token": c["id"], "why": "no Ethereum or Solana contract (its own chain): not covered"})
    old = jload(sp("universe.json"), {}).get("tokens", {})
    toks, mints = {}, list(sol)
    top: dict[str, tuple] = {}
    for i in range(0, len(mints), 30):                     # each coin's largest pool, 30 coins a request
        d = S._gt.get(f"{K.GT}/networks/solana/tokens/multi/{','.join(mints[i:i + 30])}", {"include": "top_pools"}, S.GT_HDR)
        res = {p["id"]: S.f((p.get("attributes") or {}).get("reserve_in_usd")) for p in (d or {}).get("included", [])}
        for t in (d or {}).get("data", []):
            a = t.get("attributes") or {}
            pools = (((t.get("relationships") or {}).get("top_pools") or {}).get("data")) or []
            best = max(pools, key=lambda p: res.get(p["id"]) or 0, default=None)
            if a.get("address") and best:
                top[a["address"]] = (best["id"].split("_", 1)[-1], res.get(best["id"]))
    for mint, b in sol.items():
        k = key_of("solana", mint)
        pool, reserve = top.get(mint) or ((old.get(k) or {}).get("pool"), (old.get(k) or {}).get("reserve_usd"))
        if not pool:
            excluded.append({"chain": "solana", "symbol": b["symbol"], "token": mint, "why": "no DEX pool found"})
            continue
        toks[k] = {"chain": "solana", "token": mint, "pool": pool, "symbol": b["symbol"], "name": b["id"], "reserve_usd": reserve,
                   "rank": b["rank"], "supply": b["supply"]}
    return {"generated_at": now.isoformat(), "tokens": toks, "eth": eth, "excluded": excluded[:300], "top": len(coins)}


# ---------------------------------------------------------------- history (finished days + the cache's recent days)
def load_days(today: date) -> tuple[dict, dict, dict]:
    days, wallets, cov = {}, {}, {}
    for i in range(1, K.DAILY_KEEP_D + 1):
        d = (today - timedelta(days=i)).isoformat()
        for r in read_csv(sp("days", f"{d}.csv")):
            k = r["key"]
            if r["type"] == "T":
                days.setdefault(k, {})[d] = {x: fnum(r[x]) or 0.0 for x in ("buy_qty", "sell_qty", "buy_usd", "sell_usd", "bot_usd")} | {
                    "buyers": int(fnum(r["buyers"]) or 0), "sellers": int(fnum(r["sellers"]) or 0), "close": fnum(r["close"])}
                cov.setdefault(k, {})[d] = fnum(r["coverage"]) or 0.0
            else:
                wallets.setdefault(k, []).append({"date": d, "wallet": r["wallet"], **{x: fnum(r[x]) or 0.0 for x in
                                                  ("buy_qty", "sell_qty", "buy_usd", "sell_usd")}, "n": int(fnum(r["n"]) or 0)})
    return days, wallets, cov


DAY_FIELDS = ["type", "key", "wallet", "buy_qty", "sell_qty", "buy_usd", "sell_usd", "n", "buyers", "sellers", "bot_usd", "coverage", "close"]


def write_finished_days(today: date, raw: dict, polls: dict):
    """Each day before today, once, while its swaps are still in the cache."""
    for back in (1, 2):
        d = (today - timedelta(days=back)).isoformat()
        p = sp("days", f"{d}.csv")
        if p.exists():
            continue
        rows = []
        for k, trades in raw.items():
            ds, ws = E.daily([t for t in trades if t["ts"][:10] == d])
            cv = E.coverage_by_day([(E.parse(a), E.parse(b)) for a, b in polls.get(k, [])]).get(d, 0.0)
            if d in ds or cv > 0:
                t = ds.get(d) or {"buy_qty": 0, "sell_qty": 0, "buy_usd": 0, "sell_usd": 0, "buyers": 0, "sellers": 0, "bot_usd": 0, "n": 0, "close": None}
                rows.append({"type": "T", "key": k, **t, "coverage": cv})
                rows += [{"type": "W", "key": k, **w} for w in ws]
        if rows:
            append_csv(p, rows, DAY_FIELDS)
    for old in sp("days", "x").parent.glob("*.csv"):        # keep DAILY_KEEP_D days
        if old.stem < (today - timedelta(days=K.DAILY_KEEP_D + 1)).isoformat():
            old.unlink()


# ---------------------------------------------------------------- one run
def evidence_for(k: str, info: dict, tok: dict, days: dict, wallets: list, cov: dict, candles: list, today: date) -> dict:
    circ, circ_src = E.circulating(tok)
    reserve = tok.get("reserve_usd") or info.get("reserve_usd")
    w = {n: E.window_metrics(days, wallets, cov, today, n, circ, reserve) for n in K.WINDOWS}
    return {"key": k, "circ": circ, "circ_src": circ_src, "reserve": reserve, "w": w, "weekly": E.weekly_nets(days, today),
            "price": E.price_context(candles, today, 14), "buyers": []}


RUN_STARTED = time.monotonic()
RUN_LIMIT_S = 48 * 60                                     # the job may run 60 minutes; leave room to save and publish


def tracked_tokens(uni: dict, wl: dict) -> dict:
    """The DEX pools to poll: the universe plus every non-archived DEX candidate. Exchange-flow (cex:) and stealth
    entries are followed by their own steps, and a token without a known pool can't be polled."""
    tracked = {k: v for k, v in uni.get("tokens", {}).items() if v.get("pool")}
    for k, e in wl["entries"].items():
        info = e.get("info") or {}
        if e.get("state") != "archived" and ":" in k and not k.startswith(("cex:", "stealth:")) and k not in tracked and info.get("pool"):
            tracked[k] = info
    return tracked


def run(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    CACHE.mkdir(parents=True, exist_ok=True)
    uni = jload(sp("universe.json"), {})
    if not uni.get("tokens") or "eth" not in uni or (now - E.parse(uni["generated_at"])).total_seconds() > 86400:
        fresh = build_universe(now)
        if fresh["tokens"]:
            uni = fresh
            jsave(sp("universe.json"), uni, indent=1)
    wl = jload(sp("watchlist.json"), {"entries": {}})
    tracked = tracked_tokens(uni, wl)

    # 1) swaps (A), recent-day aggregation from the cache
    raw = jload(CACHE / "raw.json", {})
    polls = jload(CACHE / "polls.json", {})
    last_poll = jload(CACHE / "last_poll.json", {})
    cutoff = (today - timedelta(days=K.RAW_KEEP_D - 1)).isoformat()
    gaps, sol_new = {}, {}
    for k, info in tracked.items():
        tr = S.gt_trades(info["chain"], info["pool"])
        if tr is None:
            continue
        prev = E.parse(last_poll[k]) if k in last_poll else None
        a, b = E.poll_interval(now, prev, tr)
        polls.setdefault(k, []).append([a.isoformat(), b.isoformat()])
        last_poll[k] = now.isoformat()
        seen = {E.trade_key(t) for t in raw.get(k, [])}
        fresh_trades = [t for t in tr if E.trade_key(t) not in seen and not t.get("anomalous") and t.get("qty")]
        raw[k], _ = E.merge_trades(raw.get(k, []), tr)
        gaps[k] = len(tr) >= 300 and prev is not None and E.parse(tr[0]["ts"]) > prev
        if info["chain"] == "solana" and fresh_trades:
            sol_new.setdefault(k, []).extend(fresh_trades)
    for k in list(raw):
        raw[k] = [t for t in raw[k] if t["ts"][:10] >= cutoff]
    for k in list(polls):
        polls[k] = [iv for iv in polls[k] if iv[1][:10] >= (today - timedelta(days=14)).isoformat()]
    write_finished_days(today, raw, polls)
    jsave(CACHE / "raw.json", raw)
    jsave(CACHE / "polls.json", polls)
    jsave(CACHE / "last_poll.json", last_poll)

    days, wallets, cov = load_days(today)
    for k, trades in raw.items():                          # today (and any day not yet written) from the cache
        ds, ws = E.daily(trades)
        cv = E.coverage_by_day([(E.parse(a), E.parse(b)) for a, b in polls.get(k, [])])
        for d, row in ds.items():
            if d not in days.get(k, {}):
                days.setdefault(k, {})[d] = row
                wallets.setdefault(k, []).extend(w for w in ws if w["date"] == d)
        for d, c in cv.items():
            cov.setdefault(k, {}).setdefault(d, c)

    # 2) token facts (supply, market cap, price) and daily candles
    toks = {}
    for chain in K.CHAINS:
        addrs = [v["token"] for v in tracked.values() if v["chain"] == chain]
        for a, t in S.gt_tokens(chain, addrs).items():
            toks[key_of(chain, a)] = t
    cstore = jload(CACHE / "candles.json", {})
    if cstore.get("_day") != today.isoformat():
        for k, info in tracked.items():
            c = S.gt_daily(info["chain"], info["pool"])
            if c:
                cstore[k] = c
        cstore["_day"] = today.isoformat()
        jsave(CACHE / "candles.json", cstore)

    # 3) evidence, buyers' balances, transfers, score, routes, state
    events, entries, evid = [], wl["entries"], {}
    bal_cache = jload(CACHE / "balances.json", {})
    detections = read_csv(sp("detections.csv"))
    for k, info in tracked.items():
        tok = toks.get(k, {})
        m = evidence_for(k, info, tok, days.get(k, {}), wallets.get(k, []), cov.get(k, {}), cstore.get(k, []), today)
        ex = K.EXCHANGE_WALLETS.get(info["chain"], {})
        prelim = W.routes(m)
        e = entries.get(k)
        if prelim or (e and e.get("state") not in (None, "archived")) or W.score(m)["total"] >= 35:
            for tw in (m["w"][14]["top"] or [])[:K.TOP_WALLETS]:   # how big is each top buyer's buying against what it held?
                ck = f"{k}|{tw['wallet']}|{today}"
                if ck not in bal_cache:
                    bal_cache[ck] = (S.sol_balance(tw["wallet"], info["token"]) if info["chain"] == "solana" else
                                     S.eth_balance(tw["wallet"], info["token"], tok.get("decimals")) if S.etherscan_key() and tok.get("decimals") is not None else None)
                rel = E.buyer_relative(tw["net_qty"], bal_cache[ck])
                m["buyers"].append({**tw, **rel, "exchange": ex.get(tw["wallet"].lower() if info["chain"] == "eth" else tw["wallet"])})
            if info["chain"] == "eth" and S.etherscan_key() and tok.get("decimals") is not None:
                trs = S.eth_transfers(info["token"], tok["decimals"])
                if trs:
                    swaps = {t["tx"] for t in raw.get(k, [])}
                    c = E.classify_transfers(trs, swaps, ex, {b["wallet"].lower() for b in m["buyers"]})
                    m["transfers_in"], m["to_exchange"] = c["transfers_in"], c["to_exchange"]
        # distribution: the addresses that were buying at detection have since sold half of it, or net selling of 0.05%+ of supply
        if e and e.get("detection"):
            det_w = {x["wallet"]: x["net_qty"] for x in e["detection"].get("top", [])}
            since_d = e["detection"]["at"][:10]
            sold = sum(-(w["buy_qty"] - w["sell_qty"]) for w in wallets.get(k, []) if w["wallet"] in det_w and w["date"] > since_d)
            bought = sum(v for v in det_w.values() if v > 0)
            s7 = m["w"][7].get("share")
            if bought > 0 and sold >= K.DISTRIBUTION * bought:
                m["distribution"] = f"the addresses buying at detection have since sold {sold / bought:.0%} of what they had bought"
            elif s7 is not None and s7 <= -0.0005:
                m["distribution"] = f"net selling of {-s7:.3%} of supply over 7 days"
        sc = W.score(m)
        rts = W.routes(m)
        conf, conf_why = W.confidence(m)
        st, why = W.next_state(e, m, rts, sc, now.isoformat())
        evid[k] = {"m": m, "score": sc, "routes": rts, "conf": conf}
        if st is None:
            continue
        if e is None:
            e = entries[k] = {"key": k, "info": info, "state": None, "since": now.isoformat(), "history": []}
        if st != e["state"]:
            events.append({"t": now.isoformat(), "key": k, "symbol": info.get("symbol"), "from": e["state"] or "", "to": st, "reason": why,
                           "score": sc["total"], "version": K.SCORE_VERSION})
            if st in ("accumulation_detected", "concentrated_ownership_risk") and not e.get("detection"):
                px = tok.get("price_usd")
                e["detection"] = {"at": now.isoformat(), "route": ",".join(r["route"] for r in rts), "score": sc, "price": px,
                                  "w7_net_qty": m["w"][7].get("net_qty"), "top": m["w"][14]["top"], "evidence": [r["why"] for r in rts],
                                  "circ": m["circ"], "version": K.SCORE_VERSION}
                detections.append({"key": k, "chain": info["chain"], "symbol": info.get("symbol"), "at": now.isoformat(),
                                   "price": px, "route": e["detection"]["route"], "score": sc["total"], "version": K.SCORE_VERSION})
                append_csv(sp("detections.csv"), [detections[-1]], ["key", "chain", "symbol", "at", "price", "route", "score", "version"])
            e["state"], e["since"] = st, now.isoformat()
        e.update(score=sc["total"], confidence=conf, confidence_why=conf_why, flags=W.flags(m), routes=rts, updated=now.isoformat(),
                 info={**info, "symbol": info.get("symbol") or tok.get("symbol")})
    jsave(CACHE / "balances.json", {k: v for k, v in bal_cache.items() if k.endswith(today.isoformat()) or
                                    k.endswith((today - timedelta(days=1)).isoformat())})

    # 4) exchange flow (C), once a day: taker buying on Binance with the price still quiet
    xf = jload(CACHE / "xflow.json", {})
    if xf.get("_day") != today.isoformat():
        xf = exchange_flow(now)
    for sym, x in xf.get("rows", {}).items():
        k = f"cex:{sym}"
        e = entries.get(k)
        q = x.get("qualifies")
        st = ("accumulation_detected" if q else None) if not e or e["state"] in ("archived",) else \
             ("accumulation_weakening" if (x["lift"] or 0) < 0.01 and e["state"] == "accumulation_detected" else
              "accumulation_detected" if q and e["state"] == "accumulation_weakening" else
              "archived" if e["state"] == "accumulation_weakening" and W.days_between(e["since"], now.isoformat()) >= K.ARCHIVE_D else e["state"])
        if st is None:
            continue
        if e is None:
            e = entries[k] = {"key": k, "info": {"chain": "binance", "symbol": sym.replace("USDT", ""), "pair": sym}, "state": None,
                              "since": now.isoformat(), "history": []}
        if st != e["state"]:
            why = (f"taker buying {x['share14']:.1%} of volume over 14 days vs {x['share_base']:.1%} before, price {W.pct(x['chg14'])}"
                   if st == "accumulation_detected" else "taker buying back to normal" if st == "accumulation_weakening" else "no renewed buying in 30 days")
            events.append({"t": now.isoformat(), "key": k, "symbol": sym, "from": e["state"] or "", "to": st, "reason": why, "score": None,
                           "version": K.SCORE_VERSION})
            if st == "accumulation_detected" and not e.get("detection"):
                e["detection"] = {"at": now.isoformat(), "route": "X", "price": x["close"], "evidence": [why], "version": K.SCORE_VERSION}
                detections.append({"key": k, "chain": "binance", "symbol": sym, "at": now.isoformat(), "price": x["close"], "route": "X",
                                   "score": "", "version": K.SCORE_VERSION})
                append_csv(sp("detections.csv"), [detections[-1]], ["key", "chain", "symbol", "at", "price", "route", "score", "version"])
            e["state"], e["since"] = st, now.isoformat()
        e.update(flow=x, updated=now.isoformat(), confidence="Low", confidence_why="exchange data never says who bought",
                 flags=["Exchange trading activity only: buyers' identities are unknown"])
    # 4b) stealth accumulation (D120): Solana live from the swaps above, Ethereum replayed from full history then live
    stealth = stealth_step(now, uni, tracked, sol_new, cstore, toks, entries, events, detections)
    append_csv(sp("events.csv"), events, ["t", "key", "symbol", "from", "to", "reason", "score", "version"])
    wl["entries"], wl["updated"] = entries, now.isoformat()
    jsave(sp("watchlist.json"), wl)

    # 5) forward results for detections whose horizon has passed
    candles = {k: v for k, v in cstore.items() if not k.startswith("_")}
    candles.update({f"cex:{s}": x["candles"] for s, x in xf.get("rows", {}).items() if x.get("candles")})
    chain_of = {k: (v.get("chain") if isinstance(v, dict) else None) for k, v in tracked.items()} | {k: "binance" for k in candles if k.startswith("cex:")}
    done = {(r["key"], r["at"], r["horizon"]) for r in read_csv(sp("forward.csv"))}
    new_fwd = [r for r in F.evaluate(detections, candles, chain_of, now) if (r["key"], r["at"], str(r["horizon"])) not in done]
    append_csv(sp("forward.csv"), new_fwd, ["key", "symbol", "route", "at", "horizon", "ret", "random", "edge", "mfe", "mae", "n_random", "status"])
    fwd_rows = [{**r, "horizon": int(r["horizon"]), **{x: fnum(r[x]) for x in ("ret", "random", "edge", "mfe", "mae")}} for r in read_csv(sp("forward.csv"))]

    # 6) the $100 USDC paper account
    acc = jload(sp("paper.json"), None) or P.new_account()
    usdc = S.bn("ticker/price", {"symbol": "USDCUSDT"})
    usdc_px = fnum((usdc or {}).get("price"))
    usdc_ok = usdc_px is not None and abs(usdc_px - 1) <= K.USDC_DEPEG
    prices = {k: toks.get(k, {}).get("price_usd") for k in tracked}
    reserves = {k: toks.get(k, {}).get("reserve_usd") or tracked[k].get("reserve_usd") for k in tracked}
    lows: dict = {}                                        # hourly prices only; a gap through the stop fills at the worse price
    cands = {k: {"key": k, "chain": tracked[k]["chain"], "symbol": tracked[k].get("symbol"), "state": e.get("state"),
                 "price": prices.get(k), "price_at": now, "reserve": reserves.get(k), "route": (e.get("detection") or {}).get("route"),
                 "repricing": (evid.get(k, {}).get("m", {}).get("price") or {}).get("repricing"),
                 "low14": min((c["l"] for c in (cstore.get(k) or [])[-14:] if c.get("l")), default=None),
                 "why": "; ".join(r["why"] for r in e.get("routes") or [])} for k, e in entries.items() if k in tracked}
    for k, e in entries.items():                           # stealth signals: their own exits (+60% / -30%), set by the replay's rules
        if k.startswith("stealth:") and e.get("state") in W.ACTIVE and e.get("price"):
            px = e["price"]
            prices[k], reserves[k] = px, e.get("reserve")
            cands[k] = {"key": k, "chain": e["info"]["chain"], "symbol": e["info"]["symbol"], "state": e["state"], "price": px,
                        "price_at": E.parse(e["price_at"]), "reserve": e.get("reserve"), "route": "S", "repricing": False,
                        "stop": px * (1 - ST.TRADE["stop"]), "target": px * (1 + ST.TRADE["take_profit"]),
                        "why": (e.get("detection") or {}).get("evidence", [""])[0]}
    closed = P.manage(acc, cands, prices, lows, reserves, now)
    plans = {}
    for k, c in sorted(cands.items(), key=lambda kv: -(entries[kv[0]].get("score") or 0)):
        p = P.plan(c, acc, prices, usdc_ok, now)
        plans[k] = p
        if p["eligible"]:
            P.buy(acc, c, p, now)
    acc["decisions"] = acc["decisions"][-300:]
    jsave(sp("paper.json"), acc, indent=1)
    mk = P.mark(acc, prices)
    append_csv(sp("paper_equity.csv"), [{"t": now.isoformat(), **{x: round(mk[x], 4) for x in ("equity", "cash", "realized", "unrealized", "open_risk")}}],
               ["t", "equity", "cash", "realized", "unrealized", "open_risk"])

    # 7) distressed recovery, once a day
    rec = jload(sp("recovery.json"), {})
    if (rec.get("generated_at") or "")[:10] != today.isoformat():
        r2 = recovery_scan(now)
        if r2.get("rows"):
            rec = r2
            jsave(sp("recovery.json"), rec, indent=1)
    bt = jload(sp("backtest.json"), {})

    reg = S.registry(jload(sp("registry.json"), {}))
    jsave(sp("registry.json"), reg, indent=1)
    snap = snapshot(now, uni, entries, evid, events, detections, fwd_rows, acc, mk, closed, plans, rec, bt, reg, gaps, usdc_px)
    snap["stealth"] = stealth
    OUT.mkdir(parents=True, exist_ok=True)
    jsave(OUT / "snapshot.json", snap)
    log.info("accum: %d tracked, %d on the watchlist, %d events, %d new forward results", len(tracked),
             sum(1 for e in entries.values() if e["state"] in W.ACTIVE), len(events), len(new_fwd))
    return snap


def stealth_step(now, uni, tracked, sol_new, cstore, toks, entries, events, detections) -> dict:
    """Solana: today's new swaps go through the same day-by-day rules (forward only: no free Solana history).
    Ethereum: the replay engine (accum/history.py). Recent signals become watchlist entries (route S)."""
    sol_states = jload(CACHE / "sol_stealth.json", {})
    for k, trades in sol_new.items():
        st = sol_states.get(k) or {"symbol": tracked[k].get("symbol"), "address": tracked[k]["token"], "signals": [], "watch": [], "lp": []}
        tr = ST.Tracker(st.get("tracker"))
        closes = {datetime.fromtimestamp(c["t"], tz=timezone.utc).date().isoformat(): c["c"] for c in cstore.get(k, []) if c.get("c")}
        supply = E.circulating(toks.get(k, {}))[0] or tracked[k].get("supply")
        for t in sorted(trades, key=lambda x: x["ts"]):
            d = t["ts"][:10]
            if tr.day and d != tr.day:
                HI.finish_day(st, tr, closes, supply, {}, chain="solana")
            tr.day = d
            tr.add(d, {t["wallet"]: t["qty"]} if t["side"] == "buy" else {}, {t["wallet"]: t["qty"]} if t["side"] == "sell" else {})
        st["tracker"], st["prices"], st["last_day"], st["supply"] = tr.to_state(), closes, tr.day, supply
        sol_states[k] = st
    jsave(CACHE / "sol_stealth.json", sol_states)
    budget = int(K.ETH_CALLS_PER_RUN)
    left = RUN_LIMIT_S - (time.monotonic() - RUN_STARTED) - 6 * 60   # recovery and the paper account still to come
    seconds = int(max(60, min(K.ETH_SECONDS_PER_RUN, left)))
    eth = HI.run(uni.get("eth", []), now, max_calls=budget, max_seconds=seconds) if uni.get("eth") else {}
    sol_res = HI.results([{**v, "start_day": v.get("start_day") or (v.get("last_day") or now.date().isoformat())} for v in sol_states.values()], now)
    out = {"eth": eth, "solana": {k: sol_res[k] for k in ("progress", "signals", "n_signals", "all", "verdict", "level", "tokens")},
           "rules": ST.RULES, "trade": ST.TRADE}
    jsave(sp("stealth.json"), out)
    # live signals (last 3 days) -> the watchlist; the accumulators selling a quarter -> distribution
    for chain, res in (("eth", eth), ("solana", sol_res)):
        for r in (res or {}).get("signals", []):
            k = f"stealth:{chain}:{r['address']}"
            e = entries.get(k)
            t = r.get("trade") or {}
            if not r.get("live") and not e:
                continue
            if e is None:
                e = entries[k] = {"key": k, "info": {"chain": chain, "symbol": r["symbol"], "token": r["address"]}, "state": None,
                                  "since": now.isoformat(), "history": []}
            st_new = ("distribution_detected" if t.get("reason") == "the accumulators started selling" else
                      "archived" if t.get("reason") and t["reason"] != "still open" else "accumulation_detected")
            if st_new != e["state"]:
                why = (f"{r['accumulators']} addresses ({r.get('groups') or r['accumulators']} probable owners) quietly bought "
                       f"{r['share']:.2%} of the supply in small chunks since {r.get('since')}, price {W.pct(r['quiet_change'])} over 30 days"
                       if st_new == "accumulation_detected" else t.get("reason") or "")
                events.append({"t": now.isoformat(), "key": k, "symbol": r["symbol"], "from": e["state"] or "", "to": st_new, "reason": why,
                               "score": None, "version": K.SCORE_VERSION})
                if st_new == "accumulation_detected" and not e.get("detection"):
                    e["detection"] = {"at": now.isoformat(), "route": "S", "price": r.get("price"), "evidence": [why], "signal_day": r["day"],
                                      "version": K.SCORE_VERSION}
                    detections.append({"key": k, "chain": chain, "symbol": r["symbol"], "at": now.isoformat(), "price": r.get("price"),
                                       "route": "S", "score": "", "version": K.SCORE_VERSION})
                    append_csv(sp("detections.csv"), [detections[-1]], ["key", "chain", "symbol", "at", "price", "route", "score", "version"])
                e["state"], e["since"] = st_new, now.isoformat()
            px = S.gt_tokens(chain, [r["address"]]).get(r["address"]) if e["state"] in W.ACTIVE else None
            e.update(updated=now.isoformat(), confidence="Medium" if chain == "eth" else "Low",
                     confidence_why="full transfer history (Ethereum)" if chain == "eth" else "swaps seen since the lab started (Solana)",
                     flags=["Pump-and-dump risk: the accumulators decide when to sell", "Groups are probable links (same funder), not proven"],
                     stealth=r, price=(px or {}).get("price_usd"), reserve=(px or {}).get("reserve_usd"), price_at=now.isoformat())
    return out


def exchange_flow(now: datetime) -> dict:
    """Binance: share of volume bought by takers (market buyers) over 14 days against the 90 days before; buyer
    identities are never known. A coin qualifies when that share rose by ROUTE_X lift with the price still in the band."""
    info = S.bn("exchangeInfo") or {}
    syms = [s["symbol"] for s in info.get("symbols", []) if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
            and s.get("baseAsset", "").lower() not in R.STABLES | R.WRAPPED]
    tick = S.bn("ticker/24hr") or []
    vol = {t["symbol"]: fnum(t.get("quoteVolume")) or 0 for t in tick if isinstance(t, dict)}
    syms = [s for s in syms if vol.get(s, 0) >= K.ROUTE_X["min_quote_vol"]]
    try:                                                   # tokenised stocks are not coins (D112)
        from scanner.data import stock_tokens
        stocks = stock_tokens()
    except Exception:  # noqa: BLE001
        stocks = frozenset()
    last = {t["symbol"]: fnum(t.get("lastPrice")) for t in tick if isinstance(t, dict)}
    syms = [s for s in syms if s[:-4] not in stocks and "USD" not in s[:-4] and not (last.get(s) and 0.97 <= last[s] <= 1.03)]
    rows = {}
    for s in syms:
        k = S.bn("klines", {"symbol": s, "interval": "1d", "limit": 120})
        if not k or len(k) < 110:
            continue
        k = k[:-1]                                         # finished days only
        q = [fnum(x[7]) or 0 for x in k]
        tb = [fnum(x[10]) or 0 for x in k]
        a, b = sum(tb[-14:]), sum(q[-14:])
        a0, b0 = sum(tb[-104:-14]), sum(q[-104:-14])
        if not b or not b0:
            continue
        sh, base = a / b, a0 / b0
        c = [fnum(x[4]) for x in k]
        chg = c[-1] / c[-15] - 1
        lo, hi = K.ROUTE_X["price_band"]
        rows[s] = {"share14": sh, "share_base": base, "lift": sh - base, "chg14": chg, "close": c[-1], "quote_vol_24h": vol.get(s),
                   "qualifies": sh - base >= K.ROUTE_X["lift"] and lo <= chg <= hi,
                   "candles": [{"t": int(x[0]) // 1000, "o": fnum(x[1]), "h": fnum(x[2]), "l": fnum(x[3]), "c": fnum(x[4])} for x in k]}
    out = {"_day": now.date().isoformat(), "rows": rows}
    jsave(CACHE / "xflow.json", out)
    return out


def recovery_scan(now: datetime) -> dict:
    coins = S.cg_markets()
    protos = S.llama("protocols") or []
    by_gecko = {p["gecko_id"]: p for p in protos if isinstance(p, dict) and p.get("gecko_id")}
    fees = S.llama("overview/fees", {"excludeTotalDataChart": "true", "excludeTotalDataChartBreakdown": "true"}) or {}
    fee_by_name = {(x.get("name") or "").lower(): x.get("total30d") for x in fees.get("protocols", []) if isinstance(x, dict)}
    rows, rejected = [], []
    for c in coins:
        ok, why = R.screen(c)
        if not ok:
            if (c.get("ath_change_percentage") or 0) <= -75:
                rejected.append({"id": c["id"], "symbol": c.get("symbol"), "why": why})
            continue
        pr = by_gecko.get(c["id"])
        llama = {"tvl": pr.get("tvl"), "fees_30d": fee_by_name.get((pr.get("name") or "").lower()), "name": pr.get("name")} if pr else None
        kl = S.bn_daily(f"{c['symbol'].upper()}USDT", limit=1000, quiet=True)
        closes = [fnum(x[4]) for x in kl] if kl else None
        hist = None
        if kl:                                             # the first daily candle on Binance: how long the history really is
            first = kl if len(kl) < 1000 else S.bn_daily(f"{c['symbol'].upper()}USDT", start_ms=0, limit=1, quiet=True)
            if first:
                hist = (now - datetime.fromtimestamp(first[0][0] / 1000, tz=timezone.utc)).days
        sc = R.score(c, llama, closes[-120:] if closes else None, hist)
        rows.append({"id": c["id"], "symbol": c["symbol"].upper(), "name": c.get("name"), "price": c.get("current_price"),
                     "drawdown": -c["ath_change_percentage"] / 100, "ath": c.get("ath"), "ath_date": c.get("ath_date"),
                     "market_cap": c.get("market_cap"), "fdv": c.get("fully_diluted_valuation"), "volume": c.get("total_volume"),
                     "circ": c.get("circulating_supply"), "max_supply": c.get("max_supply") or c.get("total_supply"),
                     "llama": llama, "history_days": hist, "binance": bool(kl), "score": sc})
    rows.sort(key=lambda r: -r["score"]["total"])
    return {"generated_at": now.isoformat(), "rows": rows, "rejected": rejected[:100], "screened": len(coins)}


def backtest(now: datetime | None = None) -> dict:
    """Weekly. Every Binance USDT coin (trading and delisted) with its full daily history."""
    now = now or datetime.now(timezone.utc)
    info = S.bn("exchangeInfo") or {}
    syms = [s["symbol"] for s in info.get("symbols", []) if s.get("quoteAsset") == "USDT" and s.get("baseAsset", "").lower() not in R.STABLES | R.WRAPPED]
    cache = jload(CACHE / "bt_klines.json", {})
    series = {}
    for s in syms:
        ks = cache.get(s, [])
        start = ks[-1][0] + 86400000 if ks else 0
        while True:
            part = S.bn_daily(s, start_ms=start, limit=1000)
            if not part:
                break
            ks += [[x[0], x[1], x[2], x[3], x[4], x[5], x[6], x[7]] for x in part if not ks or x[0] > ks[-1][0]]
            if len(part) < 1000:
                break
            start = part[-1][0] + 86400000
        if ks and ks[-1][6] > now.timestamp() * 1000:
            ks = ks[:-1]                                   # drop the unfinished day
        cache[s] = ks
        if len(ks) > K.REC_HISTORY_D:
            series[s] = ks
    jsave(CACHE / "bt_klines.json", cache)
    btc = series.get("BTCUSDT")
    rows = R.bt_signals(series, btc)
    fp = hashlib.sha256(json.dumps(sorted((s, len(v), v[-1][0] if v else 0) for s, v in series.items())).encode()).hexdigest()[:16]
    delisted = sum(1 for s in series if s not in {x["symbol"] for x in info.get("symbols", []) if x.get("status") == "TRADING"})
    out = {"generated_at": now.isoformat(), "version": K.RECOVERY_VERSION, "coins": len(series), "delisted_coins": delisted,
           "signals": len(rows), "inputs_fingerprint": fp, "rules": {"drawdown": K.REC_MIN_DRAWDOWN, "history_d": K.REC_HISTORY_D,
                                                                     "min_volume": 1e6, "off_low": 0.1, "cost": K.BT_COST},
           "results": {str(h): R.bt_summary(rows, h) for h in K.BT_HORIZONS},
           "recent": [{**r, "t": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).date().isoformat()} for r in rows[-40:]]}
    jsave(sp("backtest.json"), out, indent=1)
    return out


# ---------------------------------------------------------------- the page's snapshot
def snapshot(now, uni, entries, evid, events, detections, fwd_rows, acc, mk, closed, plans, rec, bt, reg, gaps, usdc_px) -> dict:
    def ev_view(k):
        x = evid.get(k)
        if not x:
            return None
        m = x["m"]
        return {"circ": m["circ"], "circ_src": m["circ_src"], "reserve": m["reserve"], "price": m["price"], "weekly": m["weekly"],
                "windows": {n: {kk: v for kk, v in (m["w"][n] or {}).items() if kk != "top"} for n in K.WINDOWS},
                "top": m["w"][14]["top"], "buyers": m["buyers"], "transfers_in": len(m.get("transfers_in") or []),
                "to_exchange": m.get("to_exchange") or [], "distribution": m.get("distribution"), "score": x["score"]}
    rows = []
    for k, e in entries.items():
        rows.append({"key": k, "symbol": (e.get("info") or {}).get("symbol"), "chain": (e.get("info") or {}).get("chain"),
                     "token": (e.get("info") or {}).get("token"), "pool": (e.get("info") or {}).get("pool"), "state": e.get("state"),
                     "label": W.LABEL.get(e.get("state"), e.get("state")), "since": e.get("since"), "score": e.get("score"),
                     "confidence": e.get("confidence"), "confidence_why": e.get("confidence_why"), "flags": e.get("flags") or [],
                     "routes": e.get("routes") or [], "detection": e.get("detection"), "updated": e.get("updated"),
                     "flow": {kk: v for kk, v in (e.get("flow") or {}).items() if kk != "candles"} or None, "evidence": ev_view(k)})
    rows.sort(key=lambda r: (r["state"] not in W.ACTIVE, -(r["score"] or 0)))
    recent_ev = (read_csv(sp("events.csv")) or [])[-80:]
    return {"generated_at": now.isoformat(), "version": K.SCORE_VERSION, "paper_only": True, "live_trading": False,
            "universe": {"tokens": len(uni.get("tokens", {})) + len(uni.get("eth", [])), "top": uni.get("top"),
                         "by_chain": {"solana": len(uni.get("tokens", {})), "eth": len(uni.get("eth", []))},
                         "excluded": uni.get("excluded", [])[:60], "at": uni.get("generated_at")},
            "watchlist": rows, "events": recent_ev[::-1], "detections": len(detections),
            "forward": {"summary": {str(h): F.summary(fwd_rows, h) for h in K.HORIZONS}, "rows": fwd_rows[-60:][::-1]},
            "paper": {"account": {**mk, "start": acc["start"]}, "positions": acc["positions"], "trades": acc["trades"][-40:][::-1],
                      "closed_now": closed, "plans": {k: v for k, v in plans.items() if entries.get(k, {}).get("state") in W.ACTIVE},
                      "usdc_usd": usdc_px, "rules": acc["rules"], "live_trading": False},
            "recovery": {k: rec.get(k) for k in ("generated_at", "rows", "rejected", "screened")}, "backtest": bt,
            "sources": reg, "gaps_now": [k for k, v in gaps.items() if v], "settings": {
                "routes": {"A": K.ROUTE_A, "B": K.ROUTE_B, "C": K.ROUTE_C, "X": K.ROUTE_X}, "weights": K.WEIGHTS,
                "rec_weights": K.REC_WEIGHTS, "min_trade_usd": K.MIN_TRADE_USD, "windows": K.WINDOWS}}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if "--backtest" in argv:
        b = backtest()
        log.info("backtest: %d coins (%d delisted), %d signals", b["coins"], b["delisted_coins"], b["signals"])
        return 0
    run()
    bt = jload(sp("backtest.json"), {})               # the weekly backtest, started by whichever hourly run finds it due
    if not bt.get("generated_at") or (datetime.now(timezone.utc) - E.parse(bt["generated_at"])).days >= 7:
        try:
            b = backtest()
            log.info("backtest: %d coins (%d delisted), %d signals", b["coins"], b["delisted_coins"], b["signals"])
        except Exception as e:  # noqa: BLE001  the hourly results are already saved
            log.warning("backtest failed: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
