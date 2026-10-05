"""ICT page: nightly backtests (how each kind of setup really did) and a scan every 15 minutes.

  python -m ict.run backtest   # 60 days of 15-minute candles per market -> state/ict/stats.json
  python -m ict.run scan       # live setups + track record -> .cache/ict_out/snapshot.json, state/ict/history.csv

Crypto comes from Binance; forex, gold and US index futures from Yahoo (which web pages can't read directly, so the
page relies on this snapshot for them). The website recomputes crypto setups live with site/ictengine.js.
"""
from __future__ import annotations

import json
import logging
import math
import sys

import numpy as np
import pandas as pd

from core.config import REPO_ROOT, state_path
from fx import checks as C
from fx.pairs import spread as fx_spread
from ict import engine as I
from scanner.quality import adjust_r, quality
from scanner.rank import grade as grade_of
from scanner.rank import score_from_r

log = logging.getLogger("omega.ict")
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT",
          "DOTUSDT", "TRXUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT"]
FX = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X", "USDCAD": "USDCAD=X",
      "NZDUSD": "NZDUSD=X", "USDCHF": "USDCHF=X", "XAUUSD": "GC=F"}
INDEX = {"ES": "ES=F", "NQ": "NQ=F", "YM": "YM=F"}
CORR = {"BTCUSDT": "ETHUSDT", "EURUSD": "GBPUSD", "GBPUSD": "EURUSD", "AUDUSD": "NZDUSD", "NZDUSD": "AUDUSD",
        "USDCHF": "USDJPY", "USDJPY": "USDCHF", "USDCAD": "USDCHF", "XAUUSD": "XAGUSD", "ES": "NQ", "NQ": "ES", "YM": "ES"}
YAHOO_EXTRA = {"XAGUSD": "SI=F"}
LABEL = {"XAUUSD": "Gold", "ES": "S&P 500 futures", "NQ": "Nasdaq 100 futures", "YM": "Dow futures"}
CLASSES = {"crypto": "Crypto", "fx": "Forex & gold", "index": "US index futures"}
BUCKETS = [("0-3", 0, 3), ("4-5", 4, 5), ("6-9", 6, 9)]
PRIOR_K = 10
MIN_PROVEN = 30


def klass(sym: str) -> str:
    return "crypto" if sym in CRYPTO else "index" if sym in INDEX else "fx"


def kind(sym: str) -> str:
    """Finer than klass(): gold behaves differently from currency pairs (warnings use their own limits)."""
    return "metal" if sym == "XAUUSD" else klass(sym)


def label(sym: str) -> str:
    if sym in LABEL:
        return LABEL[sym]
    if sym.endswith("USDT"):
        return sym[:-4]
    return f"{sym[:3]}/{sym[3:]}"


def cost(sym: str) -> float:
    """Round trip as a fraction of the price. Crypto: 0.1% per side on Binance plus a little slippage on the stop.
    Forex and gold: typical retail spread. Index futures: about one tick plus commission."""
    k = klass(sym)
    if k == "crypto":
        return 0.0022
    if k == "index":
        return 0.0001
    return fx_spread(sym)


def bucket(count: int) -> str:
    return next(b for b, lo, hi in BUCKETS if lo <= count <= hi)


# ---------------------------------------------------------------------------------------------- data
def _crypto(sym: str, interval: str, n: int) -> pd.DataFrame:
    from scanner.data import candles
    return candles(sym, interval, n)


def _yahoo(sym: str, interval: str, n: int, full: bool) -> pd.DataFrame:
    from stocks import data as Y
    ysym = FX.get(sym) or INDEX.get(sym) or YAHOO_EXTRA.get(sym)
    if full:
        df, _ = Y._yahoo(ysym, "15m" if interval == "15m" else "60m", "60d" if interval == "15m" else "730d")
        step = pd.Timedelta(minutes=15 if interval == "15m" else 60)
        df = df[df.index + step <= pd.Timestamp.now(tz="UTC")]
        return df.tail(n)
    return Y.candles(ysym, interval, n)


def load(sym: str, interval: str, n: int, full: bool = False) -> pd.DataFrame | None:
    try:
        df = _crypto(sym, interval, n) if sym.endswith("USDT") else _yahoo(sym, interval, n, full)
        return df if df is not None and len(df) >= 100 else None
    except Exception as e:  # noqa: BLE001
        log.warning("ict data %s %s: %s", sym, interval, e)
        return None


def market(sym: str, n15: int, n1h: int, full: bool = False) -> tuple:
    d15, d1h = load(sym, "15m", n15, full), load(sym, "1h", n1h, full)
    cs = CORR.get(sym, "BTCUSDT" if sym.endswith("USDT") else None)
    c15 = load(cs, "15m", n15, full) if cs else None
    return d15, d1h, c15


# ---------------------------------------------------------------------------------------------- backtest stats
def summarize(rows: list[dict]) -> dict:
    fin = [r for r in rows if r["status"] in ("win", "loss", "time")]
    rs = np.array([r["r"] for r in fin]) if fin else np.array([])
    n = len(fin)
    sd = float(rs.std(ddof=1)) if n > 1 else float("nan")
    avg = float(rs.mean()) if n else float("nan")
    done = [r for r in rows if r["status"] not in ("pending", "active")]
    return {"detected": len(done), "filled": n, "wins": int(sum(r["status"] == "win" for r in fin)),
            "losses": int(sum(r["status"] == "loss" for r in fin)), "time": int(sum(r["status"] == "time" for r in fin)),
            "fill_rate": n / len(done) if done else float("nan"), "win_rate": (sum(r["status"] == "win" for r in fin) / n) if n else float("nan"),
            "avg_r": avg, "t": avg / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan"),
            "avg_rr": float(np.mean([r["rr"] for r in fin])) if fin else float("nan"),
            "random_avg_r": _nanmean([r.get("base_r") for r in done])}


def _nanmean(xs) -> float:
    v = [float(x) for x in xs if x is not None and x == x]
    return float(np.mean(v)) if v else float("nan")


def stats_for(rows: list[dict]) -> dict:
    out = {"overall": summarize(rows), "buckets": {}, "factors": {}}
    for b, lo, hi in BUCKETS:
        out["buckets"][b] = summarize([r for r in rows if lo <= r["count"] <= hi])
    for f in I.FACTORS:
        w = summarize([r for r in rows if r["conf"][f]])
        wo = summarize([r for r in rows if not r["conf"][f]])
        out["factors"][f] = {"with_n": w["filled"], "with_r": w["avg_r"], "with_win": w["win_rate"],
                             "without_n": wo["filled"], "without_r": wo["avg_r"], "without_win": wo["win_rate"]}
    return out


def backtest() -> dict:
    allrows: dict[str, list] = {k: [] for k in CLASSES}
    per: dict[str, dict] = {}
    days: dict[str, float] = {}
    for sym in CRYPTO + list(FX) + list(INDEX):
        d15, d1h, c15 = market(sym, 5760, 1500, full=True)
        if d15 is None:
            continue
        S = I.setups(d15, d1h, c15, cost=cost(sym), kind=kind(sym))
        k = klass(sym)
        allrows[k] += S
        per[sym] = summarize(S)
        days[k] = max(days.get(k, 0), (d15.index[-1] - d15.index[0]).total_seconds() / 86400)
        log.info("ict backtest %s: %d setups", sym, len(S))
    out = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "params": I.P,
           "classes": {k: {**stats_for(v), "days": round(days.get(k, 0), 1),
                           "risk_q": [float(np.quantile([r["risk_pct"] for r in v], q)) for q in (0.25, 0.5, 0.75)] if v else None}
                       for k, v in allrows.items()},
           "instruments": per}
    state_path("ict/stats.json").write_text(json.dumps(clean(out), indent=1))
    return out


# ---------------------------------------------------------------------------------------------- probability
def judge(s: dict, st: dict | None, extra_pen: float = 0.0, vol: dict | None = None, probation: dict | None = None) -> dict:
    """Chance of reaching the target first, from how similar past setups did (same market type and number of
    confluences), pulled towards break-even when there are few examples. Expected result in R after costs; then the
    same adjustments as the other pages (warnings, news and manipulation checks, jumpiness, probation) give the
    score out of 10 and the grade. site/ict/app.js judge() is the same."""
    b = (st or {}).get("buckets", {}).get(bucket(s["count"]), {})
    n, w = b.get("filled", 0) or 0, b.get("wins", 0) or 0
    p0 = 1 / (1 + s["rr"])
    p = (w + p0 * PRIOR_K) / (n + PRIOR_K)
    exp_r = p * s["rr"] - (1 - p) - s["cost_r"]
    avg, t = b.get("avg_r"), b.get("t")
    if n < MIN_PROVEN:
        evidence = "Unproven"
    elif avg is None or not (avg > 0):
        evidence = "Weak history"
    elif t is not None and t >= 1.5:
        evidence = "Held up in tests"
    else:
        evidence = "Promising"
    pen = s.get("ctx", {}).get("pen", 0.0) + extra_pen + ((probation or {}).get("penalty", 0.0) if (probation or {}).get("active") else 0.0)
    adj = adjust_r(exp_r, s["risk_pct"], vol) - pen
    sc = score_from_r(adj)
    rq = (st or {}).get("risk_q")
    risk = None if not rq else "Low" if s["risk_pct"] < rq[0] else "Medium" if s["risk_pct"] < rq[1] else "High" if s["risk_pct"] < rq[2] else "Very high"
    return {"prob": p, "exp_r": exp_r, "exp_r_adj": adj, "score": sc, "grade": grade_of(sc), "evidence": evidence,
            "risk_level": risk, "bucket": bucket(s["count"]), "hist_n": n, "hist_avg_r": avg, "hist_fill": b.get("fill_rate")}


# ---------------------------------------------------------------------------------------------- news and manipulation
def currencies_of(sym: str) -> str:
    """A 'pair' whose two currencies are the ones whose news moves this market (fx.checks.news_in)."""
    k = kind(sym)
    return sym if k == "fx" else "XAUUSD" if k == "metal" else "USDUSD"


def market_checks(s: dict, sym: str, events: list, now: pd.Timestamp, df15: pd.DataFrame | None, th: dict) -> dict:
    """News (all markets: big US releases move crypto too) and, for forex, gold and futures, the rate-fix and
    thin-market checks of the Forex page. Penalties in R; `hard` blocks the setup like on the Forex page."""
    checks, hard, pen = [], [], 0.0
    pair = currencies_of(sym)
    close_by = pd.Timestamp((s.get("fill_t") or s["t"]) + I.P["hold_bars"] * 900_000, unit="ms", tz="UTC")
    soon = C.news_in(events, pair, now, now + pd.Timedelta(minutes=th["news_before_min"]))
    if soon:
        hard.append(f"{soon[0]['title']} ({soon[0]['country']}) in the next {int(th['news_before_min'])} minutes: "
                    "no new orders until it's out.")
        checks.append({"key": "news_soon", "ok": False, "text": hard[-1]})
    inside = C.news_in(events, pair, now, close_by)
    if inside:
        pen += 0.25
        e = inside[0]
        checks.append({"key": "news_window", "ok": False, "text": f"High-impact news before this trade's time limit: {e['title']} "
                       f"({e['country']}) at {e['at'].strftime('%a %H:%M')} UTC. Prices can jump straight through stops."})
    else:
        checks.append({"key": "news_window", "ok": True, "text": "No high-impact news before the trade's time limit."})
    if kind(sym) != "crypto":
        shift_t = pd.Timestamp(s["t"] + 900_000, unit="ms", tz="UTC")
        fx_name = C.in_fix(shift_t)
        if fx_name:
            pen += 0.10
            checks.append({"key": "fix", "ok": False, "text": f"The structure shift happened inside the {fx_name} window, where "
                           "dealer orders have been used to push prices (the banks fined in 2014-15 did exactly this)."})
        else:
            checks.append({"key": "fix", "ok": True, "text": "The structure shift didn't happen inside a rate-fixing window."})
        spikes = C.fix_spikes(df15, th) if df15 is not None else []
        if spikes:
            pen += 0.10
            checks.append({"key": "fix_spike", "ok": False, "text": f"A spike-and-reverse at the {spikes[-1]['fix']} recently: "
                           "the footprint of fix manipulation in this market."})
        else:
            checks.append({"key": "fix_spike", "ok": True, "text": "No spike-and-reverse at recent rate fixes."})
        thin = C.thin_market(now)
        if thin:
            pen += 0.05
            checks.append({"key": "thin", "ok": False, "text": thin})
    return {"checks": checks, "hard": hard, "pen": pen}


# ---------------------------------------------------------------------------------------------- scan
def clean(x):
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.floating,)):
        return clean(float(x))
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def chart(df: pd.DataFrame, n: int = 96) -> dict:
    d = df.tail(n)
    return {"t": [int(v) for v in d.index.as_unit("ms").asi8], "open": d["open"].round(8).tolist(), "high": d["high"].round(8).tolist(),
            "low": d["low"].round(8).tolist(), "close": d["close"].round(8).tolist()}


HIST = "ict/history.csv"
HIST_COLS = ["id", "class", "sym", "side", "t", "seen_at", "count", "entry", "stop", "target", "rr", "cost_r", "status", "r",
             "risk_pct", "score", "grade", "base_r"]


def track(found: list[dict], now: pd.Timestamp) -> pd.DataFrame:
    p = state_path(HIST)
    h = pd.read_csv(p, dtype={"id": str}) if p.exists() else pd.DataFrame(columns=HIST_COLS)
    for col in HIST_COLS:
        if col not in h:
            h[col] = np.nan
    h["grade"] = h["grade"].astype(object)
    known = set(h["id"])
    final = {"win", "loss", "time", "missed", "expired"}
    by_id = {s["id"]: s for s in found}
    for i, row in h.iterrows():
        s = by_id.get(row["id"])
        if s is not None and (row["status"] not in final or not row["base_r"] == row["base_r"]):
            h.loc[i, ["status", "r", "base_r"]] = [s["status"], s["r"], s.get("base_r", np.nan)]
    new = [s for s in found if s["id"] not in known and now.value // 10**6 - s["t"] <= 24 * 3_600_000]
    if new:
        h = pd.concat([h, pd.DataFrame([{**{c: s.get(c) for c in HIST_COLS}, "seen_at": str(now)} for s in new])], ignore_index=True)
    h.to_csv(p, index=False)
    return h


def quality_frame(h: pd.DataFrame) -> pd.DataFrame:
    """The live record in the form scanner/quality.py reads (results per $1 risked = R)."""
    h = h.reindex(columns=sorted(set(h.columns) | set(HIST_COLS)))
    d = h[h["status"].isin(["win", "loss", "time"]) & h["risk_pct"].notna() & h["grade"].notna()].copy()
    d["net_ret"] = d["r"].astype(float) * d["risk_pct"].astype(float)
    d["sl_pct"] = -d["risk_pct"].astype(float)
    d["risk_unit"] = d["risk_pct"].astype(float)
    d["baseline_ret"] = d["base_r"].astype(float) * d["risk_pct"].astype(float)
    return d


def self_check(h: pd.DataFrame) -> tuple[dict, dict]:
    """Per market type: the grade check and jumpiness adjustment (as on every page), and probation when the live
    record does worse than coin-flip entries with the same stops and targets."""
    qual, prob = {}, {}
    h = h.reindex(columns=sorted(set(h.columns) | set(HIST_COLS)))
    for k in CLASSES:
        d = quality_frame(h[h["class"] == k])
        qual[k] = quality(d)
        fin = h[(h["class"] == k) & h["status"].isin(["win", "loss", "time"])]
        n = len(fin)
        avg = float(fin["r"].astype(float).mean()) if n else float("nan")
        base = _nanmean(fin["base_r"].tolist()) if n else float("nan")
        active = n >= MIN_PROVEN and base == base and avg < base
        prob[k] = {"active": bool(active), "n": n, "avg_r": avg, "random_avg_r": base, "penalty": 0.1}
    return qual, prob


def record(h: pd.DataFrame) -> dict:
    out = {}
    for k in CLASSES:
        d = h[(h["class"] == k)]
        rows = [{"status": r["status"], "r": float(r["r"]) if r["r"] == r["r"] else 0.0, "rr": float(r["rr"]), "count": int(r["count"]),
                 "base_r": r.get("base_r")} for r in d.to_dict("records")]
        out[k] = summarize(rows) if rows else {}
    fin = h[h["status"].isin(["win", "loss", "time"])].tail(30)
    out["recent"] = [{"sym": r.sym, "label": label(r.sym), "side": r.side, "t": int(r.t), "status": r.status, "r": float(r.r)}
                     for r in fin.itertuples()][::-1]
    return out


def scan() -> dict:
    from fx.live import news as fx_news

    now = pd.Timestamp.now(tz="UTC")
    sp = state_path("ict/stats.json")
    stats = json.loads(sp.read_text()) if sp.exists() else None
    th = C.thresholds(None)
    try:
        events = fx_news()
    except Exception as e:  # noqa: BLE001
        log.warning("news calendar: %s", e)
        events = []
    hp = state_path(HIST)
    qual, prob = self_check(pd.read_csv(hp, dtype={"id": str}) if hp.exists() else pd.DataFrame(columns=HIST_COLS))
    classes = {k: {"label": v, "setups": [], "markets": [], "held_back": []} for k, v in CLASSES.items()}
    found = []
    for sym in CRYPTO + list(FX) + list(INDEX):
        d15, d1h, c15 = market(sym, 700, 400)
        if d15 is None:
            continue
        k = klass(sym)
        st = (stats or {}).get("classes", {}).get(k)
        a15 = I.arrays(d15)
        bias = float(I.htf_bias(I.arrays(d1h), a15["t"])[-1]) if d1h is not None and len(d1h) else 0.0
        closes = d15["close"].to_numpy(float)
        classes[k]["markets"].append({"sym": sym, "label": label(sym), "cost": cost(sym), "last": float(closes[-1]),
                                      "last_t": int(d15.index[-1].value // 10**6), "bias": bias,
                                      "chg24": float(closes[-1] / closes[-97] - 1) if len(closes) > 97 else None})
        S = I.setups(d15, d1h, c15, cost=cost(sym), start=max(0, len(d15) - 250), kind=kind(sym))
        idx = d15.index.as_unit("ms").asi8
        for s in S:
            s.update({"id": f"{sym}|{s['side']}|{s['t']}", "class": k, "sym": sym, "label": label(sym)})
            if s["fill"] >= 0:
                s["fill_t"] = int(idx[s["fill"]])
            mc = market_checks(s, sym, events, now, d15, th) if s["status"] in ("pending", "active") else {"checks": [], "hard": [], "pen": 0.0}
            j = judge(s, st, mc["pen"], (qual.get(k) or {}).get("vol"), prob.get(k))
            s.update({"score": j["score"], "grade": j["grade"]})
            found.append(s)
            if s["status"] not in ("pending", "active"):
                continue
            row = {**{x: s[x] for x in ("id", "sym", "label", "side", "t", "level", "level_px", "sweep", "fvg_top", "fvg_bot", "entry",
                                        "stop", "target", "target_name", "rr", "risk_pct", "cost_r", "conf", "count", "killzone",
                                        "status", "suggested", "ctx")},
                   "fill_t": s.get("fill_t"), "expires_t": s["t"] + I.P["fill_bars"] * 900_000,
                   "close_by_t": (s.get("fill_t") or s["t"]) + I.P["hold_bars"] * 900_000,
                   "checks": mc["checks"], "hard": mc["hard"], "mkt_pen": mc["pen"], **j, "chart": chart(d15)}
            (classes[k]["held_back"] if mc["hard"] and s["status"] == "pending" else classes[k]["setups"]).append(row)
    for k in classes:
        classes[k]["setups"].sort(key=lambda x: (x["status"] != "pending", -x["score"]))
    h = track(found, now)
    qual, prob = self_check(h)
    upcoming = sorted([{"title": e["title"], "country": e["country"], "at": str(pd.Timestamp(e["date"]).tz_convert("UTC"))}
                       for e in events if pd.Timestamp(e["date"]).tz_convert("UTC") >= now - pd.Timedelta(hours=1)],
                      key=lambda e: e["at"])[:30]
    snap = {"generated_at": str(now), "params": I.P, "factors": I.FACTORS, "classes": classes, "events": upcoming,
            "news_before_min": th["news_before_min"], "quality": qual, "probation": prob,
            "stats": (stats or {}).get("classes"), "stats_at": (stats or {}).get("generated_at"), "record": record(h)}
    out = REPO_ROOT / ".cache" / "ict_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task = argv[0] if argv else "scan"
    if task == "backtest":
        backtest()
    elif task == "scan":
        if not state_path("ict/stats.json").exists():
            backtest()
        snap = scan()
        log.info("ict scan: %s", {k: len(v["setups"]) for k, v in snap["classes"].items()})
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

