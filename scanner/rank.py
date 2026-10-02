"""Turn model probabilities into a ranked, plain-English top-5 list."""
from __future__ import annotations

import numpy as np
import pandas as pd

from scanner.model import PT, SL

GRADES = [(6.5, "Strong"), (5.6, "Moderate"), (5.0, "Weak"), (-1, "Avoid - watch only")]


def risk_level(ru: float) -> str:
    if ru < 0.03:
        return "Low"
    if ru < 0.06:
        return "Medium"
    if ru < 0.09:
        return "High"
    return "Very high"


def grade(score: float) -> str:
    return next(g for th, g in GRADES if score >= th)


def red_flags(f: pd.Series, quote_vol_24h: float) -> tuple[float, list[str]]:
    """ASI-lite: penalties (in R units) and plain-English warnings for manipulation-prone situations."""
    pen, warn = 0.0, []
    if f["ret_24h"] > np.log(1.30) or f["ret_1h"] > np.log(1.08):
        pen += 0.15
        warn.append(f"Already up {np.expm1(f['ret_24h']):.0%} in 24h. Chasing coins that just spiked often ends in "
                    "buying the top.")
    if f["volume_surge"] > 4 and abs(f["ret_24h"]) < 0.01:
        pen += 0.10
        warn.append("Trading activity is unusually high but the price isn't moving. That can be fake "
                    "(wash) trading.")
    if quote_vol_24h < 10e6:
        pen += 0.05
        warn.append("Fairly thin trading (under $10M a day). Prices can jump around and are easier to push.")
    if f["rsi_14"] > 80:
        pen += 0.05
        warn.append("Looks 'overheated' (RSI above 80). Short pullbacks are common after this.")
    if f["btc_ret_24h"] < np.log(0.95):
        warn.append("The whole crypto market (Bitcoin) fell more than 5% in the last day. Most coins follow Bitcoin.")
    return pen, warn


def reasons(f: pd.Series) -> list[str]:
    """Pick the clearest plain-English reasons behind the idea."""
    out: list[tuple[float, str]] = []
    if f["buy_pressure_6h"] > 0.02:
        out.append((f["buy_pressure_6h"] * 20, f"Buyers have been more eager than sellers over the last 6 hours "
                                                f"({50 + 100 * f['buy_pressure_6h']:.0f}% of trades were buys)."))
    if f["ema24_dist"] > 0 and f["ema72_dist"] > 0:
        out.append((0.8, "Price is above its 1-day and 3-day average, so the short-term trend is up."))
    if f["rel_strength_24h"] > 0.02:
        out.append((f["rel_strength_24h"] * 20, f"It did {np.expm1(f['rel_strength_24h']):.1%} better than Bitcoin "
                                                  "over the last day (it's stronger than the market)."))
    if f["volume_surge"] > 1.5:
        out.append((min(f["volume_surge"] / 3, 1.0), f"Trading activity is {f['volume_surge']:.1f}x higher than "
                                                      "a normal day, so people are paying attention to it."))
    if f["dist_low_7d"] < 0.05 and f["ret_4h"] > 0:
        out.append((0.6, "It is bouncing up from near its lowest price of the week."))
    if 45 <= f["rsi_14"] <= 65:
        out.append((0.3, "Momentum is healthy: not overheated, not collapsing."))
    if f["dist_high_7d"] > -0.03:
        out.append((0.5, "It is trading close to its highest price of the week. A breakout above it is possible."))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:3]] or ["The model sees a slightly better-than-usual pattern, with no single "
                                       "strong reason."]


def market_mood(p: np.ndarray, win_r: float, loss_r: float) -> dict:
    """How the computer rates the market overall this hour (average over every coin scanned)."""
    e = p * win_r - (1 - p) * loss_r
    share = float((e > 0).mean())
    label = "Favourable" if share > 0.6 else "Mixed" if share > 0.35 else "Unfavourable"
    return {"label": label, "share_positive": share, "avg_chance_of_profit": float(np.mean(p))}


def rank(latest: pd.DataFrame, p: np.ndarray, quote_vol: dict[str, float], big_movers: dict[str, dict],
         top_n: int = 5, win_r: float = 1.0, loss_r: float = 1.0, base_rate: float = 0.5) -> list[dict]:
    """``latest``: one row per symbol (features + risk_unit + close + ts)."""
    ideas = []
    for (sym, f), prob in zip(latest.iterrows(), p):
        ru = float(f["risk_unit"])
        # Expected result per $1 put at risk, fees included (from the model's chance and the typical size
        # of past wins/losses). 0 = break-even. It is an estimate, not a promise.
        exp_r = prob * win_r - (1 - prob) * loss_r
        pen, warns = red_flags(f, quote_vol.get(sym, 0.0))
        adj_r = exp_r - pen
        score = float(10 / (1 + np.exp(-6 * adj_r)))
        close = float(f["close"])
        bm = big_movers.get(sym, {})
        ideas.append({
            "symbol": sym, "coin": sym[:-4], "score": round(score, 1), "grade": grade(score),
            "chance_of_profit": round(float(prob), 3), "expected_r": round(float(adj_r), 3),
            "risk_level": risk_level(ru), "risk_unit": ru,
            "price_now": close,
            "take_profit": close * (1 + PT * ru), "take_profit_pct": PT * ru,
            "safety_exit": close * (1 - SL * ru), "safety_exit_pct": -SL * ru,
            "time_limit_hours": 24,
            "size_for_10usd_risk": 10 / (SL * ru),
            "why": reasons(f), "warnings": warns,
            "week_up20_pct": bm.get("up_pct"), "week_down20_pct": bm.get("down_pct"),
            "ts": str(f["ts"]),
        })
    ideas.sort(key=lambda d: -d["score"])
    for i, d in enumerate(ideas[:top_n], 1):
        d["rank"] = i
    return ideas[:top_n]
