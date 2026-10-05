"""Turn model probabilities into a ranked, plain-English top-5 list for one trading speed."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.defence import adaptive_penalty, declutter_exits
from scanner.model import PT, SL
from scanner.quality import adjust_r
from scanner.styles import STYLES, Style

GRADES = [(6.5, "Strong"), (5.6, "Moderate"), (5.0, "Weak"), (-1, "Avoid - watch only")]


def risk_level(ru: float, style: Style = STYLES["day"]) -> str:
    # relative to the speed's own typical range
    span = style.max_risk - style.min_risk
    x = (ru - style.min_risk) / span
    return "Low" if x < 0.15 else "Medium" if x < 0.35 else "High" if x < 0.6 else "Very high"


def grade(score: float) -> str:
    return next(g for th, g in GRADES if score >= th)


def score_from_r(exp_r: float) -> float:
    return float(10 / (1 + np.exp(-6 * exp_r)))


def red_flags(f: pd.Series, quote_vol_24h: float, style: Style) -> tuple[float, list[str]]:
    """ASI-lite: penalties (in units of money risked) and plain-English warnings."""
    pen, warn = 0.0, []
    if f["ret_24b"] > np.log(1.30) or f["ret_1b"] > np.log(1.08):
        pen += 0.15
        warn.append(f"Already up {np.expm1(f['ret_24b']):.0%} in the last {style.bars_text(24)}. Chasing coins "
                    "that just spiked often ends in buying the top.")
    if f["volume_surge"] > 4 and abs(f["ret_24b"]) < 0.01:
        pen += 0.10
        warn.append("Trading activity is unusually high but the price isn't moving. That can be fake "
                    "(wash) trading.")
    if quote_vol_24h < 10e6:
        pen += 0.05
        warn.append("Fairly thin trading (under $10M a day). Prices can jump around and are easier to push.")
    if f["rsi_14"] > 80:
        pen += 0.05
        warn.append("Looks 'overheated' (RSI above 80). Short pullbacks are common after this.")
    if f["btc_ret_24b"] < np.log(0.97):
        warn.append(f"Bitcoin fell more than 3% in the last {style.bars_text(24)}. Most coins follow Bitcoin.")
    return pen, warn


def reasons(f: pd.Series, style: Style) -> list[str]:
    out: list[tuple[float, str]] = []
    if f["buy_pressure_6b"] > 0.02:
        out.append((f["buy_pressure_6b"] * 20, f"Buyers have been more eager than sellers over the last "
                                                f"{style.bars_text(6)} ({50 + 100 * f['buy_pressure_6b']:.0f}% "
                                                "of trades were buys)."))
    if f["ema24_dist"] > 0 and f["ema72_dist"] > 0:
        out.append((0.8, f"Price is above its average of the last {style.bars_text(24)} and of the last "
                         f"{style.bars_text(72)}, so the trend is up."))
    if f["rel_strength_24b"] > 0.01:
        out.append((f["rel_strength_24b"] * 30, f"It did {np.expm1(f['rel_strength_24b']):.1%} better than "
                                                  f"Bitcoin over the last {style.bars_text(24)}."))
    if f["volume_surge"] > 1.5:
        out.append((min(f["volume_surge"] / 3, 1.0), f"Trading activity is {f['volume_surge']:.1f}x higher than "
                                                      "usual, so people are paying attention to it."))
    if f["dist_low_168b"] < 0.03 and f["ret_4b"] > 0:
        out.append((0.6, f"It is bouncing up from near its lowest price of the last {style.bars_text(168)}."))
    if 45 <= f["rsi_14"] <= 65:
        out.append((0.3, "Momentum is healthy: not overheated, not collapsing."))
    if f["dist_high_168b"] > -0.02:
        out.append((0.5, f"It is close to its highest price of the last {style.bars_text(168)}. A breakout "
                         "is possible."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:3]] or ["The model sees a slightly better-than-usual pattern, with no single "
                                       "strong reason."]


def market_mood(p: np.ndarray, win_r: float, loss_r: float, ru: np.ndarray | None = None) -> dict:
    e = p * win_r - (1 - p) * loss_r - (FEE_R / ru if ru is not None else 0.0)
    share = float((e > 0).mean())
    label = "Favourable" if share > 0.6 else "Mixed" if share > 0.35 else "Unfavourable"
    return {"label": label, "share_positive": share, "avg_chance_beats_market": float(np.mean(p))}


FEE_R = 0.002  # round-trip fees as a fraction; divided by the safety-exit distance to get "per $1 risked"


def expected_r(prob: float, win_r: float, loss_r: float, ru: float = 0.02) -> float:
    """Expected result per $1 put at risk, fees included, ASSUMING THE MARKET AS A WHOLE GOES NOWHERE.
    The edge comes only from beating the typical coin. 0 = break-even. An estimate, not a promise."""
    return prob * win_r - (1 - prob) * loss_r - FEE_R / (SL * ru)


def rank(latest: pd.DataFrame, p: np.ndarray, quote_vol: dict[str, float], big_movers: dict[str, dict],
         top_n: int = 5, win_r: float = 1.0, loss_r: float = 1.0, style: Style = STYLES["day"],
         adaptive: dict | None = None) -> list[dict]:
    """``latest``: one row per symbol (features + risk_unit + close + ts + low_24b)."""
    ideas = []
    for (sym, f), prob in zip(latest.iterrows(), p):
        ru = float(f["risk_unit"])
        pen, warns = red_flags(f, quote_vol.get(sym, 0.0), style)
        apen, awarn = adaptive_penalty(f, adaptive or {})
        pen += apen
        warns += awarn
        adj_r = adjust_r(expected_r(prob, win_r, loss_r, ru), ru, (adaptive or {}).get("vol")) - pen
        score = score_from_r(adj_r)
        close = float(f["close"])
        bm = big_movers.get(sym, {})
        made = pd.Timestamp(f["ts"]) + pd.Timedelta(minutes=style.bar_minutes)   # candle close = decision time
        stop, target = declutter_exits(close, close * (1 - SL * ru), close * (1 + PT * ru),
                                       float(f.get("low_24b", np.nan)), ru, SL)
        ideas.append({
            "symbol": sym, "coin": sym[:-4], "style": style.key, "score": round(score, 1), "grade": grade(score),
            "chance_beats_market": round(float(prob), 3), "expected_r": round(float(adj_r), 3),
            "risk_level": risk_level(ru, style), "risk_unit": ru, "price_now": close,
            "take_profit": target, "take_profit_pct": target / close - 1,
            "safety_exit": stop, "safety_exit_pct": stop / close - 1,
            "hold_minutes": style.horizon_minutes, "hold_text": style.hold_text,
            "exit_by": str(made + pd.Timedelta(minutes=style.horizon_minutes)),
            "size_for_10usd_risk": 10 / (1 - stop / close),
            "pre_ret": float(f["ret_24b"]) if np.isfinite(f["ret_24b"]) else None,
            "vol_surge": float(f["volume_surge"]) if np.isfinite(f["volume_surge"]) else None,
            "why": reasons(f, style), "warnings": warns,
            "week_up20_pct": bm.get("up_pct"), "week_down20_pct": bm.get("down_pct"),
            "ts": str(f["ts"]), "suggested_at": str(made),
        })
    ideas.sort(key=lambda d: -d["score"])
    for i, d in enumerate(ideas[:top_n], 1):
        d["rank"] = i
    return ideas[:top_n]


def apply_integrity(ideas: list[dict], results: dict[str, dict], top_n: int = 5) -> list[dict]:
    """Lower each candidate's score by its fake-signal penalty, re-rank, keep the best ``top_n``."""
    for d in ideas:
        r = results.get(d["symbol"])
        d["integrity"] = r
        if r:
            d["expected_r"] = round(d["expected_r"] - r["penalty"], 3)
            d["score"] = round(score_from_r(d["expected_r"]), 1)
            d["grade"] = grade(d["score"])
    ideas.sort(key=lambda d: -d["score"])
    for i, d in enumerate(ideas, 1):
        d["rank"] = i
    return ideas[:top_n]


def apply_probation(ideas: list[dict], adaptive: dict, style_key: str) -> list[dict]:
    """A speed whose recent ideas did worse than random picks gets lower scores (self-improvement)."""
    pr = (adaptive.get("probation") or {}).get(style_key) or {}
    if pr.get("active"):
        for d in ideas:
            d["expected_r"] = round(d["expected_r"] - pr.get("penalty", 0.1), 3)
            d["score"] = round(score_from_r(d["expected_r"]), 1)
            d["grade"] = grade(d["score"])
            d["warnings"] = d["warnings"] + [f"This speed is on probation: its last {pr.get('n')} ideas did worse "
                                             "than picking coins at random, so its scores are lowered."]
        ideas.sort(key=lambda d: -d["score"])
    for i, d in enumerate(ideas, 1):
        d["rank"] = i
    return ideas
