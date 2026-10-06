"""Whale tracker (PAPER ONLY): do the best Hyperliquid traders of the last month keep winning, and does mirroring
their whole positions pay, after our real delay and costs?

Research behind it (DECISIONS D80): over 250 random active whales, last month's top 20% (directional traders only)
made a median +7.2% the next month and 75% stayed profitable, against -0.9% and 38% for the bottom 20%; but copying
their single opening trades and holding for a fixed time had no edge even with no delay. So this page mirrors whole
positions (entries, adds, cuts and exits together) and tests that, month by month.

    python -m whales.run rank    # nightly: new cohort when the current one is 30 days old; leaderboard figures
    python -m whales.run scan    # every 15 minutes: positions, changes, paper mirror -> .cache/whales_out/snapshot.json

Cohorts: the 20 whales with the best ROI over the last month among accounts of $1M+, trading this week, with
turnover under 30x a month (not market-making bots) and profitable overall; plus 20 random eligible whales as the
comparison. A cohort is frozen for 30 days (an honest forward test: chosen first, judged afterwards).
Mirror: each followed whale gets an equal share of the paper money and its positions are copied in proportion to
its account (its own leverage, total capped at 5x); re-copied every scan, paying 0.07% per side on what changes and
the hourly funding. Compared with mirroring the random whales and with holding BTC.
"""
from __future__ import annotations

import json
import logging
import math
import random
import sys
import time

import pandas as pd
import requests

from core.config import REPO_ROOT, state_path
from lab.run import clean
from whales import outlook

log = logging.getLogger("omega.whales")
API = "https://api.hyperliquid.xyz/info"
BOARD = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
N_LEAD, N_RAND = 20, 20
MIN_ACCT = 1_000_000.0
MAX_TURNOVER = 30.0
COHORT_DAYS = 30
COST_SIDE = 0.0007          # taker fee 0.045% + slippage
MAX_GROSS = 5.0
BIG_NOTIONAL = 1_000_000.0  # a "big" new position worth showing (and alerting, once the mirror holds up)
MIN_DAYS = 30               # days of mirror results before a verdict
_s = requests.Session()
_s.headers.update({"User-Agent": "mempool-omega research (paper only)"})


def post(body: dict, tries: int = 5):
    for i in range(tries):
        try:
            r = _s.post(API, json=body, timeout=45)
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            time.sleep(0.25)
            return r.json()
        except requests.RequestException as e:  # noqa: PERF203
            if i == tries - 1:
                raise
            log.debug("retry %s: %s", body.get("type"), e)
            time.sleep(2 * (i + 1))
    return None


def _load(name: str, default):
    p = state_path("whales", name)
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except ValueError:
        return default


def _save(name: str, obj) -> None:
    state_path("whales", name).write_text(json.dumps(clean(obj), indent=0))


# ---------------------------------------------------------------------------------------------- ranking
def perf(r: dict, window: str, key: str) -> float:
    try:
        return float(dict(r["windowPerformances"])[window][key])
    except (KeyError, ValueError, TypeError):
        return float("nan")


def eligible(r: dict) -> bool:
    acct = float(r.get("accountValue") or 0)
    if acct < MIN_ACCT:
        return False
    mv, wv = perf(r, "month", "vlm"), perf(r, "week", "vlm")
    return mv > 0 and wv > 0 and mv / acct < MAX_TURNOVER and perf(r, "allTime", "pnl") > 0


def summary(r: dict) -> dict:
    acct = float(r["accountValue"])
    return {"addr": r["ethAddress"], "name": r.get("displayName"), "acct": acct, "roi_m": perf(r, "month", "pnl") / acct,
            "pnl_m": perf(r, "month", "pnl"), "roi_w": perf(r, "week", "roi"), "pnl_all": perf(r, "allTime", "pnl"),
            "turnover": perf(r, "month", "vlm") / acct}


def month_return(r: dict) -> float:
    """Last month's profit over today's account: the measure the research ranked on. (The leaderboard's own ROI can
    be inflated by money deposited during the month.)"""
    return perf(r, "month", "pnl") / float(r["accountValue"])


def choose(rows: list[dict], seed: int) -> tuple[list[dict], list[dict]]:
    pool = [r for r in rows if eligible(r)]
    pos = [r for r in pool if month_return(r) > 0]
    lead = sorted(pos, key=lambda r: -month_return(r))[:N_LEAD]
    rest = [r for r in pool if r not in lead]
    rnd = random.Random(seed).sample(rest, min(N_RAND, len(rest)))
    return [summary(r) for r in lead], [summary(r) for r in rnd]


def rank() -> dict:
    """Nightly: start a new cohort when the current one has run 30 days; refresh the leaderboard figures."""
    now = pd.Timestamp.now(tz="UTC")
    rows = requests.get(BOARD, timeout=120, headers={"User-Agent": "mempool-omega research"}).json()["leaderboardRows"]
    cohorts = _load("cohorts.json", [])
    cur = cohorts[-1] if cohorts else None
    if cur is None or now - pd.Timestamp(cur["start"], unit="ms", tz="UTC") >= pd.Timedelta(days=COHORT_DAYS):
        lead, rnd = choose(rows, int(now.strftime("%Y%m%d")))
        cohorts.append({"id": now.strftime("%Y-%m-%d"), "start": int(now.value // 10**6), "leaders": lead, "random": rnd,
                        "eligible": sum(1 for r in rows if eligible(r))})
        _save("cohorts.json", cohorts)
        log.info("new cohort %s: %d leaders, %d random", cohorts[-1]["id"], len(lead), len(rnd))
    follow = {w["addr"] for c in cohorts[-1:] for w in c["leaders"] + c["random"]}
    board = {r["ethAddress"]: summary(r) for r in rows if r["ethAddress"] in follow}
    top_now = [summary(r) for r in sorted([r for r in rows if eligible(r) and month_return(r) > 0], key=lambda r: -month_return(r))[:30]]
    _save("board.json", {"at": int(now.value // 10**6), "board": board, "top_now": top_now, "whales": len(rows),
                         "eligible": sum(1 for r in rows if eligible(r))})
    return cohorts[-1]


# ---------------------------------------------------------------------------------------------- scanning and the mirror
def market() -> tuple[dict, dict]:
    """Mid prices and hourly funding rates per coin."""
    meta, ctxs = post({"type": "metaAndAssetCtxs"})
    mids, funding = {}, {}
    for u, c in zip(meta["universe"], ctxs):
        try:
            mids[u["name"]] = float(c.get("midPx") or c.get("markPx"))
            funding[u["name"]] = float(c.get("funding") or 0.0)
        except (TypeError, ValueError):
            continue
    return mids, funding


def positions(addr: str) -> tuple[float, dict]:
    d = post({"type": "clearinghouseState", "user": addr}) or {}
    acct = float((d.get("marginSummary") or {}).get("accountValue") or 0.0)
    out = {}
    for ap in d.get("assetPositions") or []:
        p = ap.get("position") or {}
        try:
            szi = float(p["szi"])
        except (KeyError, TypeError, ValueError):
            continue
        if szi == 0:
            continue
        out[p["coin"]] = {"szi": szi, "entry": float(p.get("entryPx") or 0), "value": float(p.get("positionValue") or 0),
                          "upnl": float(p.get("unrealizedPnl") or 0), "lev": float((p.get("leverage") or {}).get("value") or 0),
                          "liq": float(p["liquidationPx"]) if p.get("liquidationPx") else None}
    return acct, out


def exposure(books: dict, mids: dict) -> dict:
    """Mirror exposure per coin, as a multiple of the paper money: each whale gets an equal share and is copied in
    proportion to its own account. Gross capped at MAX_GROSS."""
    live = {a: b for a, b in books.items() if b["acct"] > 0}
    if not live:
        return {}
    exp: dict[str, float] = {}
    for b in live.values():
        for coin, p in b["pos"].items():
            if coin in mids:
                exp[coin] = exp.get(coin, 0.0) + p["szi"] * mids[coin] / b["acct"] / len(live)
    gross = sum(abs(v) for v in exp.values())
    if gross > MAX_GROSS:
        exp = {k: v * MAX_GROSS / gross for k, v in exp.items()}
    return exp


def step(eq: float, exp_prev: dict, exp_new: dict, mids_prev: dict, mids: dict, funding: dict, hours: float) -> float:
    """One scan of the paper mirror: price moves on the old exposure, funding over the interval, costs on the change."""
    r = sum(v * (mids[c] / mids_prev[c] - 1) for c, v in exp_prev.items() if c in mids and c in mids_prev and mids_prev[c] > 0)
    fund = sum(v * funding.get(c, 0.0) * hours for c, v in exp_prev.items())        # longs pay positive funding
    keys = set(exp_prev) | set(exp_new)
    cost = COST_SIDE * sum(abs(exp_new.get(c, 0.0) - exp_prev.get(c, 0.0)) for c in keys)
    return max(eq * (1 + r - fund - cost), 0.0)


def changes(prev: dict, books: dict, mids: dict) -> list[dict]:
    out = []
    for a, b in books.items():
        old = (prev.get(a) or {}).get("pos") or {}
        for coin in set(old) | set(b["pos"]):
            o, n = old.get(coin), b["pos"].get(coin)
            px = mids.get(coin, 0.0)
            if n and not o:
                kind = "opened"
            elif o and not n:
                kind = "closed"
            elif o and n and math.copysign(1, o["szi"]) != math.copysign(1, n["szi"]):
                kind = "flipped"
            elif o and n and abs(n["szi"]) > abs(o["szi"]) * 1.25:
                kind = "added"
            elif o and n and abs(n["szi"]) < abs(o["szi"]) * 0.75:
                kind = "cut"
            else:
                continue
            ref = n or o
            # moved: the money that changed hands in this move (a flip closes the old side and opens the new one)
            szo, szn = (o or {"szi": 0.0})["szi"], (n or {"szi": 0.0})["szi"]
            moved = (abs(szo) + abs(szn) if kind == "flipped" else abs(szn - szo)) * px
            out.append({"addr": a, "coin": coin, "kind": kind, "side": "long" if ref["szi"] > 0 else "short",
                        "notional": abs((n or {"szi": 0})["szi"]) * px if n else abs(o["szi"]) * px,
                        "moved": moved, "entry": (n or o)["entry"], "price": px})
    return sorted(out, key=lambda x: -x["notional"])


def verdict(m: pd.DataFrame) -> dict:
    """Leaders' mirror against the random whales' mirror, day by day (paired), once there are MIN_DAYS of results."""
    if m.empty:
        return {"level": "warn", "text": "The mirror has just started: results build up from today.", "days": 0}
    m = m.copy()
    m["t"] = pd.to_datetime(m["t"], unit="ms", utc=True)
    d = m.set_index("t")[["eq_lead", "eq_rand", "eq_btc"]].resample("1D").last().dropna()
    days = len(d)
    out = {"days": days, "lead": float(m["eq_lead"].iloc[-1] - 1), "rand": float(m["eq_rand"].iloc[-1] - 1), "btc": float(m["eq_btc"].iloc[-1] - 1)}
    if days < MIN_DAYS:
        return {**out, "level": "warn", "text": f"{days} day(s) of results so far; a verdict needs at least {MIN_DAYS}."}
    r = d.pct_change().dropna()
    diff = r["eq_lead"] - r["eq_rand"]
    t = float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))) if len(diff) > 2 and diff.std(ddof=1) > 0 else float("nan")
    out["t"] = t
    if out["lead"] <= 0:
        return {**out, "level": "bad", "text": "Mirroring last month's best whales lost money after costs."}
    if not (t >= 2):
        return {**out, "level": "warn", "text": "Made money, but not clearly more than mirroring random whales: could be luck."}
    if out["lead"] <= out["btc"]:
        return {**out, "level": "warn", "text": "Beat random whales but not simply holding BTC."}
    return {**out, "level": "good", "text": "Held up: the mirror made money after costs and beat both random whales and holding BTC."}


def scan() -> dict:
    now = pd.Timestamp.now(tz="UTC")
    now_ms = int(now.value // 10**6)
    cohorts = _load("cohorts.json", [])
    if not cohorts:
        rank()
        cohorts = _load("cohorts.json", [])
    cur = cohorts[-1]
    mids, funding = market()
    books = {"lead": {}, "rand": {}}
    for grp, key in (("lead", "leaders"), ("rand", "random")):
        for w in cur[key]:
            try:
                acct, pos = positions(w["addr"])
            except Exception as e:  # noqa: BLE001
                log.warning("positions %s: %s", w["addr"][:10], e)
                continue
            books[grp][w["addr"]] = {"acct": acct, "pos": pos}
    last = _load("last.json", {})
    exp = {g: exposure(books[g], mids) for g in books}
    mp = state_path("whales", "mirror.csv")
    m = pd.read_csv(mp) if mp.exists() else pd.DataFrame(columns=["t", "cohort", "eq_lead", "eq_rand", "eq_btc", "gross_lead", "gross_rand"])
    if last and last.get("cohort") == cur["id"] and len(m):
        hours = max((now_ms - last["t"]) / 3_600_000, 0.0)
        prev = m.iloc[-1]
        eq_l = step(float(prev["eq_lead"]), last["exp"]["lead"], exp["lead"], last["mids"], mids, funding, hours)
        eq_r = step(float(prev["eq_rand"]), last["exp"]["rand"], exp["rand"], last["mids"], mids, funding, hours)
        eq_b = float(prev["eq_btc"]) * mids.get("BTC", 1.0) / last["mids"].get("BTC", mids.get("BTC", 1.0))
    else:   # a new cohort (or the first run): buy in at today's prices, paying the cost of opening the copies
        prev_l = float(m["eq_lead"].iloc[-1]) if len(m) else 1.0
        prev_r = float(m["eq_rand"].iloc[-1]) if len(m) else 1.0
        eq_b = float(m["eq_btc"].iloc[-1]) if len(m) else 1.0
        eq_l = prev_l * (1 - COST_SIDE * sum(abs(v) for v in exp["lead"].values()))
        eq_r = prev_r * (1 - COST_SIDE * sum(abs(v) for v in exp["rand"].values()))
    m = pd.concat([m, pd.DataFrame([{"t": now_ms, "cohort": cur["id"], "eq_lead": eq_l, "eq_rand": eq_r, "eq_btc": eq_b,
                                     "gross_lead": sum(abs(v) for v in exp["lead"].values()),
                                     "gross_rand": sum(abs(v) for v in exp["rand"].values())}])], ignore_index=True)
    mp.write_text(m.to_csv(index=False))
    moved = changes((last.get("books") or {}).get("lead") or {}, books["lead"], mids) if last.get("cohort") == cur["id"] else []
    _save("last.json", {"t": now_ms, "cohort": cur["id"], "mids": {c: mids[c] for c in set(exp["lead"]) | set(exp["rand"]) | {"BTC"} if c in mids},
                        "exp": exp, "books": {"lead": books["lead"]}})
    net: dict[str, float] = {}
    gross: dict[str, float] = {}
    for b in books["lead"].values():
        for coin, p in b["pos"].items():
            net[coin] = net.get(coin, 0.0) + p["szi"] * mids.get(coin, 0.0)
            gross[coin] = gross.get(coin, 0.0) + abs(p["szi"]) * mids.get(coin, 0.0)
    held = sorted(gross, key=lambda c: -gross[c])[:25]          # the coins "What the whales hold now" lists
    try:   # market signals and the outlook on probation; never allowed to stop the mirror
        ol = outlook.refresh(post, _load, _save, held, {c: (1 if v > 0 else -1 if v < 0 else 0) for c, v in net.items()}, mids, now_ms)
    except Exception as e:  # noqa: BLE001
        log.warning("outlook: %s", e)
        ol = {"coins": {}, "record": None}
    return snapshot(now, cur, cohorts, books, exp, moved, m, mids, ol)


def snapshot(now, cur, cohorts, books, exp, moved, m, mids, ol=None) -> dict:
    ol = ol or {"coins": {}, "record": None}
    board = _load("board.json", {})
    meta = {w["addr"]: w for w in cur["leaders"] + cur["random"]}
    leaders = []
    for i, w in enumerate(cur["leaders"]):
        b = books["lead"].get(w["addr"]) or {"acct": None, "pos": {}}
        now_b = (board.get("board") or {}).get(w["addr"]) or {}
        gross = sum(abs(p["szi"]) * mids.get(c, 0) for c, p in b["pos"].items())
        leaders.append({**w, "rank": i + 1, "acct_now": b["acct"], "n_pos": len(b["pos"]), "gross_lev": gross / b["acct"] if b["acct"] else None,
                        "roi_m_now": now_b.get("roi_m"), "upnl": sum(p["upnl"] for p in b["pos"].values())})
    pos = []
    for a, b in books["lead"].items():
        for coin, p in b["pos"].items():
            pos.append({"addr": a, "name": (meta.get(a) or {}).get("name"), "coin": coin, "side": "long" if p["szi"] > 0 else "short",
                        "notional": abs(p["szi"]) * mids.get(coin, 0), "entry": p["entry"], "price": mids.get(coin), "upnl": p["upnl"],
                        "lev": p["lev"], "liq": p["liq"], "share": abs(p["szi"]) * mids.get(coin, 0) / b["acct"] if b["acct"] else None})
    cons: dict[str, dict] = {}
    for p in pos:
        c = cons.setdefault(p["coin"], {"coin": p["coin"], "long_n": 0, "short_n": 0, "long_usd": 0.0, "short_usd": 0.0})
        c[f"{p['side']}_n"] += 1
        c[f"{p['side']}_usd"] += p["notional"]
    for c in cons.values():
        c["net_usd"] = c["long_usd"] - c["short_usd"]
        c["mirror"] = exp["lead"].get(c["coin"], 0.0)
        c["price"] = mids.get(c["coin"])
        c["signals"] = ol["coins"].get(c["coin"])
    curve = m[m["cohort"] == cur["id"]] if len(m) else m
    ver = verdict(m)
    hist = []
    for c in cohorts:
        seg = m[m["cohort"] == c["id"]]
        if len(seg) > 1:
            hist.append({"id": c["id"], "lead": float(seg["eq_lead"].iloc[-1] / seg["eq_lead"].iloc[0] - 1),
                         "rand": float(seg["eq_rand"].iloc[-1] / seg["eq_rand"].iloc[0] - 1), "btc": float(seg["eq_btc"].iloc[-1] / seg["eq_btc"].iloc[0] - 1),
                         "days": float((seg["t"].iloc[-1] - seg["t"].iloc[0]) / 86_400_000)})
    snap = {"generated_at": str(now), "cohort": {"id": cur["id"], "start": cur["start"], "eligible": cur.get("eligible"), "days": COHORT_DAYS},
            "leaders": leaders, "positions": sorted(pos, key=lambda p: -p["notional"])[:80],
            "consensus": sorted(cons.values(), key=lambda c: -(c["long_usd"] + c["short_usd"]))[:25],
            "changes": [x | {"name": (meta.get(x["addr"]) or {}).get("name")} for x in moved[:150]],
            "big_changes": [x for x in moved if x["notional"] >= BIG_NOTIONAL and x["kind"] in ("opened", "flipped", "added")][:20],
            "mirror": {"exp": sorted(([k, v] for k, v in exp["lead"].items()), key=lambda kv: -abs(kv[1]))[:15],
                       "gross_lead": sum(abs(v) for v in exp["lead"].values()), "gross_rand": sum(abs(v) for v in exp["rand"].values()),
                       "curve": [[int(r.t), round(float(r.eq_lead), 5), round(float(r.eq_rand), 5), round(float(r.eq_btc), 5)] for r in curve.iloc[::max(1, len(curve) // 400)].itertuples()]},
            "verdict": ver, "history": hist, "board_at": board.get("at"), "top_now": (board.get("top_now") or [])[:10],
            "research": {"whales": 250, "trades": 885531, "top_next": 0.072, "top_profitable": 0.75, "bottom_next": -0.009, "bottom_profitable": 0.38, "rho": 0.36},
            "costs": {"side": COST_SIDE, "max_gross": MAX_GROSS}, "outlook": ol["record"]}
    out = REPO_ROOT / ".cache" / "whales_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task = argv[0] if argv else "scan"
    if task == "rank":
        rank()
    elif task == "scan":
        s = scan()
        log.info("whales scan: %d leaders, %d positions, %d changes, verdict %s", len(s["leaders"]), len(s["positions"]), len(s["changes"]), s["verdict"]["level"])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
