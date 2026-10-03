"""Fake-signal (manipulation) checks for the scanner's top candidates, using live public data.

Mirrors site/engine.js (integrity section) exactly; tests/test_web_engine.py checks the two agree.

  walls       spoofing: big orders near the price that vanish between two order-book snapshots
  thin_book   few real orders within 2 % of the price, or a wide spread: easy to push around
  wash        'ping-pong' trades of identical size bouncing buy/sell within seconds: fake volume
  venues      price / 24 h move differs between exchanges, or no other exchange to confirm it
  whale       a few very large trades are behind the recent activity
Each failed check adds a penalty (in units of money risked) that lowers the idea's score.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core.log import get_logger
from ingest.cex import _binance_get
from ingest.http import client

log = get_logger("scanner.integrity")
PENALTY = {"walls": 0.15, "thin_book": 0.05, "wash": 0.10, "venues": 0.15, "venues_none": 0.03, "whale": 0.05}
MAX_PENALTY = 0.15 + 0.05 + 0.10 + 0.15 + 0.05


def _near(levels, mid, band=0.02):
    return [(p, q) for p, q in levels if abs(p / mid - 1) <= band]


def check_walls(book1: dict, book2: dict) -> dict:
    bid, ask = book1["bids"][0][0], book1["asks"][0][0]
    mid = (bid + ask) / 2
    lv = _near(book1["bids"], mid) + _near(book1["asks"], mid)
    if len(lv) < 5:
        return {"key": "walls", "ok": None, "text": "Not enough orders near the price to check for fake walls."}
    notional = [p * q for p, q in lv]
    med = float(np.median(notional))
    total = sum(notional)
    walls = [(p, q, p * q) for (p, q), n in zip(lv, notional) if n > 5 * med and n > 1000]
    if not walls:
        return {"key": "walls", "ok": True, "text": "No suspicious giant orders near the price (no sign of spoofing)."}
    after = {p: q for p, q in book2["bids"] + book2["asks"]}
    wsum = sum(n for _, _, n in walls)
    kept = sum(n * min(after.get(p, 0.0), q) / q for p, q, n in walls) / wsum
    share = wsum / total
    if share > 0.3 and kept < 0.5:
        return {"key": "walls", "ok": False,
                "text": f"Large orders ({share:.0%} of the nearby order book) vanished within seconds. That is a "
                        "classic spoofing trick to fake demand or supply."}
    return {"key": "walls", "ok": True, "text": "Big orders near the price stayed in place (they look real)."}


def check_thin_book(book: dict) -> dict:
    bid, ask = book["bids"][0][0], book["asks"][0][0]
    mid = (bid + ask) / 2
    depth = sum(p * q for p, q in _near(book["bids"], mid) + _near(book["asks"], mid))
    spread = (ask - bid) / mid
    if depth < 50_000 or spread > 0.003:
        return {"key": "thin_book", "ok": False,
                "text": f"Only about ${depth:,.0f} of orders within 2% of the price (spread {spread:.2%}). One "
                        "large trade can push it around."}
    return {"key": "thin_book", "ok": True, "text": f"About ${depth:,.0f} of orders within 2% of the price. "
                                                    "Healthy depth."}


def check_wash(trades: list[dict]) -> dict:
    """Wash trading leaves 'ping-pong' prints: the exact same size traded back and forth (buy, then sell)
    within ~2 seconds. Measured on real Binance data, normal coins show 0-0.6 % such pairs; the textbook
    Benford / identical-size tests flagged the biggest, cleanest coins (market-maker bots), so they are not used."""
    if len(trades) < 100:
        return {"key": "wash", "ok": None, "text": "Too few recent trades to check for fake volume."}
    pairs = sum(1 for a, b in zip(trades[:-1], trades[1:])
                if b["qty"] == a["qty"] and b["buyer_maker"] != a["buyer_maker"] and b["time"] - a["time"] <= 2000)
    share = pairs / (len(trades) - 1)
    if share > 0.02:
        return {"key": "wash", "ok": False,
                "text": f"{share:.1%} of recent trades were back-and-forth trades of the exact same size within "
                        "2 seconds. That's typical of fake (wash) trading to make a coin look busy."}
    return {"key": "wash", "ok": True, "text": "Recent trades look natural (no back-and-forth fake trading)."}


def check_venues(price: float, change_pct: float, others: list[dict]) -> dict:
    if not others:
        return {"key": "venues", "ok": None, "penalty": PENALTY["venues_none"],
                "text": "Not traded on the other exchanges checked (OKX, Gate.io), so the move can't be confirmed."}
    dev = max(abs(o["price"] / price - 1) for o in others)
    chg_diff = abs(change_pct - float(np.median([o["change_pct"] for o in others])))
    if dev > 0.015 or chg_diff > 8:
        return {"key": "venues", "ok": False,
                "text": f"The price or daily move on Binance differs from other exchanges (up to {dev:.1%} apart, "
                        f"{chg_diff:.0f} points different over 24 hours). It may be pushed on one exchange only."}
    return {"key": "venues", "ok": True, "text": f"Other exchanges confirm the price ({len(others)} checked)."}


def check_whale(candles: pd.DataFrame) -> dict:
    if "n_trades" not in candles or len(candles) < 60:
        return {"key": "whale", "ok": None, "text": "Not enough history to check trade sizes."}
    avg = (candles["qv"] / candles["n_trades"].replace(0, np.nan)).to_numpy()
    recent = np.nanmean(avg[-6:])
    usual = np.nanmedian(avg[-168:])
    ratio = recent / usual if usual and usual > 0 else np.nan
    if np.isfinite(ratio) and ratio > 4:
        return {"key": "whale", "ok": False,
                "text": f"Recent trades are {ratio:.0f}x bigger than usual. A few very large players are behind "
                        "the activity (a whale or a coordinated push)."}
    return {"key": "whale", "ok": True, "text": "Activity comes from many normal-sized trades."}


def combine(checks: list[dict]) -> dict:
    pen = 0.0
    for c in checks:
        if c["ok"] is False:
            pen += PENALTY[c["key"]]
        elif c["ok"] is None:
            pen += c.get("penalty", 0.0)
    known = [c for c in checks if c["ok"] is not None]
    return {"checks": checks, "penalty": round(pen, 6), "passed": sum(1 for c in known if c["ok"]),
            "checked": len(known), "trust": round(max(0.0, 1 - pen / MAX_PENALTY), 3)}


# ------------------------------------------------------------------------------------------- live data
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


def run_checks(symbols: list[str], candles: dict[str, pd.DataFrame], universe: pd.DataFrame,
               gap_seconds: float = 3.0) -> dict[str, dict]:
    venues = other_venues()
    uni = universe.set_index("symbol")
    with ThreadPoolExecutor(6) as ex:
        b1 = dict(zip(symbols, ex.map(lambda s: _safe(_book, s), symbols)))
        time.sleep(gap_seconds)
        b2 = dict(zip(symbols, ex.map(lambda s: _safe(_book, s), symbols)))
        tr = dict(zip(symbols, ex.map(lambda s: _safe(_trades, s), symbols)))
    out = {}
    for s in symbols:
        checks = []
        if b1[s] and b2[s] and b1[s]["bids"] and b1[s]["asks"]:
            checks += [check_walls(b1[s], b2[s]), check_thin_book(b1[s])]
        if tr[s]:
            checks.append(check_wash(tr[s]))
        if s in uni.index:
            base = s[:-4]
            checks.append(check_venues(float(uni.loc[s, "lastPrice"]), float(uni.loc[s, "priceChangePercent"]),
                                       venues.get(base, [])))
        if s in candles:
            checks.append(check_whale(candles[s]))
        out[s] = combine(checks)
    return out


def _safe(fn, s):
    try:
        return fn(s)
    except Exception as e:  # noqa: BLE001
        log.warning("integrity data %s: %s", s, e)
        return None
