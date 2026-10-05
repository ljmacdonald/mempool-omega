"""Can the ICT rules be improved, honestly? A walk-forward test of six changes.

  python -m ict.research        # -> state/ict/research.json (shown on the ICT page's Track record tab)

Data: crypto (8 large coins) 2 years of 1-hour and 6 months of 15-minute Binance candles; forex majors, gold and US
index futures 2 years of 1-hour and 60 days of 15-minute Yahoo candles.

Each market's history is split in time: the first two thirds are for choosing (training), the last third is looked at
once, to judge (testing). Every variant is reported on both, next to random entries with the same stop distance,
reward:risk and costs. The variant chosen for a market type is the one that did best *in training*; it only counts as
an improvement if, in the test third, it made money, beat random, and did so by more than luck (paired t >= 2) on at
least 30 trades.

Variants (each builds on the costs and timeframe of the one it names):
  base15     the live page's rules: 15-minute candles, 1-hour trend, taker fees
  h1         the same rules on 1-hour candles with the daily trend (stops about 4x wider, so fees matter less)
  h1_cheap   h1 + limit-order fees on crypto (0.075% a side with the BNB discount, plus slippage on stops) and no
             setup whose stop is smaller than 4x the round-trip cost
  h1_daily   h1_cheap + the daily trend must agree (ICT's higher-timeframe bias as a rule, not a checklist item)
  h1_partial h1_daily + half closed at 1R and the stop moved to break-even
  h1_model   h1_cheap setups filtered by a small model trained only on the training third, using each setup's
             features (checklist, sweep, gap, stop size, hour, reward:risk); trades only when it predicts a gain
"""
from __future__ import annotations

import json
import logging
import math
import sys

import numpy as np
import pandas as pd

from core.config import state_path
from ict import engine as I
from ict import run as R

log = logging.getLogger("omega.ict.research")
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "LINKUSDT"]
FX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "NZDUSD", "USDCHF", "XAUUSD"]
INDEX = ["ES", "NQ", "YM"]
P_H1 = I.P_H1
CHEAP_CRYPTO = 0.0017
MIN_RISK_COSTS = 4.0
VARIANTS = {
    "base15": "Live rules: 15-minute candles, taker fees",
    "h1": "1-hour candles, daily trend",
    "h1_cheap": "1-hour + limit-order fees, no tiny stops",
    "h1_daily": "... + daily trend must agree",
    "h1_partial": "... + half off at 1R, stop to break-even",
    "h1_model": "1-hour + a model picks the setups",
}
MIN_TEST = 30


# ---------------------------------------------------------------------------------------------- data
def _yahoo(sym: str, interval: str, rng: str) -> pd.DataFrame | None:
    from stocks import data as Y
    ysym = R.FX.get(sym) or R.INDEX.get(sym) or R.YAHOO_EXTRA.get(sym)
    try:
        df, _ = Y._yahoo(ysym, interval, rng)
        step = pd.Timedelta(minutes=15 if interval == "15m" else 60)
        return df[df.index + step <= pd.Timestamp.now(tz="UTC")]
    except Exception as e:  # noqa: BLE001
        log.warning("yahoo %s %s: %s", sym, interval, e)
        return None


def _binance(sym: str, interval: str, n: int) -> pd.DataFrame | None:
    from scanner.data import candles
    try:
        return candles(sym, interval, n)
    except Exception as e:  # noqa: BLE001
        log.warning("binance %s %s: %s", sym, interval, e)
        return None


def load(sym: str) -> dict:
    """15-minute and 1-hour candles (plus the correlated market's) for one market."""
    if sym.endswith("USDT"):
        return {"15m": _binance(sym, "15m", 17_280), "1h": _binance(sym, "1h", 17_520)}
    return {"15m": _yahoo(sym, "15m", "60d"), "1h": _yahoo(sym, "60m", "730d")}


def daily(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


# ---------------------------------------------------------------------------------------------- variants
def run_market(sym: str, data: dict, corr: dict) -> list[dict]:
    """All variants' setups for one market, each tagged with its variant, split and features."""
    k = R.klass(sym)
    std_cost = R.cost(sym)
    cheap = CHEAP_CRYPTO if k == "crypto" else std_cost
    out = []
    d15, d1 = data.get("15m"), data.get("1h")
    c15, c1 = corr.get("15m"), corr.get("1h")
    if d15 is not None and len(d15) > 500:
        for s in I.setups(d15, d1, c15, cost=std_cost, kind=R.kind(sym)):
            out.append({**s, "variant": "base15"})
    if d1 is None or len(d1) < 1000:
        return out
    dd = daily(d1)
    raw = I.setups(d1, dd, c1, cost=0.0, kind=R.kind(sym), p=P_H1, bar_ms=3_600_000, htf_ms=86_400_000)
    part = I.setups(d1, dd, c1, cost=0.0, kind=R.kind(sym), p=P_H1, bar_ms=3_600_000, htf_ms=86_400_000, manage="partial")
    for s, sp in zip(raw, part):
        gross, gross_p, gross_b = s["r"], sp["r"], s["base_r"]
        for v, cost, min_risk, need_htf, use_partial in (("h1", std_cost, 0, False, False), ("h1_cheap", cheap, MIN_RISK_COSTS, False, False),
                                                         ("h1_daily", cheap, MIN_RISK_COSTS, True, False), ("h1_partial", cheap, MIN_RISK_COSTS, True, True)):
            if s["risk_pct"] < min_risk * cost or (need_htf and not s["conf"]["htf"]):
                continue
            cr = cost / s["risk_pct"]
            r = (gross_p if use_partial else gross) - cr if s["status"] in ("win", "loss", "time") else gross
            out.append({**s, "variant": v, "cost_r": cr, "r": r, "base_r": gross_b - cr if gross_b == gross_b else gross_b,
                        "status": sp["status"] if use_partial else s["status"]})
    for o in out:
        o["sym"], o["class"] = sym, k
    return out


def features(s: dict) -> list[float]:
    risk = abs(s["entry"] - s["stop"]) or 1e-12
    hour = pd.Timestamp(s["t"], unit="ms", tz="UTC").tz_convert("America/New_York").hour
    return [s["count"], *[float(s["conf"][f]) for f in I.FACTORS], math.log(s["risk_pct"]), s["rr"], float(s["side"] == "buy"),
            float(s["target_name"] == "2x risk"), I.LEVEL_RANK.get(s["level"], I.LEVEL_RANK.get(I.FLIP.get(s["level"], ""), 0)),
            abs(s["fvg_top"] - s["fvg_bot"]) / risk, abs(s["level_px"] - s["sweep"]) / risk, hour]


def model_variant(rows: list[dict]) -> list[dict]:
    """h1_model: a small gradient-boosted model trained on the training third's h1_cheap fills predicts each setup's R;
    in the test third only setups it expects to gain on are taken."""
    import lightgbm as lgb

    base = [r for r in rows if r["variant"] == "h1_cheap"]
    out = []
    for k in {r["class"] for r in base}:
        tr = [r for r in base if r["class"] == k and r["split"] == "train" and r["status"] in ("win", "loss", "time")]
        te = [r for r in base if r["class"] == k and r["split"] == "test"]
        if len(tr) < 100 or not te:
            continue
        def fit(rs):
            m = lgb.LGBMRegressor(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=30, subsample=0.8,
                                  subsample_freq=1, colsample_bytree=0.8, verbose=-1, random_state=7)
            return m.fit(np.array([features(r) for r in rs]), np.clip([r["r"] for r in rs], -3, 6))

        # training score from 5 time-ordered folds (each predicted by a model that never saw it), so the model
        # can't look better in training than it really is
        tr.sort(key=lambda r: r["t"])
        folds = np.array_split(np.arange(len(tr)), 5)
        pred_tr = np.empty(len(tr))
        for f in folds:
            rest = [tr[i] for i in range(len(tr)) if i not in set(f)]
            pred_tr[f] = fit(rest).predict(np.array([features(tr[i]) for i in f]))
        pred_te = fit(tr).predict(np.array([features(r) for r in te]))
        out += [{**r, "variant": "h1_model"} for r, p in zip(tr, pred_tr) if p > 0]
        out += [{**r, "variant": "h1_model"} for r, p in zip(te, pred_te) if p > 0]
    return out


# ---------------------------------------------------------------------------------------------- scoring
def summary(rows: list[dict]) -> dict:
    fin = [r for r in rows if r["status"] in ("win", "loss", "time") and r["r"] == r["r"]]
    n = len(fin)
    if not n:
        return {"n": 0}
    rs = np.array([r["r"] for r in fin])
    bs = np.array([r["base_r"] for r in fin])
    ok = ~np.isnan(bs)
    diff = rs[ok] - bs[ok]
    sd = rs.std(ddof=1) if n > 1 else float("nan")
    dsd = diff.std(ddof=1) if len(diff) > 1 else float("nan")
    return {"n": n, "win_rate": float((rs > 0).mean()), "avg_r": float(rs.mean()),
            "t": float(rs.mean() / (sd / math.sqrt(n))) if n > 1 and sd > 0 else None,
            "random_avg_r": float(bs[ok].mean()) if ok.any() else None,
            "edge": float(diff.mean()) if len(diff) else None,
            "edge_t": float(diff.mean() / (dsd / math.sqrt(len(diff)))) if len(diff) > 1 and dsd > 0 else None}


def split(rows: list[dict]) -> None:
    """First two thirds of each market's history = train, last third = test (by the market's own span)."""
    by = {}
    for r in rows:
        by.setdefault((r["sym"], r["variant"].startswith("h1")), []).append(r["t"])
    cut = {key: min(ts) + (max(ts) - min(ts)) * 2 / 3 for key, ts in by.items()}
    for r in rows:
        r["split"] = "train" if r["t"] < cut[(r["sym"], r["variant"].startswith("h1"))] else "test"


def judge(classes: dict) -> None:
    for c in classes.values():
        cands = [(v, x) for v, x in c["variants"].items() if x["train"].get("n", 0) >= MIN_TEST and x["train"].get("edge") is not None]
        if not cands:
            c.update(chosen=None, passed=False, verdict="Not enough training trades to choose a variant.")
            continue
        v, x = max(cands, key=lambda vx: vx[1]["train"]["edge"])
        te = x["test"]
        passed = te.get("n", 0) >= MIN_TEST and (te.get("avg_r") or -1) > 0 and (te.get("edge") or -1) > 0 and (te.get("edge_t") or 0) >= 2
        c.update(chosen=v, passed=bool(passed))
        if passed:
            c["verdict"] = (f"{VARIANTS[v]} held up on unseen data: {te['avg_r']:+.2f}R per trade vs {te['random_avg_r']:+.2f}R for random "
                            f"entries over {te['n']} trades. A candidate for the live page, to be confirmed by the live record.")
        else:
            n, avg, rnd, et = te.get("n", 0), te.get("avg_r"), te.get("random_avg_r"), te.get("edge_t")
            why = (f"too few to judge, only {n} trades in the unseen period ({MIN_TEST} are needed)" if n < MIN_TEST
                   else "it lost money there" if not (avg or 0) > 0
                   else "it didn't beat random entries there" if not (te.get("edge") or 0) > 0
                   else f"its lead over random entries could still be luck (t = {et:.1f}; 2 is needed)")
            c["verdict"] = (f"The version that did best while choosing ({VARIANTS[v]}) made {avg if avg is not None else float('nan'):+.2f}R "
                            f"per trade on unseen data vs {rnd if rnd is not None else float('nan'):+.2f}R for random entries: "
                            f"{why}. No improvement is proven; the live page keeps its rules.")


def research() -> dict:
    rows: list[dict] = []
    cache: dict[str, dict] = {}

    def get(sym):
        if sym not in cache:
            cache[sym] = load(sym)
        return cache[sym]

    spans = {}
    for sym in CRYPTO + FX + INDEX:
        data = get(sym)
        cs = R.CORR.get(sym, "BTCUSDT" if sym.endswith("USDT") else None)
        corr = get(cs) if cs else {}
        got = run_market(sym, data, corr)
        rows += got
        for tf in ("15m", "1h"):
            d = data.get(tf)
            if d is not None and len(d):
                spans.setdefault(R.klass(sym), {})[tf] = [str(d.index[0].date()), str(d.index[-1].date())]
        log.info("research %s: %d setups across variants", sym, len(got))
    split(rows)
    rows += model_variant(rows)
    classes = {}
    for k in R.CLASSES:
        classes[k] = {"label": R.CLASSES[k], "spans": spans.get(k, {}), "variants": {}}
        for v in VARIANTS:
            sub = [r for r in rows if r["class"] == k and r["variant"] == v]
            classes[k]["variants"][v] = {"train": summary([r for r in sub if r["split"] == "train"]),
                                         "test": summary([r for r in sub if r["split"] == "test"])}
    judge(classes)
    out = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "variants": VARIANTS, "min_test": MIN_TEST, "classes": classes}
    state_path("ict/research.json").write_text(json.dumps(R.clean(out), indent=1))
    return out


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = research()
    for k, c in out["classes"].items():
        log.info("%s: chosen %s, passed %s. %s", k, c.get("chosen"), c.get("passed"), c.get("verdict"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
