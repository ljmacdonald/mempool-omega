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
from fx.pairs import spread as fx_spread
from ict import engine as I

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
            "avg_rr": float(np.mean([r["rr"] for r in fin])) if fin else float("nan")}


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
        S = I.setups(d15, d1h, c15, cost=cost(sym))
        k = klass(sym)
        allrows[k] += S
        per[sym] = summarize(S)
        days[k] = max(days.get(k, 0), (d15.index[-1] - d15.index[0]).total_seconds() / 86400)
        log.info("ict backtest %s: %d setups", sym, len(S))
    out = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "params": I.P,
           "classes": {k: {**stats_for(v), "days": round(days.get(k, 0), 1)} for k, v in allrows.items()},
           "instruments": per}
    state_path("ict/stats.json").write_text(json.dumps(clean(out), indent=1))
    return out


# ---------------------------------------------------------------------------------------------- probability
def judge(s: dict, st: dict | None) -> dict:
    """Chance of reaching the target first, from how similar past setups did (same market type and number of
    confluences), pulled towards break-even when there are few examples. Expected result in R after costs."""
    b = (st or {}).get("buckets", {}).get(bucket(s["count"]), {})
    n, w = b.get("filled", 0) or 0, b.get("wins", 0) or 0
    p0 = 1 / (1 + s["rr"])
    p = (w + p0 * PRIOR_K) / (n + PRIOR_K)
    exp_r = p * s["rr"] - (1 - p) - s["cost_r"]
    avg, t = b.get("avg_r"), b.get("t")
    if n < MIN_PROVEN:
        grade = "Unproven"
    elif avg is None or not (avg > 0):
        grade = "Weak history"
    elif t is not None and t >= 1.5:
        grade = "Held up in tests"
    else:
        grade = "Promising"
    return {"prob": p, "exp_r": exp_r, "grade": grade, "bucket": bucket(s["count"]), "hist_n": n, "hist_avg_r": avg,
            "hist_fill": b.get("fill_rate")}


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
HIST_COLS = ["id", "class", "sym", "side", "t", "seen_at", "count", "entry", "stop", "target", "rr", "cost_r", "status", "r"]


def track(found: list[dict], now: pd.Timestamp) -> pd.DataFrame:
    p = state_path(HIST)
    h = pd.read_csv(p, dtype={"id": str}) if p.exists() else pd.DataFrame(columns=HIST_COLS)
    known = set(h["id"])
    final = {"win", "loss", "time", "missed", "expired"}
    by_id = {s["id"]: s for s in found}
    for i, row in h.iterrows():
        s = by_id.get(row["id"])
        if s is not None and row["status"] not in final:
            h.loc[i, ["status", "r"]] = [s["status"], s["r"]]
    new = [s for s in found if s["id"] not in known and now.value // 10**6 - s["t"] <= 24 * 3_600_000]
    if new:
        h = pd.concat([h, pd.DataFrame([{**{c: s.get(c) for c in HIST_COLS}, "seen_at": str(now)} for s in new])], ignore_index=True)
    h.to_csv(p, index=False)
    return h


def record(h: pd.DataFrame) -> dict:
    out = {}
    for k in CLASSES:
        d = h[(h["class"] == k)]
        rows = [{"status": r["status"], "r": float(r["r"]) if r["r"] == r["r"] else 0.0, "rr": float(r["rr"]), "count": int(r["count"])}
                for r in d.to_dict("records")]
        out[k] = summarize(rows) if rows else {}
    fin = h[h["status"].isin(["win", "loss", "time"])].tail(30)
    out["recent"] = [{"sym": r.sym, "label": label(r.sym), "side": r.side, "t": int(r.t), "status": r.status, "r": float(r.r)}
                     for r in fin.itertuples()][::-1]
    return out


def scan() -> dict:
    now = pd.Timestamp.now(tz="UTC")
    sp = state_path("ict/stats.json")
    stats = json.loads(sp.read_text()) if sp.exists() else None
    classes = {k: {"label": v, "setups": [], "markets": []} for k, v in CLASSES.items()}
    found = []
    for sym in CRYPTO + list(FX) + list(INDEX):
        d15, d1h, c15 = market(sym, 700, 400)
        if d15 is None:
            continue
        k = klass(sym)
        last_t = int(d15.index[-1].value // 10**6)
        classes[k]["markets"].append({"sym": sym, "label": label(sym), "cost": cost(sym), "last": float(d15["close"].iloc[-1]),
                                      "last_t": last_t})
        S = I.setups(d15, d1h, c15, cost=cost(sym), start=max(0, len(d15) - 250))
        idx = d15.index.as_unit("ms").asi8
        for s in S:
            s.update({"id": f"{sym}|{s['side']}|{s['t']}", "class": k, "sym": sym, "label": label(sym)})
            if s["fill"] >= 0:
                s["fill_t"] = int(idx[s["fill"]])
            found.append(s)
            if s["status"] in ("pending", "active"):
                j = judge(s, (stats or {}).get("classes", {}).get(k))
                classes[k]["setups"].append({**{x: s[x] for x in ("id", "sym", "label", "side", "t", "level", "level_px", "sweep", "fvg_top",
                                                                   "fvg_bot", "entry", "stop", "target", "target_name", "rr", "risk_pct",
                                                                   "cost_r", "conf", "count", "killzone", "status")},
                                             "fill_t": s.get("fill_t"), "expires_t": s["t"] + I.P["fill_bars"] * 900_000,
                                             "close_by_t": (s.get("fill_t") or s["t"]) + I.P["hold_bars"] * 900_000,
                                             **j, "chart": chart(d15)})
    for k in classes:
        classes[k]["setups"].sort(key=lambda x: (x["status"] != "pending", -x["exp_r"]))
    h = track(found, now)
    snap = {"generated_at": str(now), "params": I.P, "factors": I.FACTORS, "classes": classes,
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

