"""Opportunity survival (B): how often an opportunity is still profitable after a realistic delay, checked in time order.

Each candidate (see mev/shadow.py) is re-quoted at several delays after discovery. For a submission method and a
delay, a candidate "survives" when the re-quoted round trip still clears that method's minimum output
(economics.min_out): that is exactly when an atomic transaction with the minimum-output check would succeed.

Landing (does the transaction get into a block in time?) is a separate question that shadow mode cannot answer: it is
always INSUFFICIENT_EVIDENCE here.

Validation is time-ordered: the survival rate and the survivors' average profit are learned from the earlier 70% of
candidates and checked against the later 30%. With too few candidates the verdict is INSUFFICIENT_EVIDENCE.
"""
from __future__ import annotations

import math

from mev import config as K
from mev.economics import Costs, ev_per_landed, min_out

INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
NOT_VIABLE = "NOT_VIABLE"
PROMISING = "PROMISING_IN_SHADOW"
FIT_SHARE = 0.7


def wilson_low(k: int, n: int, z: float = 1.645) -> float:
    """One-sided 95% lower bound for a rate: the conservative estimate used for decisions."""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / d)


def outcome(c: dict, costs: Costs, delay: int) -> tuple[bool, int] | None:
    """(survived, profit if executed then) for one candidate, or None when it wasn't an opportunity for this method at
    discovery or the re-check at this delay is missing."""
    if c["out"] < min_out(c["amount_in"], costs):
        return None
    r = next((x for x in c.get("rechecks", []) if x.get("delay") == delay and x.get("out") is not None), None)
    if r is None:
        return None
    profit = int(r["out"]) - int(c["amount_in"]) - costs.on_success
    return r["out"] >= min_out(c["amount_in"], costs), profit


def _rate(rows: list[tuple[bool, int]]) -> dict:
    k = sum(ok for ok, _ in rows)
    good = [p for ok, p in rows if ok]
    return {"n": len(rows), "survived": k, "rate": k / len(rows) if rows else None, "rate_low": wilson_low(k, len(rows)),
            "avg_profit_if_ok": sum(good) / len(good) if good else None}


def evaluate(cands: list[dict], costs_of, delay: int) -> dict:
    """costs_of(candidate) -> Costs for one method, priced with the fees seen when that candidate was found."""
    cands = sorted(cands, key=lambda c: c["found_at"])
    rows = [(c, o) for c in cands if (o := outcome(c, costs_of(c), delay)) is not None]
    out = {"delay": delay, "all": _rate([o for _, o in rows]), "landing": INSUFFICIENT}
    cut = int(len(rows) * FIT_SHARE)
    fit, test = [o for _, o in rows[:cut]], [o for _, o in rows[cut:]]
    out["fit"], out["test"] = _rate(fit), _rate(test)
    if len(rows) < K.MIN_CANDIDATES or len(test) < K.MIN_TEST:
        out.update(verdict=INSUFFICIENT, reason=f"{len(rows)} opportunities with a {delay}-second re-check "
                                                f"({K.MIN_CANDIDATES} needed, {K.MIN_TEST} of them in the later check).")
        return out
    # conservative forecast from the earlier part, then what the later part actually showed (per landed attempt)
    ref = costs_of(rows[cut][0])
    out["ev_forecast"] = ev_per_landed(out["fit"]["rate_low"], out["fit"]["avg_profit_if_ok"] or 0, ref)
    out["ev_test"] = sum(p if ok else -costs_of(c).on_revert for c, (ok, p) in rows[cut:]) / len(test)
    if out["ev_forecast"] is None or out["ev_forecast"] <= 0 or out["ev_test"] <= 0:
        out.update(verdict=NOT_VIABLE, reason="After every cost, opportunities that were re-checked later did not pay "
                                              "on average, even assuming every transaction lands.")
    else:
        out.update(verdict=PROMISING, reason="Re-checked opportunities still paid after costs in the later check, "
                                             "assuming they land. Landing itself is unmeasured, so this is not "
                                             "enough to trade.")
    return out
