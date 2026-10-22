"""Strategy 4: directional systematic on GMX v2 (D123). Two signal families, tracked separately.

  4a trend            1-hour bars: long when the close is above the 50-bar EMA and breaks the prior 20-bar high;
                      short on the mirror; skipped when ATR(14) is in the bottom 20% of its last 30 days
  4b mean reversion   15-minute bars: fade a close more than 2.5 standard deviations from the 4-hour VWAP, only
                      when the 1-hour trend is flat (EMA50 moved less than 0.5 ATR over 10 hours)
Both: enter at the next bar's open; stop 1.5 ATR; take profit 3 ATR or a 2-ATR trailing stop; a gap through the
stop exits at the worse open (AT-06); a bar touching both stop and target counts as the stop. Size so a stop-out
loses 0.5% of the sleeve INCLUDING every cost, leverage at most 2x and margin at most 20% of the sleeve; a trade
whose costs alone exceed that budget isn't taken (logged). Costs: GMX's 0.06% fee each way (the conservative
case), price impact at today's market balance, the real hourly funding and borrowing for the side, execution fees.
Prices: Binance's public candles (years of history; GMX's oracle tracks the same exchange prices). The contrarian
filter (skip the crowded side above 70/30 open interest) needs open-interest history the free sources don't keep,
so it is applied going forward only.
"""
from __future__ import annotations

import bisect
from decimal import Decimal

from perp import costs as CO
from perp.config import load
from perp.money import ZERO, Trade, d, iso

H = Decimal(3600)


def ema(xs: list[float], n: int) -> list[float | None]:
    k, out, e = 2 / (n + 1), [], None
    for i, x in enumerate(xs):
        e = x if e is None else x * k + e * (1 - k)
        out.append(e if i >= n - 1 else None)
    return out


def atr(h: list[float], lo: list[float], c: list[float], n: int) -> list[float | None]:
    tr = [h[0] - lo[0]] + [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]
    out, a = [], None
    for i, x in enumerate(tr):
        a = x if a is None else (a * (n - 1) + x) / n
        out.append(a if i >= n else None)
    return out


def signals_trend(bars: list[list], P: dict) -> list[tuple[int, int]]:
    """[(bar index, side)] for 4a, using bars up to and including that index only."""
    c, h, lo = [b[4] for b in bars], [b[2] for b in bars], [b[3] for b in bars]
    e, a = ema(c, P["ema"]), atr(h, lo, c, P["atr"])
    W, out = P["atr_floor_window"], []
    for i in range(max(P["ema"], P["breakout"] + 1, W), len(bars)):
        if e[i] is None or a[i] is None:
            continue
        hist = sorted(x for x in a[i - W:i] if x is not None)
        if not hist or a[i] <= hist[int(P["atr_floor_pct"] * len(hist))]:
            continue                                   # dead market
        hi20, lo20 = max(h[i - P["breakout"]:i]), min(lo[i - P["breakout"]:i])
        if c[i] > e[i] and c[i] > hi20:
            out.append((i, 1))
        elif c[i] < e[i] and c[i] < lo20:
            out.append((i, -1))
    return out


def signals_reversion(bars15: list[list], bars1h: list[list], P: dict) -> list[tuple[int, int]]:
    """[(15-minute bar index, side)] for 4b; the 1-hour filter only uses hours already closed."""
    c1, h1, l1 = [b[4] for b in bars1h], [b[2] for b in bars1h], [b[3] for b in bars1h]
    e1, a1 = ema(c1, P["ema"]), atr(h1, l1, c1, P["atr"])
    t1 = [b[0] for b in bars1h]
    flat = {}
    for j in range(10, len(bars1h)):
        if e1[j] is not None and e1[j - 10] is not None and a1[j] is not None:
            flat[t1[j]] = abs(e1[j] - e1[j - 10]) < P["flat_slope_atr"] * a1[j]
    n, out = P["mr_vwap_bars"], []
    for i in range(n, len(bars15)):
        win = bars15[i - n + 1:i + 1]
        vol = sum(b[7] for b in win)
        if vol <= 0:
            continue
        vwap = sum((b[2] + b[3] + b[4]) / 3 * b[7] for b in win) / vol
        cs = [b[4] for b in win]
        m = sum(cs) / n
        sd = (sum((x - m) ** 2 for x in cs) / n) ** 0.5
        if sd <= 0:
            continue
        z = (bars15[i][4] - vwap) / sd
        if abs(z) <= P["mr_sigma"]:
            continue
        k = bisect.bisect_right(t1, bars15[i][0] + 900_000 - 3_600_000) - 1  # the last hour closed by this bar's end
        if k < 0 or not flat.get(t1[k]):
            continue
        out.append((i, -1 if z > 0 else 1))
    return out


def run_trades(bars: list[list], sigs: list[tuple[int, int]], market: dict, start_usd, label: str, book: str,
               cost_mult: Decimal = Decimal(1)) -> tuple[list[Trade], list[dict]]:
    """Simulates entries at the next open, exits per the rules, every cost itemised. Returns trades and rejections."""
    P, R = load()["directional"], load()["risk"]
    c, h, lo = [b[4] for b in bars], [b[2] for b in bars], [b[3] for b in bars]
    a = atr(h, lo, c, P["atr"])
    sleeve = d(start_usd)
    budget = sleeve * d(R["max_loss_per_position"])
    cap = sleeve * d(R["max_margin_share"]) * d(R["max_leverage"])
    trades, rejected, busy_until = [], [], -1
    for i, side in sigs:
        if i <= busy_until or i + 1 >= len(bars) or a[i] is None:
            continue
        entry = bars[i + 1][1]
        stop_dist = P["stop_atr"] * a[i]
        hold_guess = 24
        rates = market["rate_at"](bars[i][0] // 1000)
        ctx = {"settings": market["settings"], "oi_long": market["oi"][0], "oi_short": market["oi"][1],
               "funding_ps_long": rates.get("fund_long"), "funding_ps_short": rates.get("fund_short"),
               "borrow_ps_long": rates.get("borrow_long"), "borrow_ps_short": rates.get("borrow_short"),
               "execution_fee_usd": market["exec_fee"]}
        try:
            probe = CO.estimate_costs("gmx", ctx, side, Decimal(1000), hold_guess)
        except CO.NotTradeable as e:
            rejected.append({"t": iso(bars[i][0] // 1000), "why": f"not tradeable: {e}"})
            continue
        fixed = probe["items"]["execution_fee"] + probe["items"]["gas"]
        var_share = (probe["base"] * cost_mult - fixed * cost_mult) / 1000
        loss_share = d(stop_dist / entry) + var_share
        n = (budget - fixed * cost_mult) / loss_share if budget > fixed * cost_mult else ZERO
        n = min(n, cap)
        if n < 10:
            rejected.append({"t": iso(bars[i][0] // 1000), "why": "costs alone exceed the 0.5% risk budget at this account size"})
            continue
        stop = entry - side * stop_dist
        target = entry + side * P["take_atr"] * a[i]
        best, exit_px, why, j = entry, None, None, i + 1
        while j < len(bars):
            o, hi, lw, cl = bars[j][1], h[j], lo[j], c[j]
            if j > i + 1 and ((side > 0 and o <= stop) or (side < 0 and o >= stop)):
                exit_px, why = o, "gap through the stop"
                break
            hit_stop = (lw <= stop) if side > 0 else (hi >= stop)
            hit_tp = (hi >= target) if side > 0 else (lw <= target)
            if hit_stop:
                exit_px, why = stop, "stop"
                break
            if hit_tp:
                exit_px, why = target, "take profit (3 ATR)"
                break
            best = max(best, cl) if side > 0 else min(best, cl)
            trail = best - side * P["trail_atr"] * a[i]
            if (side > 0 and best > entry and trail > stop) or (side < 0 and best < entry and trail < stop):
                stop = trail
            j += 1
        if exit_px is None:
            break
        items = CO.gmx_round_trip(ctx, side, n, 0)          # fees, impact, execution; holding costs from the actual path:
        k = "long" if side > 0 else "short"
        t0, t1_ = bars[i + 1][0] // 1000 // 3600 * 3600, bars[j][0] // 1000
        path = [market["rate_at"](t) for t in range(t0, t1_, 3600)] or [rates]
        for src, dst in ((f"fund_{k}", "funding"), (f"borrow_{k}", "borrow")):
            vals = [r.get(src) for r in path]
            if any(v is None for v in vals):
                rejected.append({"t": iso(bars[i][0] // 1000), "why": f"missing {dst} history during the hold (trade dropped)"})
                items = None
                break
            items[dst] = n * sum(vals, ZERO) * H * Decimal(max(1, (t1_ - t0) // 3600)) / len(vals)
        if items is None:
            busy_until = j
            continue
        tr = Trade(label, book, market["name"], side, n, iso(bars[i + 1][0] // 1000))
        tr.gross = d(side) * n * (d(exit_px) / d(entry) - 1)
        for k, v in items.items():
            tr.add_cost(k, v * cost_mult if v > 0 else v)
        tr.est_costs = {k: str(v) for k, v in CO.gmx_round_trip(ctx, side, n, hold_guess).items()}
        tr.closed, tr.reason = iso(bars[j][0] // 1000), why
        trades.append(tr)
        busy_until = j
    return trades, rejected


def limit_group(trades: list[Trade]) -> list[Trade]:
    """AT-09 in the backtest: across all markets, at most one open position per side at a time (first come)."""
    kept: list[Trade] = []
    for t in sorted(trades, key=lambda t: t.opened):
        if not any(k.side == t.side and k.opened <= t.opened < (k.closed or "9") for k in kept):
            kept.append(t)
    return kept
