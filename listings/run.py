"""New exchange listings, live: what listed recently on Binance, OKX and Gate.io, what is about to list, and graded
ideas from the listing rules that held up in listings/research.py, judged like every other page.

    python -m listings.run research   # nightly: re-test the rules on every listing since 2023
    python -m listings.run scan       # every 30 minutes: listings now, ideas, track record -> .cache/listings_out/snapshot.json

Rules that failed the test never produce trade ideas; when one of them fires on a fresh listing (for example
"buy in the first hour"), the listing shows how that rule really did instead. PAPER ONLY: nothing is traded.
"""
from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from core.config import REPO_ROOT, state_path
from lab.run import clean
from listings import data as D
from listings import research as RS
from scanner.quality import adjust_r, quality
from scanner.rank import grade as grade_of
from scanner.rank import score_from_r

log = logging.getLogger("omega.listings")
RECENT_DAYS = 130            # listings this young are tracked (ideas need up to 90 days + 30 days to finish)
SHOW_DAYS = 45               # listed this recently: shown in the table
UPCOMING_DAYS = 14
MIN_GATE_VOL = 500_000       # Gate.io lists a lot: only pairs trading at least this much a day ($)
THIN_VOL = 2_000_000
K_HIT, K_AVG = 20, 30
LEVEL_PEN = {"good": 0.0, "warn": 0.10, "bad": 0.30}
EVIDENCE = {"good": "Held up in tests", "warn": "Not proven in tests", "bad": "Failed in tests"}
FRESH_H = {"first_hour": 3, "day_one": 6}       # an entry counts as new this many hours after it happened (daily rules: 26 h)
HIST = "listings/ideas.csv"
HIST_COLS = ["id", "rule", "exchange", "sym", "base", "side", "listed", "t_in", "entry", "days", "seen_at", "grade", "score", "prob",
             "risk_unit", "status", "net", "base_ret"]
MIN_PROVEN = 30


# ---------------------------------------------------------------------------------------------- listings now
def recent_listings(now: pd.Timestamp) -> tuple[list[dict], list[dict]]:
    """(listed in the last RECENT_DAYS days, scheduled to list in the next UPCOMING_DAYS days)."""
    now_ms = int(now.value // 10**6)
    rec, up = [], []
    try:
        syms = [s for s in D.binance_symbols() if s["status"] == "TRADING"]
        first = D.binance_first_days([s["symbol"] for s in syms])
        stocks = D.tokenized_stock_batches(first, {s["symbol"]: s["base"] for s in syms})
        for s in syms:
            f = first.get(s["symbol"])
            if f and now_ms - f <= RECENT_DAYS * D.DAY_MS and s["symbol"] not in stocks:
                rec.append({"exchange": "binance", "symbol": s["symbol"], "base": s["base"], "list_ms": f, "delisted": False})
    except Exception as e:  # noqa: BLE001
        log.warning("binance listings: %s", e)
    try:
        okx = D.okx_pairs()
        okx_stocks = D.tokenized_stock_batches({s["symbol"]: s["list_ms"] for s in okx}, {s["symbol"]: s["base"] for s in okx}, "okx")
        for s in okx:
            if s["symbol"] in okx_stocks:
                continue
            if s["list_ms"] > now_ms and s["list_ms"] - now_ms <= UPCOMING_DAYS * D.DAY_MS:
                up.append({"exchange": "okx", "symbol": s["symbol"], "base": s["base"], "list_ms": s["list_ms"]})
            elif s["state"] == "live" and 0 <= now_ms - s["list_ms"] <= RECENT_DAYS * D.DAY_MS:
                rec.append({"exchange": "okx", "symbol": s["symbol"], "base": s["base"], "list_ms": s["list_ms"], "delisted": False})
    except Exception as e:  # noqa: BLE001
        log.warning("okx listings: %s", e)
    try:
        vol = {t["currency_pair"]: float(t.get("quote_volume") or 0) for t in D._get(f"{D.GATE}/spot/tickers")}
        for s in D.gate_pairs():
            if s["list_ms"] > now_ms and s["list_ms"] - now_ms <= UPCOMING_DAYS * D.DAY_MS:
                up.append({"exchange": "gate", "symbol": s["symbol"], "base": s["base"], "list_ms": s["list_ms"]})
            elif s["state"] == "tradable" and 0 <= now_ms - s["list_ms"] <= SHOW_DAYS * D.DAY_MS and vol.get(s["symbol"], 0) >= MIN_GATE_VOL:
                rec.append({"exchange": "gate", "symbol": s["symbol"], "base": s["base"], "list_ms": s["list_ms"], "delisted": False})
    except Exception as e:  # noqa: BLE001
        log.warning("gate listings: %s", e)
    return rec, sorted(up, key=lambda x: x["list_ms"])


def phase(age_d: float, from_high: float, above_week1: bool) -> str:
    if age_d < 1:
        return "First day: the wildest swings"
    if age_d < 7:
        return "First week"
    if from_high <= -0.5:
        return "Down more than half from its high"
    if above_week1:
        return "Above its first-week high"
    return "Settling"


def describe(ev: dict, d: pd.DataFrame, h: pd.DataFrame | None, now: pd.Timestamp) -> dict:
    age_d = (now.value // 10**6 - ev["list_ms"]) / D.DAY_MS
    first_px = float(h["open"].iloc[0]) if h is not None and not h.empty else float(d["open"].iloc[0])
    last = float((h if h is not None and not h.empty and age_d < 2 else d)["close"].iloc[-1])
    hi = float(d["high"].max())
    wk1 = float(d["high"].iloc[:7].max())
    vol = float(d["qv"].iloc[-2] if len(d) >= 2 else d["qv"].iloc[-1])        # the last full day (today has barely started)
    return {**{k: ev[k] for k in ("exchange", "symbol", "base", "list_ms")}, "age_d": age_d, "first": first_px, "last": last,
            "asof": int(now.value // 10**6), "chg": last / first_px - 1, "high": hi, "from_high": last / hi - 1, "vol24": vol,
            "phase": phase(age_d, last / hi - 1, age_d >= 8 and last > wk1)}


# ---------------------------------------------------------------------------------------------- ideas
def fresh_entries(ev: dict, d: pd.DataFrame, h: pd.DataFrame | None, now: pd.Timestamp) -> list[tuple[str, int, float, pd.Timestamp]]:
    """Rules whose entry happened just now on this listing: (rule, daily index, entry price, entry time)."""
    out = []
    for rule in RS.RULES:
        dd = d if rule.removeprefix("short_") in ("first_hour",) else d.iloc[:-1]          # daily rules: finished days only
        e = RS.entry(rule, dd, h)
        if not e:
            continue
        k, px, t = e
        age_h = (now - t).total_seconds() / 3600
        if 0 <= age_h <= FRESH_H.get(rule.removeprefix("short_"), 26):
            out.append((rule, k, px, t))
    return out


def held(r: dict) -> dict:
    """The results at the rule's chosen holding time (JSON keys are strings)."""
    return r["holds"].get(str(r["chosen"])) or r["holds"][r["chosen"]]


def judge(rule: str, research: dict, ev: dict, row: dict, px: float, vol: dict | None, probation: dict | None) -> dict:
    r = research["rules"][rule]
    m = held(r)["all"]
    side = RS.SIDE[rule]
    n = int(m.get("n") or 0)
    prob = ((m.get("hit") or 0) * n + 0.5 * K_HIT) / (n + K_HIT)
    exp_ret = (m.get("avg") or 0.0) * n / (n + K_AVG)
    ru = abs(m.get("avg_loss") or 0.0) or RS.STOP
    exp_r = exp_ret / ru
    warnings, pen = [], LEVEL_PEN[r["level"]]
    moved = side * (row["last"] / px - 1)
    if (m.get("avg_win") or 0) > 0 and moved > 0.5 * m["avg_win"]:
        pen += 0.10
        warnings.append(f"The price already moved {moved:+.1%} in this trade's favour since the rule's entry: much of the usual gain may be gone.")
    if row["vol24"] < THIN_VOL:
        pen += 0.10
        warnings.append(f"Thin trading (about ${row['vol24'] / 1e6:.1f}M in the last day): expect large slippage, and prices can be pushed around.")
    if side < 0:
        warnings.append("Selling short needs a futures account (not available to US residents on Binance or OKX) and pays a "
                        f"funding fee (tested at {research.get('funding', 0.001):.1%} a day; it can be much higher on new listings). "
                        "New listings can also spike violently: the safety exit is 30% above the entry. If you already hold the coin "
                        "(e.g. from an airdrop), simply selling is the low-risk version of this idea.")
    if probation and probation.get("active"):
        pen += probation.get("penalty", 0.1)
        warnings.append("On probation: this rule's live ideas have done worse than holding big coins.")
    adj = adjust_r(exp_r, ru, vol) - pen
    sc = score_from_r(adj)
    holder = held(r).get("holder")
    return {"prob": prob, "exp_ret": exp_ret, "exp_r_adj": adj, "score": sc, "grade": grade_of(sc), "evidence": EVIDENCE[r["level"]],
            "level": r["level"], "risk_unit": ru, "warnings": warnings, "hist_n": n, "avg_win": m.get("avg_win"), "avg_loss": m.get("avg_loss"),
            "days": r["chosen"], "holder_avg": (holder or {}).get("avg"), "moved": moved}


# ---------------------------------------------------------------------------------------------- track record
def track(found: list[dict], results: dict, now: pd.Timestamp) -> pd.DataFrame:
    p = state_path(HIST)
    h = pd.read_csv(p, dtype={"id": str}) if p.exists() else pd.DataFrame(columns=HIST_COLS)
    h = h.reindex(columns=HIST_COLS)
    for c in ("grade", "status"):
        h[c] = h[c].astype(object)
    known = set(h["id"])
    new = [x for x in found if x["id"] not in known]
    if new:
        h = pd.concat([h, pd.DataFrame([{**{c: x.get(c) for c in HIST_COLS}, "status": "open", "seen_at": str(now)} for x in new])], ignore_index=True)
    for i, row in h[h["status"] == "open"].iterrows():
        res = results.get(row["id"])
        if res is not None:
            h.loc[i, ["status", "net", "base_ret"]] = ["closed", res[0], res[1]]
    h.to_csv(p, index=False)
    return h


def resolve(h: pd.DataFrame, data: dict, basket: pd.DataFrame) -> dict:
    """Finished ideas: (net after costs, holding big coins over the same days) by id."""
    out = {}
    for row in h[h["status"] == "open"].to_dict("records"):
        d = data.get((row["exchange"], row["sym"]))
        if d is None:
            continue
        t_in = pd.Timestamp(int(row["t_in"]), unit="ms", tz="UTC")
        k = int(((t_in - pd.Timedelta(seconds=1)).normalize() - d.index[0]).days)
        k = max(0, min(k, len(d) - 1))
        x = RS.hold(d.iloc[:-1], k, float(row["entry"]), int(row["days"]), False, int(row["side"]))
        if not x:
            continue
        j, px, _ = x
        side = int(row["side"])
        t_out = d.index[j] + pd.Timedelta(days=1)
        fund = RS.FUNDING * (t_out - t_in).total_seconds() / 86400 if side < 0 else 0.0
        out[row["id"]] = (side * (px / float(row["entry"]) - 1) - RS.COST - fund, side * RS.basket_return(basket, t_in, t_out) - RS.COST)
    return out


def self_check(h: pd.DataFrame, names: dict) -> tuple[dict, dict]:
    h = h.reindex(columns=HIST_COLS)
    d = h[(h["status"] == "closed") & h["net"].notna() & h["grade"].notna()].copy()
    d["net_ret"] = d["net"].astype(float)
    d["sl_pct"] = -RS.STOP
    d["risk_unit"] = d["risk_unit"].astype(float)
    d["baseline_ret"] = d["base_ret"].astype(float)
    d["symbol"] = d["rule"]
    qual = quality(d, group=lambda k: names.get(k, k), order=list(names.values()))
    prob = {}
    for k in names:
        f = d[d["rule"] == k]
        n = len(f)
        avg = float(f["net_ret"].mean()) if n else float("nan")
        base = float(f["baseline_ret"].mean()) if n else float("nan")
        prob[k] = {"active": bool(n >= MIN_PROVEN and base == base and avg < base), "n": n, "avg": avg, "random": base, "penalty": 0.1}
    return qual, prob


def record(h: pd.DataFrame, names: dict) -> dict:
    d = h[h["status"] == "closed"]
    out = {}
    for k in names:
        f = d[d["rule"] == k]
        nets = f["net"].astype(float)
        out[k] = {"n": int(len(f)), "hit": float((nets > 0).mean()) if len(f) else None, "avg": float(nets.mean()) if len(f) else None,
                  "random": float(f["base_ret"].astype(float).mean()) if len(f) else None, "open": int((h["rule"] == k).sum() - len(f))}
    out["recent"] = [{"rule": r["rule"], "name": names.get(r["rule"], r["rule"]), "base": r["base"], "exchange": r["exchange"], "side": int(r["side"]),
                      "t_in": int(r["t_in"]), "grade": r["grade"], "net": float(r["net"])} for r in d.tail(30).to_dict("records")][::-1]
    return out


# ---------------------------------------------------------------------------------------------- scan
def _load(ev: dict, now: pd.Timestamp):
    try:
        d = D.candles(ev["exchange"], ev["symbol"], "1d", ev["list_ms"] - D.DAY_MS, RECENT_DAYS + 2)
        age_h = (now.value // 10**6 - ev["list_ms"]) / D.HOUR_MS
        h = D.candles(ev["exchange"], ev["symbol"], "1h", ev["list_ms"] - D.HOUR_MS, 48) if age_h <= 72 else None
        return ev, d, h
    except Exception as e:  # noqa: BLE001
        log.warning("listing %s %s: %s", ev["exchange"], ev["symbol"], e)
        return ev, None, None


def scan() -> dict:
    now = pd.Timestamp.now(tz="UTC")
    rp = state_path("listings", "research.json")
    research = json.loads(rp.read_text()) if rp.exists() else RS.research()
    names = {k: v["name"] for k, v in RS.RULES.items()}
    hp = state_path(HIST)
    h0 = pd.read_csv(hp, dtype={"id": str}) if hp.exists() else pd.DataFrame(columns=HIST_COLS)
    qual, prob = self_check(h0, names)
    rec, upcoming = recent_listings(now)
    with ThreadPoolExecutor(6) as ex:
        loaded = list(ex.map(lambda e: _load(e, now), rec))
    rows, ideas, data, cautions = [], [], {}, []
    for ev, d, h in loaded:
        if d is None or d.empty or RS.is_stable(d) or D.stock_like(ev["exchange"], ev["base"], d):
            continue
        data[(ev["exchange"], ev["symbol"])] = d
        row = describe(ev, d, h, now)
        if row["age_d"] <= SHOW_DAYS:
            rows.append(row)
        if ev["exchange"] not in research.get("exchanges", []):
            continue
        for rule, _k, px, t in fresh_entries(ev, d, h, now):
            r = research["rules"][rule]
            if r["level"] == "bad":
                hold = held(r)["all"]
                cautions.append({"exchange": ev["exchange"], "base": ev["base"], "rule": rule, "name": r["name"], "avg": hold.get("avg"),
                                 "hit": hold.get("hit"), "n": hold.get("n"), "days": r["chosen"], "symbol": ev["symbol"]})
                continue
            j = judge(rule, research, ev, row, px, (qual or {}).get("vol"), prob.get(rule))
            side = RS.SIDE[rule]
            ideas.append({"id": f"{rule}|{ev['exchange']}|{ev['symbol']}|{int(t.value // 10**6)}", "rule": rule, "name": r["name"], "rule_text": r["text"],
                          "exchange": ev["exchange"], "sym": ev["symbol"], "base": ev["base"], "listed": ev["list_ms"], "side": side,
                          "t_in": int(t.value // 10**6), "entry": px, "last": row["last"], "asof": row["asof"], "vol24": row["vol24"],
                          "stop": px * (1 - RS.STOP * side), "cost": RS.COST, **j})
    ideas.sort(key=lambda x: -x["score"])
    for i, x in enumerate(ideas):
        x["rank"] = i + 1
    basket = D.basket_daily(int((now - pd.Timedelta(days=RECENT_DAYS + 40)).value // 10**6))
    h = track(ideas, {}, now)
    h = track([], resolve(h, data, basket), now)
    qual, prob = self_check(h, names)
    snap = {"generated_at": str(now), "research": {k: research[k] for k in ("generated_at", "since", "cost", "stop", "funding", "facts", "exchanges", "cut")
                                                   if k in research} | {"rules": research["rules"]},
            "listings": sorted(rows, key=lambda r: -r["list_ms"]), "upcoming": upcoming, "ideas": ideas, "cautions": cautions,
            "quality": qual, "probation": prob, "record": record(h, names), "names": names}
    out = REPO_ROOT / ".cache" / "listings_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task = argv[0] if argv else "scan"
    if task == "research":
        RS.research()
    elif task == "scan":
        snap = scan()
        log.info("listings scan: %d listings, %d upcoming, %d ideas, %d cautions", len(snap["listings"]), len(snap["upcoming"]),
                 len(snap["ideas"]), len(snap["cautions"]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
