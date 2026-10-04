"""Fake-signal (manipulation) checks for the scanner's top candidates, using live public data.

Adversary model (docs/ADVERSARY.md): the code is public, so assume a manipulator reads every rule and
tunes their tricks to pass it. Countermeasures built in here:

* SECRET, ROTATING THRESHOLDS - every limit is jittered +/-15 % from a private seed (per device in the
  browser, per run on the server) that also rotates every hour, so there is no fixed line to aim at.
* MULTI-SNAPSHOT WALLS - three order-book snapshots at random gaps; fake walls must stay up (and risk
  being filled) for an unknown, longer time.
* PRICE-IMPACT - real buying moves price; volume that does not move price is fake however it is dressed.
* ENGINEERED SETUP - a case resting on cheap-to-fake signals without a lasting trend is penalised.

Checks:
  walls       big orders near the price that vanish across snapshots (spoofing)
  thin_book   few real orders within 2 % of the price, or a wide spread
  wash        identical-size trades bouncing buy/sell within 2 s (self-trading)
  impact      volume far above normal but the price barely reacts (fake volume of any shape)
  venues      price / daily move not confirmed by OKX and Gate.io
  whale       a few very large trades behind the activity
  engineered  strong cheap signals (volume burst, buy spike) without a lasting price trend
Mirrors site/engine.js exactly; tests/test_web_engine.py checks the two agree.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core.log import get_logger
from ingest.cex import _binance_get
from ingest.http import client

log = get_logger("scanner.integrity")
PENALTY = {"walls": 0.15, "thin_book": 0.05, "wash": 0.10, "impact": 0.10, "venues": 0.15, "venues_none": 0.03,
           "whale": 0.05, "engineered": 0.10}
MAX_PENALTY = sum(v for k, v in PENALTY.items() if k != "venues_none")
BASE = {"wall_mult": 5.0, "wall_share": 0.30, "wall_keep": 0.50, "thin_depth": 50_000.0, "thin_spread": 0.003,
        "wash_share": 0.02, "impact_surge": 2.5, "impact_ratio": 0.45, "venue_dev": 0.015, "venue_chg": 8.0,
        "whale_ratio": 4.0, "eng_surge": 3.0, "eng_bp": 0.05}
M32 = 0xFFFFFFFF


# ------------------------------------------------------------------------------- secret thresholds
def mulberry32(seed: int):
    """Small seeded PRNG, bit-identical to the JavaScript version in site/engine.js."""
    a = seed & M32

    def rnd() -> float:
        nonlocal a
        a = (a + 0x6D2B79F5) & M32
        t = ((a ^ (a >> 15)) * (1 | a)) & M32
        t = (((t + (((t ^ (t >> 7)) * (61 | t)) & M32)) & M32) ^ t) & M32
        return ((t ^ (t >> 14)) & M32) / 4294967296

    return rnd


def thresholds(seed: int | None) -> dict:
    """Each limit jittered to 85-115 % of its base value. seed=None -> the plain base values."""
    if seed is None:
        return dict(BASE)
    rnd = mulberry32(seed)
    return {k: v * (0.85 + 0.30 * rnd()) for k, v in BASE.items()}


def run_seed() -> int:
    """Private seed: OMEGA_SECRET_SEED (GitHub secret) if set, else random per run; rotates every hour."""
    base = os.environ.get("OMEGA_SECRET_SEED")
    s = int(base) if base and base.isdigit() else int.from_bytes(os.urandom(4), "little")
    return (s ^ int(time.time() // 3600)) & M32


# ------------------------------------------------------------------------------- checks
def _near(levels, mid, band=0.02):
    return [(p, q) for p, q in levels if abs(p / mid - 1) <= band]


def check_walls(books: list[dict], t: dict = BASE) -> dict:
    b1 = books[0]
    mid = (b1["bids"][0][0] + b1["asks"][0][0]) / 2
    lv = _near(b1["bids"], mid) + _near(b1["asks"], mid)
    if len(lv) < 5 or len(books) < 2:
        return {"key": "walls", "ok": None, "text": "Not enough orders near the price to check for fake walls."}
    notional = [p * q for p, q in lv]
    med = float(np.median(notional))
    total = sum(notional)
    walls = [(p, q, p * q) for (p, q), n in zip(lv, notional) if n > t["wall_mult"] * med and n > 1000]
    if not walls:
        return {"key": "walls", "ok": True, "text": "No suspicious giant orders near the price (no sign of spoofing)."}
    wsum = sum(n for _, _, n in walls)
    kept = 1.0
    for later in books[1:]:   # a real order must survive EVERY snapshot
        after = {p: q for p, q in later["bids"] + later["asks"]}
        kept = min(kept, sum(n * min(after.get(p, 0.0), q) / q for p, q, n in walls) / wsum)
    share = wsum / total
    if share > t["wall_share"] and kept < t["wall_keep"]:
        return {"key": "walls", "ok": False,
                "text": f"Large orders ({share:.0%} of the nearby order book) disappeared between our order-book "
                        "checks. That's spoofing: fake orders shown to trick other traders."}
    return {"key": "walls", "ok": True, "text": f"Big orders near the price stayed in place across "
                                                f"{len(books)} checks (they look real)."}


def check_thin_book(book: dict, t: dict = BASE) -> dict:
    bid, ask = book["bids"][0][0], book["asks"][0][0]
    mid = (bid + ask) / 2
    depth = sum(p * q for p, q in _near(book["bids"], mid) + _near(book["asks"], mid))
    spread = (ask - bid) / mid
    if depth < t["thin_depth"] or spread > t["thin_spread"]:
        return {"key": "thin_book", "ok": False,
                "text": f"Only about ${depth:,.0f} of orders within 2% of the price (spread {spread:.2%}). One "
                        "large trade can push it around."}
    return {"key": "thin_book", "ok": True, "text": f"About ${depth:,.0f} of orders within 2% of the price. "
                                                    "Healthy depth."}


def check_wash(trades: list[dict], t: dict = BASE) -> dict:
    if len(trades) < 100:
        return {"key": "wash", "ok": None, "text": "Too few recent trades to check for fake volume."}
    pairs = sum(1 for a, b in zip(trades[:-1], trades[1:])
                if b["qty"] == a["qty"] and b["buyer_maker"] != a["buyer_maker"] and b["time"] - a["time"] <= 2000)
    share = pairs / (len(trades) - 1)
    if share > t["wash_share"]:
        return {"key": "wash", "ok": False,
                "text": f"{share:.1%} of recent trades were back-and-forth trades of the exact same size within "
                        "2 seconds. That's typical of fake (wash) trading to make a coin look busy."}
    return {"key": "wash", "ok": True, "text": "Recent trades look natural (no back-and-forth fake trading)."}


def check_impact(candles: pd.DataFrame, t: dict = BASE) -> dict:
    """Fake volume of ANY shape (random sizes, random timing) still fails to move the price. Real markets follow
    the square-root law (price move ~ sqrt(money traded)), so impact is measured as move / sqrt(volume): genuine
    surges keep a ratio near 1, while volume that moves nothing drops well below."""
    if len(candles) < 60:
        return {"key": "impact", "ok": None, "text": "Not enough history to compare volume with price movement."}
    qv = candles["qv"].to_numpy(float)
    move = np.abs(np.log(candles["close"].to_numpy(float) / candles["open"].to_numpy(float)))
    imp = np.where(qv > 0, move / np.sqrt(np.where(qv > 0, qv, 1.0)), np.nan)
    usual_qv = np.nanmedian(qv[-168:])
    usual_imp = np.nanmedian(imp[-168:])
    surge = np.nanmean(qv[-6:]) / usual_qv if usual_qv > 0 else np.nan
    ratio = np.nanmedian(imp[-6:]) / usual_imp if usual_imp > 0 else np.nan
    if np.isfinite(surge) and np.isfinite(ratio) and surge > t["impact_surge"] and ratio < t["impact_ratio"]:
        return {"key": "impact", "ok": False,
                "text": f"Trading volume is {surge:.1f}x normal, but the price reacts only {ratio:.0%} as much as that "
                        "much trading normally moves it. Real buying moves prices; this looks like fake volume."}
    return {"key": "impact", "ok": True, "text": "The price reacts normally to the amount being traded."}


def check_venues(price: float, change_pct: float, others: list[dict], t: dict = BASE) -> dict:
    if not others:
        return {"key": "venues", "ok": None, "penalty": PENALTY["venues_none"],
                "text": "Not traded on the other exchanges checked (OKX, Gate.io), so the move can't be confirmed."}
    dev = max(abs(o["price"] / price - 1) for o in others)
    chg_diff = abs(change_pct - float(np.median([o["change_pct"] for o in others])))
    if dev > t["venue_dev"] or chg_diff > t["venue_chg"]:
        return {"key": "venues", "ok": False,
                "text": f"The price or daily move on Binance differs from other exchanges (up to {dev:.1%} apart, "
                        f"{chg_diff:.0f} points different over 24 hours). It may be pushed on one exchange only."}
    return {"key": "venues", "ok": True, "text": f"Other exchanges confirm the price ({len(others)} checked)."}


def check_whale(candles: pd.DataFrame, t: dict = BASE) -> dict:
    if "n_trades" not in candles or len(candles) < 60:
        return {"key": "whale", "ok": None, "text": "Not enough history to check trade sizes."}
    avg = (candles["qv"] / candles["n_trades"].replace(0, np.nan)).to_numpy()
    recent = np.nanmean(avg[-6:])
    usual = np.nanmedian(avg[-168:])
    ratio = recent / usual if usual and usual > 0 else np.nan
    if np.isfinite(ratio) and ratio > t["whale_ratio"]:
        return {"key": "whale", "ok": False,
                "text": f"Recent trades are {ratio:.0f}x bigger than usual. A few very large players are behind "
                        "the activity (a whale or a coordinated push)."}
    return {"key": "whale", "ok": True, "text": "Activity comes from many normal-sized trades."}


def check_engineered(f: dict, t: dict = BASE) -> dict:
    """Manipulators who know the scanner build the look it likes: a burst of volume and buying. A genuine
    move usually also has a lasting trend behind it, which is far more expensive to fake."""
    cheap = (f.get("volume_surge", 0) > t["eng_surge"]) or (f.get("buy_pressure_6b", 0) > t["eng_bp"])
    lasting = f.get("ret_72b", 0) > 0 and f.get("ema72_dist", 0) > 0
    if cheap and not lasting:
        return {"key": "engineered", "ok": False,
                "text": "The case rests on signals that are cheap to fake (a burst of volume or buying) with no "
                        "lasting price trend behind it. Manipulators build exactly this look to attract buyers."}
    return {"key": "engineered", "ok": True, "text": "The setup is backed by a longer-lasting trend, not just a "
                                                    "short burst of activity." if cheap else
            "No artificial-looking burst of activity."}


def combine(checks: list[dict], penalties: dict | None = None) -> dict:
    """penalties: learned per-check penalties from the nightly self-tuning (state/web/adaptive.json)."""
    pt = {**PENALTY, **(penalties or {})}
    pen = 0.0
    for c in checks:
        if c["ok"] is False:
            pen += pt[c["key"]]
        elif c["ok"] is None:
            pen += c.get("penalty", 0.0)
    known = [c for c in checks if c["ok"] is not None]
    return {"checks": checks, "penalty": round(pen, 6), "passed": sum(1 for c in known if c["ok"]),
            "checked": len(known), "trust": round(max(0.0, 1 - pen / MAX_PENALTY), 3)}


# ------------------------------------------------------------------------------- live data
def _book(symbol: str) -> dict:
    d = _binance_get("/api/v3/depth", {"symbol": symbol, "limit": 100})
    return {"bids": [(float(p), float(q)) for p, q in d["bids"]], "asks": [(float(p), float(q)) for p, q in d["asks"]]}


def _trades(symbol: str) -> list[dict]:
    d = _binance_get("/api/v3/trades", {"symbol": symbol, "limit": 1000})
    return [{"price": float(t["price"]), "qty": float(t["qty"]), "buyer_maker": bool(t["isBuyerMaker"]),
             "time": int(t["time"])} for t in d]


def other_venues() -> dict[str, list[dict]]:
    """base asset -> [{venue, price, change_pct}] from OKX and Gate.io bulk tickers (one call each)."""
    out: dict[str, list[dict]] = {}
    try:
        for t in client().get("https://www.okx.com/api/v5/market/tickers", {"instType": "SPOT"})["data"]:
            if t["instId"].endswith("-USDT") and float(t.get("open24h") or 0) > 0 and float(t.get("last") or 0) > 0:
                out.setdefault(t["instId"][:-5], []).append(
                    {"venue": "OKX", "price": float(t["last"]), "change_pct": (float(t["last"]) / float(t["open24h"]) - 1) * 100})
    except Exception as e:  # noqa: BLE001
        log.warning("okx tickers: %s", e)
    try:
        for t in client().get("https://api.gateio.ws/api/v4/spot/tickers"):
            if t["currency_pair"].endswith("_USDT") and float(t.get("last") or 0) > 0 and t.get("change_percentage") not in (None, ""):
                out.setdefault(t["currency_pair"][:-5], []).append(
                    {"venue": "Gate.io", "price": float(t["last"]), "change_pct": float(t["change_percentage"])})
    except Exception as e:  # noqa: BLE001
        log.warning("gate tickers: %s", e)
    return out


def snapshot_gaps(rnd) -> list[float]:
    """Random gaps (seconds) between the three order-book snapshots: 2-5 s, then 4-10 s."""
    return [2 + 3 * rnd(), 4 + 6 * rnd()]


def run_checks(symbols: list[str], candles: dict[str, pd.DataFrame], universe: pd.DataFrame,
               features: dict[str, dict] | None = None, seed: int | None = None,
               penalties: dict | None = None) -> dict[str, dict]:
    seed = run_seed() if seed is None else seed
    t = thresholds(seed)
    gaps = snapshot_gaps(mulberry32(seed ^ 0x9E3779B9))
    venues = other_venues()
    uni = universe.set_index("symbol")
    with ThreadPoolExecutor(6) as ex:
        snaps = [dict(zip(symbols, ex.map(lambda s: _safe(_book, s), symbols)))]
        for g in gaps:
            time.sleep(g)
            snaps.append(dict(zip(symbols, ex.map(lambda s: _safe(_book, s), symbols))))
        tr = dict(zip(symbols, ex.map(lambda s: _safe(_trades, s), symbols)))
    out = {}
    for s in symbols:
        checks = []
        books = [sn[s] for sn in snaps if sn[s] and sn[s]["bids"] and sn[s]["asks"]]
        if len(books) >= 2:
            checks += [check_walls(books, t), check_thin_book(books[0], t)]
        if tr[s]:
            checks.append(check_wash(tr[s], t))
        if s in candles:
            checks.append(check_impact(candles[s], t))
        if s in uni.index:
            checks.append(check_venues(float(uni.loc[s, "lastPrice"]), float(uni.loc[s, "priceChangePercent"]),
                                       venues.get(s[:-4], []), t))
        if s in candles:
            checks.append(check_whale(candles[s], t))
        if features and s in features:
            checks.append(check_engineered(features[s], t))
        out[s] = combine(checks, penalties)
    return out


def _safe(fn, s):
    try:
        return fn(s)
    except Exception as e:  # noqa: BLE001
        log.warning("integrity data %s: %s", s, e)
        return None
