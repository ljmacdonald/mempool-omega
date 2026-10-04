"""All-in cost of a DEX round trip: what you actually take out. Mirrored in site/dexengine.js.

Buy:  gas -> pool fee -> price impact (constant-product: the pool's price moves against you as you trade)
      -> sandwich bots (MEV) -> snipers / front-runners on a published idea -> buy tax
Sandwich bots only attack swaps big enough to pay for their own two pool fees: a bot must move the price
itself (trading about price-move x depth), so it profits only when your swap is larger than about
2 x fee x depth. Below that the expected sandwich cost is ~0; it rises to the full allowance at twice that.
Sell: sell tax -> pool fee -> price impact -> sandwich bots -> gas
depth = half the pool's real liquidity (the SOL/ETH/BNB/stablecoin side).
"""
from __future__ import annotations


def mev_share(size: float, pool_fee: float, depth: float) -> float:
    """Fraction of the sandwich allowance a swap of this size actually attracts (0..1)."""
    need = 2 * pool_fee * depth
    return 1.0 if need <= 0 else min(max(size / need - 1, 0.0), 1.0)


def dex_cost(amount: float, price: float, liq_usd: float, pool_fee: float, buy_tax: float, sell_tax: float,
             gas_usd: float, mev: float, sniper: float) -> dict:
    q = max(liq_usd / 2, 1.0)
    a_in = max(amount - gas_usd, 0.0)
    after_fee = a_in * (1 - pool_fee)
    imp = after_fee / (q + after_fee)                       # share lost to price impact
    mb = mev * mev_share(after_fee, pool_fee, q)
    coins = after_fee * (1 - imp) / price * (1 - mb) * (1 - sniper) * (1 - buy_tax)
    buy = {"gas": min(gas_usd, amount), "fee": a_in * pool_fee, "impact": after_fee * imp,
           "mev": after_fee * (1 - imp) * mb, "sniper": after_fee * (1 - imp) * (1 - mb) * sniper}
    buy["tax"] = after_fee * (1 - imp) * (1 - mb) * (1 - sniper) * buy_tax

    def out(exit_px: float) -> dict:
        gross = coins * exit_px
        y = gross * (1 - sell_tax)
        z = y * (1 - pool_fee)
        simp = z / (q + z)
        ms = mev * mev_share(z, pool_fee, q)
        recv = z * (1 - simp) * (1 - ms) - gas_usd
        sell = {"tax": gross * sell_tax, "fee": y * pool_fee, "impact": z * simp, "mev": z * (1 - simp) * ms,
                "gas": gas_usd}
        costs = {k: buy.get(k, 0.0) + sell.get(k, 0.0) for k in ("fee", "impact", "tax", "mev", "gas")}
        costs["sniper"] = buy["sniper"]
        return {"net": recv - amount, "received": recv, "costs": costs}

    # break-even exit price: the money out rises with the exit price, so solve it by bisection
    target = amount
    lo, hi = 0.0, price
    if coins > 0:
        for _ in range(200):
            if out(hi)["received"] >= target:
                break
            hi *= 2
        else:
            hi = float("inf")
    be = float("inf")
    if coins > 0 and hi != float("inf"):
        for _ in range(200):
            mid = (lo + hi) / 2
            if out(mid)["received"] >= target:
                hi = mid
            else:
                lo = mid
        be = hi
    rt = -out(price)["net"] / amount if amount > 0 else 0.0
    return {"coins": coins, "impact_buy": imp, "break_even": be, "round_trip": rt, "at": out}
