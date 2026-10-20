"""Stealth-accumulation replay and live tracking on Ethereum (D120), from each token's full transfer history (Etherscan,
free key). Every hour it continues where it stopped: the last 2 years of every top-600 coin with an Ethereum contract
are replayed day by day through accum/stealth.py, then each token is followed live.

State per token lives in the job cache (.cache/accum/eth/<address>.json.gz): it can always be rebuilt from Etherscan,
so losing the cache only costs time. The results (signals and their paper trades) are saved to
state/accum/stealth.json for the page and the record.
"""
from __future__ import annotations

import gzip
import json
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from accum import config as K
from accum import sources as S
from accum import stealth as ST

log = logging.getLogger("accum.history")
DIR = Path(".cache/accum/eth")
HISTORY_D = 730
BIG_CAP = 2e9                     # coins above $2B: only the last 90 days (quietly collecting 0.5% of them isn't realistic)
BIG_HISTORY_D = 90
WORKERS = 4                       # tokens replayed side by side (requests are still paced to the free key's 3 a second)
PAGE = 1000                       # the free plan returns at most 1,000 transfers a request
PER_TOKEN_CALLS = 40              # per run, so every token moves forward
FUNDER_CAP = 20                   # a "funder" that funded this many accumulators is a service, not one person


def load(addr: str) -> dict:
    p = DIR / f"{addr}.json.gz"
    try:
        return json.loads(gzip.decompress(p.read_bytes()))
    except (OSError, ValueError):
        return {}


def save(addr: str, st: dict):
    DIR.mkdir(parents=True, exist_ok=True)
    (DIR / f"{addr}.json.gz").write_bytes(gzip.compress(json.dumps(st, separators=(",", ":")).encode(), 6))


def day_of(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def block_at(ts: int) -> int | None:
    r = S.es({"module": "block", "action": "getblocknobytime", "timestamp": ts, "closest": "before"})
    try:
        return int(r)
    except (TypeError, ValueError):
        return None


def pools_for(addr: str) -> list[str]:
    out, shared = set(), set()
    for page in (1, 2):
        d = S._gt.get(f"{K.GT}/networks/eth/tokens/{addr}/pools", {"page": page}, S.GT_HDR)
        for p in (d or {}).get("data", []):
            a = ((p.get("attributes") or {}).get("address") or "").lower()
            dex = (((p.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id", "")
            if len(a) == 42:
                out.add(a)
            if "v4" in dex:
                shared.add("0x000000000004444c5dc75cb358380d2e3de08a90")
            if "balancer" in dex:
                shared |= {"0xba12222222228d8ba445958a75a0704d566bf2c8", "0xba1333333333a1ba1108e8412f11850a5c319ba9"}
        if not d or len(d.get("data", [])) < 20:
            break
    return sorted(out | shared)


def prices_for(symbol: str, addr: str, since: str, price_now: float | None = None) -> dict[str, float]:
    """Daily closes: Binance if the coin trades there under the same ticker AND at the same price (two coins can share
    a ticker), else GeckoTerminal's top pools for this exact contract (merged by day)."""
    out: dict[str, float] = {}
    ks = S.bn_daily(f"{symbol.upper()}USDT", start_ms=int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp() * 1000),
                    limit=1000, quiet=True)
    last = S.f(ks[-1][4]) if ks else None
    if ks and price_now and last and abs(last / price_now - 1) <= 0.05:
        for k in ks:
            out[day_of(int(k[0]) // 1000)] = S.f(k[4])
    if len(out) >= 300:
        return out
    d = S._gt.get(f"{K.GT}/networks/eth/tokens/{addr}/pools", {"page": 1}, S.GT_HDR)
    pools = sorted(((p.get("attributes") or {}) for p in (d or {}).get("data", [])), key=lambda a: -(S.f(a.get("reserve_in_usd")) or 0))[:3]
    for a in pools:
        c = S.gt_daily("eth", a.get("address"), days=1000) if a.get("address") else None
        for x in c or []:
            out.setdefault(day_of(x["t"]), x["c"])
    return out


def funder(addr: str, cache: dict) -> str | None:
    with _funder_lock:
        if addr in cache:
            return cache[addr]
    r = S.es({"module": "account", "action": "txlist", "address": addr, "startblock": 0, "endblock": 99999999, "page": 1, "offset": 20, "sort": "asc"})
    f = None
    for t in r if isinstance(r, list) else []:
        if (t.get("to") or "").lower() == addr and int(t.get("value") or 0) > 0:
            f = (t.get("from") or "").lower()
            break
    with _funder_lock:
        cache[addr] = f
    return f


def link(acc: list[dict], cache: dict, tracker: ST.Tracker):
    """Probable groups: addresses first funded by the same wallet (not an exchange, not a service). Labelled
    'probably linked, not proven'."""
    ex = K.EXCHANGE_WALLETS.get("eth", {})
    counts: dict[str, int] = {}
    with _funder_lock:
        fs = list(cache.values())
    for f in fs:
        if f:
            counts[f] = counts.get(f, 0) + 1
    for x in acc:
        f = funder(x["address"], cache)
        if f and f not in ex and counts.get(f, 0) < FUNDER_CAP and f not in ST.ROUTERS:
            tracker.group[x["address"]] = f
            x["group"] = f
            x["funder"] = f


_budget_lock = threading.Lock()
_funder_lock = threading.Lock()


def take(budget: list) -> bool:
    """Spend one request from the run's shared allowance."""
    with _budget_lock:
        if budget[0] <= 0:
            return False
        budget[0] -= 1
        return True


def advance(tok: dict, st: dict, budget: list[int], now: datetime, funders: dict) -> dict:
    """Move one token forward by up to PER_TOKEN_CALLS pages. tok: {address, symbol, id, supply}."""
    addr = tok["address"]
    if not st:
        st = {"address": addr, "symbol": tok["symbol"], "id": tok["id"], "signals": [], "watch": [], "transfers": 0, "calls": 0, "lp": []}
    if not st.get("pools") and st.get("transfers"):   # replayed without knowing its pools (a failed lookup): start again
        st.update(cursor=None, tracker=None, transfers=0, signals=[], watch=[], lp=[], caught_up=False, max_acc_share=0.0)
    if not st.get("pools") or (st.get("pools_at") or "") < (now - timedelta(days=7)).isoformat():
        found = pools_for(addr)
        if found or not st.get("pools"):
            st["pools"], st["pools_at"] = found, now.isoformat()
    if not st["pools"]:                               # no pool known yet (or none exists): nothing can be read as a swap
        st["waiting"] = "no DEX pool found yet"
        return st
    st.pop("waiting", None)
    if st.get("cursor") is None:
        back = BIG_HISTORY_D if (tok.get("mcap") or 0) > BIG_CAP else HISTORY_D
        b = block_at(int((now - timedelta(days=back)).timestamp()))
        take(budget)
        if b is None:
            return st
        st["cursor"], st["start_day"], st["history_days"] = b, (now - timedelta(days=back)).date().isoformat(), back
    if not st.get("prices") or (st.get("prices_at") or "")[:10] < now.date().isoformat():
        st["prices"], st["prices_at"] = prices_for(tok["symbol"], addr, st["start_day"], tok.get("price")), now.isoformat()
    supply = tok.get("supply") or st.get("supply")
    st["supply"] = supply
    pools = set(st["pools"])
    tr = ST.Tracker(st.get("tracker"))
    prices = st["prices"]
    calls = 0
    while calls < PER_TOKEN_CALLS and take(budget):
        r = S.es({"module": "account", "action": "tokentx", "contractaddress": addr, "startblock": st["cursor"], "endblock": 99999999,
                  "page": 1, "offset": PAGE, "sort": "asc"})
        calls += 1
        if r is None:
            break
        rows = r if isinstance(r, list) else []
        full = len(rows) >= PAGE
        if full:                                      # leave the last block for the next page (it may be cut)
            lastb = rows[-1]["blockNumber"]
            rows = [x for x in rows if x["blockNumber"] != lastb]
            next_cursor = int(lastb)
            if not rows:                              # one block with 1,000+ transfers: skip it, say so
                st["skipped_blocks"] = st.get("skipped_blocks", 0) + 1
                st["cursor"] = int(lastb) + 1
                continue
        else:
            next_cursor = (int(rows[-1]["blockNumber"]) + 1) if rows else st["cursor"]
        dec = int(rows[0].get("tokenDecimal") or 18) if rows else 18
        i = 0
        while i < len(rows):                          # one transaction at a time
            h = rows[i]["hash"]
            fn = (rows[i].get("functionName") or "").lower()
            grp = []
            while i < len(rows) and rows[i]["hash"] == h:
                x = rows[i]
                grp.append({"from": x["from"].lower(), "to": x["to"].lower(), "qty": int(x["value"]) / 10 ** dec})
                i += 1
            d = day_of(int(rows[i - 1]["timeStamp"]))
            if tr.day and d != tr.day:
                finish_day(st, tr, prices, supply, funders)
            tr.day = d
            lp = ST.liquidity_kind(fn)
            if lp:                                    # adding or removing liquidity is neither a buy nor a sell
                ST.note_liquidity(st, tr, d, lp, grp, pools)
                continue
            buyers, sellers = ST.classify(grp, pools)
            if buyers or sellers:
                tr.add(d, buyers, sellers)
        st["transfers"] += len(rows)
        st["cursor"] = next_cursor
        if not full:
            st["caught_up"] = True
            break
    st["calls"] += calls
    st["tracker"] = tr.to_state()
    st["last_day"] = tr.day
    st["updated"] = now.isoformat()
    return st


def finish_day(st: dict, tr: ST.Tracker, prices: dict, supply, funders: dict, chain: str = "eth"):
    """A day is finished: track the open signals' accumulators, roll the window, look for a new signal."""
    day = tr.day
    for w in st["watch"]:                             # accumulators' net selling, per day, after each signal
        if w.get("closed"):
            continue
        net_sell = sum(tr.today.get(a, [0, 0])[1] - tr.today.get(a, [0, 0])[0] for a in w["addresses"])
        if net_sell:
            w["sold_by_day"][day] = w["sold_by_day"].get(day, 0.0) + net_sell
        if ST._days(w["day"], day) > ST.TRADE["max_days"] + 5:
            w["closed"] = True
    tr.close_day()
    price = prices.get(day)
    p30 = prices.get(ST._minus(day, 30))
    quiet = (price / p30 - 1) if price and p30 else None
    acc = tr.accumulators(supply, price)
    if acc and supply:                                # how close this token has come to a signal (shown on the page)
        tot = sum(x["net"] for x in acc) / supply
        if tot > st.get("max_acc_share", 0.0):
            st["max_acc_share"], st["max_acc_day"] = tot, day
    sig = ST.signal(acc, supply, quiet, day, tr.last_signal)
    if sig:
        if chain == "eth" and S.etherscan_key():
            link(acc, funders, tr)
            sig["groups"] = len({x["group"] for x in acc})
            sig["top"] = acc[:8]
        tr.last_signal = day
        sig["price"] = price
        st["signals"].append(sig)
        st["watch"].append({"day": day, "addresses": [x["address"] for x in acc], "net": sum(x["net"] for x in acc), "sold_by_day": {}})


def results(states: list[dict], now: datetime) -> dict:
    """Every signal with its paper trade, against the same trade on random coins on the same day."""
    rows = []
    prices = {s["address"]: s.get("prices") or {} for s in states}
    keys = sorted(prices)
    for s in states:
        for sig, w in zip(s.get("signals", []), s.get("watch", [])):
            tr = ST.simulate(sig["day"], s.get("prices") or {}, w.get("sold_by_day") or {}, w.get("net") or 0)
            rnd = random.Random(f"{s['address']}{sig['day']}")
            others = [k for k in keys if k != s["address"] and any(d > sig["day"] for d in prices[k])]
            pick = rnd.sample(others, min(30, len(others)))
            base = [ST.simulate_price_only(sig["day"], prices[k]) for k in pick]
            base = [b for b in base if b is not None]
            rows.append({"symbol": s["symbol"], "address": s["address"], "day": sig["day"], "share": sig["share"],
                         "accumulators": sig["accumulators"], "groups": sig.get("groups"), "since": sig.get("since"),
                         "quiet_change": sig["quiet_change"], "price": sig.get("price"), "trade": tr,
                         "lp_by_accumulators": [e for e in s.get("lp", []) if e["day"] >= sig["day"] and set(e["by"]) & set(w["addresses"])][:5],
                         "random": sum(base) / len(base) if base else None, "n_random": len(base),
                         "top": [{k: x.get(k) for k in ("address", "group", "buy_days", "share", "median_buy_usd", "buys", "sold", "bought", "funder")}
                                 for x in sig.get("top", [])[:5]],
                         "live": sig["day"] >= (now - timedelta(days=3)).date().isoformat()})
    rows.sort(key=lambda r: r["day"])
    done = [r for r in rows if r["trade"] and r["trade"]["ret"] is not None and r["random"] is not None]

    def stats(xs):
        n = len(xs)
        if not n:
            return {"n": 0}
        ret = [x["trade"]["ret"] for x in xs]
        e = [x["trade"]["ret"] - x["random"] for x in xs]
        me = sum(e) / n
        sd = (sum((v - me) ** 2 for v in e) / (n - 1)) ** 0.5 if n > 2 else None
        return {"n": n, "avg": sum(ret) / n, "hit": sum(1 for v in ret if v > 0) / n, "random": sum(x["random"] for x in xs) / n,
                "edge": me, "t_edge": me / (sd / n ** 0.5) if sd else None,
                "pumped": sum(1 for x in xs if x["trade"]["best"] >= ST.TRADE["take_profit"]) / n,
                "reasons": {k: sum(1 for x in xs if x["trade"]["reason"] == k) for k in {x["trade"]["reason"] for x in xs}},
                "median_days": sorted(x["trade"]["days"] for x in xs)[n // 2]}
    allr, late = stats(done), stats(done[len(done) * 2 // 3:])
    if allr["n"] < 30:
        verdict, level = f"Too few finished signals to judge ({allr['n']}; 30 needed).", "warn"
    elif allr["avg"] <= 0:
        verdict, level = "Lost money after costs: buying when the quiet accumulation appeared did not pay.", "bad"
    elif (allr["t_edge"] or 0) < 2:
        verdict, level = "Made money, but not clearly better than the same trade on random coins: could be luck.", "warn"
    elif not (late["n"] >= 10 and late["edge"] > 0 and (late["t_edge"] or 0) >= 1 and late["avg"] > 0):
        verdict, level = "Worked earlier, but not clearly in the most recent third: the edge seems to have faded.", "warn"
    else:
        verdict, level = "Held up: made money after costs, beat random coins by more than luck, and still did recently.", "good"
    prog = {"tokens": len(states), "caught_up": sum(1 for s in states if s.get("caught_up")),
            "transfers": sum(s.get("transfers", 0) for s in states), "calls": sum(s.get("calls", 0) for s in states),
            "days_replayed": sum(ST._days(s["start_day"], s["last_day"]) for s in states if s.get("start_day") and s.get("last_day"))}
    return {"generated_at": now.isoformat(), "rules": ST.RULES, "trade": ST.TRADE, "history_days": HISTORY_D, "progress": prog,
            "signals": rows[-150:][::-1], "n_signals": len(rows), "all": allr, "late": late, "verdict": verdict, "level": level,
            "tokens": [{"symbol": s["symbol"], "address": s["address"], "caught_up": bool(s.get("caught_up")), "last_day": s.get("last_day"),
                        "transfers": s.get("transfers", 0), "pools": len(s.get("pools") or []), "signals": len(s.get("signals", [])),
                        "closest": s.get("max_acc_share", 0.0), "closest_day": s.get("max_acc_day"), "waiting": s.get("waiting"),
                        "history_days": s.get("history_days"),
                        "price_days": len(s.get("prices") or {})} for s in states]}


def run(universe: list[dict], now: datetime, max_calls: int = 3000, max_seconds: int = 900) -> dict:
    """universe: [{address, symbol, id, supply}] for coins with an Ethereum contract."""
    if not S.etherscan_key():
        return {"generated_at": now.isoformat(), "unavailable": "Etherscan key not set: the Ethereum replay can't run."}
    t0 = time.monotonic()
    budget = [max_calls]
    fpath = Path(".cache/accum/funders.json")
    try:
        funders = json.loads(fpath.read_text())
    except (OSError, ValueError):
        funders = {}
    order = sorted(universe, key=lambda t: (load(t["address"]).get("caught_up", False), t.get("rank", 999)))
    states: dict[str, dict] = {}

    def work(tok):
        st = load(tok["address"])
        if budget[0] > 0 and time.monotonic() - t0 < max_seconds:
            try:
                st = advance(tok, st, budget, now, funders)
                save(tok["address"], st)
            except Exception as e:  # noqa: BLE001  one token never stops the rest
                log.warning("replay %s: %s", tok.get("symbol"), e)
        return tok["address"], st

    with ThreadPoolExecutor(WORKERS) as ex:
        for a, st in ex.map(work, order):
            if st:
                states[a] = st
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(json.dumps(funders))
    out = results(list(states.values()), now)
    out["calls_this_run"] = max_calls - budget[0]
    return out
