"""Scanner entry points.

  python -m scanner.run hourly   # rank top 5 ideas, settle old ones, write state/suggestions/LATEST.md
  python -m scanner.run train    # nightly: retrain the pooled model + 90-day big-mover stats
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
from scanner.data import daily_candles, load_all, select_universe
from scanner.features import big_mover_stats, coin_features
from scanner.model import ScannerModel, build_dataset, risk_unit

log = get_logger("scanner.run")
MODEL_DIR = state_path("models", "x").parent


def train(n_hours: int = 1000) -> dict:
    uni = select_universe()
    candles = load_all(uni["symbol"].tolist(), n_hours)
    data = build_dataset(candles)
    m = ScannerModel().fit(data)
    m.save(MODEL_DIR)
    bm = {}
    for s in candles:
        try:
            bm[s] = big_mover_stats(daily_candles(s, 97).iloc[:-1].tail(96))
        except Exception as e:  # noqa: BLE001
            log.warning("daily %s: %s", s, e)
    state_path("reports", "scanner_bigmovers.json").write_text(json.dumps(bm, indent=2))
    state_path("reports", "scanner_train.json").write_text(json.dumps(m.info, indent=2, default=str))
    return m.info


def hourly(top_n: int = 5) -> list[dict]:
    if not (MODEL_DIR / "scanner_model.txt").exists():
        log.warning("no scanner model yet - training first")
        train()
    m = ScannerModel.load(MODEL_DIR)
    uni = select_universe()
    hist = track.load_history()
    need = set(uni["symbol"]) | (set(hist.loc[hist["status"] == "open", "symbol"]) if len(hist) else set())
    candles = load_all(sorted(need), 240)
    btc = candles.get("BTCUSDT")
    rows = []
    for s in uni["symbol"]:
        df = candles.get(s)
        if df is None or len(df) < 200:
            continue
        f = coin_features(df, btc).iloc[-1].copy()
        f["risk_unit"] = float(risk_unit(df).iloc[-1])
        f["close"] = float(df["close"].iloc[-1])
        f["ts"] = df.index[-1]
        rows.append(f.rename(s))
    latest = pd.DataFrame(rows)
    p = m.predict(latest)
    bm_path = state_path("reports", "scanner_bigmovers.json")
    big = json.loads(bm_path.read_text()) if bm_path.exists() else {}
    qv = dict(zip(uni["symbol"], uni["quoteVolume"]))
    from scanner.rank import market_mood, rank

    mood = market_mood(p, m.win_r, m.loss_r)
    ideas = rank(latest, p, qv, big, top_n, m.win_r, m.loss_r, m.base_rate)
    settled = track.resolve(candles)
    track.append(ideas, len(latest))
    board = track.scoreboard()
    payload = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "coins_scanned": len(latest),
               "model": {**m.info, "base_rate": m.base_rate}, "market_mood": mood, "ideas": ideas, "scoreboard": board, "settled_this_run": settled}
    state_path("suggestions", "latest.json").write_text(json.dumps(payload, indent=2, default=str))
    state_path("suggestions", "LATEST.md").write_text(render_markdown(payload))
    if os.environ.get("OMEGA_SCANNER_ALERTS", "1") == "1":
        send("info", render_telegram(payload))
    log.info("scanner: %d coins, top=%s", len(latest), [d["coin"] for d in ideas])
    return ideas


def _pct(x) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.0%}"


def _price(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.6g}"


def render_markdown(pl: dict) -> str:
    t = pd.Timestamp(pl["generated_at"]).strftime("%Y-%m-%d %H:%M UTC")
    L = [f"# Top 5 trade ideas: {t}", "",
         "> **Paper / education only. Not financial advice.** These are *ideas ranked by the computer*, not "
         "promises. Coins that can rise 20% can also fall 20%. Never put in money you can't afford to lose.", "",
         f"Scanned **{pl['coins_scanned']} coins**. Each idea is a *buy*, with a planned exit either way.", "",
         f"**Market mood: {pl['market_mood']['label']}.** The computer sees a positive expected result for "
         f"{pl['market_mood']['share_positive']:.0%} of coins this hour. When the mood is *Unfavourable*, "
         "sitting out is a perfectly good choice.", ""]
    for d in pl["ideas"]:
        L += [f"## {d['rank']}. {d['coin']}: score {d['score']}/10 ({d['grade']}) · risk: {d['risk_level']}", "",
              "| | Price | Change |", "|---|---|---|",
              f"| Buy near | {_price(d['price_now'])} | |",
              f"| Take profit at | {_price(d['take_profit'])} | +{d['take_profit_pct']:.1%} |",
              f"| Safety exit at | {_price(d['safety_exit'])} | {d['safety_exit_pct']:.1%} |",
              "| Give up after | 24 hours | sell at whatever the price is |", "",
              f"- **Chance this idea ends in profit (computer's estimate):** {d['chance_of_profit']:.0%} "
              f"(average coin right now: {pl['market_mood']['avg_chance_of_profit']:.0%})",
              f"- **Sizing tip:** to risk only $10 on this idea, buy about **${d['size_for_10usd_risk']:,.0f}** worth.",
              f"- **Big-mover history (last 90 days):** within a week it rose 20%+ {_pct(d['week_up20_pct'])} of "
              f"the time and fell 20%+ {_pct(d['week_down20_pct'])} of the time.",
              "", "**Why it was picked:**"] + [f"- {w}" for w in d["why"]]
        if d["warnings"]:
            L += ["", "**⚠️ Be careful:**"] + [f"- {w}" for w in d["warnings"]]
        L.append("")
    b = pl.get("scoreboard", {})
    L += ["---", "## Track record (how past ideas actually did)", ""]
    if b.get("closed"):
        L += [f"- Ideas settled: **{b['closed']}** (still open: {b['still_open']})",
              f"- Win rate: **{b['win_rate']:.0%}**",
              f"- Average result per idea after fees: **{b['avg_return_per_idea']:+.2%}**",
              f"- Average if you had picked coins at random: {b['random_pick_avg_return']:+.2%}",
              f"- $100 in every idea would have made: **${b['if_100usd_each_total_pnl']:+,.0f}** in total"]
    else:
        L.append("Not enough history yet. Ideas are settled 24 hours after they're made.")
    mi = pl.get("model", {})
    if mi:
        L += ["", f"*Model test on past data: top-5 ideas won {_pct(mi.get('top5_win_rate'))} vs "
                  f"{_pct(mi.get('all_ideas_win_rate'))} for all coins; average {mi.get('top5_avg_net_ret', 0):+.2%} vs "
                  f"{mi.get('all_ideas_avg_net_ret', 0):+.2%} per idea. Past results do not guarantee future ones.*"]
    L += ["", "New to this? Read [the beginner's guide](../../docs/BEGINNERS_GUIDE.md)."]
    return "\n".join(L) + "\n"


def render_telegram(pl: dict) -> str:
    lines = [f"Top 5 ideas this hour (paper only, not advice) · market mood: {pl['market_mood']['label']}"]
    for d in pl["ideas"]:
        lines.append(f"{d['rank']}. {d['coin']} {d['score']}/10 {d['grade']} · risk {d['risk_level']} · "
                     f"buy ~{_price(d['price_now'])} → TP {_price(d['take_profit'])} (+{d['take_profit_pct']:.1%}) "
                     f"/ exit {_price(d['safety_exit'])} ({d['safety_exit_pct']:.1%}) · 24h")
    b = pl.get("scoreboard", {})
    if b.get("closed"):
        lines.append(f"Track record: {b['closed']} settled, win rate {b['win_rate']:.0%}, "
                     f"avg {b['avg_return_per_idea']:+.2%}/idea")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Hourly top-5 coin scanner (paper / education only)")
    ap.add_argument("cmd", choices=["hourly", "train"])
    a = ap.parse_args()
    if a.cmd == "train":
        print(json.dumps(train(), indent=2, default=str))
    else:
        for d in hourly():
            print(f"{d['rank']}. {d['coin']:<8} {d['score']:>4}/10 {d['grade']:<20} risk={d['risk_level']}")


if __name__ == "__main__":
    main()
