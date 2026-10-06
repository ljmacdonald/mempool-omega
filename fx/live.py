"""Forex scan (GitHub Actions, every 15 minutes, Sunday evening to Friday evening New York time).

Every pair is ranked in both directions: a "sell" idea is scored on the inverted price (1/price), so one model
covers buying and selling. Snapshot -> .cache/fx_out/snapshot.json (published on the `data` branch); track
record (main list) -> state/fx/, about once an hour.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import requests

from core.config import REPO_ROOT, state_path
from core.log import get_logger
from dex.live import clean
from fx import checks as C
from fx.pairs import ALL, EXOTICS, METALS, NAMES, SWAP_PER_NIGHT, kind, label, pip, round_step, spread, yahoo
from scanner.features import coin_features
from scanner.model import PT, SL, ScannerModel, risk_unit
from scanner.quality import adjust_r
from scanner.quality import load as load_quality
from scanner.rank import grade, score_from_r
from scanner.styles import FX_STYLES
from stocks.data import load_all

log = get_logger("fx.live")
TOP_KEEP = 15
CHART_BARS = {"fx_today": 64, "fx_days": 96}


def out_path():
    d = REPO_ROOT / ".cache" / "fx_out"
    d.mkdir(parents=True, exist_ok=True)
    return d / "snapshot.json"


def invert(df: pd.DataFrame) -> pd.DataFrame:
    """The 'sell' view of a pair: price 1/x, so rising means the pair is falling."""
    out = pd.DataFrame({"open": 1 / df["open"], "high": 1 / df["low"], "low": 1 / df["high"], "close": 1 / df["close"]},
                       index=df.index)
    for c in ("volume", "qv", "taker_buy_volume"):
        out[c] = df[c]
    return out


def both_sides(cs: dict[str, pd.DataFrame], pairs: list[str]) -> dict[str, pd.DataFrame]:
    out = {}
    for p in pairs:
        df = cs.get(yahoo(p))
        if df is not None and len(df) >= 150:
            out[f"{p}:buy"], out[f"{p}:sell"] = df, invert(df)
    return out


def load_models() -> dict:
    from scanner.live import models_dir

    return {k: ScannerModel.load(models_dir(), k) for k in FX_STYLES if ScannerModel.exists(models_dir(), k)}


def news() -> list[dict]:
    """High-impact economic events this week and next (ForexFactory's free calendar), cached for 6 hours."""
    p = state_path("fx", "news.json")
    try:
        cached = json.loads(p.read_text())
        if pd.Timestamp.now(tz="UTC") - pd.Timestamp(cached["at"]) < pd.Timedelta(hours=6):
            return cached["events"]
    except (OSError, ValueError, KeyError):
        pass
    events = []
    for week in ("thisweek", "nextweek"):
        try:
            r = requests.get(f"https://nfs.faireconomy.media/ff_calendar_{week}.json", timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            if r.ok:
                events += [e for e in r.json() if e.get("impact") == "High"]
        except Exception as e:  # noqa: BLE001
            log.warning("calendar %s: %s", week, e)
    p.write_text(json.dumps({"at": str(pd.Timestamp.now(tz="UTC")), "events": events}, indent=1))
    return events


def market_open(t: pd.Timestamp) -> bool:
    ny = t.tz_convert("America/New_York")
    mins = ny.hour * 60 + ny.minute
    return not (ny.weekday() == 5 or (ny.weekday() == 4 and mins >= 17 * 60) or (ny.weekday() == 6 and mins < 17 * 60))


def sell_by(now: pd.Timestamp, key: str) -> pd.Timestamp:
    t = now + pd.Timedelta(minutes=FX_STYLES[key].horizon_minutes)
    ny = t.tz_convert("America/New_York")
    fri_close = (now.tz_convert("America/New_York") + pd.Timedelta(days=(4 - now.tz_convert("America/New_York").weekday()) % 7)
                 ).normalize().replace(hour=16)
    if t.tz_convert("America/New_York") > fri_close and ny.weekday() in (4, 5, 6):
        t = fri_close.tz_convert("UTC")              # close before the weekend gap
    return C.avoid_fix(t)


def declutter(pair: str, side: str, price: float, tp: float, sl: float, hunt: dict) -> tuple[float, float]:
    """Exits off the levels stop-hunters aim at: round levels (every 50 pips) and levels just swept."""
    step, buf = round_step(pair), pip(pair) * 5
    if side == "buy":
        rn = round(sl / step) * step
        if abs(sl - rn) <= buf:
            sl = rn - buf
        if hunt.get("swept_low") and abs(sl - hunt["swept_low"]["level"]) <= 3 * buf:
            sl = hunt["swept_low"]["level"] - buf
        g = np.ceil(tp / step) * step
        if 0 <= g - tp <= buf:
            tp = g - buf
    else:
        rn = round(sl / step) * step
        if abs(sl - rn) <= buf:
            sl = rn + buf
        g = np.floor(tp / step) * step
        if 0 <= tp - g <= buf:
            tp = g + buf
    return float(tp), float(sl)


def score_side(pair: str, side: str, df: pd.DataFrame, df_pair: pd.DataFrame, df15_pair: pd.DataFrame | None,
               key: str, m: ScannerModel, t: dict, events: list, now: pd.Timestamp, tri: dict,
               vol: dict | None = None) -> dict:
    st = FX_STYLES[key]
    f = coin_features(df, None).iloc[-1]
    p = float(m.predict(f.to_frame().T)[0])
    ru = float(risk_unit(df, st).iloc[-1])
    px = float(df_pair["close"].iloc[-1])
    checks, pen, hard = [], 0.0, []

    def chk(k, ok, text, penalty=0.0):
        checks.append({"key": k, "ok": ok, "text": text})
        return penalty if ok is False else 0.0

    end = sell_by(now, key)
    # 1. news inside the holding time / about to happen
    ev = C.news_in(events, pair, now, end)
    soon = C.news_in(events, pair, now, now + pd.Timedelta(minutes=t["news_before_min"]))
    if soon:
        hard.append(f"{soon[0]['title']} ({soon[0]['country']}) in the next {int(t['news_before_min'])} minutes. "
                    "Wait until it's out.")
    pen += chk("news", not ev, ("High-impact news inside the holding time: " + "; ".join(
        f"{e['title']} ({e['country']}, {e['at'].strftime('%a %H:%M')} UTC)" for e in ev[:2]) + ". Prices can jump "
        "1-2% in seconds and spreads explode.") if ev else "No major announcements for these currencies before the "
        "sell-by time.", PEN_NEWS[key])
    # 2. fix windows
    now_fix = C.in_fix(now)
    pen += chk("fix_now", not now_fix, f"Inside the {now_fix} window: banks were fined for pushing prices here. "
               "Wait until it's over." if now_fix else "Not inside a rate-fixing window (London 4 pm, ECB, Tokyo).",
               C.PEN["in_fix"])
    spikes = C.fix_spikes(df15_pair, t) if df15_pair is not None else []
    pen += chk("fix_spike", not spikes, (f"Unusual spike-and-reverse at the {spikes[-1]['fix']} recently "
               f"({spikes[-1]['move']:+.2%}): the footprint of fix manipulation.") if spikes else
               "No abnormal moves around recent fixes.", C.PEN["fix_spike"])
    # 3. stop hunts / failed breakouts (on this side's view of the price)
    hunt = C.stop_hunt(df, t)
    if hunt["failed_high"]:
        pen += chk("failed_breakout", False, f"A push {'above' if side == 'buy' else 'below'} a recent "
                   f"{'high' if side == 'buy' else 'low'} {hunt['failed_high']['bars_ago']} candles ago failed and "
                   "reversed: often a trap to catch breakout buyers.", C.PEN["failed_breakout"])
    else:
        chk("failed_breakout", True, "No failed breakout against this direction.")
    if hunt["swept_low"]:
        pen += chk("stop_hunt", False, f"Stops just {'below a recent low' if side == 'buy' else 'above a recent high'} "
                   f"were triggered {hunt['swept_low']['bars_ago']} candles ago and the price snapped back (a stop "
                   "hunt). Your safety exit is kept away from that level.", C.PEN["stop_hunt"])
    # 4. thin market
    thin = C.thin_market(now)
    pen += chk("thin", not thin, thin or "Normal trading hours: enough liquidity.", C.PEN["thin"])
    # 5. price cross-check
    dev = tri.get(pair)
    if dev is not None and abs(dev) > t["tri_dev"]:
        hard.append(f"Its price is {dev:+.2%} away from the price implied by the related pairs: a broken or off-market "
                    "quote.")
    elif dev is not None:
        chk("cross_check", True, "Price agrees with the related pairs.")
    managed = C.managed_note(pair)
    warns = [managed] if managed else []
    if kind(pair) == "exotic":
        warns.append("Exotic pair: shown for information. Spreads are often 0.3-1% or more and prices can be "
                     "steered by the central bank.")
    nights = max(int(st.horizon_minutes // (24 * 60)), 0) if key == "fx_days" else 0
    cost = spread(pair) + nights * SWAP_PER_NIGHT
    r = adjust_r(p * m.win_r - (1 - p) * m.loss_r - cost / (SL * ru), ru, vol) - pen
    sc = score_from_r(r)
    # exits in the pair's own price
    if side == "buy":
        tp, sl = px * (1 + PT * ru), px * (1 - SL * ru)
    else:
        tp, sl = px / (1 + PT * ru), px / (1 - SL * ru)
    tp, sl = declutter(pair, side, px, tp, sl, hunt)
    n = CHART_BARS[key]
    return {"pair": pair, "label": label(pair), "side": side, "kind": kind(pair), "style": key,
            "headline": C.side_text(pair, side), "p": round(p, 4), "risk_unit": round(ru, 5), "penalty": round(pen, 4),
            "expected_r": round(r, 4), "score": round(sc, 2), "grade": grade(sc), "price": px, "take_profit": tp,
            "safety_exit": sl, "take_profit_pct": tp / px - 1, "safety_exit_pct": sl / px - 1, "spread": spread(pair),
            "swap_nights": nights, "cost": cost, "hard": hard, "checks": checks, "warnings": warns,
            "sell_by": str(end), "pip": pip(pair), "win_r": m.win_r, "loss_r": m.loss_r, "candle_time": str(df.index[-1]),
            "pre_ret": float(f["ret_24b"]) if np.isfinite(f["ret_24b"]) else None,
            "why": why(f, st, side), "news": [{"title": e["title"], "country": e["country"], "at": str(e["at"])} for e in ev[:3]],
            "chart": {"t": [int(x.timestamp()) for x in df_pair.index[-n:]],
                      **{k: [float(v) for v in df_pair[k].iloc[-n:]] for k in ("open", "high", "low", "close")}}}


PEN_NEWS = {"fx_today": C.PEN["news_today"], "fx_days": C.PEN["news_days"]}


def why(f: pd.Series, st, side: str) -> list[str]:
    word = "rising" if side == "buy" else "falling"
    out: list[tuple[float, str]] = []
    if f["ema24_dist"] > 0 and f["ema72_dist"] > 0:
        out.append((0.8, f"The pair has been {word} steadily: price is on the right side of its averages of the last "
                         f"{st.bars_text(24)} and {st.bars_text(72)}."))
    if f["dist_low_168b"] < 0.01 and f["ret_4b"] > 0:
        out.append((0.6, f"It is turning from an extreme of the last {st.bars_text(168)}."))
    if 45 <= f["rsi_14"] <= 65:
        out.append((0.3, "Momentum is healthy: not overstretched."))
    if f["dist_high_168b"] > -0.003:
        out.append((0.5, f"It is near the edge of its range of the last {st.bars_text(168)}: a breakout is possible."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:3]] or ["The model sees a slightly better-than-usual pattern, with no single strong reason."]


FX_GROUPS = ["Major pairs", "Crosses", "Gold & silver", "Exotics"]


def fx_group(sym: str) -> str:
    """Market a track-record symbol ("EURUSD:buy") belongs to, for the grade tables."""
    return {"major": "Major pairs", "cross": "Crosses", "metal": "Gold & silver", "exotic": "Exotics"}[kind(sym.split(":")[0])]


GATE_METAL = {"XAUUSD": "XAU_USDT", "XAGUSD": "XAG_USDT"}


def metal_basis(cs15: dict, perp_closes=None) -> dict:
    """The gap between the futures price the ideas are built on (Yahoo GC=F / SI=F) and the round-the-clock gold and
    silver contracts the page reads live (Bitget, Gate.io, OKX), measured on the same finished 15-minute candles. The page
    multiplies its live price by this, so live prices, entries and the take-profit / safety-exit levels share one scale."""
    out = {}
    for pair, contract in GATE_METAL.items():
        df = cs15.get(yahoo(pair))
        if df is None or len(df) < 6:
            continue
        try:
            perp = perp_closes(contract) if perp_closes else gate_closes(contract)
        except Exception as e:  # noqa: BLE001
            log.warning("metal basis %s: %s", pair, e)
            continue
        fut = df["close"].iloc[-6:-1]                          # finished candles only
        ratios = [float(fut[t] / perp[t]) for t in fut.index if t in perp.index and perp[t] > 0]
        if len(ratios) < 2:
            continue
        b = float(np.median(ratios))
        if abs(b - 1) < 0.03:
            out[pair] = {"basis": b, "candles": len(ratios), "at": str(fut.index[-1])}
    return out


def gate_closes(contract: str) -> pd.Series:
    r = requests.get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                     params={"contract": contract, "interval": "15m", "limit": 12}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    return pd.Series({pd.Timestamp(int(x["t"]), unit="s", tz="UTC"): float(x["c"]) for x in rows}).sort_index()


def run(track_now: bool | None = None) -> dict:
    from scanner import track

    now = pd.Timestamp.now(tz="UTC")
    t = C.thresholds(C_seed())
    quality_board = load_quality("fx/scoreboard.json")       # grade check + jumpiness adjustment from the track record
    vol = (quality_board or {}).get("vol")
    events = news()
    models = load_models()
    lists: dict = {"main": {}, "exotic": {}}
    last_close = {}
    track_now = market_open(now) if track_now is None else track_now
    cs15 = load_all([yahoo(p) for p in ALL], "15m", 400)
    for key, st in FX_STYLES.items():
        m = models.get(key)
        if m is None:
            log.warning("no model for %s yet", key)
            continue
        cs = cs15 if st.interval == "15m" else load_all([yahoo(p) for p in ALL], st.interval, 400)
        if st.interval == "15m":
            last_close = {p: float(cs[yahoo(p)]["close"].iloc[-1]) for p in ALL if p not in METALS and yahoo(p) in cs}
        tri = C.triangular(last_close)
        rows = {"main": [], "exotic": []}
        for p in ALL:
            df_pair = cs.get(yahoo(p))
            if df_pair is None or len(df_pair) < 150:
                continue
            for side, df in (("buy", df_pair), ("sell", invert(df_pair))):
                try:
                    rows["exotic" if p in EXOTICS else "main"].append(
                        score_side(p, side, df, df_pair, cs15.get(yahoo(p)), key, m, t, events, now, tri, vol))
                except Exception as e:  # noqa: BLE001
                    log.warning("fx %s %s %s: %s", key, p, side, e)
        for lk, rs in rows.items():
            rs.sort(key=lambda d: (bool(d["hard"]), -d["score"]))
            lists[lk][key] = rs[:TOP_KEEP]
        if track_now and rows["main"]:
            hist = "fx/history.csv"
            ok = [d for d in rows["main"] if not d["hard"]][:5]
            insts = both_sides(cs, [d["pair"] for d in ok] + [x for x in ALL if x not in EXOTICS])
            ideas = [{"ts": d["candle_time"], "style": key, "rank": i + 1, "symbol": f"{d['pair']}:{d['side']}",
                      "score": d["score"], "grade": d["grade"], "chance_beats_market": d["p"], "risk_unit": d["risk_unit"],
                      "price_now": d["price"] if d["side"] == "buy" else 1 / d["price"],
                      "take_profit_pct": PT * d["risk_unit"], "safety_exit_pct": -SL * d["risk_unit"],
                      "pre_ret": d["pre_ret"], "cost_rt": d["cost"]} for i, d in enumerate(ok)]
            track.append(ideas, len(rows["main"]), hist, min_gap_minutes=45)
            track.resolve(insts, key, hist)
            track.scoreboard(hist, "fx/scoreboard.json", FX_STYLES, fx_group, FX_GROUPS)
    upcoming = sorted([{"title": e["title"], "country": e["country"], "at": str(pd.Timestamp(e["date"]).tz_convert("UTC"))}
                       for e in events if pd.Timestamp(e["date"]).tz_convert("UTC") >= now - pd.Timedelta(hours=1)],
                      key=lambda e: e["at"])[:25]
    movers = []
    for p in ALL:
        df = cs15.get(yahoo(p))
        if df is not None and len(df) > 100:
            day = df[df.index >= df.index[-1] - pd.Timedelta(hours=24)]
            movers.append({"pair": p, "label": label(p), "kind": kind(p), "chg": float(day["close"].iloc[-1] / day["open"].iloc[0] - 1)})
    movers.sort(key=lambda x: -abs(x["chg"]))
    payload = {"generated_at": str(now), "open": market_open(now), "quality": quality_board, "fix_windows": [
                   {"name": n, "start": str(a), "end": str(b)} for n, a, b in sorted(C.fix_windows(now), key=lambda w: w[1]) if b >= now][:5],
               "thin": C.thin_market(now), "events": upcoming, "lists": lists, "movers": movers[:20],
               "styles": {k: {"label": s.label, "interval": s.interval, "hold_text": "4 hours" if k == "fx_today" else "3 days"}
                          for k, s in FX_STYLES.items()},
               "models": {k: {"info": m.info} for k, m in models.items()}, "names": NAMES,
               "swap_per_night": SWAP_PER_NIGHT, "metal_basis": metal_basis(cs15)}
    out_path().write_text(json.dumps(clean(payload), separators=(",", ":"), default=str, allow_nan=False))
    log.info("fx: %s", {lk: {k: [f"{d['side']} {d['pair']}" for d in v[:5]] for k, v in by.items()} for lk, by in lists.items()})
    return payload


def C_seed() -> int:
    from dex.security import run_seed

    return run_seed() ^ int(os.environ.get("OMEGA_FX_SALT", "7919"))

