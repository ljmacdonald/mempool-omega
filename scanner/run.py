"""Scanner entry points.

  python -m scanner.run hourly            # all three speeds: top 5 each, settle old ideas, write LATEST.md
  python -m scanner.run train             # nightly: retrain one model per speed + 90-day big-mover stats
  python -m scanner.run once --style quick
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
import pandas as pd

from alerts.telegram import send
from core.config import state_path
from core.log import get_logger
from scanner import track
from scanner.data import config, daily_candles, load_all, select_small_universe, select_universe
from scanner.features import big_mover_stats
from scanner.live import models_dir, scan
from scanner.model import ScannerModel, build_dataset  # noqa: F401
from scanner.styles import DEFAULT_STYLE, STYLES

log = get_logger("scanner.run")


def train(styles: list[str] | None = None) -> dict:
    uni = select_universe()
    from scanner.improve import nightly, select_model

    infos, selection = {}, {}
    for key in styles or list(STYLES):
        st = STYLES[key]
        candles = load_all(uni["symbol"].tolist(), st.train_bars, st.interval)
        m, selection[key] = select_model(key, build_dataset(candles, st))   # champion vs challengers
        m.save(models_dir())
        infos[key] = m.info
    bm = {}
    try:
        small = select_small_universe()["symbol"].tolist()
    except Exception as e:  # noqa: BLE001
        log.warning("small-coin universe: %s", e)
        small = []
    for s in list(uni["symbol"]) + small:   # swing statistics for both pages
        try:
            bm[s] = big_mover_stats(daily_candles(s, 97).iloc[:-1].tail(96))
        except Exception as e:  # noqa: BLE001
            log.warning("daily %s: %s", s, e)
    state_path("reports", "scanner_bigmovers.json").write_text(json.dumps(bm, indent=2))
    state_path("reports", "scanner_train.json").write_text(json.dumps(infos, indent=2, default=str))
    infos["self_improvement"] = nightly(selection)
    return infos


def load_model(style: str) -> ScannerModel:
    if not ScannerModel.exists(models_dir(), style):
        log.warning("no %s model yet - training it first", style)
        train([style])
    return ScannerModel.load(models_dir(), style)


def hourly(top_n: int = 5) -> dict:
    uni = select_universe()
    hist = track.load_history()
    results = {}
    failures = []
    for key in STYLES:
        try:  # one speed failing must never stop the others
            opened = hist[(hist["status"] == "open") & (hist["style"] == key)]["symbol"].tolist() if len(hist) else []
            res = scan(key, load_model(key), uni, top_n, extra_symbols=opened)
            track.append(res.payload["ideas"], res.payload["coins_scanned"])
            res.payload["settled_this_run"] = track.resolve(res.candles, key)
            results[key] = res.payload
            state_path("suggestions", f"latest_{key}.json").write_text(json.dumps(res.payload, indent=2, default=str))
        except Exception as e:  # noqa: BLE001
            log.exception("scanner speed %s failed", key)
            failures.append(f"{key}: {e}")
    if not results:
        raise RuntimeError("; ".join(failures))
    board = track.scoreboard()
    try:
        small_coins(top_n)
    except Exception as e:  # noqa: BLE001
        log.exception("small coins failed")
        failures.append(f"small coins: {e}")
    combined = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "styles": results, "scoreboard": board}
    state_path("suggestions", "latest.json").write_text(json.dumps(combined, indent=2, default=str))
    state_path("suggestions", "LATEST.md").write_text(render_markdown(combined))
    if failures:
        send("error", "scanner: " + "; ".join(failures))
    if os.environ.get("OMEGA_SCANNER_ALERTS", "1") == "1":
        alert_style = os.environ.get("OMEGA_SCANNER_ALERT_STYLE", DEFAULT_STYLE)
        send("info", render_telegram(results.get(alert_style) or next(iter(results.values())), board))
    return combined


SMALL_HIST = "suggestions/small_history.csv"


def small_universe() -> pd.DataFrame:
    """Small coins that often swing 20%+ within a week (90-day statistics from the nightly run)."""
    from scanner.live import big_movers

    cfg, bm = config(), big_movers()
    uni = select_small_universe()
    swing = uni["symbol"].map(lambda s: (bm.get(s) or {}).get("up_pct"))
    return uni[pd.to_numeric(swing, errors="coerce").fillna(0) >= cfg["small_min_swing"]].reset_index(drop=True)


def small_coins(top_n: int = 5) -> dict:
    """Hourly top 10 for the Small coins page, with its own track record."""
    cfg = config()
    uni = small_universe()
    if len(uni) < 5:
        log.warning("only %d small coins swing enough; skipped", len(uni))
        return {}
    hist = track.load_history(SMALL_HIST)
    out = {}
    for key in STYLES:
        opened = hist[(hist["status"] == "open") & (hist["style"] == key)]["symbol"].tolist() if len(hist) else []
        res = scan(key, load_model(key), uni, cfg["small_top_n"], extra_symbols=opened, candidates=15,
                       board="suggestions/small_scoreboard.json", probation_hist=SMALL_HIST)
        track.append(res.payload["ideas"], res.payload["coins_scanned"], SMALL_HIST)
        res.payload["settled_this_run"] = track.resolve(res.candles, key, SMALL_HIST)
        state_path("suggestions", f"small_latest_{key}.json").write_text(json.dumps(res.payload, indent=2, default=str))
        out[key] = res.payload
        if key == "day":                               # the $100 Small-coin challenge (paper, D97): never breaks the scan
            try:
                from scanner import challenge
                from scanner.data import candles as fetch_candles
                from scanner.improve import probation
                from scanner.quality import load as load_quality

                challenge.run(res.payload["ideas"], res.candles, probation=probation(track.load_history(SMALL_HIST)),
                              quality_check=(load_quality("suggestions/small_scoreboard.json") or {}).get("check"),
                              fetch_candles=fetch_candles)
            except Exception as e:  # noqa: BLE001
                log.warning("small challenge: %s", e)
    track.scoreboard(SMALL_HIST, "suggestions/small_scoreboard.json")
    return out


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.0%}"


def price(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.6g}"


def render_markdown(c: dict) -> str:
    t = pd.Timestamp(c["generated_at"]).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Trade ideas: {t}", "",
         "> **Paper / education only. Not financial advice.** Ideas are ranked by a computer, not promised. "
         "Coins that can rise 20% can also fall 20%. Never use money you can't afford to lose.", "",
         "> This page updates once an hour. **For live updates every 1–60 minutes and a monitor that tells you "
         "when to exit, use the dashboard** (see the README).", ""]
    for key, pl in c["styles"].items():
        mood = pl["market_mood"]
        L += [f"## {STYLES[key].label}", "",
              f"Market mood: **{mood['label']}** ({mood['share_positive']:.0%} of {pl['coins_scanned']} coins look "
              "positive). If it says *Unfavourable*, sitting out is a good choice.", "",
              "| # | Coin | Score | Grade | Risk | Buy near | Take profit | Safety exit | Sell by (UTC) |",
              "|---|---|---|---|---|---|---|---|---|"]
        for d in pl["ideas"]:
            L.append(f"| {d['rank']} | **{d['coin']}** | {d['score']}/10 | {d['grade']} | {d['risk_level']} | "
                     f"{price(d['price_now'])} | {price(d['take_profit'])} (+{d['take_profit_pct']:.1%}) | "
                     f"{price(d['safety_exit'])} ({d['safety_exit_pct']:.1%}) | "
                     f"{pd.Timestamp(d['exit_by']).strftime('%H:%M %d-%b')} |")
        L.append("")
        for d in pl["ideas"][:3]:
            L.append(f"- **{d['coin']}:** " + " ".join(d["why"]) +
                     (" ⚠️ " + " ".join(d["warnings"]) if d["warnings"] else ""))
        L.append("")
    b = c.get("scoreboard", {})
    L += ["---", "## Track record (how past ideas actually did)", ""]
    if b.get("closed"):
        L += ["| Speed | Ideas checked | Ended in profit | Avg per idea | Random pick avg |", "|---|---|---|---|---|"]
        for key, s in b.get("by_style", {}).items():
            if s.get("closed"):
                L.append(f"| {STYLES[key].label} | {s['closed']} | {s['win_rate']:.0%} | "
                         f"{s['avg_return_per_idea']:+.2%} | {s['random_pick_avg_return']:+.2%} |")
    else:
        L.append("Not enough history yet. Ideas are checked once their time limit has passed.")
    L += ["", "New to this? Read [the beginner's guide](../../docs/BEGINNERS_GUIDE.md)."]
    return "\n".join(L) + "\n"


def render_telegram(pl: dict, board: dict | None = None) -> str:
    lines = [f"Top ideas · {pl['style_label']} · mood {pl['market_mood']['label']} (paper only, not advice)"]
    for d in pl["ideas"]:
        lines.append(f"{d['rank']}. {d['coin']} {d['score']}/10 {d['grade']} · buy ~{price(d['price_now'])} → "
                     f"TP {price(d['take_profit'])} (+{d['take_profit_pct']:.1%}) / exit {price(d['safety_exit'])} "
                     f"({d['safety_exit_pct']:.1%}) · sell by {pd.Timestamp(d['exit_by']).strftime('%H:%M UTC')}")
    s = (board or {}).get("by_style", {}).get(pl["style"], {})
    if s.get("closed"):
        lines.append(f"Track record: {s['closed']} checked, {s['win_rate']:.0%} profitable, "
                     f"avg {s['avg_return_per_idea']:+.2%}/idea")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Top-5 coin scanner (paper / education only)")
    ap.add_argument("cmd", choices=["hourly", "train", "once"])
    ap.add_argument("--style", default=DEFAULT_STYLE, choices=list(STYLES))
    a = ap.parse_args()
    if a.cmd == "train":
        print(json.dumps(train(), indent=2, default=str))
    elif a.cmd == "once":
        res = scan(a.style, load_model(a.style))
        for d in res.payload["ideas"]:
            print(f"{d['rank']}. {d['coin']:<8} {d['score']:>4}/10 {d['grade']:<20} risk={d['risk_level']:<9} "
                  f"TP +{d['take_profit_pct']:.1%} / SL {d['safety_exit_pct']:.1%} · sell by {d['exit_by'][:16]}")
    else:
        c = hourly()
        for key, pl in c["styles"].items():
            print(key, [(d["coin"], d["score"]) for d in pl["ideas"]])


if __name__ == "__main__":
    main()
