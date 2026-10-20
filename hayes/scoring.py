"""Engine A's scoring rules. Every function is pure, versioned, reads its thresholds from profile.yaml and returns its
components, the share of missing data and reason codes. Candles are daily dicts {d, o, h, l, c, v} (v in US dollars),
oldest first, and only ever include days up to the day being scored (no lookahead by construction).
"""
from __future__ import annotations

from datetime import date

from hayes.profile import load

VERSION = 1


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ---------------------------------------------------------------- 1. trough-based distress
def distress(candles: list[dict]) -> dict:
    """H = highest high over 365 days, L = lowest low after H, trough_dd = L/H - 1, recovery = P/L - 1.
    The band applies to the trough, so a confirmed rebound doesn't push a token out of its own band."""
    P = load()["distress"]
    out = {"version": VERSION, "pass": False, "codes": []}
    if not candles:
        return {**out, "codes": ["NO_DATA"]}
    today = candles[-1]["d"]
    win = [c for c in candles if _days(c["d"], today) < P["lookback_days"]]
    if _days(candles[0]["d"], today) < P["lookback_days"] - 1:
        out["codes"].append("SHORT_HISTORY")
    iH = max(range(len(win)), key=lambda i: win[i]["h"])
    H = win[iH]["h"]
    after = win[iH:]
    iL = min(range(len(after)), key=lambda i: after[i]["l"])
    L = after[iL]["l"]
    gaps = max((_days(a["d"], b["d"]) - 1 for a, b in zip(after, after[1:])), default=0)
    dd, rec, age = L / H - 1, candles[-1]["c"] / L - 1, _days(after[iL]["d"], today)
    out.update(H=H, H_day=win[iH]["d"], L=L, L_day=after[iL]["d"], trough_dd=dd, recovery=rec, trough_age=age, price=candles[-1]["c"],
               high_180=max(c["h"] for c in win if _days(c["d"], today) < 180), high_all=max(c["h"] for c in candles), max_gap=gaps)
    lo, hi = P["band"]
    if not lo <= dd <= hi:
        out["codes"].append("OUT_OF_BAND")
    if rec > P["recovery_cap"]:
        out["codes"].append("RECOVERED_TOO_FAR")
    if not P["trough_age_days"][0] <= age <= P["trough_age_days"][1]:
        out["codes"].append("TROUGH_AGE")
    if gaps > P["max_gap_days"]:
        out["codes"].append("CANDLE_GAP")
    out["pass"] = not out["codes"]
    return out


# ---------------------------------------------------------------- 5. bottom confirmation
def swings(candles: list[dict], n: int) -> tuple[list[int], list[int]]:
    """Indices of swing lows and highs (lowest/highest with n days either side, all known by the last candle)."""
    lows, highs = [], []
    for i in range(n, len(candles) - n):
        seg = candles[i - n:i + n + 1]
        if candles[i]["l"] == min(c["l"] for c in seg):
            lows.append(i)
        if candles[i]["h"] == max(c["h"] for c in seg):
            highs.append(i)
    return lows, highs


def confirmation(candles: list[dict], btc: dict[str, float], trough_day: str, trough: float, perps: dict | None = None) -> dict:
    """0-100 with components. perps (optional): {oi_off_peak: fraction below the decline peak, funding: rate,
    spot_share_rising: bool, oi_flat_or_falling: bool}. Without perps the last two components are dropped and the
    weights rescale; the sleeves' thresholds then rise by no_perps_threshold_add (applied by the caller)."""
    P = load()["confirmation"]
    W = P["weights"]
    comp, avail = {}, dict(W)
    iT = next((i for i, c in enumerate(candles) if c["d"] == trough_day), None)
    lows, highs = swings(candles, P["swing_n"])
    hl_idx = [i for i in lows if iT is not None and i > iT and _days(trough_day, candles[i]["d"]) >= P["higher_low_min_days"]
              and candles[i]["l"] > trough]
    comp["higher_low"] = bool(hl_idx)
    sh = [candles[i]["h"] for i in highs if iT is not None and i > iT]
    comp["structure_break"] = bool(sh) and candles[-1]["c"] > max(sh)
    ratio = [(c["d"], c["c"] / btc[c["d"]]) for c in candles if btc.get(c["d"])]
    rs = None
    if len(ratio) >= max(P["rs_lookback_days"], 50) + 1:
        now_r, then_r = ratio[-1][1], ratio[-1 - P["rs_lookback_days"]][1]
        sma50 = sum(r for _, r in ratio[-50:]) / 50
        rs = now_r > then_r and now_r > sma50
    comp["relative_strength"] = rs
    v7 = [c["v"] for c in candles[-7:]]
    v30 = [c["v"] for c in candles[-37:-7]]
    comp["volume_expansion"] = (sum(v7) / 7) / (sum(v30) / 30) >= P["volume_ratio"] if len(v30) == 30 and sum(v30) > 0 else None
    if perps:
        comp["derivatives_reset"] = (perps.get("oi_off_peak") or 0) >= 0.30 and (perps.get("funding") is not None and perps["funding"] <= 0.0001)
        comp["spot_led"] = bool(perps.get("spot_share_rising")) and bool(perps.get("oi_flat_or_falling"))
    else:
        avail.pop("derivatives_reset")
        avail.pop("spot_led")
    known = {k: v for k, v in comp.items() if v is not None and k in avail}
    weight = sum(avail[k] for k in known)
    total_w = sum(avail.values())
    score = 100 * sum(avail[k] for k, v in known.items() if v) / total_w if total_w else 0.0
    return {"version": VERSION, "score": round(score, 1), "components": comp, "perps": bool(perps),
            "missing_share": 1 - weight / total_w if total_w else 1.0,
            "last_higher_low": candles[hl_idx[-1]]["l"] if hl_idx else None}


# ---------------------------------------------------------------- 3. supply pressure
def supply_pressure(unlocks: list[dict] | None, price: float | None, adv30: float | None, circulating: float | None,
                    today: str) -> dict:
    """A30 = sum over unlocks in the next 30 days of qty x price x assumed sell share, divided by 30-day average daily
    volume (days of normal volume needed to absorb it); days 31-90 count at half weight. unlocks: [{day, qty, recipient}]
    (linear vesting already turned into daily amounts by the caller). None = schedule unknown."""
    P = load()["supply"]
    if unlocks is None:
        return {"version": VERSION, "known": False, "codes": ["SCHEDULE_UNKNOWN"]}
    if not price or not adv30:
        return {"version": VERSION, "known": False, "codes": ["PRICE_OR_VOLUME_UNKNOWN"]}
    s30, s90, rel30 = 0.0, 0.0, 0.0
    for u in unlocks:
        dd = _days(today, u["day"])
        share = P["sell_shares"].get(u.get("recipient") or "other", P["sell_shares"]["other"])
        if 0 < dd <= 30:
            s30 += u["qty"] * price * share
            rel30 += u["qty"]
        elif 30 < dd <= 90:
            s90 += 0.5 * u["qty"] * price * share
    a30 = (s30 + s90) / adv30
    pct_float = rel30 / circulating if circulating else None
    codes = []
    if a30 > P["hard_block_days"] or (pct_float is not None and pct_float >= P["max_release_pct_float"]):
        codes.append("U10_SUPPLY_HARD_BLOCK")
    elif a30 > P["penalty_days"]:
        codes.append("SUPPLY_PENALTY")
    return {"version": VERSION, "known": True, "a30": a30, "release_30d_pct_float": pct_float, "codes": codes}


# ---------------------------------------------------------------- 4. quality
def quality(fees_90d_peak_drop: float | None, price_peak_drop: float | None, mcap_to_fees_pctile: float | None,
            holders_revenue_30d: float | None, commits_ratio: float | None, runway_months: float | None,
            annual_fees: float | None) -> dict:
    """resilience = 1 - fee decline / price decline (did usage hold while price collapsed?). Missing components are
    dropped and weights rescaled; over max_missing_pct missing -> deferred. Value accrual is measured (holders revenue:
    buybacks, burns, staker share), never inferred."""
    P = load()["quality"]
    W = P["weights"]
    parts = {}
    if fees_90d_peak_drop is not None and price_peak_drop:
        r = 1 - fees_90d_peak_drop / price_peak_drop
        parts["resilience"] = max(0.0, min(1.0, r))
    if mcap_to_fees_pctile is not None:
        parts["valuation"] = 1 - mcap_to_fees_pctile              # cheaper than its own history scores higher
    if holders_revenue_30d is not None:
        parts["value_accrual"] = 1.0 if holders_revenue_30d > 0 else 0.0
    if commits_ratio is not None:
        parts["development"] = max(0.0, min(1.0, commits_ratio / 1.5))
    if runway_months is not None:
        parts["runway"] = max(0.0, min(1.0, runway_months / 24))
    known_w = sum(W[k] for k in parts)
    missing = 1 - known_w / sum(W.values())
    codes = []
    if annual_fees is None or annual_fees < P["min_annual_fees_usd"]:
        codes.append("FEES_TOO_LOW_OR_UNKNOWN")
    if missing > P["max_missing_pct"]:
        codes.append("QUALITY_DEFERRED_MISSING")
    score = 100 * sum(W[k] * v for k, v in parts.items()) / known_w if known_w else None
    if score is not None and score < P["min_score"]:
        codes.append("QUALITY_BELOW_MIN")
    return {"version": VERSION, "score": round(score, 1) if score is not None else None, "parts": parts, "missing_share": missing,
            "value_accrual": (holders_revenue_30d or 0) > 0 if holders_revenue_30d is not None else None, "codes": codes}


# ---------------------------------------------------------------- 2. hard gates
def gates(f: dict) -> list[str]:
    """Every failing gate code (any one makes the token ineligible, whatever its scores). f: the facts, with None for
    unknown; an unknown critical fact fails its gate (data never defaults)."""
    U = load()["universe"]
    fails = []
    if not f.get("usdc_market"):
        fails.append("U1_MARKET")
    if f.get("history_days") is None or f["history_days"] < U["min_history_months"] * 30.4:
        fails.append("U2_HISTORY")
    if f.get("is_stable_or_wrapped") is not False:
        fails.append("U3_TYPE")
    L = U["liquidity"]
    if (f.get("adv30") or 0) < L["min_adv_usd"] or f.get("spread_bps") is None or f["spread_bps"] > L["max_spread_bps"] or \
            (f.get("depth_2pct") or 0) < L["min_depth_2pct_usd"]:
        fails.append("U4_LIQUIDITY")
    D = U["dilution"]
    circ, mx, fdv, mc = f.get("circulating"), f.get("max_supply"), f.get("fdv"), f.get("mcap")
    if not circ or not mx or circ / mx < D["min_circulating_to_max"] or not mc or (fdv and fdv / mc > D["max_fdv_to_mcap"]):
        fails.append("U5_DILUTION")
    if f.get("stale"):
        fails.append("U6_FRESH")
    if f.get("exploit_90d"):
        fails.append("U7_SECURITY")
    if f.get("delisting"):
        fails.append("U8_DELIST")
    if "U10_SUPPLY_HARD_BLOCK" in (f.get("supply_codes") or []):
        fails.append("U10_SUPPLY")
    if f.get("quality_codes") and any(c in f["quality_codes"] for c in ("FEES_TOO_LOW_OR_UNKNOWN", "QUALITY_BELOW_MIN", "QUALITY_DEFERRED_MISSING")):
        fails.append("U11_QUALITY_MIN")
    return fails
