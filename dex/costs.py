"""All-in cost of a DEX round trip: what you actually take out. Mirrored in site/dexengine.js.

Buy:  gas -> pool fee -> price impact (constant-product: the pool's price moves against you as you trade)
      -> sandwich bots (MEV) -> snipers / front-runners on a published idea -> buy tax
Sell: sell tax -> pool fee -> price impact -> sandwich bots -> gas
depth = half the pool's real liquidity (the SOL/ETH/BNB/stablecoin side).
"""
from __future__ import annotations


def dex_cost(amount: float, price: float, liq_usd: float, pool_fee: float, buy_tax: float, sell_tax: float,
             gas_usd: float, mev: float, sniper: float) -> dict:
    q = max(liq_usd / 2, 1.0)
    a_in = max(amount - gas_usd, 0.0)
    after_fee = a_in * (1 - pool_fee)
    imp = after_fee / (q + after_fee)                       # share lost to price impact
    coins = after_fee * (1 - imp) / price * (1 - mev) * (1 - sniper) * (1 - buy_tax)
    buy = {"gas": min(gas_usd, amount), "fee": a_in * pool_fee, "impact": after_fee * imp,
           "mev": after_fee * (1 - imp) * mev, "sniper": after_fee * (1 - imp) * (1 - mev) * sniper}
    buy["tax"] = after_fee * (1 - imp) * (1 - mev) * (1 - sniper) * buy_tax

    def out(exit_px: float) -> dict:
        gross = coins * exit_px
        y = gross * (1 - sell_tax)
        z = y * (1 - pool_fee)
        simp = z / (q + z)
        recv = z * (1 - simp) * (1 - mev) - gas_usd
        sell = {"tax": gross * sell_tax, "fee": y * pool_fee, "impact": z * simp, "mev": z * (1 - simp) * mev,
                "gas": gas_usd}
        costs = {k: buy.get(k, 0.0) + sell.get(k, 0.0) for k in ("fee", "impact", "tax", "mev", "gas")}
        costs["sniper"] = buy["sniper"]
        return {"net": recv - amount, "received": recv, "costs": costs}

    # break-even exit price: invert the sell side
    k = (amount + gas_usd) / max(1 - mev, 1e-9)
    if coins > 0 and k < q:
        z = k / (1 - k / q)
        be = z / (1 - pool_fee) / (1 - sell_tax) / coins
    else:
        be = float("inf")
    rt = -out(price)["net"] / amount if amount > 0 else 0.0
    return {"coins": coins, "impact_buy": imp, "break_even": be, "round_trip": rt, "at": out}
