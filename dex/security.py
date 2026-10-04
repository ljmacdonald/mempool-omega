"""Scam and rug-pull defences for DEX tokens, written against a scammer who has read every rule here.

Mirrored line-for-line in site/dexengine.js (tests/test_dex.py checks they agree). See docs/DEX.md for the
full adversary model. The principles:

1. FAIL CLOSED. If a token can't be verified (a checker is down, data is missing), it is not suggested.
2. WORST CASE ACROSS SOURCES. GoPlus, honeypot.is and RugCheck are combined pessimistically: one red flag
   from any of them is enough, so fooling one checker is not enough.
3. REVEALED BEHAVIOUR BEATS CLAIMS. A sell simulation can be fooled (a contract can let the simulator sell and
   block real buyers). Hundreds of distinct real wallets actually selling in the last day can't be faked
   cheaply, so it is required.
4. WHAT THE OWNER *COULD* DO COUNTS, NOT WHAT IT DOES TODAY. Taxes that can be raised, blacklists, pauses,
   minting and upgradeable code are rejected while an owner can still use them, even if they look harmless now.
5. CHEAP-TO-FAKE SIGNALS ARE DISTRUSTED. Pool age (old dormant tokens get "revived"), liquidity (added just
   before a pump, pulled after), holder spread (supply split across fresh wallets), volume (bots trading
   with themselves) and paid promotion all get cross-checked or penalised.
6. SECRET, ROTATING LIMITS THAT ONLY GET STRICTER. Every limit is moved by a private, hourly-changing amount,
   always in the safe direction (minimums go up, maximums go down), so nobody can tune a token to sit just
   inside them, and the published values ($500k liquidity, 14 days, ...) are floors.
7. RULE CHANGES ARE A RED FLAG. A token whose security settings changed in the last 3 days is rejected.
"""
from __future__ import annotations

import os
import time

from scanner.integrity import M32, mulberry32

# Published floors / ceilings. "min_" limits are only ever raised by the secret jitter, "max_" only lowered.
BASE = {
    "min_liq": 500_000.0,       # real liquidity (both sides, counting only SOL/ETH/BNB/stablecoin side x2)
    "min_age_days": 14.0,       # pool age
    "min_active_days": 10.0,    # days (of the last 14) with real trading: stops "revived" dormant tokens
    "min_buyers": 200.0,        # distinct buying wallets, last 24 h
    "min_sellers": 120.0,       # distinct SELLING wallets, last 24 h: real people can get out
    "min_sell_ratio": 0.30,     # sellers / buyers; far fewer sellers than buyers = hidden sell blocks
    "min_holders": 2000.0,
    "max_tax": 0.05,            # buy or sell tax
    "max_top10": 0.50,          # share held by the 10 biggest ordinary wallets (reject above)
    "max_top10_soft": 0.30,     # penalise above
    "max_creator": 0.20,        # creator / owner share
    "max_liq_drop_6h": 0.20,    # liquidity being pulled right now
    "max_liq_drop_24h": 0.35,
    "max_liq_jump_72h": 1.00,   # liquidity doubled in 3 days: may have been added to pass filters (penalty)
    "max_liq_jump_reject": 3.00,
    "max_trades_per_trader": 8.0,   # bot wash trading: many trades per wallet
    "max_churn": 15.0,              # daily volume / liquidity: volume recycled through a small pool
    "max_insiders": 20.0,           # RugCheck insider-network wallets (reject above; penalty if any)
}
PEN = {"top10": 0.10, "holders": 0.05, "lp_unverified": 0.05, "lp_partial": 0.05, "wash": 0.10, "churn": 0.10,
       "boosted": 0.10, "metadata": 0.02, "external_call": 0.05, "liq_jump": 0.15, "insiders": 0.10,
       "creator_soft": 0.05}
EVM_DEAD = {"", "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}


def thresholds(seed: int | None) -> dict:
    """Secret limits: min_* x 1.0-1.3, max_* x 0.75-1.0 (never looser than published). seed=None -> BASE."""
    if seed is None:
        return dict(BASE)
    rnd = mulberry32(seed)
    out = {}
    for k, v in BASE.items():
        r = rnd()
        out[k] = v * (1 + 0.3 * r) if k.startswith("min_") else v * (1 - 0.25 * r)
    out["min_active_days"] = min(out["min_active_days"], 14.0)
    return out


def run_seed() -> int:
    base = os.environ.get("OMEGA_SECRET_SEED")
    s = int(base) if base and base.isdigit() else int.from_bytes(os.urandom(4), "little")
    return ((s * 2654435761) ^ int(time.time() // 3600)) & M32


# ------------------------------------------------------------------------------------- normalise
def _b(x) -> bool | None:
    """GoPlus flags are "0"/"1" strings (or {"status": "1"} on Solana); "" / missing = unknown."""
    if isinstance(x, dict):
        x = x.get("status")
    if x is None or x == "":
        return None
    return str(x) == "1"


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def _max(*xs):
    v = [x for x in xs if x is not None]
    return max(v) if v else None


def _any(*xs):
    v = [x for x in xs if x is not None]
    return any(v) if v else None


def empty_facts() -> dict:
    return {"goplus": False, "honeypot_src": None, "rugcheck_src": None, "honeypot": None, "cannot_sell_all": None,
            "cannot_buy": None, "sim_ok": None, "buy_tax": None, "sell_tax": None, "tax_modifiable": False,
            "owner_active": False, "hidden_owner": False, "take_back_ownership": False, "owner_change_balance": False,
            "selfdestruct": False, "proxy": False, "open_source": None, "mintable": False, "pausable": False,
            "blacklist": False, "whitelist": False, "cooldown": False, "anti_whale_modifiable": False,
            "external_call": False, "mint_authority": False, "freeze_authority": False, "permanent_delegate": False,
            "transfer_hook": False, "non_transferable": False, "default_frozen": False, "closable": False,
            "metadata_mutable": False, "holder_count": None, "top10_pct": None, "creator_pct": None,
            "insiders": None, "lp_secured_pct": None, "lp_unlock_soon": False, "lp_top_unlocked_eoa_pct": None,
            "lp_providers": None, "same_creator_honeypots": 0, "trusted": False, "rugged": False}


def from_goplus_evm(r: dict, pools: list[str], now_s: float) -> dict:
    """Normalise a GoPlus token_security result (EVM chains). pools = DEX pool addresses of this token."""
    f = empty_facts()
    if not r:
        return f
    f["goplus"] = True
    owner = (r.get("owner_address") or "").lower()
    f["hidden_owner"] = bool(_b(r.get("hidden_owner")))
    f["take_back_ownership"] = bool(_b(r.get("can_take_back_ownership")))
    f["owner_active"] = owner not in EVM_DEAD or f["hidden_owner"] or f["take_back_ownership"]
    f["honeypot"] = _b(r.get("is_honeypot"))
    f["cannot_sell_all"] = _b(r.get("cannot_sell_all"))
    f["cannot_buy"] = _b(r.get("cannot_buy"))
    f["buy_tax"] = _num(r.get("buy_tax"))
    f["sell_tax"] = _num(r.get("sell_tax"))
    f["tax_modifiable"] = bool(_b(r.get("slippage_modifiable")) or _b(r.get("personal_slippage_modifiable")))
    f["owner_change_balance"] = bool(_b(r.get("owner_change_balance")))
    f["selfdestruct"] = bool(_b(r.get("selfdestruct")))
    f["proxy"] = bool(_b(r.get("is_proxy")))
    f["open_source"] = _b(r.get("is_open_source"))
    f["mintable"] = bool(_b(r.get("is_mintable")))
    f["pausable"] = bool(_b(r.get("transfer_pausable")))
    f["blacklist"] = bool(_b(r.get("is_blacklisted")))
    f["whitelist"] = bool(_b(r.get("is_whitelisted")))
    f["cooldown"] = bool(_b(r.get("trading_cooldown")))
    f["anti_whale_modifiable"] = bool(_b(r.get("anti_whale_modifiable")))
    f["external_call"] = bool(_b(r.get("external_call")))
    f["same_creator_honeypots"] = int(_num(r.get("honeypot_with_same_creator")) or 0)
    f["holder_count"] = int(_num(r.get("holder_count")) or 0) or None
    cex = r.get("is_in_cex") or {}
    f["trusted"] = bool(_b(r.get("trust_list")) or (isinstance(cex, dict) and str(cex.get("listed")) == "1"))
    f["creator_pct"] = _max(_num(r.get("creator_percent")), _num(r.get("owner_percent")))
    skip = {p.lower() for p in pools} | EVM_DEAD
    top = [h for h in r.get("holders") or []
           if (h.get("address") or "").lower() not in skip and not int(h.get("is_locked") or 0) and not h.get("tag")]
    if r.get("holders"):
        f["top10_pct"] = sum(_num(h.get("percent")) or 0 for h in top[:10])
    lp = r.get("lp_holders") or []
    if lp:
        secured, top_eoa, soon = 0.0, 0.0, False
        for h in lp:
            pct = _num(h.get("percent")) or 0.0
            addr = (h.get("address") or "").lower()
            locked = bool(int(h.get("is_locked") or 0))
            if locked:
                ends = [_num(d.get("end_time")) for d in h.get("locked_detail") or []]
                ends = [e for e in ends if e]
                if ends and min(ends) < now_s + 7 * 86400:
                    soon = True
            if addr in EVM_DEAD or locked:
                secured += pct
            elif not int(h.get("is_contract") or 0):
                top_eoa = max(top_eoa, pct)
        nft = any(h.get("NFT_list") for h in lp)
        f["lp_secured_pct"] = None if nft else secured      # concentrated-liquidity positions can't be "locked"
        f["lp_top_unlocked_eoa_pct"] = None if nft else top_eoa
        f["lp_unlock_soon"] = soon
        f["lp_providers"] = int(_num(r.get("lp_holder_count")) or len(lp))
    return f


def from_goplus_sol(r: dict) -> dict:
    f = empty_facts()
    if not r:
        return f
    f["goplus"] = True
    f["mint_authority"] = bool(_b(r.get("mintable")))
    f["freeze_authority"] = bool(_b(r.get("freezable")))
    f["permanent_delegate"] = bool(_b(r.get("balance_mutable_authority")))
    f["transfer_hook"] = bool(r.get("transfer_hook")) or bool(_b(r.get("transfer_hook_upgradable")))
    f["non_transferable"] = bool(_b(r.get("non_transferable")))
    f["default_frozen"] = str(r.get("default_account_state")) == "2" or bool(_b(r.get("default_account_state_upgradable")))
    f["closable"] = bool(_b(r.get("closable")))
    f["metadata_mutable"] = bool(_b(r.get("metadata_mutable")))
    tf = r.get("transfer_fee") or {}
    rate = _num((tf.get("current_fee_rate") or {}).get("fee_rate") if isinstance(tf.get("current_fee_rate"), dict) else tf.get("fee_rate"))
    if rate is not None:
        rate = rate / 10000 if rate > 1 else rate        # basis points or fraction
        f["buy_tax"] = f["sell_tax"] = rate
    f["tax_modifiable"] = bool(_b(r.get("transfer_fee_upgradable")))
    f["owner_active"] = f["tax_modifiable"] or f["mint_authority"] or f["freeze_authority"]
    f["holder_count"] = int(_num(r.get("holder_count")) or 0) or None
    f["trusted"] = str(r.get("trusted_token")) == "1"
    hs = [h for h in r.get("holders") or [] if not int(h.get("is_locked") or 0) and not h.get("tag")]
    if r.get("holders"):
        f["top10_pct"] = sum(_num(h.get("percent")) or 0 for h in hs[:10])
    return f


def add_honeypot_is(f: dict, d: dict | None) -> dict:
    """honeypot.is: a real buy + sell simulated on a fork (Ethereum, BNB Chain)."""
    f = dict(f)
    if d is None:
        return f
    if d.get("_unsupported"):
        f["honeypot_src"] = "unsupported"      # checked, but no simulation possible: stricter seller rules apply
        return f
    f["honeypot_src"] = True
    hp = (d.get("honeypotResult") or {}).get("isHoneypot")
    sim = d.get("simulationResult") or {}
    f["honeypot"] = _any(f["honeypot"], hp)
    f["sim_ok"] = bool(d.get("simulationSuccess"))
    bt, st = _num(sim.get("buyTax")), _num(sim.get("sellTax"))
    f["buy_tax"] = _max(f["buy_tax"], bt / 100 if bt is not None else None)
    f["sell_tax"] = _max(f["sell_tax"], st / 100 if st is not None else None)
    flags = d.get("flags") or []
    if any((x.get("flag") if isinstance(x, dict) else x) in ("EXTREMELY_HIGH_TAXES", "high_fail_rate") for x in flags):
        f["honeypot"] = True
    return f


def add_rugcheck(f: dict, d: dict | None) -> dict:
    """RugCheck full report (Solana): authorities, Token-2022 extensions, insiders, LP locks."""
    f = dict(f)
    if d is None:
        return f
    f["rugcheck_src"] = True
    f["mint_authority"] = f["mint_authority"] or bool(d.get("mintAuthority"))
    f["freeze_authority"] = f["freeze_authority"] or bool(d.get("freezeAuthority"))
    tfp = _num((d.get("transferFee") or {}).get("pct"))
    if tfp is not None:
        f["buy_tax"] = _max(f["buy_tax"], tfp / 100)
        f["sell_tax"] = _max(f["sell_tax"], tfp / 100)
    ext = d.get("token_extensions") or {}
    if isinstance(ext, dict):
        f["permanent_delegate"] = f["permanent_delegate"] or bool(ext.get("permanentDelegate"))
        f["transfer_hook"] = f["transfer_hook"] or bool(ext.get("transferHook"))
    f["rugged"] = bool(d.get("rugged"))
    f["insiders"] = int(_num(d.get("graphInsidersDetected")) or 0)
    known = d.get("knownAccounts") or {}
    holders = [h for h in d.get("topHolders") or []
               if (known.get(h.get("owner")) or {}).get("type") not in ("AMM", "LOCKER")]
    if d.get("topHolders"):   # RugCheck labels pool vaults and lockers, so its figure replaces GoPlus's
        f["top10_pct"] = sum((_num(h.get("pct")) or 0) for h in holders[:10]) / 100
    locked = [_num((m.get("lp") or {}).get("lpLockedPct")) for m in d.get("markets") or []]
    locked = [x for x in locked if x is not None]
    if locked:
        f["lp_secured_pct"] = max(locked) / 100
    f["lp_providers"] = int(_num(d.get("totalLPProviders")) or 0) or f["lp_providers"]
    f["owner_active"] = f["owner_active"] or f["mint_authority"] or f["freeze_authority"]
    return f


def fingerprint(f: dict) -> str:
    """Security settings that a scammer would change after people buy. Compared over time (server)."""
    tax = lambda x: "" if x is None else f"{round(x * 100)}"  # noqa: E731
    keys = ("owner_active", "proxy", "mintable", "pausable", "blacklist", "tax_modifiable", "mint_authority",
            "freeze_authority", "permanent_delegate", "transfer_hook")
    return "|".join([tax(f["buy_tax"]), tax(f["sell_tax"])] + ["1" if f[k] else "0" for k in keys])


# ------------------------------------------------------------------------------------- assess
def assess(f: dict, m: dict, t: dict, chain: str) -> dict:
    """f: normalised facts; m: market facts (see dex/live.py). Returns hard rejections, check list, penalty."""
    hard: list[dict] = []
    checks: list[dict] = []

    def rej(key: str, text: str) -> None:
        hard.append({"key": key, "text": text})

    def chk(key: str, ok: bool | None, text: str, pen: float = 0.0) -> None:
        checks.append({"key": key, "ok": ok, "text": text, "penalty": pen if ok is False else 0.0})

    evm = chain != "solana"
    owner = f["owner_active"]
    # 1. could it be verified at all? (fail closed)
    if not f["goplus"]:
        rej("unverified", "The security check couldn't run for this token, so it isn't suggested (we never guess).")
    if evm and CHAIN_SIM.get(chain) and not f["honeypot_src"]:
        rej("unverified", "The buy-and-sell test couldn't run for this token, so it isn't suggested.")
    if chain == "solana" and not f["rugcheck_src"]:
        rej("unverified", "The Solana token check (RugCheck) couldn't run, so it isn't suggested.")
    # 2. can you sell?
    if f["honeypot"] or f["cannot_sell_all"] or f["cannot_buy"] or (f["sim_ok"] is False):
        rej("honeypot", "Honeypot: a test buy-and-sell failed or the token blocks selling. You could buy but not sell.")
    else:
        chk("sell_test", True if f["sim_ok"] else None,
            "A simulated buy and sell worked." if f["sim_ok"] else "No sell simulation is possible for this pool "
            "type, so stricter real-seller rules apply instead (below).")
    sellers, buyers = m.get("sellers_h24") or 0, m.get("buyers_h24") or 0
    strict = 1.5 if not f["sim_ok"] else 1.0      # no simulation: a scammer may have chosen that on purpose
    min_ratio = t["min_sell_ratio"] + (0.10 if not f["sim_ok"] else 0.0)
    if buyers < t["min_buyers"] or sellers < t["min_sellers"] * strict:
        rej("few_traders", f"Too few real traders: {buyers} wallets bought and {sellers} sold in the last 24 hours.")
    elif sellers / max(buyers, 1) < min_ratio:
        rej("sell_block", f"Only {sellers} wallets sold against {buyers} that bought in 24 hours. Contracts that let "
                          "checkers sell but block ordinary buyers look exactly like this.")
    else:
        chk("real_sellers", True, f"{sellers} different wallets really sold in the last 24 hours, so ordinary "
                                  "people can get out.")
    # 3. taxes
    tax = _max(f["buy_tax"], f["sell_tax"])
    if tax is not None and tax > t["max_tax"]:
        rej("tax", f"Tax of {tax:.0%} on buying or selling. That eats most short-term gains.")
    if f["tax_modifiable"] and owner:
        rej("tax_modifiable", "The owner can raise the tax at any time, even to 100% after you buy.")
    if not any(h["key"] in ("tax", "tax_modifiable") for h in hard):
        chk("tax", True, f"Tax: {0 if not tax else tax:.1%} (counted in your costs), and it can't be raised.")
    # 4. owner powers
    if f["hidden_owner"] or f["take_back_ownership"]:
        rej("hidden_owner", "Has a hidden owner or a way to take back ownership after 'giving it up'.")
    if f["owner_change_balance"] and owner:
        rej("balance_change", "The owner can change wallet balances, i.e. take your tokens.")
    if f["selfdestruct"]:
        rej("selfdestruct", "The contract can destroy itself.")
    if f["proxy"] and not f["trusted"]:
        rej("upgradeable", "The code can be replaced after our checks (upgradeable contract).")
    if evm and f["open_source"] is False:
        rej("closed_source", "The contract code is secret, so what it does can't be checked.")
    powers = [n for k, n in (("mintable", "print new tokens"), ("pausable", "pause trading"),
                             ("blacklist", "block wallets from selling"), ("whitelist", "let only chosen wallets trade"),
                             ("cooldown", "add selling delays"), ("anti_whale_modifiable", "limit how much you can sell"))
              if f[k]]
    if powers and owner:
        rej("owner_powers", "The owner can still " + ", ".join(powers) + ". Rejected while anyone holds those powers.")
    if f["external_call"]:
        if owner:
            rej("external_call", "The contract hands decisions to another contract the owner controls.")
        else:
            chk("external_call", False, "The contract relies on another contract, so its behaviour can't be fully "
                                        "checked.", PEN["external_call"])
    sol_bad = [n for k, n in (("mint_authority", "print new tokens"), ("freeze_authority", "freeze your tokens"),
                              ("permanent_delegate", "move tokens out of your wallet"),
                              ("transfer_hook", "run hidden code on every transfer"),
                              ("non_transferable", "make tokens non-transferable"),
                              ("default_frozen", "freeze new wallets by default"), ("closable", "close the token"))
               if f[k]]
    if sol_bad:
        rej("authorities", "Someone can still " + ", ".join(sol_bad) + ".")
    if not any(h["key"] in ("hidden_owner", "balance_change", "selfdestruct", "upgradeable", "closed_source",
                            "owner_powers", "external_call", "authorities") for h in hard):
        chk("owner", True, "Nobody can print tokens, freeze wallets, block sales or change the code."
            if owner is False else "An owner exists but holds none of the dangerous powers.")
    if f["metadata_mutable"]:
        chk("metadata", False, "Name and logo can still be changed (used to impersonate other tokens).", PEN["metadata"])
    if f["same_creator_honeypots"] > 0:
        rej("serial_scammer", f"The same creator made {f['same_creator_honeypots']} known scam token(s) before.")
    if f["rugged"]:
        rej("rugged", "RugCheck marks this token as already rugged.")
    # 5. liquidity: real, aged, active, and not being pulled
    liq = m.get("liq_real") or 0.0
    if liq < t["min_liq"]:
        rej("liquidity", f"Only ${liq:,.0f} of real money in the pool (minimum ${BASE['min_liq']:,.0f}).")
    else:
        chk("liquidity", True, f"${liq:,.0f} of real money in the pool (SOL/ETH/BNB or stablecoins only).")
    age = m.get("age_days") or 0.0
    if age < t["min_age_days"]:
        rej("young", f"The pool is only {age:.0f} days old (minimum {BASE['min_age_days']:.0f}).")
    act = m.get("active_days")
    if act is not None and act < t["min_active_days"]:
        rej("revived", f"Real trading on only {act} of the last 14 days. Old, quiet tokens get 'revived' to look "
                       "established before a dump.")
    elif act is not None and age >= t["min_age_days"]:
        chk("history", True, f"{age:.0f} days old and actively traded on {act} of the last 14 days.")
    d6, d24, j72 = m.get("liq_change_6h"), m.get("liq_change_24h"), m.get("liq_change_72h")
    if d6 is not None and d6 < -t["max_liq_drop_6h"]:
        rej("liq_pull", f"Pool money fell {-d6:.0%} in the last 6 hours: liquidity is being pulled.")
    elif d24 is not None and d24 < -t["max_liq_drop_24h"]:
        rej("liq_pull", f"Pool money fell {-d24:.0%} in 24 hours.")
    if j72 is not None and j72 > t["max_liq_jump_reject"]:
        rej("liq_added", f"Pool money grew {j72:.0%} in 3 days. Money added just before a pump is usually pulled "
                         "after it.")
    elif j72 is not None and j72 > t["max_liq_jump_72h"]:
        chk("liq_jump", False, f"Pool money grew {j72:.0%} in 3 days; it may be temporary.", PEN["liq_jump"])
    if f["lp_unlock_soon"]:
        rej("lp_unlock", "The locked pool money unlocks within a week.")
    if f["lp_top_unlocked_eoa_pct"] is not None and f["lp_top_unlocked_eoa_pct"] > 0.5:
        rej("lp_owner", f"One ordinary wallet owns {f['lp_top_unlocked_eoa_pct']:.0%} of the pool and can withdraw it.")
    if f["lp_secured_pct"] is None:
        chk("lp", False, "The pool's money isn't locked (normal for modern pools). This app watches it live and "
                         "warns you if it's pulled.", PEN["lp_unverified"])
    elif f["lp_secured_pct"] < 0.9:
        chk("lp", False, f"Only {f['lp_secured_pct']:.0%} of the pool's money is locked or burned.", PEN["lp_partial"])
    else:
        chk("lp", True, f"{f['lp_secured_pct']:.0%} of the pool's money is locked or burned.")
    # 6. who holds it
    top10 = f["top10_pct"]
    if top10 is not None and top10 > t["max_top10"] and not f["trusted"]:
        rej("concentrated", f"The 10 biggest wallets hold {top10:.0%}. They can crash the price at will.")
    elif top10 is not None and top10 > t["max_top10_soft"]:
        chk("holders", False, f"The 10 biggest wallets hold {top10:.0%}.", PEN["top10"])
    elif top10 is not None:
        chk("holders", True, f"The 10 biggest wallets hold {top10:.0%}.")
    cp = f["creator_pct"]
    if cp is not None and cp > t["max_creator"]:
        rej("creator", f"The creator/owner still holds {cp:.0%}.")
    elif cp is not None and cp > 0.05:
        chk("creator", False, f"The creator/owner still holds {cp:.0%}.", PEN["creator_soft"])
    hc = f["holder_count"]
    if hc is not None and hc < t["min_holders"]:
        chk("holder_count", False, f"Only {hc:,} holders. A few wallets dressed up as many is a common trick.",
            PEN["holders"])
    ins = f["insiders"]
    if ins is not None and ins > t["max_insiders"]:
        rej("insiders", f"{ins} linked 'insider' wallets (sniper or team bundle) were detected.")
    elif ins:
        chk("insiders", False, f"{ins} linked 'insider' wallets detected (bought together, may sell together).",
            PEN["insiders"])
    # 7. fake activity and paid hype
    trades = (m.get("buys_h24") or 0) + (m.get("sells_h24") or 0)
    traders = buyers + sellers
    if traders and trades / traders > t["max_trades_per_trader"]:
        chk("wash", False, f"About {trades / traders:.0f} trades per wallet in 24 hours: bots trading with "
                           "themselves to fake activity.", PEN["wash"])
    elif traders:
        chk("wash", True, "Trading comes from many different wallets, not a few bots.")
    if liq and (m.get("vol_h24") or 0) / liq > t["max_churn"]:
        chk("churn", False, f"Daily trading is {(m.get('vol_h24') or 0) / liq:.0f}x the pool size: money going round "
                            "in circles.", PEN["churn"])
    if m.get("boosted"):
        chk("boosted", False, "Someone is paying DexScreener to promote it right now. Paid hype is a classic setup "
                              "for selling to newcomers.", PEN["boosted"])
    if m.get("copycat"):
        rej("copycat", "Another token with the same name or symbol has more money behind it. This one is likely a copy.")
    if m.get("impersonator"):
        rej("copycat", "Uses the name of a major coin or stablecoin but isn't the real one.")
    if m.get("rules_changed"):
        rej("rules_changed", "Its security settings changed in the last 3 days. Scammers change the rules after "
                             "people buy.")
    pen = round(sum(c["penalty"] for c in checks), 6)
    return {"verdict": "reject" if hard else "pass", "hard": hard, "checks": checks, "penalty": pen}


CHAIN_SIM = {"ethereum": True, "bsc": True}     # networks where a buy+sell simulation is available
