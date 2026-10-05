"""Strategy lab: famous public strategies (lab/strategies.py) re-tested on our markets, after trading costs, against
random entries, and their current positions. PAPER ONLY: nothing is traded.

    python -m lab.run backtest   # nightly: full history -> state/lab/stats.json
    python -m lab.run scan       # every 15 minutes: where each strategy stands now -> .cache/lab_out/snapshot.json
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
from lab import ideas as IDEAS
from lab import strategies as L

log = logging.getLogger("omega.lab")
ADDED = "2026-10-05"                      # trades after this date happened after the strategy was put on the site
CRYPTO = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
ETFS = ["SPY", "QQQ", "IWM", "DIA"]
MIXED = ["BTCUSDT", "ETHUSDT", "SPY", "QQQ", "GC=F", "EURUSD=X", "USDJPY=X"]
LABEL = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq 100 (QQQ)", "IWM": "Russell 2000 (IWM)", "DIA": "Dow (DIA)", "GC=F": "Gold",
         "EURUSD=X": "EUR/USD", "USDJPY=X": "USD/JPY"}
STEP_MIN = {"5m": 5, "1h": 60, "1d": 1440}
FULL = {"5m": 51_840, "1h": 17_520, "1d": 3_000}       # Binance candles for the backtest (180 days, 2 years, ~8 years)
RECENT = {"5m": 1_500, "1h": 1_500, "1d": 420}
YAHOO_RANGE = {"5m": ("60d", "5d"), "1d": ("30y", "2y")}    # "max" quietly returns monthly candles
MIN_N = 30

STRATS = {
    "orb": {"exit_rule": "Sell at the take profit, the safety exit, or the end of the New York session (4 pm), whichever comes first.", "published": "2023-05-01", "name": "Opening range breakout", "style": "Day trading", "tf": "5m", "markets": ETFS, "fn": L.orb,
            "source": "Zarattini & Aziz (2023), \"Can Day Trading Really Be Profitable?\" (SSRN 4416622) - the most-read day-trading paper of recent years, copied in many GitHub repositories",
            "url": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4416622",
            "claim": "The paper reports a 24% hit rate and +0.13 per $1 risked per trade on QQQ (33% a year with 4x leverage). An independent replication got the same before costs and about zero after costs.",
            "rules": "The first 5-minute candle of the New York session sets the direction: up candle, buy; down candle, sell. Enter at the start of the second candle, safety exit at the other end of the first candle, take profit at 10 times the risk, otherwise close at the end of the day."},
    "rsi2": {"exit_rule": "Sell at the close on the first day the price closes above its 5-day average. This page lists it under Exit signals when that happens.", "published": "2008-01-01", "name": "Connors RSI(2) dip buying", "style": "Swing (days)", "tf": "1d", "markets": ETFS, "fn": L.rsi2, "watch": L.rsi2_watch,
             "source": "Larry Connors & Cesar Alvarez, \"Short Term Trading Strategies That Work\" (2008) - one of the most copied swing-trading rules on GitHub and TradingView",
             "url": "https://en.wikipedia.org/wiki/Relative_strength_index",
             "claim": "Widely quoted as winning 70% or more of trades on US index funds.",
             "rules": "Buy at the close when the price is above its 200-day average and the 2-day RSI is below 10 (a sharp dip in an uptrend). Sell at the close on the first day the price closes above its 5-day average."},
    "turtle": {"exit_rule": "Sell when the price breaks the 10-day low (buy back a short above the 10-day high), or at the safety exit.", "published": "2003-01-01", "name": "Turtle breakout (System 1)", "style": "Trend following (weeks)", "tf": "1d", "markets": MIXED, "fn": L.turtle,
               "source": "The Turtle Traders' rules (Richard Dennis & William Eckhardt, 1983; published by Curtis Faith) - implemented in hundreds of GitHub repositories",
               "url": "https://en.wikipedia.org/wiki/Turtle_trading",
               "claim": "The Turtles reportedly made over $100 million in the 1980s. Expect a low hit rate (30-40%) with a few big winners.",
               "rules": "Buy when the price breaks above the highest high of the last 20 days; sell short below the lowest low. Exit on a break of the 10-day low (10-day high for shorts) or at a safety exit 2 average daily ranges from the entry."},
    "golden": {"exit_rule": "Sell the day after the 50-day average closes back below the 200-day average.", "name": "Golden cross (50/200-day)", "style": "Long-term trend", "tf": "1d", "markets": MIXED, "fn": L.golden_cross,
               "source": "The textbook moving-average crossover - the most common first trading bot on GitHub",
               "url": "https://en.wikipedia.org/wiki/Golden_cross",
               "claim": "Promoted as catching big trends while avoiding crashes.",
               "rules": "Buy the day after the 50-day average closes above the 200-day average; sell the day after it closes back below. Buying only."},
    "supertrend": {"exit_rule": "Sells on its take-profit table (8.7% at first, falling to break-even after about 37 hours), the trailing stop, -26.5%, or when three other Supertrends turn down.", "published": "2021-06-01", "name": "freqtrade Supertrend (machine-tuned)", "style": "Swing (hours to days)", "tf": "1h", "markets": CRYPTO, "fn": L.supertrend,
                   "source": "freqtrade-strategies, the official strategy collection of the most popular open-source crypto bot (5,000+ stars): Supertrend.py",
                   "url": "https://github.com/freqtrade/freqtrade-strategies",
                   "claim": "Its settings were tuned by freqtrade's hyperopt, i.e. searched by computer to fit past prices as well as possible.",
                   "rules": "Buy when three Supertrend lines (tuned settings) all point up; sell when three others all point down. Take profit from 8.7% falling to break-even over ~37 hours, safety exit at -26.5%, trailing stop."},
    "bbandrsi": {"exit_rule": "Sells at +10%, at -25%, or when the 14-hour RSI goes above 70.", "published": "2018-06-01", "name": "freqtrade BbandRsi", "style": "Dip buying (hours to days)", "tf": "1h", "markets": CRYPTO, "fn": L.bband_rsi,
                 "source": "freqtrade-strategies (official collection): berlinguyinca/BbandRsi.py",
                 "url": "https://github.com/freqtrade/freqtrade-strategies",
                 "claim": "A classic oversold-bounce bot: buy when the price is stretched far below normal.",
                 "rules": "Buy when the 14-hour RSI is below 30 and the price closes under the lower Bollinger band; sell when the RSI goes above 70, at +10% profit, or at the -25% safety exit."},
    "s001": {"exit_rule": "Sells at 5% profit at once, 4% after 20 minutes, 3% after 30, 1% after an hour, at -10%, or on its sell pattern when in profit.", "published": "2018-01-01", "name": "freqtrade Strategy001", "style": "Scalping (minutes)", "tf": "5m", "markets": CRYPTO, "fn": L.strategy001,
             "source": "freqtrade-strategies (official collection): Strategy001.py, the sample most new bot users start from",
             "url": "https://github.com/freqtrade/freqtrade-strategies",
             "claim": "Small quick profits: takes 1-5% and only sells on its signal when in profit, so the hit rate looks high.",
             "rules": "Buy when the 20-candle average crosses above the 50 on a rising Heikin-Ashi candle; take 5% profit at once, 4% after 20 minutes, 3% after 30, 1% after an hour; safety exit at -10%."},
}


def label(sym: str) -> str:
    return LABEL.get(sym) or (sym[:-4] if sym.endswith("USDT") else sym)


def cost(sym: str) -> float:
    """Round trip as a fraction of the price: crypto 0.1% fee per side plus slippage; big US funds about a cent of
    spread and slippage each way; gold futures a tick or two; forex the typical retail spread."""
    if sym.endswith("USDT"):
        return 0.0022
    if sym == "GC=F":
        return 0.0003
    if sym.endswith("=X"):
        return fx_spread(sym[:6])
    return 0.0002


# ---------------------------------------------------------------------------------------------- data
def closed(df: pd.DataFrame, sym: str, tf: str, now: pd.Timestamp) -> pd.DataFrame:
    if tf != "1d":
        return df[df.index + pd.Timedelta(minutes=STEP_MIN[tf]) <= now]
    if sym in ETFS or sym in ("GC=F",):
        ny = df.index.tz_convert("America/New_York")
        end = pd.to_datetime([pd.Timestamp(d.date()).tz_localize("America/New_York") + pd.Timedelta(hours=16, minutes=15) for d in ny])
        return df[end <= now]
    return df[df.index + pd.Timedelta(days=1) <= now]


def closed_at(t: pd.Timestamp, sym: str, tf: str) -> int:
    """When the candle starting at t finished (ms): US funds and gold daily candles at 4 pm New York."""
    if tf == "1d" and (sym in ETFS or sym == "GC=F"):
        d = t.tz_convert("America/New_York")
        return int((pd.Timestamp(d.date()).tz_localize("America/New_York") + pd.Timedelta(hours=16)).value // 10**6)
    return int((t + pd.Timedelta(minutes=STEP_MIN[tf])).value // 10**6)


def load(sym: str, tf: str, full: bool) -> pd.DataFrame | None:
    now = pd.Timestamp.now(tz="UTC")
    try:
        if sym.endswith("USDT"):
            from scanner.data import candles
            df = candles(sym, tf, (FULL if full else RECENT)[tf])
        else:
            from stocks import data as Y
            df, _ = Y._yahoo(sym, tf, YAHOO_RANGE[tf][0 if full else 1])
        df = closed(df, sym, tf, now)
        return df if len(df) >= 50 else None
    except Exception as e:  # noqa: BLE001
        log.warning("lab data %s %s: %s", sym, tf, e)
        return None


# ---------------------------------------------------------------------------------------------- results
def summarize(rows: list[dict]) -> dict:
    nets = np.array([r["net"] for r in rows], float)
    n = len(nets)
    sd = float(nets.std(ddof=1)) if n > 1 else float("nan")
    avg = float(nets.mean()) if n else float("nan")
    base = [r["base"] for r in rows if r["base"] == r["base"]]
    rs = [r["r"] for r in rows if r.get("r") is not None]
    ed = np.array([r["net"] - r["base"] for r in rows if r["base"] == r["base"]], float)     # each trade vs its random twin
    esd = float(ed.std(ddof=1)) if len(ed) > 1 else float("nan")
    wins, losses = nets[nets > 0], nets[nets <= 0]
    return {"n": n, "hit": float((nets > 0).mean()) if n else float("nan"), "avg": avg,
            "t": avg / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan"),
            "random": float(np.mean(base)) if base else float("nan"),
            "edge": float(ed.mean()) if len(ed) else float("nan"),
            "t_edge": float(ed.mean() / (esd / math.sqrt(len(ed)))) if len(ed) > 1 and esd > 0 else float("nan"),
            "avg_win": float(wins.mean()) if len(wins) else float("nan"), "avg_loss": float(losses.mean()) if len(losses) else float("nan"),
            "total": float(nets.sum()) if n else 0.0, "avg_r": float(np.mean(rs)) if rs else None,
            "hold_h": float(np.mean([r["hold_h"] for r in rows])) if n else float("nan")}


def verdict(all_: dict, last: dict) -> tuple[str, str]:
    """(level, plain-English verdict). Held up = enough trades, made money after costs, beat random entries (each
    trade against the same trade entered at random times) by more than luck, and still beat them in the later part
    of the history: after the strategy was published where that's known, else the most recent third."""
    if all_["n"] < MIN_N:
        return "warn", f"Too few trades to judge ({all_['n']}; at least {MIN_N} needed)."
    if not all_["avg"] > 0:
        return "bad", "Lost money after trading costs."
    if not (all_["t_edge"] >= 2):
        if not all_["edge"] > 0:
            return "bad", "Made money only because the market went up: random entries held as long did as well or better."
        return "warn", "Made money after costs, but not clearly better than random entries: could be luck."
    if not (last.get("n", 0) >= 10 and last["edge"] > 0 and last["t_edge"] >= 1):
        return "warn", ("Beat random entries in the earlier years but not clearly in the later part of the history (after it was "
                        "published, where known): the edge seems to have faded.")
    return "good", "Held up: made money after costs, beat random entries by more than luck, and still did so in the later part of the history (after it was published, where known)."


def run_strategy(key: str, full: bool, data: dict | None = None) -> tuple[list[dict], list[dict], dict]:
    """Finished trades (with costs and random baseline), still-open trades, and the as-of time per market."""
    s = STRATS[key]
    done, live, asof, watch = [], [], {}, {}
    for sym in s["markets"]:
        df = (data or {}).get((sym, s["tf"])) if data is not None else None
        if df is None:
            df = load(sym, s["tf"], full)
            if data is not None and df is not None:
                data[(sym, s["tf"])] = df
        if df is None:
            continue
        asof[sym] = closed_at(df.index[-1], sym, s["tf"])
        if s.get("watch"):
            watch[sym] = s["watch"](df)
        tr = s["fn"](df)
        fin = [t for t in tr if not t.get("open")]
        base = L.random_baseline(df, fin, cost(sym))
        t0, t1 = df.index[0], df.index[-1]
        pub = pd.Timestamp(s["published"], tz="UTC") if s.get("published") else None
        cut = pub if pub is not None and t0 + (t1 - t0) / 5 <= pub <= t1 - (t1 - t0) / 5 else t0 + (t1 - t0) * 2 / 3
        idx = df.index
        for t, b in zip(fin, base):
            ti, to = idx[t["i_in"]], idx[t["i_out"]]
            done.append({"sym": sym, "label": label(sym), "side": t["side"], "t_in": int(ti.value // 10**6), "t_out": int(to.value // 10**6),
                         "entry": t["entry"], "exit": t["exit"], "net": t["gross"] - cost(sym), "base": b, "reason": t["reason"],
                         "r": t.get("r"), "hold_h": max((to - ti).total_seconds() / 3600, STEP_MIN[s["tf"]] / 60),
                         "late": bool(ti >= cut), "after_added": bool(ti >= pd.Timestamp(ADDED, tz="UTC"))})
        for t in tr:
            if t.get("open"):
                live.append({"sym": sym, "label": label(sym), "side": t["side"], "t_in": int(idx[t["i_in"]].value // 10**6), "entry": t["entry"],
                             "last": float(df["close"].iloc[-1]), "stop": t.get("stop"), "target": t.get("target"),
                             "asof": asof[sym], "fresh": bool(t["i_in"] >= len(df) - IDEAS.FRESH[s["tf"]])})
    return done, live, {"asof": asof, "watch": watch}


def stats(key: str, done: list[dict]) -> dict:
    by = {}
    for sym in STRATS[key]["markets"]:
        rows = [r for r in done if r["sym"] == sym]
        if rows:
            by[sym] = {"label": label(sym), **summarize(rows), "from": min(r["t_in"] for r in rows), "to": max(r["t_out"] for r in rows)}
    all_, early, late = summarize(done), summarize([r for r in done if not r["late"]]), summarize([r for r in done if r["late"]])
    level, text = verdict(all_, late)
    s = STRATS[key]
    lo, hi = min((r["t_in"] for r in done), default=0), max((r["t_out"] for r in done), default=0)
    pub = pd.Timestamp(s["published"], tz="UTC").value // 10**6 if s.get("published") else None
    split = "publication" if pub and lo + (hi - lo) / 5 <= pub <= hi - (hi - lo) / 5 else "thirds"
    since = summarize([r for r in done if r["after_added"]])
    return {"all": all_, "early": early, "late": late, "since_added": since, "markets": by, "level": level, "verdict": text, "split": split,
            "recent": sorted(done, key=lambda r: r["t_out"])[-12:][::-1],
            "from": min((r["t_in"] for r in done), default=None), "to": max((r["t_out"] for r in done), default=None)}


def clean(x):
    if isinstance(x, (float, np.floating)):
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


def meta(key: str) -> dict:
    return {k: v for k, v in STRATS[key].items() if k not in ("fn", "watch")} | {"markets": [{"sym": m, "label": label(m), "cost": cost(m)} for m in STRATS[key]["markets"]]}


def backtest() -> dict:
    data: dict = {}
    out = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "added": ADDED, "strategies": {}}
    for key in STRATS:
        done, _, _ = run_strategy(key, True, data)
        out["strategies"][key] = stats(key, done)
        a = out["strategies"][key]["all"]
        log.info("lab %s: %d trades, hit %.0f%%, avg %.3f%% vs random %.3f%%, t %.2f", key, a["n"], 100 * (a["hit"] or 0), 100 * (a["avg"] or 0),
                 100 * (a["random"] or 0), a["t"] if a["t"] == a["t"] else 0)
    p = state_path("lab/stats.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(clean(out), indent=1, allow_nan=False))
    return out


def scan() -> dict:
    """Where each strategy stands now, plus ranked trade ideas (lab/ideas.py) and their track record."""
    from fx.live import news as fx_news

    now = pd.Timestamp.now(tz="UTC")
    sp = state_path("lab/stats.json")
    st = json.loads(sp.read_text()) if sp.exists() else {}
    try:
        events = fx_news()
    except Exception as e:  # noqa: BLE001
        log.warning("news calendar: %s", e)
        events = []
    names = {k: v["name"] for k, v in STRATS.items()}
    hp = state_path(IDEAS.HIST)
    h0 = pd.read_csv(hp, dtype={"id": str}) if hp.exists() else pd.DataFrame(columns=IDEAS.HIST_COLS)
    qual, prob = IDEAS.self_check(h0, names)
    data: dict = {}
    snap = {"generated_at": str(now), "added": ADDED, "stats_at": st.get("generated_at"), "strategies": {}, "ideas": [], "exits": [],
            "forming": []}
    done_by_key = {}
    for key, s in STRATS.items():
        done, live, extra = run_strategy(key, False, data)
        sst = (st.get("strategies") or {}).get(key) or {}
        level = sst.get("level") or "warn"
        snap["strategies"][key] = {**meta(key), "stats": sst or None, "open": live, **extra}
        step = STEP_MIN[s["tf"]] * 60_000
        now_ms = now.value // 10**6

        # intraday strategies count only while their prices are current (daily ones: the last finished day counts)
        current = {sym: s["tf"] == "1d" or now_ms - t <= IDEAS.FRESH[s["tf"]] * step for sym, t in extra["asof"].items()}

        for d in done:
            done_by_key[(key, d["sym"], d["t_in"])] = d
            if current.get(d["sym"]) and d["t_out"] >= extra["asof"].get(d["sym"], 0) - IDEAS.FRESH[s["tf"]] * step:
                snap["exits"].append({"strategy": key, "name": s["name"],
                                      **{x: d[x] for x in ("sym", "label", "side", "t_in", "t_out", "entry", "exit", "net", "reason")}})
        for o in live:
            if not (o["fresh"] and current.get(o["sym"])):
                continue
            m = (sst.get("markets") or {}).get(o["sym"])
            hold_h = (m or {}).get("hold_h") or STEP_MIN[s["tf"]] / 60
            j = IDEAS.judge(o, m, level, o["last"], events, now, hold_h, (qual or {}).get("vol"), prob.get(key))
            snap["ideas"].append({**o, "id": f"{key}|{o['sym']}|{o['t_in']}", "strategy": key, "name": s["name"], "tf": s["tf"],
                                  "level": level, "cost": cost(o["sym"]), "exit_rule": s["exit_rule"], **j})
        if key == "rsi2":
            for sym in s["markets"]:
                f = IDEAS.rsi2_forming(data.get((sym, "1d")), data.get((sym, "5m")), now)
                if f:
                    snap["forming"].append({"strategy": key, "sym": sym, "label": label(sym), **f})
    snap["ideas"].sort(key=lambda x: -x["score"])
    for i, x in enumerate(snap["ideas"]):
        x["rank"] = i + 1
    h = IDEAS.track(snap["ideas"], done_by_key, now)
    qual, prob = IDEAS.self_check(h, names)
    snap.update({"quality": qual, "probation": prob, "record": IDEAS.record(h, names)})
    out = REPO_ROOT / ".cache" / "lab_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    task = argv[0] if argv else "scan"
    if task == "backtest":
        backtest()
    elif task == "scan":
        if not state_path("lab/stats.json").exists():
            backtest()
        snap = scan()
        log.info("lab scan: %d ideas, %d exits, %d forming", len(snap["ideas"]), len(snap["exits"]), len(snap["forming"]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
