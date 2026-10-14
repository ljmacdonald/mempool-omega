"""Transaction safety (E): inspect a transaction before it could ever be signed, and fail closed.

The transaction is described as a plain dict (decoded instructions), so every check can be tested without a wallet:
  {"fee_payer": str, "wallet_sol": int, "amount_in": int, "input_mint": str, "output_mint": str,
   "min_out": int, "expected_costs": int, "atomic": bool,
   "instructions": [{"program": str, "kind": str, "accounts": [{"key": str, "writable": bool}], ...}]}
kinds used: "set_cu_limit" (units), "set_cu_price" (micro_lamports), "transfer" (to, lamports),
"swap" (route of mints), "create_ata", "close_account".

Simulation, where one is run, is a filter and never a guarantee; it is not a substitute for these checks.
"""
from __future__ import annotations

from mev import config as K


class Rejected(Exception):
    pass


def inspect(tx: dict) -> dict:
    """Returns {"ok": True, "checks": [...]} or raises Rejected naming the first failed check. Missing data fails."""
    checks = []

    def need(cond, name, why):
        if not cond:
            raise Rejected(f"{name}: {why}")
        checks.append(name)

    try:
        ins = tx["instructions"]
        payer, wallet = tx["fee_payer"], int(tx["wallet_sol"])
        amount_in, mout, costs = int(tx["amount_in"]), int(tx["min_out"]), int(tx["expected_costs"])
        mints = (tx["input_mint"], tx["output_mint"])
    except (KeyError, TypeError, ValueError) as e:
        raise Rejected(f"missing or malformed field: {e}") from e

    need(isinstance(ins, list) and ins, "instructions present", "no instructions")
    need(tx.get("atomic") is True, "atomic", "the round trip must be one transaction; it is never split into separate swaps")
    need(all(m in K.MINTS for m in mints), "token allowlist", f"{mints} not all allowlisted")
    need(mints[0] == mints[1] == K.SOL, "round trip", "must start and end in SOL")
    need(0 < amount_in <= K.MAX_INPUT_LAMPORTS, "max input", f"{amount_in} lamports over the {K.MAX_INPUT_LAMPORTS} cap")
    need(mout >= amount_in + costs + K.MIN_PROFIT, "minimum output",
         "the minimum output doesn't cover the input, every cost and the minimum profit")
    tip_total = cu_price = cu_limit = 0
    for i, x in enumerate(ins):
        prog, kind = x.get("program"), x.get("kind")
        need(prog in K.PROGRAMS, f"#{i} program allowed", f"unknown program {prog}")
        if prog == "ComputeBudget111111111111111111111111111111":
            need(kind in ("set_cu_limit", "set_cu_price"), f"#{i} compute budget", f"unexpected {kind}")
            if kind == "set_cu_limit":
                cu_limit = int(x["units"])
            else:
                cu_price = int(x["micro_lamports"])
        elif prog == "11111111111111111111111111111111":
            need(kind == "transfer" and x.get("to") in K.JITO_TIP_ACCOUNTS, f"#{i} system transfer",
                 "SOL may only move to a known Jito tip account")
            tip_total += int(x["lamports"])
        elif prog == "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4":
            route = x.get("route") or []
            need(kind == "swap" and route and all(m in K.MINTS for m in route), f"#{i} swap route",
                 "every token on the route must be allowlisted")
        else:
            need(kind in ("create_ata", "close_account", "token_transfer_own"), f"#{i} token instruction",
                 f"unexpected {kind}")
            if kind == "close_account":
                need(x.get("destination") == payer, f"#{i} close destination", "rent must return to the wallet")
        for a in x.get("accounts", []):
            key = a.get("key", "")
            if key.startswith(K.DONTFRONT_PREFIX):
                need(not a.get("writable"), f"#{i} dontfront read-only", "the jitodontfront account must be read-only")
    need(tip_total <= K.MAX_TIP_LAMPORTS, "tip ceiling", f"tip {tip_total} over {K.MAX_TIP_LAMPORTS}")
    need(cu_price <= K.MAX_CU_PRICE, "fee ceiling", f"priority fee price {cu_price} over {K.MAX_CU_PRICE}")
    need(0 < cu_limit <= 1_400_000, "compute limit set", "an explicit compute-unit limit is required")
    worst_fee = K.BASE_FEE + cu_limit * cu_price // 1_000_000 + tip_total
    need(wallet - amount_in - worst_fee >= K.MIN_SOL_RESERVE, "SOL reserve",
         f"would leave less than {K.MIN_SOL_RESERVE} lamports in the wallet")
    return {"ok": True, "checks": checks}
