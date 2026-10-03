"""Live scanning and trade monitoring. Used by the dashboard (refresh every 1/2/5/15/30/60 minutes,
or on demand) and by the hourly GitHub job.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import state_path
from core.log import get_logger
from scanner.data import last_prices, load_all, select_universe
from scanner.features import coin_features
from scanner.model import PT, SL, ScannerModel, risk_unit
from scanner.rank import apply_integrity, expected_r, market_mood, rank, score_from_r
from scanner.styles import STYLES, Style, human_duration

log = get_logger("scanner.live")


def models_dir() -> Path:
    return state_path("models", "x").parent


def big_movers() -> dict:
    p = state_path("reports", "scanner_bigmovers.json")
    return json.loads(p.read_text()) if p.exists() else {}


@dataclass
class ScanResult:
    payload: dict
    candles: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)   # symbol -> score (all scanned coins)


def latest_rows(candles: dict[str, pd.DataFrame], style: Style, symbols: list[str]) -> pd.DataFrame:
    btc = candles.get("BTCUSDT")
    rows = []
    for s in symbols:
        df = candles.get(s)
        if df is None or len(df) < 100:
            continue
        f = coin_features(df, btc).iloc[-1].copy()
        f["risk_unit"] = float(risk_unit(df, style).iloc[-1])
        f["close"] = float(df["close"].iloc[-1])
        f["low_24b"] = float(df["low"].iloc[-24:].min())
        f["ts"] = df.index[-1]
        rows.append(f.rename(s))
    return pd.DataFrame(rows)


def scan(style_key: str, model: ScannerModel, universe: pd.DataFrame | None = None, top_n: int = 5,
         extra_symbols: list[str] | None = None, integrity: bool = True, candidates: int = 10) -> ScanResult:
    style = STYLES[style_key]
    uni = universe if universe is not None else select_universe()
    syms = sorted(set(uni["symbol"]) | set(extra_symbols or []) | {"BTCUSDT"})
    candles = load_all(syms, style.live_bars, style.interval)
    latest = latest_rows(candles, style, list(uni["symbol"]))
    if latest.empty:
        raise RuntimeError("no market data")
    p = model.predict(latest)
    qv = dict(zip(uni["symbol"], uni["quoteVolume"]))
    from scanner.defence import load_adaptive

    ideas = rank(latest, p, qv, big_movers(), candidates if integrity else top_n, model.win_r, model.loss_r, style,
                 load_adaptive())
    if integrity:
        from scanner.integrity import run_checks

        try:
            feats = {d["symbol"]: latest.loc[d["symbol"]].to_dict() for d in ideas}
            res = run_checks([d["symbol"] for d in ideas], candles, uni, feats)
        except Exception as e:  # noqa: BLE001
            log.warning("fake-signal checks failed: %s", e)
            res = {}
        ideas = apply_integrity(ideas, res, top_n)
    ru = latest["risk_unit"].to_numpy()
    scores = {s: score_from_r(expected_r(pr, model.win_r, model.loss_r, r)) for s, pr, r in zip(latest.index, p, ru)}
    payload = {"generated_at": str(pd.Timestamp.now(tz="UTC")), "style": style.key, "style_label": style.label,
               "hold_text": style.hold_text, "coins_scanned": int(len(latest)),
               "market_mood": market_mood(p, model.win_r, model.loss_r, ru),
               "model": {**model.info, "base_rate": model.base_rate}, "ideas": ideas}
    return ScanResult(payload, candles, scores)


# ----------------------------------------------------------------------------------- trade monitor
def make_trade(idea: dict, entry: float | None = None, opened: pd.Timestamp | None = None) -> dict:
    """A trade the user says they took, following an idea's plan (entry defaults to the idea price)."""
    entry = float(entry or idea["price_now"])
    opened = opened or pd.Timestamp.now(tz="UTC")
    ru = float(idea["risk_unit"])
    tp_pct = idea.get("take_profit_pct", PT * ru)
    sl_pct = idea.get("safety_exit_pct", -SL * ru)
    return {"symbol": idea["symbol"], "style": idea["style"], "entry": entry,
            "take_profit": entry * (1 + tp_pct), "safety_exit": entry * (1 + sl_pct),
            "opened": str(opened.floor("s")),
            "exit_by": str((opened + pd.Timedelta(minutes=STYLES[idea["style"]].horizon_minutes)).floor("s"))}


def manual_trade(symbol: str, style_key: str, entry: float, opened: pd.Timestamp, risk_pct: float) -> dict:
    """User-entered trade: safety exit risk_pct below entry, take-profit twice as far above."""
    ru = risk_pct / 100.0
    return make_trade({"symbol": symbol, "style": style_key, "price_now": entry, "risk_unit": ru}, entry, opened)


def advise(trade: dict, price: float, now: pd.Timestamp | None = None, score: float | None = None) -> dict:
    """What to do with an open trade RIGHT NOW, in plain English."""
    now = now or pd.Timestamp.now(tz="UTC")
    tp, sl, entry = trade["take_profit"], trade["safety_exit"], trade["entry"]
    exit_by = pd.Timestamp(trade["exit_by"])
    left = exit_by - now
    pnl = price / entry - 1 - 0.002
    if price >= tp:
        action, level = "TAKE PROFIT NOW", "success"
        why = "The price reached your take-profit level. Selling now locks in the gain."
    elif price <= sl:
        action, level = "EXIT NOW (safety exit)", "error"
        why = "The price fell to your safety exit. Selling now keeps a small loss from becoming a big one."
    elif left <= pd.Timedelta(0):
        action, level = "TIME'S UP: SELL NOW", "warning"
        why = "This idea had a time limit and it has passed. The expected move didn't happen in time."
    elif score is not None and score < 4.5:
        action, level = "CONSIDER LEAVING EARLY", "warning"
        why = (f"The reasons for this trade have faded (current score {score:.1f}/10). "
               "Leaving early is reasonable; otherwise keep your safety exit in place.")
    else:
        action, level = "HOLD", "info"
        why = f"Neither exit has been reached. Check again soon; time left: {human_duration(left.total_seconds() / 60)}."
    to_tp = tp / price - 1
    to_sl = sl / price - 1
    progress = float(np.clip((price - sl) / (tp - sl), 0, 1)) if tp > sl else 0.5
    return {"action": action, "level": level, "why": why, "pnl_pct": pnl, "price": price,
            "to_take_profit_pct": to_tp, "to_safety_exit_pct": to_sl, "progress": progress,
            "minutes_left": max(left.total_seconds() / 60, 0.0)}


def current_prices(symbols: list[str]) -> dict[str, float]:
    return last_prices(symbols) if symbols else {}
