"""The cost model (D123), shared by every strategy: estimate_costs() returns an itemised round trip.

GMX v2 (from the market's own published settings, never hard-coded):
  position fee   0.04% when the trade improves the long/short balance, 0.06% when it worsens it (per market)
  price impact   GMX's formula: factor x (imbalance after ^ exponent - imbalance before ^ exponent), positive
                 (a rebate) when the imbalance shrinks, capped for negative impact at the market's maximum
  borrowing      per second on the position, by side; funding per second, by side (paid or received)
  execution fee  the keeper fee each order pays (measured from recent real orders) + Arbitrum gas
Hyperliquid (hedge venue): documented taker fee, slippage from walking the live order book (or an explicit
assumption for history), hourly funding.

Three estimates: base (best estimate), adverse (every cost x2, no rebates, half the income) for eligibility, and
extreme (x4, no rebates, no income) for risk. A missing input makes the market NOT_TRADEABLE: costs are never
guessed as zero.
"""
from __future__ import annotations

from decimal import Decimal

from perp.config import load
from perp.money import ZERO, d

HOUR = Decimal(3600)


class NotTradeable(Exception):
    pass


def need(x, what: str):
    if x is None:
        raise NotTradeable(f"missing {what}")
    return d(x)


def gmx_impact(oi_long, oi_short, side: int, delta, s: dict) -> Decimal:
    """GMX position price impact in USD for changing `side` open interest by `delta` (negative on close).
    Positive = the trader gains (imbalance shrinks), negative = cost."""
    L, S = need(oi_long, "long open interest"), need(oi_short, "short open interest")
    fp, fn = need(s.get("impact_pos"), "impact factor +"), need(s.get("impact_neg"), "impact factor -")
    ep, en = need(s.get("impact_exp_pos"), "impact exponent +"), need(s.get("impact_exp_neg"), "impact exponent -")
    L2, S2 = (L + d(delta), S) if side > 0 else (L, S + d(delta))
    a, b = L - S, L2 - S2

    def pw(x, e):
        return abs(x) ** e if x else ZERO
    if (a >= 0) == (b >= 0) or a == 0 or b == 0:
        if abs(b) < abs(a):
            imp = fp * (pw(a, ep) - pw(b, ep))
        else:
            imp = -fn * (pw(b, en) - pw(a, en))
    else:                                            # crosses over: the first part helps, the rest hurts
        imp = fp * pw(a, ep) - fn * pw(b, en)
    if imp < 0 and s.get("max_impact_neg") is not None:
        imp = max(imp, -d(s["max_impact_neg"]) * abs(d(delta)))
    return imp


def gmx_round_trip(ctx: dict, side: int, size, hold_hours) -> dict:
    """Base-case itemised costs of opening and later closing `size` USD on GMX (positive = paid)."""
    size, hrs = d(size), d(hold_hours)
    s = ctx["settings"]
    L, S = ctx.get("oi_long"), ctx.get("oi_short")
    imp_open = gmx_impact(L, S, side, size, s)
    L1, S1 = (d(L) + size, d(S)) if side > 0 else (d(L), d(S) + size)
    imp_close = gmx_impact(L1, S1, side, -size, s)

    def fee(imp):
        return need(s.get("fee_pos") if imp >= 0 else s.get("fee_neg"), "position fee factor")
    fund = need(ctx.get("funding_ps_long" if side > 0 else "funding_ps_short"), "funding rate")
    borrow = need(ctx.get("borrow_ps_long" if side > 0 else "borrow_ps_short"), "borrowing rate")
    xfee = need(ctx.get("execution_fee_usd"), "execution fee")
    gas = d(load()["costs"]["arbitrum_tx_usd"])
    return {"open_fee": size * fee(imp_open), "close_fee": size * fee(imp_close), "price_impact": -(imp_open + imp_close),
            "borrow": size * borrow * hrs * HOUR, "funding": size * fund * hrs * HOUR,
            "execution_fee": 2 * xfee, "gas": 2 * gas}


def book_slippage(book: dict | None, side: int, size, mid) -> Decimal | None:
    """USD cost of a market order of `size` USD against an L2 book {bids, asks: [(px, sz)]} vs the mid."""
    if not book:
        return None
    levels = book["asks"] if side > 0 else book["bids"]
    left, cost = d(size), ZERO
    for px, sz in levels:
        px, sz = d(px), d(sz)
        take = min(left, px * sz)
        cost += take * abs(px / d(mid) - 1)
        left -= take
        if left <= 0:
            return cost
    return None                                       # the book is too thin for this size


def hl_round_trip(ctx: dict, side: int, size, hold_hours, assumed_slip_bps=2) -> dict:
    """Hyperliquid hedge leg: documented taker fee both ways, slippage (live book, else an explicit assumption), funding."""
    size, hrs = d(size), d(hold_hours)
    C = load()["costs"]["hyperliquid"]
    slip = book_slippage(ctx.get("book"), side, size, ctx.get("mid")) if ctx.get("book") else None
    slip = 2 * slip if slip is not None else size * d(assumed_slip_bps) / 10000 * 2
    rate = need(ctx.get("funding_hourly"), "hedge funding rate")            # positive: longs pay shorts
    return {"hedge_fee": size * d(C["taker"]) * 2, "hedge_slippage": slip, "hedge_funding": D_side(side) * size * rate * hrs}


def D_side(side: int) -> Decimal:
    return Decimal(side)


def scenarios(items: dict) -> dict:
    """base / adverse / extreme totals from an itemised base estimate."""
    C = load()["costs"]
    out = {}
    for name, mult, income in (("base", Decimal(1), Decimal(1)), ("adverse", d(C["adverse_multiplier"]), Decimal("0.5")),
                               ("extreme", d(C["extreme_multiplier"]), ZERO)):
        tot = sum(((v * mult) if v > 0 else (v * income) for v in items.values()), ZERO)
        out[name] = tot
    return out


def estimate_costs(venue: str, ctx: dict, side: int, size, hold_hours) -> dict:
    """The one entry point every strategy calls before any paper order. Raises NotTradeable on missing inputs."""
    items = gmx_round_trip(ctx, side, size, hold_hours) if venue == "gmx" else hl_round_trip(ctx, side, size, hold_hours)
    return {"items": items, **scenarios(items)}


def gmx_settings(raw: dict) -> dict:
    """Converts a GMX marketInfo record (30-decimal fixed point) into the cost model's settings."""

    def f(k):
        return (d(raw[k]) / (Decimal(10) ** 30)) if raw.get(k) is not None else None
    return {"fee_pos": f("positionFeeFactorForPositiveImpact"), "fee_neg": f("positionFeeFactorForNegativeImpact"),
            "impact_pos": f("positionImpactFactorPositive"), "impact_neg": f("positionImpactFactorNegative"),
            "impact_exp_pos": f("positionImpactExponentFactorPositive"), "impact_exp_neg": f("positionImpactExponentFactorNegative"),
            "max_impact_neg": f("maxPositionImpactFactorNegative"), "swap_fee_pos": f("swapFeeFactorForPositiveImpact"),
            "swap_fee_neg": f("swapFeeFactorForNegativeImpact")}
