"""Fetch raw security data (GoPlus, honeypot.is, RugCheck) and normalise it with dex/security.py."""
from __future__ import annotations

import time

from core.log import get_logger
from dex import security as S
from dex.chains import CHAINS
from dex.data import http

log = get_logger("dex.fetch")


def goplus(chain: str, tokens: list[str]) -> dict[str, dict]:
    cid = CHAINS[chain]["goplus"]
    url = ("https://api.gopluslabs.io/api/v1/solana/token_security" if chain == "solana"
           else f"https://api.gopluslabs.io/api/v1/token_security/{cid}")
    out: dict[str, dict] = {}
    for tok in tokens:             # the free service answers one token per request
        for attempt in range(3):   # when busy it answers "OK"-shaped errors with no result: wait and retry
            try:
                d = http().get(url, {"contract_addresses": tok})
            except Exception as e:  # noqa: BLE001
                log.warning("goplus %s %s: %s", chain, tok, e)
                d = {}
            res = d.get("result") or {}
            if d.get("code") == 1 and res:
                for k, v in res.items():
                    out[k if chain == "solana" else k.lower()] = v
                break
            log.warning("goplus %s %s: code %s %s (attempt %d)", chain, tok, d.get("code"), d.get("message"), attempt + 1)
            time.sleep(10 * (attempt + 1))
    return out


def honeypot_is(chain: str, token: str) -> dict | None:
    cid = CHAINS[chain]["honeypot"]
    if not cid:
        return None
    try:
        r = http().session.get("https://api.honeypot.is/v2/IsHoneypot", params={"address": token, "chainID": cid},
                               timeout=30)
        if r.status_code == 404:          # no pool type it can simulate (e.g. v3/v4 only): not a failure
            return {"_unsupported": True}
        r.raise_for_status()
        time.sleep(0.6)
        return r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("honeypot.is %s %s: %s", chain, token, e)
        return None


def rugcheck(token: str) -> dict | None:
    try:
        return http().get(f"https://api.rugcheck.xyz/v1/tokens/{token}/report")
    except Exception as e:  # noqa: BLE001
        log.warning("rugcheck %s: %s", token, e)
        return None


def facts_for(chain: str, tokens: list[str], pools_by_token: dict[str, list[str]]) -> tuple[dict, dict]:
    """Normalised facts per token, plus the raw responses (kept for tests and debugging)."""
    gp = goplus(chain, tokens)
    raw, out = {}, {}
    now = time.time()
    for t in tokens:
        r = gp.get(t)
        if chain == "solana":
            f = S.from_goplus_sol(r or {})
            rc = rugcheck(t)
            f = S.add_rugcheck(f, rc)
            raw[t] = {"goplus": r, "rugcheck": _slim_rugcheck(rc)}
        else:
            f = S.from_goplus_evm(r or {}, pools_by_token.get(t, []), now)
            hp = honeypot_is(chain, t) if CHAINS[chain]["honeypot"] else None
            f = S.add_honeypot_is(f, hp)
            raw[t] = {"goplus": r, "honeypot": hp}
        out[t] = f
    return out, raw


def _slim_rugcheck(d: dict | None) -> dict | None:
    if not d:
        return d
    keep = ("mintAuthority", "freezeAuthority", "transferFee", "token_extensions", "rugged", "graphInsidersDetected",
            "knownAccounts", "topHolders", "markets", "totalLPProviders")
    s = {k: d.get(k) for k in keep}
    s["topHolders"] = [{"owner": h.get("owner"), "pct": h.get("pct"), "insider": h.get("insider")}
                       for h in (d.get("topHolders") or [])[:20]]
    s["markets"] = [{"lp": {"lpLockedPct": (m.get("lp") or {}).get("lpLockedPct")}} for m in (d.get("markets") or [])[:20]]
    owners = {h["owner"] for h in s["topHolders"]}
    s["knownAccounts"] = {k: {"type": (v or {}).get("type")} for k, v in (d.get("knownAccounts") or {}).items()
                          if k in owners}
    return s
