"""Submission adapters (D) and status reconciliation. Built against the official documentation as of 14 Oct 2026:

Solana RPC   sendTransaction (base64, maxRetries 0: this code decides about retries), getSignatureStatuses
             (null = not found; confirmationStatus processed / confirmed / finalized; err set = landed but failed).
Jito         POST {block engine}/api/v1/bundles  sendBundle: up to 5 signed transactions, base64; a tip is required;
             default limit 1 request per second per IP per region. Returns a bundle id.
             getInflightBundleStatuses (last 5 minutes): Invalid / Pending / Failed / Landed.
             POST /api/v1/transactions?bundleOnly=true  sendTransaction as a revert-protected one-transaction bundle.
             jitodontfront: a bundle holding a transaction that lists an account starting with "jitodontfront" is
             rejected unless that transaction is first. Jito: it "may help reduce sandwich attacks but is not
             guaranteed to do so", and applies only inside Jito's block engine, not to other ordering.

Every adapter can build its request, but `send` always goes through policy.require_live(), which refuses: this build
has no wallet and no signer (G).
"""
from __future__ import annotations

from mev import config as K
from mev.policy import require_live


class Adapter:
    name = "base"

    def payload(self, signed_b64: list[str]) -> dict:
        raise NotImplementedError

    def send(self, signed_b64: list[str]) -> str:
        require_live()                      # raises: live sending is off
        raise AssertionError("unreachable")


class RpcAdapter(Adapter):
    name = "rpc_plain"
    url = K.RPC

    def payload(self, signed_b64):
        if len(signed_b64) != 1:
            raise ValueError("a plain RPC submission carries exactly one transaction")
        return {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                "params": [signed_b64[0], {"encoding": "base64", "skipPreflight": False, "maxRetries": 0}]}


class JitoBundleAdapter(Adapter):
    name = "jito_bundle"
    url = K.JITO_BLOCK_ENGINE + "/api/v1/bundles"

    def payload(self, signed_b64):
        if not 1 <= len(signed_b64) <= 5:
            raise ValueError("a Jito bundle holds 1 to 5 transactions")
        return {"jsonrpc": "2.0", "id": 1, "method": "sendBundle", "params": [signed_b64, {"encoding": "base64"}]}


class JitoDontFrontAdapter(Adapter):
    name = "jito_dontfront"
    url = K.JITO_BLOCK_ENGINE + "/api/v1/transactions?bundleOnly=true"

    def payload(self, signed_b64):
        if len(signed_b64) != 1:
            raise ValueError("sendTransaction carries exactly one transaction")
        return {"jsonrpc": "2.0", "id": 1, "method": "sendTransaction", "params": [signed_b64[0], {"encoding": "base64"}]}


ADAPTERS = {a.name: a for a in (RpcAdapter(), JitoBundleAdapter(), JitoDontFrontAdapter())}

# ------------------------------------------------------------------------------------------- reconciliation
LANDED_OK, LANDED_FAILED, NOT_LANDED, WAIT = "LANDED_OK", "LANDED_FAILED", "NOT_LANDED", "WAIT_AND_RECONCILE"


def reconcile(sig_status: dict | None, bundle_status: str | None, blockhash_expired: bool,
              wallet_unchanged: bool | None) -> str:
    """What happened to a submission. Never answers "resubmit" while the outcome is ambiguous: a transaction counts as
    not landed only when its blockhash has expired (it can no longer land), no status shows it, and the wallet's
    balances confirm nothing moved."""
    if sig_status:
        if sig_status.get("err") is not None:
            return LANDED_FAILED
        if sig_status.get("confirmationStatus") in ("confirmed", "finalized"):
            return LANDED_OK
        return WAIT                                        # processed only: could still be dropped
    if bundle_status == "Landed":
        return WAIT                                        # landed per Jito, but confirm the signature on-chain first
    if bundle_status in (None, "Pending"):
        if not blockhash_expired:
            return WAIT
    if blockhash_expired and wallet_unchanged is True:
        return NOT_LANDED
    return WAIT                                            # anything unclear: do nothing until it is clear
