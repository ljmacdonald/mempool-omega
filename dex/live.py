"""Hourly DEX scan (GitHub Actions): find candidate tokens, check them for scams, rank them, write the snapshot
the website uses (state/dex/snapshot.json) and keep the track record.

The website re-checks the top candidates live (prices, liquidity, security) and recomputes the costs for
the visitor's own amount, so a rug pull between hourly runs is still caught.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict

import numpy as np
import pandas as pd

from core.config import state_path
from core.log import get_logger
from dex import security as S
from dex.chains import CHAINS, NATIVE_WRAPPED, NOT_IDEAS, PROTECTED, STABLES, norm
from dex.costs import dex_cost
from dex.data import ds_boosted, ds_pairs, gas_usd, gt_pools
from dex.fetch import facts_for
from scanner.defence import adaptive_penalty, declutter_exits
from scanner.features import coin_features
from scanner.model import PT, SL, ScannerModel, risk_unit
from scanner.rank import grade, reasons, score_from_r
from scanner.styles import DEX_STYLES, Style

log = get_logger("dex.live")
REF_AMOUNT = 100.0          # costs used for the server's own ranking and the track record
MAX_CANDIDATES = 60
DEX_FEE = {"uniswap_v2": 0.003, "pancakeswap_v2": 0.0025, "sushiswap": 0.003, "raydium": 0.0025,
           "pumpswap": 0.0025, "meteora": 0.005, "orca": 0.003}


def dex_state(*parts: str):
    return state_path("dex", *parts)


def _load(name: str, default):
    try:
        return json.loads(dex_state(name).read_text())
    except (OSError, ValueError):
        return default


def clean(x):
    """JSON for browsers: NaN/inf become null (JSON has no NaN), numpy numbers become plain numbers."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (float, np.floating)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def _save(name: str, obj) -> None:
    dex_state(name).write_text(json.dumps(clean(obj), indent=1, default=str, allow_nan=False))


def pool_fee(p: dict) -> float:
    if p.get("fee_pct") == p.get("fee_pct") and p.get("fee_pct") is not None:     # not NaN
        return float(p["fee_pct"]) / 100
    name = p.get("name") or ""
    if "%" in name:
        try:
            return float(name.rsplit(" ", 1)[-1].rstrip("%")) / 100
        except ValueError:
            pass
    return DEX_FEE.get(p.get("dex", ""), 0.003)


# --------------------------------------------------------------------------------------- discovery
def discover() -> tuple[list[dict], list[dict], dict]:
    """Candidate tokens (deepest acceptable pool each) + early rejections + reference (native) pools."""
    cands, rejected, refs = [], [], {}
    now = pd.Timestamp.now(tz="UTC")
    boosted = ds_boosted()
    for chain, c in CHAINS.items():
        seen: dict[str, dict] = {}
        for sort, pages in (("h24_volume_usd_desc", 4), ("h24_tx_count_desc", 1)):
            for p in gt_pools(chain, sort, pages):
                seen[p["pool"]] = p
        for dex in c.get("dexes", []):        # established exchanges: lists not flooded by brand-new launches
            for p in gt_pools(chain, "h24_volume_usd_desc", 1, dex):
                seen[p["pool"]] = p
        pools = list(seen.values())
        quotes = c["quotes"]
        # reference pool: wrapped native / stablecoin with the most money in it
        natives = [p for p in pools if (p["base_symbol"] or "").upper() in NATIVE_WRAPPED and p["token"] in quotes
                   and (p["quote_symbol"] or "").upper() in STABLES]
        if natives:
            refs[chain] = max(natives, key=lambda p: p["reserve_usd"] or 0)
        by_token: dict[str, list[dict]] = defaultdict(list)
        for p in pools:
            sym = (p["base_symbol"] or "").upper()
            if p["quote"] not in quotes or p["token"] in quotes:
                continue
            if sym in PROTECTED:
                rejected.append({**_brief(p), "reasons": ["Uses the name of a major coin or stablecoin but isn't "
                                                          "the real one."]})
                continue
            if sym in NOT_IDEAS or "xstock" in (p["base_name"] or "").lower():   # stables, wrapped majors, stocks
                continue
            by_token[p["token"]].append(p)
        # copycats: same symbol or name as a token with more money behind it, on the same network
        total = {t: sum(p["reserve_usd"] or 0 for p in ps) for t, ps in by_token.items()}
        best_by_name: dict[str, tuple[float, str]] = {}
        for t, ps in by_token.items():
            for key in {(ps[0]["base_symbol"] or "").upper(), (ps[0]["base_name"] or "").lower()} - {""}:
                if total[t] > best_by_name.get(key, (-1, ""))[0]:
                    best_by_name[key] = (total[t], t)
        for t, ps in by_token.items():
            ps.sort(key=lambda p: -(p["reserve_usd"] or 0))
            main = ps[0]
            keys = {(main["base_symbol"] or "").upper(), (main["base_name"] or "").lower()} - {""}
            copy = any(best_by_name[k][1] != t for k in keys)
            created = [pd.Timestamp(p["created"]) for p in ps if p.get("created")]
            age = (now - min(created)).total_seconds() / 86400 if created else 0.0
            tx = main["tx_h24"]
            cand = {**main, "first_created": str(min(created)) if created else None,
                    "pools": [{"pool": p["pool"], "dex": p["dex"], "reserve_usd": p["reserve_usd"], "fee": pool_fee(p),
                               "quote_symbol": p["quote_symbol"], "tx_h24": p["tx_h24"], "vol_h24": p["vol_h24"]}
                              for p in ps[:4]],
                    "age_days": age, "copycat": copy, "boosted": (c["ds"], t) in boosted,
                    "pool_fee": pool_fee(main)}
            _sum_pools(cand)
            tx = cand["tx_h24"]
            why = []
            if (main["reserve_usd"] or 0) < 0.8 * S.BASE["min_liq"]:
                why.append(f"Only ${main['reserve_usd'] or 0:,.0f} in its biggest pool.")
            if age < S.BASE["min_age_days"]:
                why.append(f"Only {age:.0f} days old.")
            if tx.get("buyers", 0) < S.BASE["min_buyers"] / 2:
                why.append(f"Only {tx.get('buyers', 0)} buyers in 24 hours.")
            if why:
                if (main["reserve_usd"] or 0) >= 100_000:
                    rejected.append({**_brief(cand), "reasons": why})
                continue
            cands.append(cand)
    cands.sort(key=lambda p: -(p["reserve_usd"] or 0))
    return cands[:MAX_CANDIDATES], rejected, refs


UNIVERSE_HOURS = 6       # the candidate list changes slowly: rediscover every 6 h, refresh stats hourly


def universe() -> tuple[list[dict], list[dict], dict]:
    """Candidates, early rejections and reference pools: rediscovered every 6 hours, otherwise the saved list
    with fresh numbers (GeckoTerminal batch requests: 30 pools each)."""
    from dex.data import gt_multi

    u = _load("universe.json", {})
    age_h = (time.time() - u.get("at", 0)) / 3600
    if age_h >= UNIVERSE_HOURS or not u.get("cands"):
        cands, rejected, refs = discover()
        _save("universe.json", {"at": time.time(), "cands": cands, "rejected": rejected, "refs": refs})
        return cands, rejected, refs
    cands, rejected, refs = u["cands"], u["rejected"], u["refs"]
    now = pd.Timestamp.now(tz="UTC")
    by_chain = defaultdict(list)
    for c in cands:
        by_chain[c["chain"]].extend(p["pool"] for p in c.get("pools", [{"pool": c["pool"]}]))
    for chain, r in refs.items():
        by_chain[chain].append(r["pool"])
    fresh: dict[str, dict] = {}
    for chain, pools in by_chain.items():
        fresh.update({f"{chain}:{k}": v for k, v in gt_multi(chain, list(dict.fromkeys(pools))).items()})
    for c in cands + list(refs.values()):
        f = fresh.get(f"{c['chain']}:{c['pool']}")
        if f:
            for k in ("price", "quote_price", "reserve_usd", "tx_h24", "tx_h1", "vol_h24"):
                c[k] = f[k]
        for p in c.get("pools", []):
            f = fresh.get(f"{c['chain']}:{p['pool']}")
            if f:
                p.update(reserve_usd=f["reserve_usd"], tx_h24=f["tx_h24"], vol_h24=f["vol_h24"])
        if c.get("pools"):
            _sum_pools(c)
        if c.get("first_created"):
            c["age_days"] = (now - pd.Timestamp(c["first_created"])).total_seconds() / 86400
    boosted = ds_boosted()
    for c in cands:
        c["boosted"] = (CHAINS[c["chain"]]["ds"], c["token"]) in boosted
    return cands, rejected, refs


def _sum_pools(c: dict) -> None:
    """Traders and volume across all of a token's real pools (popular tokens trade in several). Distinct-wallet
    counts can double-count a wallet that used two pools; a scammer can't cheaply fake several real pools."""
    tx: dict[str, int] = defaultdict(int)
    for p in c["pools"]:
        for k, v in (p.get("tx_h24") or {}).items():
            tx[k] += int(v or 0)
    c["tx_h24"] = dict(tx)
    c["vol_h24"] = float(sum(p.get("vol_h24") or 0 for p in c["pools"]))


def _brief(p: dict) -> dict:
    return {"chain": p["chain"], "symbol": p["base_symbol"], "token": p["token"], "pool": p["pool"],
            "dex": p["dex"], "reserve_usd": p["reserve_usd"]}


def enrich_liquidity(cands: list[dict]) -> None:
    """Real liquidity from DexScreener: only the SOL/ETH/BNB/stablecoin side counts (x2)."""
    by_chain = defaultdict(list)
    for c in cands:
        by_chain[c["chain"]].append(c)
    for chain, cs in by_chain.items():
        pairs = ds_pairs(chain, [c["token"] for c in cs])
        for c in cs:
            match = [p for p in pairs.get(c["token"], []) if norm(chain, p.get("pairAddress")) == c["pool"]]
            liq = (match[0].get("liquidity") or {}) if match else {}
            qp = c["quote_price"] if c["quote_price"] == c["quote_price"] else np.nan
            c["liq_real"] = float(2 * liq["quote"] * qp) if liq.get("quote") and qp == qp else float(c["reserve_usd"] or 0) * 0.5
            c["ds_url"] = match[0].get("url") if match else f"https://dexscreener.com/{CHAINS[chain]['ds']}/{c['pool']}"
            if match and ((match[0].get("boosts") or {}).get("active") or 0) > 0:
                c["boosted"] = True


def liquidity_history(cands: list[dict]) -> None:
    """Keep 8 days of hourly real liquidity per pool; add 6 h / 24 h / 72 h changes to each candidate."""
    hist = _load("liquidity.json", {})
    hour = int(time.time() // 3600)
    for c in cands:
        k = f"{c['chain']}:{c['pool']}"
        rows = [r for r in hist.get(k, []) if r[0] > hour - 8 * 24 and r[0] != hour] + [[hour, round(c["liq_real"])]]
        hist[k] = rows
        past = {r[0]: r[1] for r in rows}
        c["liq_change_6h"], c["liq_change_24h"], c["liq_change_72h"] = (
            _change(past, hour - h, c["liq_real"]) for h in (6, 24, 72))
    hist = {k: v for k, v in hist.items() if v and v[-1][0] > hour - 8 * 24}
    _save("liquidity.json", hist)


def _change(past: dict, at: int, now_value: float) -> float | None:
    for d in (0, -1, 1):
        v = past.get(at + d)
        if v:
            return now_value / v - 1
    return None


def rules_changed(chain: str, token: str, fp: str, store: dict) -> bool:
    k = f"{chain}:{token}"
    rows = store.setdefault(k, [])
    now = int(time.time())
    if not rows or rows[-1]["fp"] != fp:
        rows.append({"fp": fp, "since": now})
        store[k] = rows[-5:]
    return len(rows) > 1 and now - rows[-1]["since"] < 72 * 3600


# --------------------------------------------------------------------------------------- ranking
def red_flags(f: pd.Series, style: Style) -> tuple[float, list[str]]:
    pen, warn = 0.0, []
    if f["ret_24b"] > np.log(1.30) or f["ret_1b"] > np.log(1.10):
        pen += 0.15
        warn.append(f"Already up {np.expm1(f['ret_24b']):.0%} in the last {style.bars_text(24)}. Pumps on DEX tokens "
                    "are often followed by a dump onto late buyers.")
    if f["volume_surge"] > 4 and abs(f["ret_24b"]) < 0.02:
        pen += 0.10
        warn.append("Trading activity is unusually high but the price isn't moving. That can be fake (wash) trading.")
    if f["rsi_14"] > 80:
        pen += 0.05
        warn.append("Looks 'overheated' (RSI above 80). Sharp pullbacks are common after this.")
    if f["btc_ret_24b"] < np.log(0.95):
        warn.append(f"The network's main coin fell more than 5% in the last {style.bars_text(24)}.")
    return pen, warn


def expected_r(p: float, win_r: float, loss_r: float, cost_rt: float, ru: float) -> float:
    """Per $1 risked, after ALL DEX costs at the reference amount, assuming the market goes nowhere."""
    return p * win_r - (1 - p) * loss_r - cost_rt / (SL * ru)


def score_candidates(cands: list[dict], candles: dict, refs: dict, models: dict, chains: dict,
                     adaptive: dict) -> None:
    """Adds per-style model results to each candidate (in place)."""
    for c in cands:
        df = candles.get(f"{c['chain']}:{c['pool']}")
        c["styles"] = {}
        if df is None or len(df) < 200:
            continue
        ref = candles.get(f"ref:{c['chain']}")
        f = coin_features(df, ref).iloc[-1]
        ch = chains[c["chain"]]
        close = float(df["close"].iloc[-1])
        for key, st in DEX_STYLES.items():
            m = models.get(key)
            if m is None:
                continue
            p = float(m.predict(f.to_frame().T)[0])
            ru = float(risk_unit(df, st).iloc[-1])
            pen, warn = red_flags(f, st)
            apen, awarn = adaptive_penalty(f, adaptive)
            stop, target = declutter_exits(close, close * (1 - SL * ru), close * (1 + PT * ru),
                                           float(df["low"].iloc[-24:].min()), ru, SL)
            cost = dex_cost(REF_AMOUNT, close, c["liq_real"], c["pool_fee"], c["facts"]["buy_tax"] or 0.0,
                            c["facts"]["sell_tax"] or 0.0, ch["gas_usd"], ch["mev"], ch["sniper"])
            c["styles"][key] = {
                "p": round(p, 5), "risk_unit": round(ru, 5), "flag_penalty": pen + apen, "warnings": warn + awarn,
                "why": reasons(f, st), "take_profit_pct": target / close - 1, "safety_exit_pct": stop / close - 1,
                "pre_ret": float(f["ret_24b"]) if np.isfinite(f["ret_24b"]) else None,
                "vol_surge": float(f["volume_surge"]) if np.isfinite(f["volume_surge"]) else None,
                "cost_rt_ref": cost["round_trip"], "win_r": m.win_r, "loss_r": m.loss_r}
        c["close"] = close
        c["candle_time"] = str(df.index[-1])
        c["active_days"] = active_days(df)


def active_days(df: pd.DataFrame, min_usd: float = 50_000.0) -> int:
    d = df["qv"].resample("1D").sum().iloc[-15:-1]
    return int((d >= min_usd).sum())


def ideas_for(cands: list[dict], style_key: str, top_n: int = 5) -> list[dict]:
    st = DEX_STYLES[style_key]
    rows = []
    for c in cands:
        s = c.get("styles", {}).get(style_key)
        if not s or c["assess"]["verdict"] != "pass":
            continue
        r = expected_r(s["p"], s["win_r"], s["loss_r"], s["cost_rt_ref"], s["risk_unit"]) - s["flag_penalty"] \
            - c["assess"]["penalty"]
        sc = score_from_r(r)
        rows.append({"symbol": f"{c['chain']}:{c['token']}", "coin": c["base_symbol"], "chain": c["chain"],
                     "pool": c["pool"], "style": style_key, "score": round(sc, 1), "grade": grade(sc),
                     "chance_beats_market": s["p"], "expected_r": round(r, 3), "risk_unit": s["risk_unit"],
                     "price_now": c["close"], "take_profit_pct": s["take_profit_pct"],
                     "safety_exit_pct": s["safety_exit_pct"], "cost_rt": s["cost_rt_ref"],
                     "pre_ret": s["pre_ret"], "vol_surge": s["vol_surge"], "ts": c["candle_time"],
                     "integrity": {"penalty": c["assess"]["penalty"], "checks": c["assess"]["checks"]},
                     "hold_minutes": st.horizon_minutes})
    rows.sort(key=lambda d: -d["score"])
    for i, d in enumerate(rows[:top_n], 1):
        d["rank"] = i
    return rows[:top_n]


# --------------------------------------------------------------------------------------- the hourly run
def chain_params(refs: dict, adaptive: dict) -> dict:
    out = {}
    learned = adaptive.get("sniper") or {}
    for chain, c in CHAINS.items():
        native = float(refs[chain]["price"]) if chain in refs else np.nan
        out[chain] = {"name": c["name"], "mev": c["mev"], "sniper": float(learned.get(chain, c["sniper"])),
                      "gas_usd": round(gas_usd(chain, native), 4) if native == native or "gas_usd" in c else 1.0,
                      "native_price": native, "protect": c["protect"], "explorer": c["explorer"], "ds": c["ds"],
                      "gt": c["gt"], "goplus": c["goplus"], "honeypot": c["honeypot"], "rugcheck": c["rugcheck"],
                      "quotes": c["quotes"]}
    return out


def load_models() -> dict:
    from scanner.live import models_dir

    return {k: ScannerModel.load(models_dir(), k) for k in DEX_STYLES if ScannerModel.exists(models_dir(), k)}


def hourly() -> dict:
    from dex import cache
    from dex.improve import load_adaptive
    from scanner import track

    seed = S.run_seed()
    t = S.thresholds(seed)
    adaptive = load_adaptive()
    cands, rejected, refs = universe()
    log.info("%d candidates, %d early rejections", len(cands), len(rejected))
    enrich_liquidity(cands)
    liquidity_history(cands)
    chains = chain_params(refs, adaptive)
    # security first (other services): price history is only downloaded for tokens that could pass
    fps = _load("fingerprints.json", {})
    by_chain = defaultdict(list)
    for c in cands:
        by_chain[c["chain"]].append(c)
    for chain, cs in by_chain.items():
        facts, _ = facts_for(chain, [c["token"] for c in cs], {c["token"]: [p["pool"] for p in c["pools"]] for c in cs})
        for c in cs:
            c["facts"] = facts[c["token"]]
            c["rules_changed"] = rules_changed(chain, c["token"], S.fingerprint(c["facts"]), fps) \
                if c["facts"]["goplus"] else False
    _save("fingerprints.json", fps)
    for c in cands:
        c["active_days"] = None
        c["assess"] = S.assess(c["facts"], market_facts(c), t, c["chain"])
    pre = [c for c in cands if c["assess"]["verdict"] == "pass"]
    log.info("%d of %d candidates pass the pre-check; downloading their price history", len(pre), len(cands))
    candles: dict[str, pd.DataFrame] = {}
    deadline = time.time() + 60 * float(os.environ.get("OMEGA_DEX_CANDLE_MINUTES", "15"))
    for chain, r in refs.items():
        try:
            candles[f"ref:{chain}"] = cache.candles(chain, r["pool"], 400)
        except Exception as e:  # noqa: BLE001
            log.warning("reference candles %s: %s", chain, e)
    open_pools = _open_pools()
    for c in pre + [c for c in cands if c not in pre and f"{c['chain']}:{c['token']}" in open_pools]:
        if time.time() > deadline:
            log.warning("candle time budget used up")
            break
        try:
            candles[f"{c['chain']}:{c['pool']}"] = cache.candles(c["chain"], c["pool"], 400)   # >= 15 days
        except Exception as e:  # noqa: BLE001
            log.warning("candles %s %s: %s", c["chain"], c["base_symbol"], e)
    models = load_models()
    score_candidates(cands, candles, refs, models, chains, adaptive)
    for c in cands:
        c["assess"] = S.assess(c["facts"], market_facts(c), t, c["chain"])
    payload = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "chains": chains,
               "models": {k: {"win_r": m.win_r, "loss_r": m.loss_r, "base_rate": m.base_rate, "info": m.info}
                          for k, m in models.items()},
               "styles": {k: {"label": s.label, "hold_minutes": s.horizon_minutes, "hold_text": s.hold_text,
                              "bar_minutes": s.bar_minutes} for k, s in DEX_STYLES.items()},
               "thresholds_published": S.BASE, "ref_amount": REF_AMOUNT, "adaptive": adaptive,
               "tokens": [snapshot_row(c) for c in cands if c.get("styles")],
               "rejected": [{**_brief(c), "reasons": [h["text"] for h in c["assess"]["hard"]]}
                            for c in cands if c["assess"]["verdict"] == "reject"] + rejected[:40]}
    dex_state("snapshot.json").write_text(json.dumps(clean(payload), separators=(",", ":"), default=str, allow_nan=False))
    # track record (server ranking at $100, all networks)
    hist = "dex/history.csv"
    ideas = {}
    for key in DEX_STYLES:
        ideas[key] = ideas_for(cands, key)
        track.append(ideas[key], len(cands), hist)
    settle = {f"{c['chain']}:{c['token']}": candles.get(f"{c['chain']}:{c['pool']}") for c in cands}
    settle = {k: v for k, v in settle.items() if v is not None and len(v)}
    _fill_open_candles(settle, hist)
    for key in DEX_STYLES:
        track.resolve(settle, key, hist)
    first_hour_runup(settle, hist)
    board = track.scoreboard(hist, "dex/scoreboard.json", DEX_STYLES)
    log.info("dex hourly: %d candidates, %d passed, ideas %s", len(cands),
             sum(c["assess"]["verdict"] == "pass" for c in cands), {k: [d["coin"] for d in v] for k, v in ideas.items()})
    return {"ideas": ideas, "scoreboard": board, "candidates": len(cands)}


def _open_pools() -> set[str]:
    from scanner import track

    h = track.load_history("dex/history.csv")
    return set(h.loc[h["status"] == "open", "symbol"]) if len(h) else set()


def market_facts(c: dict) -> dict:
    tx = c.get("tx_h24") or {}
    return {"liq_real": c.get("liq_real", 0.0), "age_days": c.get("age_days", 0.0), "active_days": c.get("active_days"),
            "buyers_h24": tx.get("buyers", 0), "sellers_h24": tx.get("sellers", 0), "buys_h24": tx.get("buys", 0),
            "sells_h24": tx.get("sells", 0), "vol_h24": c.get("vol_h24") or 0.0,
            "liq_change_6h": c.get("liq_change_6h"), "liq_change_24h": c.get("liq_change_24h"),
            "liq_change_72h": c.get("liq_change_72h"), "boosted": bool(c.get("boosted")),
            "copycat": bool(c.get("copycat")), "impersonator": False, "rules_changed": bool(c.get("rules_changed"))}


def snapshot_row(c: dict) -> dict:
    keep = ("chain", "dex", "pool", "token", "base_symbol", "base_name", "quote_symbol", "quote_price", "price",
            "close", "liq_real", "reserve_usd", "pool_fee", "age_days", "active_days", "tx_h24", "tx_h1", "vol_h24",
            "liq_change_6h", "liq_change_24h", "liq_change_72h", "boosted", "copycat", "rules_changed", "ds_url",
            "pools", "candle_time", "facts", "styles")
    row = {k: c.get(k) for k in keep}
    row["assess"] = {k: c["assess"][k] for k in ("verdict", "hard", "penalty")}   # the browser re-runs the checks
    return row


def _fill_open_candles(settle: dict, hist: str) -> None:
    """Open ideas whose token left the candidate list still need prices to be settled."""
    from dex import cache
    from scanner import track

    h = track.load_history(hist)
    if h.empty:
        return
    open_ = h[(h["status"] == "open") & ~h["symbol"].isin(list(settle))]
    for (sym, pool), _ in list(open_.groupby(["symbol", "pool"]))[:25]:
        chain = sym.split(":", 1)[0]
        try:
            settle[sym] = cache.candles(chain, pool, 300)
        except Exception as e:  # noqa: BLE001
            log.warning("settle candles %s: %s", sym, e)


def first_hour_runup(settle: dict, hist: str) -> None:
    """How far each idea rose in the hour after it was published, vs the other candidates at the same time.
    Front-runners buying our list push this up; dex/improve.py turns the excess into the 'sniper' cost."""
    from scanner import track

    h = track.load_history(hist)
    if h.empty:
        return
    if "first_hour_runup" not in h:
        h["first_hour_runup"] = np.nan
        h["first_hour_runup_base"] = np.nan
    todo = h[h["first_hour_runup"].isna()]
    base_cache: dict[str, float] = {}

    def runup(df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
        if ts not in df.index:
            return None
        i = df.index.get_loc(ts)
        if i + 1 >= len(df):
            return None
        return float(df["high"].iloc[i + 1] / df["close"].iloc[i] - 1)

    for i, row in todo.iterrows():
        df = settle.get(row["symbol"])
        ts = pd.Timestamp(row["ts"])
        r = runup(df, ts) if df is not None else None
        if r is None:
            continue
        if row["ts"] not in base_cache:
            vals = [x for x in (runup(d, ts) for d in settle.values()) if x is not None]
            base_cache[row["ts"]] = float(np.median(vals)) if vals else np.nan
        h.loc[i, ["first_hour_runup", "first_hour_runup_base"]] = [r, base_cache[row["ts"]]]
    h.to_csv(state_path(hist), index=False)
