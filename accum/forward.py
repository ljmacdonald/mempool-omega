"""Forward evaluation of every watchlist detection (D119): did the token do better afterwards than the other tokens we
were watching over the same days? Pure functions.

Each detection keeps its own frozen time and price. At 7/14/30/60/90 days its return (daily close to daily close,
minus a round trip's costs) is compared with the average return of every other universe token on the same chain over
the same days: the "random pick" yardstick. Failures and tokens that later vanish are kept (a token with no price at
the horizon is recorded as "no price", never dropped silently).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from accum import config as K


def close_on(candles: list[dict], day: datetime) -> float | None:
    """The close of the last daily candle at or before `day` (within 3 days, else None)."""
    t = day.timestamp()
    best = None
    for c in candles:
        if c["t"] <= t and c.get("c"):
            best = c
    if best is None or t - best["t"] > 3 * 86400:
        return None
    return best["c"]


def excursions(candles: list[dict], t0: datetime, t1: datetime, p0: float) -> tuple[float | None, float | None]:
    seg = [c for c in candles if t0.timestamp() < c["t"] <= t1.timestamp() and c.get("h") and c.get("l")]
    if not seg:
        return None, None
    return max(c["h"] for c in seg) / p0 - 1, min(c["l"] for c in seg) / p0 - 1


def evaluate(detections: list[dict], candles: dict[str, list[dict]], chain_of: dict[str, str], now: datetime) -> list[dict]:
    """One row per detection and horizon that has passed. detections: {key, chain, at, price, route, score}."""
    out = []
    for d in detections:
        t0 = datetime.fromisoformat(d["at"].replace("Z", "+00:00"))
        day0 = datetime(t0.year, t0.month, t0.day, tzinfo=timezone.utc)
        for h in K.HORIZONS:
            t1 = day0 + timedelta(days=h)
            if t1 + timedelta(days=1) > now:
                continue
            c = candles.get(d["key"]) or []
            p0, p1 = close_on(c, day0), close_on(c, t1)
            cost = 2 * K.DEX_FEE
            ret = (p1 / p0 - 1 - cost) if p0 and p1 else None
            others = []
            for k, cs in candles.items():
                if k == d["key"] or chain_of.get(k) != d["chain"]:
                    continue
                a, b = close_on(cs, day0), close_on(cs, t1)
                if a and b:
                    others.append(b / a - 1 - cost)
            base = sum(others) / len(others) if others else None
            mfe, mae = excursions(c, day0, t1, p0) if p0 else (None, None)
            out.append({"key": d["key"], "symbol": d.get("symbol"), "route": d.get("route"), "at": d["at"], "horizon": h,
                        "ret": ret, "random": base, "edge": (ret - base) if ret is not None and base is not None else None,
                        "mfe": mfe, "mae": mae, "n_random": len(others), "status": "ok" if ret is not None else "no price"})
    return out


def summary(rows: list[dict], horizon: int = 30) -> dict:
    rs = [r for r in rows if r["horizon"] == horizon and r["edge"] is not None]
    n = len(rs)
    if not n:
        return {"horizon": horizon, "n": 0, "avg": None, "random": None, "edge": None, "t_edge": None, "hit": None,
                "no_price": sum(1 for r in rows if r["horizon"] == horizon and r["status"] != "ok")}
    e = [r["edge"] for r in rs]
    m = sum(e) / n
    sd = (sum((x - m) ** 2 for x in e) / (n - 1)) ** 0.5 if n > 2 else None
    return {"horizon": horizon, "n": n, "avg": sum(r["ret"] for r in rs) / n, "random": sum(r["random"] for r in rs) / n,
            "edge": m, "t_edge": (m / (sd / n ** 0.5)) if sd else None, "hit": sum(1 for r in rs if r["ret"] > 0) / n,
            "no_price": sum(1 for r in rows if r["horizon"] == horizon and r["status"] != "ok")}
