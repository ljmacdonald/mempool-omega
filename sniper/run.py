"""Sniper lab (PAPER ONLY): does buying brand-new DEX tokens that pass our scam and rug-pull checks make money, at
the speed we can actually achieve, after real costs? And how much do the checks save you?

Every run (every 15 minutes, GitHub Actions):
1. The newest pools on Solana, BNB Chain and Ethereum (GeckoTerminal), paired with SOL/ETH/BNB or a stablecoin
   and holding at least $10k, are checked with the DEX page's scam and rug-pull tests (dex/security.py), every
   scam test exactly as strict; only the age/size/activity minimums that no new token can meet are relaxed.
2. Every checked token is paper-bought at the price when the checks finished (what we could really do), passed
   AND rejected, so the lab can show what the checks saved you from. Costs: pool fee, price impact, sandwich bots,
   taxes, gas and a sniper-competition cost (bots that bought seconds after launch sell into our buy).
3. Each paper buy is sold by three fixed plans (take profit / safety exit / time limit), judged on 5-minute
   candles after the entry; a crash straight through the safety exit fills at the crash price, not the stop.
4. Results per plan: passed tokens against rejected ones over the same hours (paired), the share that rugged,
   our real delay after launch, and the verdict (lab.run.verdict: enough trades, profit after costs, better than
   buying without the checks, still true in the most recent third). Ideas are graded like every other page.

    python -m sniper.run      -> state/sniper/positions.csv, .cache/sniper_out/snapshot.json
"""
from __future__ import annotations

import json
import logging
import sys
import time

import numpy as np
import pandas as pd

from core.config import REPO_ROOT, state_path
from dex import data as G
from dex import security as S
from dex.chains import CHAINS, NOT_IDEAS, PROTECTED
from dex.costs import dex_cost
from dex.fetch import facts_for
from lab.run import clean, summarize, verdict
from scanner.quality import quality
from scanner.rank import grade as grade_of
from scanner.rank import score_from_r

log = logging.getLogger("omega.sniper")
NETS = ("solana", "bsc", "ethereum")
MIN_LIQ = 10_000.0
MAX_AGE_MIN = 60.0
CHECKS_PER_CHAIN = 8           # security checks per run and network (free services are rate-limited)
SNIPE_COST = 0.02              # bots that bought in the first seconds sell into a late buyer: extra cost on entry
AMOUNT = 100.0
PLANS = {
    "quick": {"name": "Quick", "tp": 0.25, "sl": 0.15, "hours": 0.5},
    "standard": {"name": "Standard", "tp": 0.50, "sl": 0.25, "hours": 2.0},
    "runner": {"name": "Runner", "tp": 1.00, "sl": 0.35, "hours": 12.0},
}
FINAL_AFTER_H = 12.5           # judge a position once the longest plan has run its course
RUG_DROP = 0.90                # fell 90% from the entry, or the pool lost 90% of its money: rugged
GAP = 0.8                      # a candle whose low is below 80% of the stop crashed through it (filled at the low)
HIST = "sniper/positions.csv"
COLS = ["id", "chain", "pool", "token", "symbol", "name", "dex", "created", "entry_t", "delay_min", "entry", "reserve",
        "reserve0", "pool_fee", "buy_tax", "sell_tax", "cost_rt", "verdict", "reasons", "warnings", "grade", "score", "plan",
        "status", "last", "last_t", "last_reserve", "rug"] + [f"{p}_{k}" for p in PLANS for k in ("net", "reason")]
K_HIT, K_AVG = 20, 30
MIN_PROVEN = 30


def launch_thresholds() -> dict:
    """The DEX page's limits with only the age/size/activity minimums relaxed (a token minutes old can't have
    14 days of history or 2,000 holders). Every scam test stays exactly as strict."""
    t = S.thresholds(None, "standard")
    t.update({"min_liq": MIN_LIQ, "min_age_days": 0.0, "min_active_days": 0.0, "min_buyers": 0.0, "min_sellers": 0.0,
              "min_sell_ratio": 0.0, "min_holders": 0.0})
    return t


# ---------------------------------------------------------------------------------------------- discovery
def new_pools(chain: str, now: pd.Timestamp, pages: int = 2) -> list[dict]:
    net = CHAINS[chain]["gt"]
    out = []
    for page in range(1, pages + 1):
        try:
            d = G.http().get(f"{G.GT}/networks/{net}/new_pools", {"page": page, "include": "base_token,quote_token"})
        except Exception as e:  # noqa: BLE001
            log.warning("new pools %s: %s", chain, e)
            break
        toks = {t["id"]: t["attributes"] for t in d.get("included", [])}
        out += [G.pool_record(chain, p, toks) for p in d.get("data", [])]
    for r in out:
        r["age_min"] = (now - pd.Timestamp(r["created"])).total_seconds() / 60 if r.get("created") else float("nan")
    return out


def eligible(r: dict, chain: str) -> bool:
    sym = (r.get("base_symbol") or "").upper()
    return (r["quote"] in CHAINS[chain]["quotes"] and (r.get("reserve_usd") or 0) >= MIN_LIQ and r["age_min"] == r["age_min"]
            and 0 <= r["age_min"] <= MAX_AGE_MIN and sym not in NOT_IDEAS and r["token"] not in CHAINS[chain]["quotes"])


def real_liquidity(pools: list[dict]) -> list[dict]:
    """Only real money counts (the SOL/ETH/BNB/stablecoin side, from DexScreener): a pool's own reserve figure can
    be made of the scammer's token (seen: "$3.4 billion" new pools). Not verifiable yet: skipped (fail closed)."""
    from dex.live import enrich_liquidity
    if not pools:
        return []
    try:
        enrich_liquidity(pools)
    except Exception as e:  # noqa: BLE001
        log.warning("liquidity: %s", e)
        return []
    out = []
    for r in pools:
        claimed = 0.5 * (r.get("reserve_usd") or 0)
        verified = r.get("liq_real") is not None and abs((r.get("liq_real") or 0) - claimed) > 1e-6     # not the fallback
        if verified and r["liq_real"] >= MIN_LIQ:
            out.append(r)
    return out


def market(r: dict) -> dict:
    tx = r.get("tx_h24") or {}
    return {"liq_real": r.get("liq_real") or 0.0, "age_days": (r.get("age_min") or 0) / 1440, "active_days": None,
            "buyers_h24": tx.get("buyers", 0), "sellers_h24": tx.get("sellers", 0), "buys_h24": tx.get("buys", 0),
            "sells_h24": tx.get("sells", 0), "vol_h24": r.get("vol_h24") or 0.0, "liq_change_6h": None, "liq_change_24h": None,
            "liq_change_72h": None, "boosted": bool(r.get("boosted")), "copycat": False,
            "impersonator": (r.get("base_symbol") or "").upper() in PROTECTED, "rules_changed": False}


def launch_warnings(f: dict, m: dict, chain: str) -> list[str]:
    w = []
    if not f.get("sim_ok"):
        w.append("No buy-and-sell simulation for this token yet: nobody has proven you can sell it." if chain == "solana"
                 else "The sell simulation didn't confirm selling works.")
    if (m.get("sellers_h24") or 0) == 0:
        w.append("No wallet has sold yet: the first sellers are usually the launch bots and the creator.")
    if f.get("insiders"):
        w.append(f"{f['insiders']} linked insider wallets bought together: they often sell together.")
    if (f.get("top10_pct") or 0) > 0.30:
        w.append(f"The 10 biggest wallets hold {f['top10_pct']:.0%}.")
    if f.get("lp_secured_pct") is None or (f.get("lp_secured_pct") or 0) < 0.9:
        w.append("The pool's money isn't (fully) locked: it can be pulled.")
    return w


# ---------------------------------------------------------------------------------------------- outcomes
def simulate(c: pd.DataFrame, entry: float, entry_t: pd.Timestamp, plan: dict) -> tuple[float, str] | None:
    """Exit price ratio (exit / entry) and reason, from 5-minute candles that start after the entry."""
    end = entry_t + pd.Timedelta(hours=plan["hours"])
    x = c[(c.index >= entry_t) & (c.index < end)]
    if x.empty:
        return None
    tp, sl = entry * (1 + plan["tp"]), entry * (1 - plan["sl"])
    for _, b in x.iterrows():
        if b["low"] <= sl:
            if b["low"] < GAP * sl:
                return max(b["low"], 0.0) / entry, "crashed through the safety exit"
            return min(b["open"], sl) / entry, "safety exit"
        if b["high"] >= tp:
            return max(b["open"], tp) / entry, "take profit"
    return float(x["close"].iloc[-1]) / entry, "time limit"


def finish(row: dict, now: pd.Timestamp) -> dict:
    """Judge a position on its 5-minute candles once every plan has run its course."""
    try:
        c = G.gt_ohlcv(row["chain"], row["pool"], n=200, timeframe="minute", aggregate=5)
    except Exception as e:  # noqa: BLE001
        log.warning("candles %s %s: %s", row["chain"], row["pool"], e)
        return row
    entry_t = pd.Timestamp(row["entry_t"])
    if c is None or c.empty or c.index.max() < entry_t:
        if now - entry_t > pd.Timedelta(hours=36):
            row["status"] = "no data"
        return row
    cost = float(row["cost_rt"])
    lows = c[(c.index >= entry_t) & (c.index < entry_t + pd.Timedelta(hours=FINAL_AFTER_H))]["low"]
    crashed = bool(len(lows) and lows.min() <= (1 - RUG_DROP) * float(row["entry"]))
    pulled = bool(row.get("last_reserve") == row.get("last_reserve") and row.get("last_reserve") is not None
                  and float(row["last_reserve"] or 0) < (1 - RUG_DROP) * float(row.get("reserve0") or row["reserve"]))
    row["rug"] = bool(crashed or pulled)
    for p, plan in PLANS.items():
        r = simulate(c, float(row["entry"]), entry_t, plan)
        if r is None:
            continue
        ratio, why = r
        row[f"{p}_net"] = ratio * (1 - cost) - 1
        row[f"{p}_reason"] = why
    row["status"] = "closed" if all(row.get(f"{p}_net") == row.get(f"{p}_net") and row.get(f"{p}_net") is not None for p in PLANS) else "no data"
    return row


# ---------------------------------------------------------------------------------------------- results
def results(h: pd.DataFrame) -> dict:
    """Per plan: tokens that passed vs ones rejected (bought anyway), paired by network and time: each passed
    trade's baseline is the average result of rejected tokens on the same network within 6 hours."""
    d = h[h["status"] == "closed"].copy()
    if d.empty:
        return {p: {"passed": summarize([]), "rejected": summarize([]), "early": summarize([]), "late": summarize([]),
                    "level": "warn", "verdict": "No finished paper snipes yet: results build up from the first day.", "rug_passed": None,
                    "rug_rejected": None} for p in PLANS}
    d["t"] = pd.to_datetime(d["entry_t"], utc=True)
    t0, t1 = d["t"].min(), d["t"].max()
    cut = t0 + (t1 - t0) * 2 / 3
    out = {}
    for p in PLANS:
        rows_p, rows_r = [], []
        rej = d[d["verdict"] == "reject"]
        for r in d.to_dict("records"):
            net = r.get(f"{p}_net")
            if net is None or net != net:
                continue
            row = {"net": float(net), "hold_h": PLANS[p]["hours"], "late": bool(r["t"] >= cut), "rug": bool(r.get("rug"))}
            if r["verdict"] == "pass":
                near = rej[(rej["chain"] == r["chain"]) & ((rej["t"] - r["t"]).abs() <= pd.Timedelta(hours=6))][f"{p}_net"].astype(float)
                row["base"] = float(near.mean()) if len(near) else float("nan")
                rows_p.append(row)
            else:
                row["base"] = float("nan")
                rows_r.append(row)
        a, early, late = summarize(rows_p), summarize([r for r in rows_p if not r["late"]]), summarize([r for r in rows_p if r["late"]])
        level, text = verdict(a, late)
        out[p] = {"passed": a, "rejected": summarize(rows_r), "early": early, "late": late, "level": level, "verdict": text,
                  "rug_passed": float(np.mean([r["rug"] for r in rows_p])) if rows_p else None,
                  "rug_rejected": float(np.mean([r["rug"] for r in rows_r])) if rows_r else None}
    return out


def chosen_plan(res: dict) -> str:
    """The plan with the best edge on the older two thirds (standard until there's evidence)."""
    best, key = None, "standard"
    for p, r in res.items():
        e = r["early"]
        if e["n"] >= 10 and e["edge"] == e["edge"] and (best is None or e["edge"] > best):
            best, key = e["edge"], p
    return key


def judge(res: dict, plan: str, warnings: list[str], penalty: float, cost_rt: float) -> dict:
    r = res[plan]["passed"]
    z = lambda x: float(x) if x is not None and x == x else 0.0  # noqa: E731
    n = int(z(r.get("n")))
    prob = (z(r.get("hit")) * n + 0.5 * K_HIT) / (n + K_HIT)
    exp = z(r.get("avg")) * n / (n + K_AVG) if n else -cost_rt          # no evidence yet: you start down the costs
    ru = abs(z(r.get("avg_loss"))) or PLANS[plan]["sl"]
    pen = {"good": 0.0, "warn": 0.10, "bad": 0.30}[res[plan]["level"]] + penalty + 0.03 * len(warnings)
    adj = exp / ru - pen
    sc = score_from_r(adj)
    return {"prob": prob, "exp_ret": exp, "exp_r_adj": adj, "score": sc, "grade": grade_of(sc), "hist_n": n,
            "evidence": {"good": "Held up in paper tests", "warn": "Not proven yet", "bad": "Failed in paper tests"}[res[plan]["level"]]}


# ---------------------------------------------------------------------------------------------- run
def load() -> pd.DataFrame:
    p = state_path(HIST)
    h = pd.read_csv(p, dtype={"id": str, "pool": str, "token": str}) if p.exists() else pd.DataFrame(columns=COLS)
    h = h.reindex(columns=COLS)
    for c in ("verdict", "reasons", "warnings", "grade", "plan", "status", "dex", "name", "symbol"):
        h[c] = h[c].astype(object)
    for p in PLANS:
        h[f"{p}_reason"] = h[f"{p}_reason"].astype(object)
    return h


def run() -> dict:
    now = pd.Timestamp.now(tz="UTC")
    h = load()
    known = set(h["id"])
    res = results(h)
    plan = chosen_plan(res)
    t = launch_thresholds()
    fresh, rejected = [], []
    for chain in NETS:
        seen: dict[str, dict] = {}
        for r in new_pools(chain, now):
            if eligible(r, chain) and f"{chain}|{r['pool']}" not in known:
                seen.setdefault(r["pool"], r)
        pools = sorted(seen.values(), key=lambda r: -(r.get("reserve_usd") or 0))[: CHECKS_PER_CHAIN * 2]
        pools = real_liquidity(pools)[:CHECKS_PER_CHAIN]
        if not pools:
            continue
        try:
            facts, _ = facts_for(chain, [r["token"] for r in pools], {r["token"]: [r["pool"]] for r in pools})
        except Exception as e:  # noqa: BLE001
            log.warning("security %s: %s", chain, e)
            continue
        live = G.gt_multi(chain, [r["pool"] for r in pools])            # the price when our checks finished
        try:
            gas = G.gas_usd(chain, next((v["quote_price"] for v in live.values() if v.get("quote_symbol") in ("WETH", "ETH", "WBNB", "BNB", "SOL", "WSOL")), 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            gas = float(CHAINS[chain].get("gas_usd", 0.5))
        for r in pools:
            cur = live.get(r["pool"]) or r
            px = cur.get("price")
            if not px or px != px or px <= 0:
                continue
            m = market({**r, **{k: cur.get(k) for k in ("tx_h24", "vol_h24") if cur.get(k) is not None}})
            f = facts.get(r["token"]) or S.empty_facts()
            a = S.assess(f, m, t, chain)
            fee = (r.get("fee_pct") / 100) if r.get("fee_pct") == r.get("fee_pct") and r.get("fee_pct") else 0.003
            bt, st = f.get("buy_tax") or 0.0, f.get("sell_tax") or 0.0
            cost = dex_cost(AMOUNT, px, m["liq_real"], fee, bt, st, gas, CHAINS[chain]["mev"], SNIPE_COST)["round_trip"]
            warns = launch_warnings(f, m, chain) if a["verdict"] == "pass" else []
            j = judge(res, plan, warns, a["penalty"], cost) if a["verdict"] == "pass" else {"grade": None, "score": None}
            row = {"id": f"{chain}|{r['pool']}", "chain": chain, "pool": r["pool"], "token": r["token"], "symbol": r["base_symbol"],
                   "name": r.get("base_name"), "dex": r["dex"], "created": r["created"], "entry_t": str(now),
                   "delay_min": (now - pd.Timestamp(r["created"])).total_seconds() / 60, "entry": px, "reserve": m["liq_real"],
                   "pool_fee": fee, "buy_tax": bt, "sell_tax": st, "cost_rt": min(cost, 0.99), "verdict": a["verdict"],
                   "reasons": " | ".join(x["text"] for x in a["hard"]), "warnings": " | ".join(warns), "grade": j.get("grade"),
                   "score": j.get("score"), "plan": plan, "status": "open", "last": px, "last_t": str(now), "last_reserve": cur.get("reserve_usd") or r.get("reserve_usd"),
                   "reserve0": cur.get("reserve_usd") or r.get("reserve_usd")}
            h = pd.concat([h, pd.DataFrame([row]).reindex(columns=COLS)], ignore_index=True)
            if a["verdict"] == "pass":
                fresh.append({**row, **j, "checks": [c["text"] for c in a["checks"] if c["ok"]], "warn_list": warns,
                              "url": r.get("ds_url") or f"https://dexscreener.com/{CHAINS[chain]['ds']}/{r['pool']}"})
            else:
                rejected.append({k: row[k] for k in ("chain", "symbol", "dex", "delay_min", "reserve")} | {"reasons": [x["text"] for x in a["hard"]][:3]})
    # follow open positions: live price and pool money; judge the ones whose plans have all run their course
    for chain in NETS:
        op = h[(h["status"] == "open") & (h["chain"] == chain)]
        if op.empty:
            continue
        live = G.gt_multi(chain, op["pool"].tolist())
        for i, r in op.iterrows():
            cur = live.get(r["pool"])
            if cur and cur.get("price") == cur.get("price") and cur.get("price"):
                h.loc[i, ["last", "last_t", "last_reserve"]] = [cur["price"], str(now), cur.get("reserve_usd")]
            if now - pd.Timestamp(r["entry_t"]) >= pd.Timedelta(hours=FINAL_AFTER_H):
                fin = finish({**r.to_dict(), **{k: h.loc[i, k] for k in ("last", "last_reserve")}}, now)
                for k in ["status", "rug"] + [f"{p}_{x}" for p in PLANS for x in ("net", "reason")]:
                    if k in fin:
                        if k in h.columns and not k.endswith("_net") and h[k].dtype != object:
                            h[k] = h[k].astype(object)      # read back from CSV as numbers when still empty: True/text won't fit
                        h.loc[i, k] = fin[k]
                time.sleep(0.2)
    state_path(HIST).write_text(h.to_csv(index=False))
    res = results(h)
    return snapshot(h, res, chosen_plan(res), fresh, rejected, now)


def exit_price(r: dict, plan: str) -> float | None:
    """The price the plan sold at (the stored result is after costs: net = exit / entry x (1 - costs) - 1)."""
    try:
        net, entry, cost = float(r[f"{plan}_net"]), float(r["entry"]), float(r["cost_rt"])
    except (TypeError, ValueError, KeyError):
        return None
    return entry * (net + 1) / (1 - cost) if net == net and entry == entry and cost < 1 else None


def snapshot(h: pd.DataFrame, res: dict, plan: str, fresh: list, rejected: list, now: pd.Timestamp) -> dict:
    op = h[h["status"] == "open"].copy()
    open_rows = []
    for r in op.to_dict("records"):
        if not (r.get("entry") and r.get("last")):
            continue
        r0, r1 = r.get("reserve0"), r.get("last_reserve")
        pool_chg = float(r1) / float(r0) - 1 if r0 and r1 and r0 == r0 and r1 == r1 and float(r0) > 0 else None
        open_rows.append({k: r.get(k) for k in ("chain", "symbol", "dex", "verdict", "grade", "entry", "last", "last_t", "entry_t", "delay_min", "reserve", "cost_rt")}
                         | {"pool_chg": pool_chg}
                         | {"ret": float(r["last"]) / float(r["entry"]) * (1 - float(r["cost_rt"] or 0)) - 1,
                            "url": f"https://dexscreener.com/{CHAINS[r['chain']]['ds']}/{r['pool']}"})
    closed = h[h["status"] == "closed"].tail(40)
    recent = [{"chain": r["chain"], "symbol": r["symbol"], "verdict": r["verdict"], "entry_t": r["entry_t"], "rug": bool(r["rug"]) if r["rug"] == r["rug"] else None,
               "net": r.get(f"{plan}_net"), "reason": r.get(f"{plan}_reason"), "entry": r.get("entry"),
               "exit": exit_price(r, plan)} for r in closed.to_dict("records")][::-1]
    delays = h["delay_min"].astype(float).dropna()
    d = h[(h["status"] == "closed") & (h["verdict"] == "pass") & h["grade"].notna()].copy()
    if len(d):
        d["net_ret"] = d[f"{plan}_net"].astype(float)
        d["sl_pct"] = -PLANS[plan]["sl"]
        d["risk_unit"] = PLANS[plan]["sl"]
        d["baseline_ret"] = np.nan
    qual = quality(d.dropna(subset=["net_ret"])) if len(d) else quality(pd.DataFrame())
    for x in fresh:
        x["rank"] = 0
    fresh.sort(key=lambda x: -(x.get("score") or 0))
    for i, x in enumerate(fresh):
        x["rank"] = i + 1
    counts = {"checked": int(len(h)), "passed": int((h["verdict"] == "pass").sum()), "rejected": int((h["verdict"] == "reject").sum()),
              "open": int(len(op)), "closed": int((h["status"] == "closed").sum())}
    snap = {"generated_at": str(now), "plans": PLANS, "plan": plan, "results": res, "ideas": fresh, "rejected": rejected[:30],
            "open": sorted(open_rows, key=lambda r: r["entry_t"], reverse=True)[:60], "recent": recent, "counts": counts,
            "speed": {"median_delay_min": float(delays.median()) if len(delays) else None, "p90_delay_min": float(delays.quantile(0.9)) if len(delays) else None},
            "costs": {"snipe": SNIPE_COST, "min_liq": MIN_LIQ, "median_cost_rt": float(h["cost_rt"].astype(float).median()) if len(h) else None},
            "quality": qual}
    out = REPO_ROOT / ".cache" / "sniper_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = run()
    log.info("sniper: %s, %d passed now, %d rejected now", s["counts"], len(s["ideas"]), len(s["rejected"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
