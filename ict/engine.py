"""ICT (Inner Circle Trader) setups as exact, testable rules. site/ictengine.js is a line-by-line port (tests check
they agree).

A setup (described for a BUY; a SELL is the same rules on the price turned upside down):
  1. Liquidity sweep: price trades below a level where sell stops sit (previous day low, Asian session low, equal
     lows or a recent swing low) and closes back above it within 3 candles.
  2. Market structure shift with displacement: after the sweep, a candle closes above the last swing high, with at
     least one strong candle (body >= 1 x ATR) in the move.
  3. Fair value gap (FVG) left by that move: entry is a limit order at the middle of the gap ("consequent
     encroachment"). Stop just below the sweep low. Target: the nearest untaken buy-side liquidity (previous day
     high, equal highs, a swing high) at least 2x the risk away, else exactly 2x the risk.
Confluences that are counted, not required: 1-hour structure agrees, entry in discount (lower half of the last
24 h range), FVG inside the optimal trade entry zone (62-79% retracement), FVG overlapping the order block, kill zone,
silver bullet hour, SMT divergence with a correlated market, a major liquidity level swept, entry below the New York
midnight open.

Outcome (simulate): limit fill within 16 candles, else expired; target hit before fill = missed; after the fill the
stop is checked first in every candle (conservative); time limit 96 candles. Results are in R (multiples of the
risk) after the round-trip cost.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

P = {"piv": 2, "struct_look": 60, "liq_look": 96, "reclaim": 3, "disp_atr": 1.0, "fvg_min_atr": 0.1,
     "stop_atr": 0.1, "max_sweep_atr": 2.0, "min_rr": 2.0, "max_rr": 6.0, "eq_atr": 0.1, "range_look": 96,
     "tgt_look": 192, "fill_bars": 16, "hold_bars": 96, "min_risk_cost": 1.0, "smt_look": 24}
FACTORS = ["htf", "discount", "ote", "ob", "killzone", "silver", "smt", "major", "midnight"]
LEVEL_RANK = {"previous day low": 4, "Asian session low": 3, "equal lows": 2, "recent swing low": 1}
FLIP = {"previous day low": "previous day high", "Asian session low": "Asian session high", "equal lows": "equal highs",
        "recent swing low": "recent swing high", "previous day high": "previous day low", "equal highs": "equal lows",
        "recent swing high": "recent swing low", "2x risk": "2x risk"}


# ---------------------------------------------------------------------------------------------- arrays & time
def arrays(df: pd.DataFrame) -> dict:
    ny = df.index.tz_convert("America/New_York")
    return {"t": df.index.as_unit("ms").asi8.astype(np.int64), "o": df["open"].to_numpy(float), "h": df["high"].to_numpy(float),
            "l": df["low"].to_numpy(float), "c": df["close"].to_numpy(float),
            "day": (ny.year * 10000 + ny.month * 100 + ny.day).to_numpy(np.int64), "mins": (ny.hour * 60 + ny.minute).to_numpy(np.int64)}


def flip(a: dict) -> dict:
    """Upside-down prices: a SELL setup on `a` is a BUY setup on flip(a)."""
    return {**a, "o": -a["o"], "h": -a["l"], "l": -a["h"], "c": -a["c"]}


def atr(a: dict, n: int = 14) -> np.ndarray:
    h, lo, c = a["h"], a["l"], a["c"]
    pc = np.r_[np.nan, c[:-1]]
    tr = np.fmax(h - lo, np.fmax(np.abs(h - pc), np.abs(lo - pc)))
    tr[0] = h[0] - lo[0]
    out = np.full(len(h), np.nan)
    for i in range(n - 1, len(h)):
        out[i] = tr[i - n + 1:i + 1].mean()
    return out


def pivots(a: dict, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Swing highs / lows: strictly beyond the k candles on each side. Known only k candles later."""
    h, lo = a["h"], a["l"]
    n = len(h)
    ph, pl = np.zeros(n, bool), np.zeros(n, bool)
    for j in range(k, n - k):
        hs = np.r_[h[j - k:j], h[j + 1:j + k + 1]]
        ls = np.r_[lo[j - k:j], lo[j + 1:j + k + 1]]
        ph[j] = h[j] > hs.max()
        pl[j] = lo[j] < ls.min()
    return ph, pl


def sessions(a: dict) -> dict:
    """Per candle: previous New York day's high/low, the Asian session range (8 pm-midnight New York the evening
    before) and the New York midnight open."""
    n = len(a["t"])
    pdh, pdl, ah, al, mo = (np.full(n, np.nan) for _ in range(5))
    days = []
    for i in range(n):
        if not days or days[-1][0] != a["day"][i]:
            days.append([a["day"][i], i, i])
        days[-1][2] = i
    info = {}
    for d, s, e in days:
        info[d] = (a["h"][s:e + 1].max(), a["l"][s:e + 1].min(), s, e)
    for idx, (_, s, e) in enumerate(days):
        prev = days[idx - 1] if idx > 0 else None
        if prev is not None:
            pdh[s:e + 1], pdl[s:e + 1] = info[prev[0]][0], info[prev[0]][1]
            m = [i for i in range(prev[1], prev[2] + 1) if a["mins"][i] >= 20 * 60]
            if m:
                ah[s:e + 1] = max(a["h"][i] for i in m)
                al[s:e + 1] = min(a["l"][i] for i in m)
        mo[s:e + 1] = a["o"][s]
    return {"pdh": pdh, "pdl": pdl, "ah": ah, "al": al, "mo": mo}


def htf_bias(a1h: dict | None, t15: np.ndarray, bar_ms: int = 900_000) -> np.ndarray:
    """1-hour structure for each 15-minute candle: +1 after the last close above a 1-hour swing high, -1 after the
    last close below a 1-hour swing low, 0 if neither yet. Uses only 1-hour candles that had closed."""
    out = np.zeros(len(t15))
    if a1h is None or len(a1h["t"]) < 10:
        return out
    k = 2
    ph, pl = pivots(a1h, k)
    n = len(a1h["t"])
    bias = np.zeros(n)
    last_h = last_l = np.nan
    b = 0
    for i in range(n):
        j = i - k                       # a pivot at j is known once candle i = j + k has closed
        if j >= 0 and ph[j]:
            last_h = a1h["h"][j]
        if j >= 0 and pl[j]:
            last_l = a1h["l"][j]
        if not math.isnan(last_h) and a1h["c"][i] > last_h:
            b, last_h = 1, np.nan
        elif not math.isnan(last_l) and a1h["c"][i] < last_l:
            b, last_l = -1, np.nan
        bias[i] = b
    close_1h = a1h["t"] + 3_600_000
    j = 0
    cur = 0.0
    for i in range(len(t15)):
        while j < n and close_1h[j] <= t15[i] + bar_ms:
            cur = bias[j]
            j += 1
        out[i] = cur
    return out


def align(a: dict, b: dict | None) -> np.ndarray | None:
    """Positions in b of each of a's candle times (-1 if missing)."""
    if b is None:
        return None
    pos = {int(t): i for i, t in enumerate(b["t"])}
    return np.array([pos.get(int(t), -1) for t in a["t"]])


# ---------------------------------------------------------------------------------------------- detection
def detect_buys(a: dict, a1h: dict | None = None, corr: dict | None = None, cost: float = 0.0, start: int = 0,
                p: dict | None = None) -> list[dict]:
    p = {**P, **(p or {})}
    h, lo, o, c = a["h"], a["l"], a["o"], a["c"]
    n = len(h)
    k = p["piv"]
    ph, pl = pivots(a, k)
    at = atr(a)
    ses = sessions(a)
    bias = htf_bias(a1h, a["t"])
    cpos = align(a, corr)
    out = []
    for m in range(max(start, 30), n):
        if math.isnan(at[m]):
            continue
        # structure high: the most recent swing high confirmed before m
        j = -1
        for q in range(m - 1 - k, max(m - p["struct_look"], k) - 1, -1):
            if ph[q]:
                j = q
                break
        if j < 0:
            continue
        H = h[j]
        if not (c[m] > H and c[m - 1] <= H):
            continue
        seg = lo[j + 1:m]
        if len(seg) == 0:
            continue
        s = j + 1 + int(np.argmin(seg))
        sl_px = lo[s]
        atr_s = at[s] if not math.isnan(at[s]) else at[m]
        reclaim_end = min(s + p["reclaim"], m)
        reclaim_px = c[s:reclaim_end + 1].max()
        cands = []
        for name, lv in (("previous day low", ses["pdl"][s]), ("Asian session low", ses["al"][s])):
            if not math.isnan(lv) and sl_px < lv < reclaim_px and lv - sl_px <= p["max_sweep_atr"] * atr_s:
                cands.append((LEVEL_RANK[name], name, lv))
        for q in range(s - 1 - k, max(s - p["liq_look"], k) - 1, -1):
            if pl[q] and sl_px < lo[q] < reclaim_px and lo[q] - sl_px <= p["max_sweep_atr"] * atr_s \
                    and (q + 1 >= s or lo[q + 1:s].min() >= lo[q]):
                eq = any(pl[r] and abs(lo[r] - lo[q]) <= p["eq_atr"] * atr_s
                         for r in range(max(s - p["liq_look"], k), s - k) if r != q)
                name = "equal lows" if eq else "recent swing low"
                cands.append((LEVEL_RANK[name], name, lo[q]))
                break
        if not cands:
            continue
        cands.sort(key=lambda x: -x[0])
        _, lvl_name, lvl = cands[0]
        if (c[s + 1:m + 1] - o[s + 1:m + 1]).max(initial=-np.inf) < p["disp_atr"] * at[m]:
            continue
        best = None
        for q in range(s + 2, m + 1):
            gap = lo[q] - h[q - 2]
            if gap >= p["fvg_min_atr"] * at[m] and (best is None or gap > best[0]):
                best = (gap, q)
        if best is None:
            continue
        fq = best[1]
        f_top, f_bot = lo[fq], h[fq - 2]
        entry = (f_top + f_bot) / 2
        stop = sl_px - p["stop_atr"] * atr_s
        risk = entry - stop
        if risk <= 0 or risk < p["min_risk_cost"] * cost * abs(entry):
            continue
        # target: nearest untaken buy-side liquidity at least min_rr away
        tg = []
        if not math.isnan(ses["pdh"][m]) and ses["pdh"][m] > h[s:m + 1].max():
            tg.append((ses["pdh"][m], "previous day high"))
        for q in range(m - k, max(m - p["tgt_look"], k) - 1, -1):
            if ph[q] and h[q + 1:m + 1].max() < h[q]:
                eq = any(ph[r] and r != q and abs(h[r] - h[q]) <= p["eq_atr"] * at[m]
                         for r in range(max(m - p["tgt_look"], k), m - k + 1))
                tg.append((h[q], "equal highs" if eq else "recent swing high"))
        tg = sorted(x for x in tg if x[0] >= entry + p["min_rr"] * risk)
        if tg and tg[0][0] <= entry + p["max_rr"] * risk:
            target, t_name = tg[0]
        else:
            target, t_name = entry + p["min_rr"] * risk, "2x risk"
        leg_hi = h[s:m + 1].max()
        z_top = leg_hi - 0.62 * (leg_hi - sl_px)
        z_bot = leg_hi - 0.79 * (leg_hi - sl_px)
        ob = False
        for q in range(fq - 2, max(s - 2, 0) - 1, -1):
            if c[q] < o[q]:
                ob = lo[q] <= f_top and h[q] >= f_bot
                break
        lo_r = max(0, m - p["range_look"] + 1)
        mid = (h[lo_r:m + 1].max() + lo[lo_r:m + 1].min()) / 2
        mins = a["mins"][m]
        smt = False
        if cpos is not None and s - p["smt_look"] >= 0:
            ours_prev = lo[s - p["smt_look"]:s].min()
            ci = [cpos[i] for i in range(s - p["smt_look"], m + 1)]
            if all(x >= 0 for x in ci):
                cl = corr["l"]
                prev_c = min(cl[x] for x in ci[:p["smt_look"]])
                now_c = min(cl[x] for x in ci[p["smt_look"]:])
                smt = sl_px < ours_prev and now_c > prev_c
        conf = {
            "htf": bias[m] > 0,
            "discount": entry < mid,
            "ote": f_bot <= z_top and f_top >= z_bot,
            "ob": ob,
            "killzone": (120 <= mins < 300) or (420 <= mins < 600) or (600 <= mins < 720),
            "silver": (180 <= mins < 240) or (600 <= mins < 660) or (840 <= mins < 900),
            "smt": smt,
            "major": lvl_name != "recent swing low",
            "midnight": not math.isnan(ses["mo"][m]) and entry < ses["mo"][m],
        }
        out.append({"m": m, "s": s, "t": int(a["t"][m]), "level": lvl_name, "level_px": float(lvl), "sweep": float(sl_px),
                    "fvg_top": float(f_top), "fvg_bot": float(f_bot), "entry": float(entry), "stop": float(stop),
                    "target": float(target), "target_name": t_name, "rr": float((target - entry) / risk),
                    "risk_pct": float(risk / abs(entry)), "cost_r": float(cost * abs(entry) / risk),
                    "conf": {f: bool(conf[f]) for f in FACTORS}, "count": int(sum(bool(conf[f]) for f in FACTORS)),
                    "killzone": zone_name(mins)})
    return out


def zone_name(mins: int) -> str:
    if 120 <= mins < 300:
        return "London open"
    if 420 <= mins < 600:
        return "New York open"
    if 600 <= mins < 720:
        return "London close"
    return ""


def simulate(a: dict, s: dict, p: dict | None = None) -> dict:
    """Outcome of a BUY setup on `a` (use the flipped arrays for a SELL)."""
    p = {**P, **(p or {})}
    h, lo, c = a["h"], a["l"], a["c"]
    n = len(h)
    m, e, st, tg = s["m"], s["entry"], s["stop"], s["target"]
    risk = e - st
    f = -1
    for t in range(m + 1, min(n, m + 1 + p["fill_bars"])):
        if lo[t] <= e:
            f = t
            break
        if h[t] >= tg:
            return {"status": "missed", "fill": -1, "exit": t, "r": 0.0}
    if f < 0:
        return {"status": "pending" if m + p["fill_bars"] >= n else "expired", "fill": -1, "exit": -1, "r": 0.0}
    for t in range(f, min(n, f + p["hold_bars"])):
        if lo[t] <= st:
            return {"status": "loss", "fill": f, "exit": t, "r": -1.0 - s["cost_r"]}
        if t > f and h[t] >= tg:
            return {"status": "win", "fill": f, "exit": t, "r": s["rr"] - s["cost_r"]}
    if f + p["hold_bars"] > n:
        return {"status": "active", "fill": f, "exit": -1, "r": float((c[n - 1] - e) / risk - s["cost_r"])}
    t = f + p["hold_bars"] - 1
    return {"status": "time", "fill": f, "exit": t, "r": float((c[t] - e) / risk - s["cost_r"])}


def setups(df15: pd.DataFrame, df1h: pd.DataFrame | None = None, corr15: pd.DataFrame | None = None, cost: float = 0.0,
           start: int = 0) -> list[dict]:
    """Both directions, with outcomes. Prices returned the right way up."""
    a = arrays(df15)
    a1 = arrays(df1h) if df1h is not None and len(df1h) else None
    cb = arrays(corr15) if corr15 is not None and len(corr15) else None
    out = []
    for side, aa, hh, cc in (("buy", a, a1, cb), ("sell", flip(a), flip(a1) if a1 else None, flip(cb) if cb else None)):
        for s in detect_buys(aa, hh, cc, cost, start):
            r = simulate(aa, s)
            if side == "sell":
                for kk in ("level_px", "sweep", "fvg_top", "fvg_bot", "entry", "stop", "target"):
                    s[kk] = -s[kk]
                s["fvg_top"], s["fvg_bot"] = s["fvg_bot"], s["fvg_top"]
                s["level"], s["target_name"] = FLIP[s["level"]], FLIP[s["target_name"]]
            out.append({**s, "side": side, **r})
    return sorted(out, key=lambda x: x["m"])
