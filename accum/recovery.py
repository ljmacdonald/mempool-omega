"""Distressed recovery (Strategy B, D119): established coins far below their peak that may still have a working
business. Kept apart from accumulation: own screen, own score, own backtest, own page tab.

A fall of 75%+ is an investigation threshold, never a buy signal. Hard exclusions override the score.

Score v1 (100 points, provisional):
  usage 20          DefiLlama: TVL and fees still there (DeFi projects; others get no points: no free usage data)
  supply 20         circulating / max (or total) supply, FDV / market cap (unlock schedules: no free source, said so)
  liquidity 15      24 h volume, absolute and against market cap
  catalysts 15      NOT MEASURED: no reliable free source; 0 points and said so (never invented)
  drawdown 10       75-90% below the peak is the band studied; deeper falls score less (more often terminal)
  stabilisation 10  from Binance daily candles: off the 60-day low, falling volatility, a higher 30-day low
  quality 10        how many of the fields above are actually known

Backtest (weekly, price and volume only - fundamentals aren't available historically, said so): every Binance USDT
coin, delisted ones included (Binance keeps their history), at each month start: 2+ years of history, 75%+ below the
previous 2-year high, $1M+ average daily volume, closed 10%+ above its 30-day low. Forward 30/90/180-day returns after
costs, against all other coins that passed the history and volume rules that day (random picks) and against Bitcoin.
A coin delisted before the horizon is exited at its last price (usually a loss). Judged like the Strategy lab:
30+ signals, made money, beat random picks with t >= 2, and still did in the most recent third.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from accum import config as K

STABLES = {"usdt", "usdc", "dai", "fdusd", "tusd", "usde", "usds", "pyusd", "busd", "usd1", "usdd", "frax", "gusd", "lusd", "eurc",
           "susde", "usdtb", "rlusd", "usdg", "usd0", "bfusd", "buidl", "usdf"}
WRAPPED = {"wbtc", "weth", "steth", "wsteth", "weeth", "cbbtc", "reth", "cbeth", "wbeth", "jitosol", "msol", "bnsol", "ezeth", "rseth",
           "lbtc", "solvbtc", "tbtc", "paxg", "xaut", "wbnb", "meth", "oseth", "sweth", "clbtc", "fbtc"}


def screen(coin: dict) -> tuple[bool, str]:
    sym = (coin.get("symbol") or "").lower()
    if sym in STABLES:
        return False, "stablecoin"
    if sym in WRAPPED or sym.startswith("w") and sym[1:] in {"btc", "eth", "sol", "bnb"}:
        return False, "wrapped or staked copy of another coin"
    dd = coin.get("ath_change_percentage")
    if dd is None:
        return False, "no all-time-high data"
    if -dd / 100 < K.REC_MIN_DRAWDOWN:
        return False, f"only {-dd:.0f}% below its peak (screen starts at {K.REC_MIN_DRAWDOWN:.0%})"
    if (coin.get("market_cap") or 0) < K.REC_MIN_MCAP:
        return False, "market cap under $10M"
    if (coin.get("total_volume") or 0) < K.REC_MIN_VOLUME:
        return False, "under $500k traded a day: not reliably tradable"
    return True, "passes the screen"


def stabilisation(closes: list[float]) -> tuple[float, str]:
    """0-1 from the last 120 daily closes."""
    if len(closes) < 90:
        return 0.0, "under 90 days of exchange candles"
    last = closes[-1]
    lo60 = min(closes[-60:])
    lo_recent, lo_prior = min(closes[-30:]), min(closes[-90:-30])
    r = [math.log(b / a) for a, b in zip(closes[-61:], closes[-60:]) if a and b]
    vol_now = (sum(x * x for x in r[-30:]) / 30) ** 0.5
    vol_before = (sum(x * x for x in r[:30]) / 30) ** 0.5
    pts, why = 0.0, []
    if last >= 1.1 * lo60:
        pts += 0.4
        why.append(f"{last / lo60 - 1:.0%} above its 60-day low")
    if vol_now < vol_before:
        pts += 0.3
        why.append("swings getting smaller")
    if lo_recent > lo_prior:
        pts += 0.3
        why.append("a higher low than the month before")
    return pts, ", ".join(why) or "no sign of stabilising yet"


def score(coin: dict, llama: dict | None, closes: list[float] | None, history_days: int | None) -> dict:
    W = K.REC_WEIGHTS
    parts = {}
    known = 0
    if llama and (llama.get("tvl") or llama.get("fees_30d")):
        tvl, fees = llama.get("tvl") or 0, llama.get("fees_30d") or 0
        u = min(1.0, (0.5 if tvl > 10e6 else 0.25 if tvl > 1e6 else 0) + (0.5 if fees > 100e3 else 0.25 if fees > 10e3 else 0))
        parts["usage"] = (W["usage"] * u, f"TVL ${tvl / 1e6:,.1f}M, fees ${fees / 1e3:,.0f}k over 30 days (DefiLlama)")
        known += 1
    else:
        parts["usage"] = (0.0, "no usage data (not a DeFi protocol on DefiLlama): not measured")
    circ, mx = coin.get("circulating_supply"), coin.get("max_supply") or coin.get("total_supply")
    if circ and mx:
        ratio = min(1.0, circ / mx)
        fdv_mc = (coin.get("fully_diluted_valuation") or 0) / coin["market_cap"] if coin.get("market_cap") else None
        parts["supply"] = (W["supply"] * ratio, f"{ratio:.0%} of the maximum supply already circulating"
                           + (f"; fully diluted value {fdv_mc:.1f}x the market cap" if fdv_mc else "") + "; unlock dates not available for free")
        known += 1
    else:
        parts["supply"] = (0.0, "supply figures incomplete: dilution risk unknown")
    vol, mc = coin.get("total_volume") or 0, coin.get("market_cap") or 0
    liq = min(1.0, (0.5 if vol >= 10e6 else 0.3 if vol >= 2e6 else 0.1) + (0.5 if mc and vol / mc >= 0.05 else 0.25 if mc and vol / mc >= 0.02 else 0))
    parts["liquidity"] = (W["liquidity"] * liq, f"${vol / 1e6:,.1f}M traded in 24 h ({(vol / mc if mc else 0):.1%} of market cap)")
    known += 1
    parts["catalysts"] = (0.0, "not measured: no reliable free source for development or catalyst dates (never invented)")
    dd = -coin["ath_change_percentage"] / 100
    parts["drawdown"] = (W["drawdown"] * (1.0 if dd <= 0.9 else 0.5 if dd <= 0.95 else 0.2),
                         f"{dd:.0%} below the peak of {coin.get('ath_date', '')[:10]}" + (" (deeper falls are more often terminal)" if dd > 0.9 else ""))
    if closes:
        s, why = stabilisation(closes)
        parts["stabilisation"] = (W["stabilisation"] * s, why)
        known += 1
    else:
        parts["stabilisation"] = (0.0, "no exchange candles (not on Binance): not measured")
    hist_ok = history_days is not None and history_days >= K.REC_HISTORY_D
    known += 1 if hist_ok else 0
    parts["quality"] = (W["quality"] * known / 5, f"{known} of 5 data checks available" + ("" if hist_ok else "; 2-year price history not verified"))
    total = sum(v[0] for v in parts.values())
    return {"version": K.RECOVERY_VERSION, "total": round(total, 1),
            "parts": {k: {"points": round(v[0], 1), "max": W[k], "why": v[1]} for k, v in parts.items()},
            "confidence": "Medium" if known >= 4 else "Low"}


# ---------------------------------------------------------------- backtest (price and volume only)
def bt_signals(series: dict[str, list[list]], btc: list[list] | None) -> list[dict]:
    """series: symbol -> daily klines [[open_ms, o, h, l, c, v, close_ms, quote_vol, ...]], oldest first."""
    rows = []
    starts = sorted({datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                     for ks in series.values() for k in ks})
    idx = {s: {k[0]: i for i, k in enumerate(ks)} for s, ks in series.items()}
    btc_idx = {k[0]: i for i, k in enumerate(btc or [])}
    for m in starts:
        t = int(m.timestamp() * 1000)
        elig, sig = [], []
        for s, ks in series.items():
            i = idx[s].get(t)
            if i is None or i < K.REC_HISTORY_D:
                continue
            closes = [float(k[4]) for k in ks[i - K.REC_HISTORY_D:i + 1]]
            qv = sum(float(k[7]) for k in ks[i - 29:i + 1]) / 30
            if qv < 1e6:
                continue
            fwd = {}
            for h in K.BT_HORIZONS:
                j = min(i + h, len(ks) - 1)
                if j <= i:
                    fwd[h] = None
                    continue
                fwd[h] = float(ks[j][4]) / closes[-1] - 1 - K.BT_COST
            rec = {"t": t, "sym": s, "fwd": fwd, "delisted_before": {h: i + h > len(ks) - 1 for h in K.BT_HORIZONS}}
            elig.append(rec)
            peak = max(closes[:-1])
            if closes[-1] <= (1 - K.REC_MIN_DRAWDOWN) * peak and closes[-1] >= 1.1 * min(closes[-30:]):
                sig.append(rec)
        for r in sig:
            out = {"t": r["t"], "sym": r["sym"], "fwd": r["fwd"], "delisted_before": r["delisted_before"], "random": {}, "btc": {}}
            for h in K.BT_HORIZONS:
                pool = [e["fwd"][h] for e in elig if e["sym"] != r["sym"] and e["fwd"][h] is not None]
                out["random"][h] = sum(pool) / len(pool) if pool else None
                bi = btc_idx.get(t)
                if btc and bi is not None and bi + h < len(btc):
                    out["btc"][h] = float(btc[bi + h][4]) / float(btc[bi][4]) - 1 - K.BT_COST
            rows.append(out)
    return rows


def bt_summary(rows: list[dict], h: int) -> dict:
    rs = sorted((r for r in rows if r["fwd"].get(h) is not None and r["random"].get(h) is not None), key=lambda r: r["t"])
    if not rs:
        return {"h": h, "n": 0}

    def stats(xs):
        n = len(xs)
        if not n:
            return {"n": 0}
        ret = [x["fwd"][h] for x in xs]
        e = [x["fwd"][h] - x["random"][h] for x in xs]
        me = sum(e) / n
        sd = (sum((v - me) ** 2 for v in e) / (n - 1)) ** 0.5 if n > 2 else None
        b = [x["btc"].get(h) for x in xs if x["btc"].get(h) is not None]
        return {"n": n, "avg": sum(ret) / n, "median": sorted(ret)[n // 2], "hit": sum(1 for v in ret if v > 0) / n,
                "random": sum(x["random"][h] for x in xs) / n, "edge": me, "t_edge": me / (sd / n ** 0.5) if sd else None,
                "btc": sum(b) / len(b) if b else None, "delisted": sum(1 for x in xs if x["delisted_before"][h])}
    cut = len(rs) * 2 // 3
    allr, late = stats(rs), stats(rs[cut:])
    if allr["n"] < 30:
        verdict, level = f"Too few signals to judge ({allr['n']}; 30 needed).", "warn"
    elif allr["avg"] <= 0:
        verdict, level = "Lost money after costs.", "bad"
    elif (allr["t_edge"] or 0) < 2:
        verdict, level = ("Made money, but not clearly better than random coins: could be luck." if allr["edge"] > 0
                          else "Made money only because the market rose: random coins did as well or better."), "warn" if allr["edge"] > 0 else "bad"
    elif not (late["n"] >= 10 and late["edge"] > 0 and (late["t_edge"] or 0) >= 1 and late["avg"] > 0):
        verdict, level = "Beat random coins earlier, but not clearly in the most recent third: the edge seems to have faded.", "warn"
    else:
        verdict, level = "Held up: made money after costs, beat random coins by more than luck, and still did recently.", "good"
    return {"h": h, "all": allr, "late": late, "verdict": verdict, "level": level,
            "from": datetime.fromtimestamp(rs[0]["t"] / 1000, tz=timezone.utc).date().isoformat(),
            "to": datetime.fromtimestamp(rs[-1]["t"] / 1000, tz=timezone.utc).date().isoformat()}

