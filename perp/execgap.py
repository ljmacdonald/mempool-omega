"""Execution-gap study (ANEE's large-position hypothesis, D123), measured on REAL public GMX orders, not our own.

For every executed GMX market order (opens and closes) in the window:
  oracle gap     how far the execution price was from the oracle price at execution, in the trader's direction
                 (positive = worse for the trader), in basis points
  documented     the price impact GMX itself reported for the order (its published formula), same sign convention
  residual       oracle gap - documented: what the documented mechanics do NOT explain (ANEE: R = observed - predicted)
  fee            the position fee charged vs the documented 0.04% / 0.06%
  delay          seconds from order creation to execution, and the oracle's move over that delay in the trader's
                 direction (from GMX's 1-minute candles, last ~7 days only)
Reported by order size. A size-dependent residual would justify a closer look; it would not by itself show
intent. Passive observation only: no orders are ever sent.
"""
from __future__ import annotations

import bisect
import statistics
from decimal import Decimal

E30 = Decimal(10) ** 30
BUCKETS = [(0, 1_000, "under $1k"), (1_000, 10_000, "$1k-10k"), (10_000, 100_000, "$10k-100k"),
           (100_000, 1_000_000, "$100k-1M"), (1_000_000, 10 ** 12, "$1M+")]


def bucket(size: float) -> str:
    return next(lab for lo, hi, lab in BUCKETS if lo <= size < hi)


def analyse(actions: list[dict], markets: dict[str, dict], minute: dict[str, dict[int, list]]) -> dict:
    """actions: tradeActions rows; markets: address(lower) -> {name, index, index_dec}; minute: index symbol ->
    {minute ts: [o, h, l, c]}."""
    created = {a["orderKey"]: a["timestamp"] for a in actions if a["eventName"] == "OrderCreated"}
    rows = []
    for a in actions:
        if a["eventName"] != "OrderExecuted" or not a.get("executionPrice") or not a.get("indexTokenPriceMax"):
            continue
        m = markets.get((a.get("marketAddress") or "").lower())
        if not m or m.get("index_dec") is None:
            continue
        size = float(Decimal(a["sizeDeltaUsd"]) / E30)
        if size < 10:
            continue
        buy = (a["orderType"] == 2) == bool(a["isLong"])        # opening a long or closing a short buys
        sgn = 1 if buy else -1
        oracle = (Decimal(a["indexTokenPriceMin"]) + Decimal(a["indexTokenPriceMax"])) / 2
        ex = Decimal(a["executionPrice"])
        gap = float((ex / oracle - 1) * sgn * 10000)
        imp = a.get("priceImpactUsd")
        doc = float(-Decimal(imp) / E30 / Decimal(str(size)) * 10000) if imp is not None else None
        fee = None
        if a.get("positionFeeAmount") and a.get("collateralTokenPriceMin"):
            fee = float(Decimal(a["positionFeeAmount"]) * Decimal(a["collateralTokenPriceMin"]) / E30 / Decimal(str(size)) * 10000)
        delay = a["timestamp"] - created[a["orderKey"]] if a["orderKey"] in created else None
        move = None
        mc = minute.get(m["index"])
        if delay is not None and mc:
            keys = sorted(mc)
            i0 = bisect.bisect_right(keys, created[a["orderKey"]]) - 1
            i1 = bisect.bisect_right(keys, a["timestamp"]) - 1
            if i0 >= 0 and i1 >= 0 and keys[i1] - keys[i0] <= 3600:
                p0, p1 = mc[keys[i0]][3], mc[keys[i1]][3]
                move = (p1 / p0 - 1) * sgn * 10000
        rows.append({"size": size, "bucket": bucket(size), "market": m["name"], "open": a["orderType"] == 2, "gap": gap,
                     "documented": doc, "residual": gap - doc if doc is not None else None, "fee": fee, "delay": delay, "move": move})

    def stats(xs):
        xs = [x for x in xs if x is not None]
        if not xs:
            return None
        xs.sort()
        return {"n": len(xs), "median": statistics.median(xs), "mean": statistics.fmean(xs), "p90": xs[int(0.9 * (len(xs) - 1))]}

    by = {}
    for _lo, _hi, lab in BUCKETS:
        rs = [r for r in rows if r["bucket"] == lab]
        by[lab] = {"orders": len(rs), **{k: stats([r[k] for r in rs]) for k in ("gap", "documented", "residual", "fee", "delay", "move")},
                   "opens": len([r for r in rs if r["open"]])}
    span = (min((a["timestamp"] for a in actions), default=None), max((a["timestamp"] for a in actions), default=None))
    big = [r["residual"] for r in rows if r["residual"] is not None and r["size"] >= 100_000]
    small = [r["residual"] for r in rows if r["residual"] is not None and r["size"] < 10_000]
    verdict = None
    if len(big) >= 30 and len(small) >= 30:
        d_ = statistics.median(big) - statistics.median(small)
        verdict = (f"Large orders' unexplained cost differs from small orders' by {d_:+.2f} basis points (median): "
                   + ("no meaningful size effect beyond the documented rules." if abs(d_) < 1 else
                      "a size effect beyond the documented rules worth a closer look (not evidence of intent)."))
    return {"orders": len(rows), "from": span[0], "to": span[1], "by_size": by, "verdict": verdict}
