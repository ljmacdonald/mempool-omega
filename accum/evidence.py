"""Evidence calculations for the Accumulation lab (D119). Pure functions: no network, no files.

Categories are kept apart (spec part 4.3):
  A  verified on-chain swaps: a DEX swap whose signing wallet (tx_from_address) bought or sold the token;
  B  wallet balance changes: a wallet's balance moved, which is NOT proof of a purchase;
  C  exchange trading activity: taker buy volume on an exchange, with no buyer identity;
  D  transfers and operational movements: never counted as buying or selling;
  E  inferred relationships: not built (no defensible free heuristic); every address is counted as an address.

Round trips (a wallet whose net that day is under ROUND_TRIP_SHARE of what it traded: arbitrage and market-making bots)
are left out of every acquisition figure and reported separately. A wallet buying many small chunks is KEPT: that is
exactly what stealth accumulation looks like (D120); it is only labelled frequent.
Missing values stay None. Nothing is ever divided by zero; nothing missing is shown as 0.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from accum import config as K


def day_of(ts: str) -> str:
    return ts[:10]


def parse(ts: str) -> datetime:
    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def trade_key(t: dict) -> str:
    return f"{t['tx']}|{t['wallet']}|{t['side']}|{t.get('qty')}"


def merge_trades(old: list[dict], new: list[dict]) -> tuple[list[dict], int]:
    """Add new swaps, never twice (the same swap comes back in the next poll)."""
    seen = {trade_key(t) for t in old}
    added = [t for t in new if trade_key(t) not in seen and not seen.add(trade_key(t))]
    return sorted(old + added, key=lambda t: t["ts"]), len(added)


def poll_interval(poll_at: datetime, prev_poll: datetime | None, trades: list[dict], limit: int = 300) -> tuple[datetime, datetime]:
    """The span of time this poll's swaps fully cover. The source returns at most `limit` swaps from the last 24 h:
    if it returned the maximum, anything older than its oldest swap may be missing."""
    full = len(trades) >= limit
    oldest = parse(trades[0]["ts"]) if trades else None
    start = prev_poll if prev_poll else poll_at - timedelta(hours=24)
    if full and oldest and oldest > start:
        start = oldest
    return max(start, poll_at - timedelta(hours=24)), poll_at


def coverage_by_day(intervals: list[tuple[datetime, datetime]]) -> dict[str, float]:
    """Share of each UTC day covered by the polls (union of intervals)."""
    iv = sorted(intervals)
    merged: list[list[datetime]] = []
    for a, b in iv:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out: dict[str, float] = defaultdict(float)
    for a, b in merged:
        t = a
        while t < b:
            d0 = datetime(t.year, t.month, t.day, tzinfo=timezone.utc)
            d1 = d0 + timedelta(days=1)
            seg = min(b, d1) - t
            out[d0.date().isoformat()] += seg.total_seconds() / 86400
            t = min(b, d1)
    return {k: round(min(1.0, v), 4) for k, v in out.items()}


def daily(trades: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """Per-day token totals and per-wallet rows from raw swaps. Bots and round trips are excluded from the totals."""
    per: dict[tuple[str, str], dict] = {}
    for t in trades:
        if t.get("anomalous") or t.get("qty") is None:
            continue
        k = (day_of(t["ts"]), t["wallet"])
        w = per.setdefault(k, {"date": k[0], "wallet": k[1], "buy_qty": 0.0, "sell_qty": 0.0, "buy_usd": 0.0, "sell_usd": 0.0, "n": 0,
                               "last_ts": "", "last_price": None})
        s = "buy" if t["side"] == "buy" else "sell"
        w[f"{s}_qty"] += t["qty"]
        w[f"{s}_usd"] += t.get("usd") or 0.0
        w["n"] += 1
        if t["ts"] >= w["last_ts"]:
            w["last_ts"], w["last_price"] = t["ts"], t.get("price")
    days: dict[str, dict] = {}
    wallets = []
    for (d, _), w in per.items():
        gross = w["buy_qty"] + w["sell_qty"]
        net = w["buy_qty"] - w["sell_qty"]
        bot = w["buy_qty"] > 0 and w["sell_qty"] > 0 and gross > 0 and abs(net) < K.ROUND_TRIP_SHARE * gross
        dd = days.setdefault(d, {"date": d, "buy_qty": 0.0, "sell_qty": 0.0, "buy_usd": 0.0, "sell_usd": 0.0, "buyers": 0, "sellers": 0,
                                 "bot_usd": 0.0, "n": 0, "close": None, "close_ts": ""})
        dd["n"] += w["n"]
        if w["last_ts"] >= dd["close_ts"] and w["last_price"] is not None:
            dd["close_ts"], dd["close"] = w["last_ts"], w["last_price"]
        if bot:
            dd["bot_usd"] += w["buy_usd"] + w["sell_usd"]
            continue
        for k in ("buy_qty", "sell_qty", "buy_usd", "sell_usd"):
            dd[k] += w[k]
        dd["buyers" if net > 0 else "sellers"] += 1 if net != 0 else 0
        net_usd = w["buy_usd"] - w["sell_usd"]
        if abs(net_usd) >= K.WALLET_KEEP_USD:
            wallets.append({k: w[k] for k in ("date", "wallet", "buy_qty", "sell_qty", "buy_usd", "sell_usd", "n")})
    for dd in days.values():
        dd.pop("close_ts", None)
    return days, wallets


def window_dates(end: date, n: int) -> list[str]:
    return [(end - timedelta(days=i)).isoformat() for i in range(n)]


def circulating(tok: dict) -> tuple[float | None, str]:
    """Circulating supply = market cap / price, both from CoinGecko through GeckoTerminal. None if either is missing."""
    mc, px = tok.get("market_cap_usd"), tok.get("price_usd")
    if mc and px and mc > 0 and px > 0:
        return mc / px, "market cap / price (CoinGecko via GeckoTerminal)"
    return None, "unavailable: no market cap for this token"


def window_metrics(days: dict[str, dict], wallets: list[dict], coverage: dict[str, float], end: date, n: int,
                   circ: float | None, reserve: float | None) -> dict:
    ds = window_dates(end, n)
    cov = [coverage.get(d, 0.0) for d in ds]
    covered = sum(1 for c in cov if c >= 0.5)
    rows = [days[d] for d in ds if d in days]
    net_qty = sum(r["buy_qty"] - r["sell_qty"] for r in rows) if rows else None
    net_usd = sum(r["buy_usd"] - r["sell_usd"] for r in rows) if rows else None
    gross_usd = sum(r["buy_usd"] + r["sell_usd"] for r in rows) if rows else None
    buy_days = sum(1 for r in rows if r["buy_qty"] - r["sell_qty"] > 0)
    per: dict[str, float] = defaultdict(float)
    per_usd: dict[str, float] = defaultdict(float)
    for w in wallets:
        if w["date"] in ds:
            per[w["wallet"]] += w["buy_qty"] - w["sell_qty"]
            per_usd[w["wallet"]] += w["buy_usd"] - w["sell_usd"]
    acc = sorted(((a, q) for a, q in per.items() if q > 0), key=lambda x: -x[1])
    dist = [a for a, q in per.items() if q < 0]
    pos = sum(q for _, q in acc)
    top3 = sum(q for _, q in acc[:3]) / pos if pos > 0 else None
    return {"days": n, "covered_days": covered, "coverage": round(sum(cov) / n, 3), "complete": covered >= n,
            "net_qty": net_qty, "net_usd": net_usd, "gross_usd": gross_usd, "buy_days": buy_days,
            "share": (net_qty / circ) if (net_qty is not None and circ) else None,
            "acc_wallets": len(acc), "dist_wallets": len(dist), "top3_share_of_buying": top3,
            "top": [{"wallet": a, "net_qty": q, "net_usd": per_usd[a], "share": (q / circ) if circ else None} for a, q in acc[:K.TOP_WALLETS]],
            "vs_liquidity": (net_usd / reserve) if (net_usd is not None and reserve) else None,
            "vs_volume": (net_usd / gross_usd) if (net_usd is not None and gross_usd) else None}


def buyer_relative(net_qty: float, balance_now: float | None) -> dict:
    """How big a wallet's buying is against what it held before. The 'before' balance is estimated as today's balance
    minus its net buying in the window (transfers in or out in between make this an estimate, said so)."""
    if balance_now is None:
        return {"label": "reference balance unavailable", "multiple": None, "before": None, "now": None}
    before = balance_now - net_qty
    if before <= max(1e-9, 0.01 * net_qty):
        return {"label": "newly observed holder", "multiple": None, "before": max(before, 0.0), "now": balance_now}
    return {"label": f"{net_qty / before:.1f}x its earlier holding", "multiple": net_qty / before, "before": before, "now": balance_now}


def price_context(candles: list[dict], end: date, n: int) -> dict:
    """Price change, realised volatility and whether the token is already repricing, from daily closes."""
    cs = [c for c in candles if c.get("c")]
    if len(cs) < 2:
        return {"change": None, "vol": None, "repricing": None}
    end_ts = datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()
    last = [c for c in cs if c["t"] <= end_ts + 86400] or cs
    ref = [c for c in last if c["t"] <= last[-1]["t"] - n * 86400]
    if not ref:
        return {"change": None, "vol": None, "repricing": None}
    p0, p1 = ref[-1]["c"], last[-1]["c"]
    seg = [c["c"] for c in last if c["t"] > ref[-1]["t"] - 1]
    rets = [math.log(b / a) for a, b in zip(seg, seg[1:]) if a and b]
    vol = (sum(r * r for r in rets) / len(rets)) ** 0.5 if rets else None
    ch = p1 / p0 - 1
    return {"change": ch, "vol": vol, "repricing": ch >= K.REPRICING}


def weekly_nets(days: dict[str, dict], end: date, weeks: int = 4) -> list[float | None]:
    out = []
    for w in range(weeks):
        ds = window_dates(end - timedelta(days=7 * w), 7)
        rows = [days[d] for d in ds if d in days]
        out.append(sum(r["buy_qty"] - r["sell_qty"] for r in rows) if rows else None)
    return out


def classify_transfers(transfers: list[dict], swap_txs: set[str], exchanges: dict[str, str], watch: set[str]) -> dict:
    """Ethereum transfers (category D): those that are legs of a known swap are swaps; the rest are transfers.
    Returns transfers into watched wallets (never counted as buying) and from watched wallets to exchanges
    (a possible-sale warning, never proof)."""
    into, to_ex = [], []
    for t in transfers:
        if t["tx"] in swap_txs:
            continue
        if t["to"] in watch:
            into.append(t)
        if t["from"] in watch and t["to"] in exchanges:
            to_ex.append({**t, "exchange": exchanges[t["to"]]})
    return {"transfers_in": into, "to_exchange": to_ex}
