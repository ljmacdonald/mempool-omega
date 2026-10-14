"""Adversarial execution economics (C). Every amount is in lamports (integers), and every cost is counted once.

Where each cost lives:
  * pool and aggregator fees, price impact, integer rounding: already inside the quoted output amounts (never added
    again here);
  * base fee: per signature, paid whenever the transaction lands, even if it then fails;
  * priority fee: compute-unit limit x price, paid on landing, including when the swap reverts on its minimum output;
  * Jito tip: paid only when the transaction carrying it lands and succeeds (a bundle that would fail is not
    committed, per Jito's documentation), so a reverted bundle costs nothing.

Three submission methods are compared on identical opportunities:
  rpc_plain      a normal transaction with a median priority fee (the baseline)
  jito_bundle    a bundle with a tip at the chosen tip-floor percentile, revert-protected
  jito_dontfront sendTransaction via Jito (bundleOnly) with a jitodontfront account, 70/30 priority fee / tip split

Expected value is reported PER LANDED ATTEMPT. The chance of landing at all can't be measured without sending real
transactions, so it stays INSUFFICIENT_EVIDENCE and live execution stays off (G).
"""
from __future__ import annotations

from dataclasses import dataclass

from mev import config as K


@dataclass(frozen=True)
class Costs:
    method: str
    on_success: int        # paid when the trade lands and succeeds
    on_revert: int         # paid when it lands but reverts on its minimum output
    on_miss: int = 0       # paid when it never lands (always 0 on Solana: an unlanded transaction is not charged)


def priority_fee(cu_price_micro: float, cu_limit: int = K.CU_LIMIT) -> int:
    return int(-(-cu_limit * min(cu_price_micro, K.MAX_CU_PRICE) // 1_000_000))   # rounded up


def method_costs(cu_price_micro: float, tip: int) -> dict[str, Costs]:
    """Fee bids are bounded by the hard ceilings; a bid buys a chance of inclusion, never a guaranteed place."""
    tip = int(min(max(tip, K.JITO_MIN_TIP), K.MAX_TIP_LAMPORTS))
    pf = priority_fee(cu_price_micro)
    # 70/30 split of the same total budget for Jito sendTransaction
    budget = pf + tip
    df_tip = max(K.JITO_MIN_TIP, int(budget * K.JITO_TIP_SPLIT))
    df_pf = budget - df_tip
    return {
        "rpc_plain": Costs("rpc_plain", K.BASE_FEE + pf, K.BASE_FEE + pf),
        "jito_bundle": Costs("jito_bundle", K.BASE_FEE + tip, 0),
        "jito_dontfront": Costs("jito_dontfront", K.BASE_FEE + df_pf + df_tip, 0),
    }


def net_profit(amount_in: int, amount_out: int, costs: Costs) -> int:
    """Profit of one landed, successful round trip after the method's own costs."""
    return int(amount_out) - int(amount_in) - costs.on_success


def min_out(amount_in: int, costs: Costs) -> int:
    """The minimum output enforced on-chain: below this the trade must revert instead of losing money."""
    return int(amount_in) + costs.on_success + K.MIN_PROFIT


def ev_per_landed(p_profit: float | None, avg_profit_if_ok: float | None, costs: Costs) -> float | None:
    """Expected result per landed attempt: survivors earn their measured (re-checked, not discovery) profit; the
    rest revert on the minimum output and pay the method's revert cost. None when the inputs aren't measured."""
    if p_profit is None or avg_profit_if_ok is None:
        return None
    return p_profit * avg_profit_if_ok - (1 - p_profit) * costs.on_revert


def tip_from_floor(floor: dict | None, pct: int = 75) -> int:
    """Tip in lamports from Jito's tip floor (values are in SOL). Falls back to the documented minimum."""
    try:
        return max(K.JITO_MIN_TIP, int(round(float(floor[f"landed_tips_{pct}th_percentile"]) * K.LAMPORTS)))
    except (TypeError, KeyError, ValueError):
        return K.JITO_MIN_TIP
