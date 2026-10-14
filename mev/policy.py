"""Launch policy (G). Modes:
  OFFLINE_TEST  recorded data only, no network (the tests)
  SHADOW        live prices and fees, nothing signed or sent (the default and the only mode the scheduled job uses)
  LIVE          locked

LIVE needs ALL of: the owner's explicit written approval (state/mev/approval.json with the fields in APPROVAL_FIELDS),
every acceptance test passing (safety, atomicity, reconciliation, economics), sufficient evidence for both landing and
survival, and a configured signer. This build ships without any signer or wallet code, so LIVE always fails closed.
Bankroll and loss limits (mev/config.py) are enforced separately from the model and can't be overridden by it.
"""
from __future__ import annotations

import json

from core.config import state_path
from mev import config as K

MODES = ("OFFLINE_TEST", "SHADOW", "LIVE")
APPROVAL_FIELDS = ("approved_by", "approved_at", "max_bankroll_lamports", "statement")
ACCEPTANCE = ("safety", "atomicity", "reconciliation", "economics")


class LiveTradingDisabled(Exception):
    pass


def live_gate(evidence: dict | None = None, tests_passed: dict | None = None) -> dict:
    """Every condition for LIVE, each with a yes/no and a reason. LIVE is allowed only if all are yes."""
    try:
        ap = json.loads(state_path("mev", "approval.json").read_text())
    except (OSError, ValueError):
        ap = {}
    ev = evidence or {}
    tp = tests_passed or {}
    rows = [
        ("Owner's written approval", all(ap.get(f) for f in APPROVAL_FIELDS), "No approval on file."),
        *[(f"Acceptance test: {t}", bool(tp.get(t)), "Not passed in a live-capable build.") for t in ACCEPTANCE],
        ("Evidence: transactions land", ev.get("landing") == "SUFFICIENT",
         "Unmeasured: shadow mode never sends a transaction."),
        ("Evidence: opportunities survive", ev.get("survival") == "PROMISING_IN_SHADOW",
         "Not shown on enough re-checked opportunities."),
        ("A signer is configured", False, "This build has no wallet or signing code by design."),
    ]
    return {"allowed": all(ok for _, ok, _ in rows),
            "conditions": [{"name": n, "ok": ok, "why": "" if ok else why} for n, ok, why in rows]}


def require_live(**kw) -> None:
    gate = live_gate(**kw)
    if not gate["allowed"]:
        missing = "; ".join(c["name"] for c in gate["conditions"] if not c["ok"])
        raise LiveTradingDisabled(f"Live sending is off ({missing}).")


def within_limits(amount_in: int, loss_today: int, wallet_sol: int, worst_fee: int) -> tuple[bool, str]:
    """Hard limits, independent of any model."""
    if amount_in > K.MAX_INPUT_LAMPORTS:
        return False, "input over the maximum"
    if loss_today >= K.DAILY_LOSS_LAMPORTS:
        return False, "daily loss limit reached"
    if wallet_sol - amount_in - worst_fee < K.MIN_SOL_RESERVE:
        return False, "would break the SOL reserve"
    return True, "ok"
