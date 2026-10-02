"""Order-book & trade microstructure signals.

Signals 1-3 of the brief:
  1. Order Flow Imbalance (OFI): book-based (Cont-Kukanov-Stoikov) and trade-sign based.
  2. Depth persistence: does resting depth survive as price approaches it?
     (Spoofed walls vanish on approach -> low persistence.)
  3. Cancel rate & iceberg detection.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def trade_ofi(volume: pd.Series, taker_buy_volume: pd.Series) -> pd.Series:
    """Signed aggressor imbalance in [-1, 1] from bar taker-buy volume."""
    return ((2 * taker_buy_volume - volume) / volume.replace(0, np.nan)).clip(-1, 1)


def _top(book: dict) -> tuple[float, float, float, float]:
    (pb, qb), (pa, qa) = book["bids"][0], book["asks"][0]
    return pb, qb, pa, qa


def book_ofi(books: list[dict]) -> float:
    """Cont-Kukanov-Stoikov OFI over a sequence of snapshots, normalised to [-1, 1]."""
    if len(books) < 2:
        return np.nan
    e = 0.0
    depth = 0.0
    for prev, cur in zip(books[:-1], books[1:]):
        if not (prev["bids"] and prev["asks"] and cur["bids"] and cur["asks"]):
            continue
        pb0, qb0, pa0, qa0 = _top(prev)
        pb1, qb1, pa1, qa1 = _top(cur)
        e += (qb1 if pb1 >= pb0 else 0) - (qb0 if pb1 <= pb0 else 0)
        e -= (qa1 if pa1 <= pa0 else 0) - (qa0 if pa1 >= pa0 else 0)
        depth += 0.5 * (qb1 + qa1)
    if depth == 0:
        return np.nan
    return float(np.tanh(e / (depth / max(len(books) - 1, 1)) / np.sqrt(len(books))))


def _mid(book: dict) -> float:
    return 0.5 * (book["bids"][0][0] + book["asks"][0][0])


def depth_persistence(books: list[dict], lag: int = 5) -> float:
    """Fraction of resting depth (on the side price moves toward) that is still there ``lag`` snapshots later.

    Real liquidity absorbs; spoof walls are pulled as price approaches. Depth-weighted across pairs,
    so big walls that flicker in and out dominate the score.
    """
    tot_all = kept_all = 0.0
    for i in range(0, len(books) - lag):
        a, b = books[i], books[i + lag]
        if not (a["bids"] and a["asks"] and b["bids"] and b["asks"]):
            continue
        m0, m1 = _mid(a), _mid(b)
        if m1 < m0:   # moving toward bids
            side_a, side_b, best, sgn = a["bids"], dict(b["bids"]), b["bids"][0][0], 1
        elif m1 > m0:  # moving toward asks
            side_a, side_b, best, sgn = a["asks"], dict(b["asks"]), b["asks"][0][0], -1
        else:
            continue
        tot = kept = 0.0
        for p, q in side_a:
            if sgn * (p - best) > 0:
                continue  # level got traded through; not informative
            tot += q
            kept += min(q, side_b.get(p, 0.0))
        tot_all += tot
        kept_all += kept
    return float(kept_all / tot_all) if tot_all > 0 else np.nan


def cancel_rate(books: list[dict], trades: list[dict]) -> float:
    """Share of removed depth NOT explained by trades (i.e. cancelled)."""
    if len(books) < 2:
        return np.nan
    removed = 0.0
    for prev, cur in zip(books[:-1], books[1:]):
        for side in ("bids", "asks"):
            cur_map = dict(cur[side])
            for p, q in prev[side]:
                dq = q - cur_map.get(p, 0.0)
                if dq > 0:
                    removed += dq
    traded = float(sum(t["qty"] for t in trades)) if trades else 0.0
    if removed <= 0:
        return 0.0
    return float(np.clip((removed - traded) / removed, 0.0, 1.0))


def iceberg_score(books: list[dict], trades: list[dict], tick_tol: float = 1e-9) -> float:
    """Share of traded volume executed at levels where traded size exceeded visible size while
    the level survived (hidden size refilling = iceberg)."""
    if not books or not trades:
        return np.nan
    btimes = np.array([b["ts"] for b in books])
    hidden = total = 0.0
    traded_at: dict[tuple[int, float], float] = {}
    for t in trades:
        i = int(np.clip(np.searchsorted(btimes, t["ts"]) - 1, 0, len(books) - 1))
        traded_at[(i, round(t["price"], 8))] = traded_at.get((i, round(t["price"], 8)), 0.0) + t["qty"]
        total += t["qty"]
    for (i, p), q in traded_at.items():
        book = books[i]
        nxt = books[min(i + 1, len(books) - 1)]
        vis = {**dict(book["bids"]), **dict(book["asks"])}.get(p)
        still = {**dict(nxt["bids"]), **dict(nxt["asks"])}.get(p)
        if vis is not None and still is not None and q > vis + tick_tol:
            hidden += q
    return float(hidden / total) if total > 0 else np.nan


def spread_bps(book: dict) -> float:
    if not (book.get("bids") and book.get("asks")):
        return np.nan
    pb, pa = book["bids"][0][0], book["asks"][0][0]
    return float((pa - pb) / (0.5 * (pa + pb)) * 1e4)


def book_features(books: list[dict], trades: list[dict] | None = None, band_bps: float = 25.0) -> dict:
    """Summarise a captured window of book snapshots/trades into bar-level book columns."""
    have_trades = bool(trades)
    trades = trades or []
    if not books:
        return {}
    last = books[-1]
    mid = _mid(last)
    lo, hi = mid * (1 - band_bps / 1e4), mid * (1 + band_bps / 1e4)
    bid_depth = float(sum(q for p, q in last["bids"] if p >= lo))
    ask_depth = float(sum(q for p, q in last["asks"] if p <= hi))
    return {
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "book_ofi": book_ofi(books),
        "depth_persist": depth_persistence(books),
        "cancel_rate": cancel_rate(books, trades) if have_trades else np.nan,
        "iceberg_score": iceberg_score(books, trades),
        "spread_bps": spread_bps(last),
    }
