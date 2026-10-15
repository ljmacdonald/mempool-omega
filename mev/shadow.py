"""SHADOW sessions (B, F): find cross-venue round trips on Solana, re-check them after realistic delays, record everything.

A candidate is a round trip of SOL sized at the $100 account's value (mev/account.py; TRADE_LAMPORTS if unknown): buy a token on one venue (Jupiter quote restricted to that DEX),
sell what that buys on another venue, back to SOL. Quoted outputs already include pool fees, price impact and integer
rounding. An "opportunity" is a candidate that clears at least one method's minimum output (economics.min_out); each
one is re-quoted, with the same amounts and venues, at DELAYS_S seconds after discovery (the actual elapsed time and
slot are recorded, since requests are paced).

Recorded per opportunity: discovery time and slot, quoted output, the fee and tip levels then, each re-check's time,
slot and output. Submission time, landing slot, actual output and realised P&L are recorded as null: shadow mode never
submits, so nothing here is a realised result.

    python -m mev.shadow              # one session (the hourly job)
    python -m mev.shadow --snapshot   # rebuild the page snapshot only
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config import state_path
from mev import account, clients
from mev import config as K
from mev.economics import method_costs, min_out, priority_fee, tip_from_floor
from mev.policy import live_gate
from mev.survival import INSUFFICIENT, evaluate

KEEP = 5000
MAX_TRACKED = 20          # opportunities re-checked per session (each re-check costs two paced quotes)
METHODS = ("rpc_plain", "jito_bundle", "jito_dontfront")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def costs_for(c: dict) -> dict:
    return method_costs(c["cu_price"], c["tip"])


def session(q: clients.Quotes, seconds: float = K.SESSION_S, tokens: dict = K.TOKENS, dexes=K.DEXES,
            passes: int | None = None, size: int = K.TRADE_LAMPORTS) -> dict:
    t0 = time.monotonic()
    fees, floor = clients.priority_fees(), clients.tip_floor()
    cu_price = fees.get("p50") or 0
    tip = tip_from_floor(floor, 75)
    quoted, opps, best = 0, [], None
    pending: list[tuple[float, dict, int]] = []           # (due monotonic time, opportunity, delay)

    def recheck_due(force=False):
        pending.sort(key=lambda x: x[0])
        while pending and (force or pending[0][0] <= time.monotonic()):
            due, c, d = pending.pop(0)
            if due > time.monotonic():
                time.sleep(due - time.monotonic())
            b = q.quote(K.SOL, c["mint"], c["amount_in"], c["buy_dex"])
            s = q.quote(c["mint"], K.SOL, b["out"], c["sell_dex"], paired=True) if b else None
            c["rechecks"].append({"delay": d, "elapsed_s": round(time.monotonic() - c["_t"], 2),
                                  "slot": s and s["slot"], "out": s and s["out"],
                                  "leg_gap_s": round(s["at"] - b["at"], 3) if s else None})

    done = 0
    while time.monotonic() - t0 < seconds and (passes is None or done < passes):
        done += 1
        for sym, mint in tokens.items():
            recheck_due()
            if time.monotonic() - t0 >= seconds:
                break
            for bd in dexes:
                for sd in dexes:
                    if sd == bd:
                        continue
                    recheck_due()
                    b = q.quote(K.SOL, mint, size, bd)
                    s = q.quote(mint, K.SOL, b["out"], sd, paired=True) if b else None
                    if not s:
                        continue
                    quoted += 1
                    gross = s["out"] - size
                    if best is None or gross > best["gross"]:
                        best = {"gross": gross, "token": sym, "buy_dex": bd, "sell_dex": sd}
                    c = {"id": f"{int(time.time() * 1000)}-{sym}-{bd}-{sd}", "found_at": now_iso(),
                         "found_slot": b["slot"], "quote_slot": s["slot"], "token": sym, "mint": mint, "buy_dex": bd,
                         "sell_dex": sd, "amount_in": size, "out": s["out"], "gross": gross,
                         "v": K.METHOD_VERSION, "leg_gap_s": round(s["at"] - b["at"], 3),
                         "cu_price": cu_price, "tip": tip, "rechecks": [], "_t": time.monotonic(),
                         "submitted_at": None, "landed_slot": None, "actual_out": None, "realized_pnl": None}
                    cs = method_costs(cu_price, tip)
                    if any(s["out"] >= min_out(size, cs[m]) for m in METHODS):
                        opps.append(c)
                        if len(opps) <= MAX_TRACKED:
                            pending.extend((c["_t"] + d, c, d) for d in K.DELAYS_S)
                        else:
                            c["not_rechecked"] = True
    recheck_due(force=True)
    for c in opps:
        c.pop("_t", None)
    return {"at": now_iso(), "seconds": round(time.monotonic() - t0), "size_lamports": size,
            "quotes": q.calls, "refused": q.refused,
            "candidates": quoted, "opportunities": len(opps), "best": best, "cu_price_p50": fees.get("p50"),
            "cu_price_p75": fees.get("p75"), "share_paying_priority": fees.get("share_paying"),
            "priority_floor_used": fees.get("floor_used"), "tip_floor": floor,
            "tip_used": tip, "new": opps}


def _read_jsonl(p: Path) -> list[dict]:
    try:
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    except OSError:
        return []


def _write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows[-KEEP:]))


def snapshot(opps: list[dict], sessions: list[dict], account_view: dict | None = None) -> dict:
    """Everything the Arbitrage lab page shows. Only opportunities measured with paired legs (METHOD_VERSION) are
    judged; earlier ones stay in the file and are counted separately."""
    paired = [c for c in opps if c.get("v", 1) >= K.METHOD_VERSION and (c.get("leg_gap_s") or 99) <= K.MAX_LEG_GAP_S]
    old, opps = [c for c in opps if c not in paired], paired
    surv = {m: [evaluate(opps, lambda c, m=m: costs_for(c)[m], d) for d in K.DELAYS_S] for m in METHODS}
    fastest = {m: surv[m][0] for m in METHODS}
    verdict = next((v["verdict"] for v in fastest.values() if v["verdict"] != INSUFFICIENT), INSUFFICIENT)
    if any(v["verdict"] == "PROMISING_IN_SHADOW" for v in fastest.values()):
        verdict = "PROMISING_IN_SHADOW"
    last = sessions[-1] if sessions else {}
    routes: dict[str, dict] = {}
    for c in opps:
        r = routes.setdefault(f"{c['token']}: {c['buy_dex']} → {c['sell_dex']}", {"n": 0, "survived_1s": 0, "best_gross": None})
        r["n"] += 1
        r1 = next((x for x in c.get("rechecks", []) if x["delay"] == K.DELAYS_S[0]), None)
        if r1 and r1.get("out") is not None and r1["out"] >= min_out(c["amount_in"], costs_for(c)["jito_bundle"]):
            r["survived_1s"] += 1
        r["best_gross"] = max(r["best_gross"] if r["best_gross"] is not None else c["gross"], c["gross"])
    return {
        "generated_at": now_iso(), "mode": "SHADOW", "trade_sol": round((last.get("size_lamports") or K.TRADE_LAMPORTS) / K.LAMPORTS, 4),
        "tokens": list(K.TOKENS), "dexes": list(K.DEXES), "delays": list(K.DELAYS_S),
        "verdict": verdict, "landing": INSUFFICIENT,
        "counts": {"sessions": len(sessions), "candidates": sum(s.get("candidates", 0) for s in sessions),
                   "opportunities": len(opps), "opportunities_unpaired": len(old), "submitted": 0, "landed": 0, "failed": 0},
        "fees_now": {"base_fee": K.BASE_FEE, "cu_limit": K.CU_LIMIT, "cu_price_p50": last.get("cu_price_p50"),
                     "cu_price_p75": last.get("cu_price_p75"), "priority_floor_used": last.get("priority_floor_used"),
                     "priority_floor": K.PRIORITY_FLOOR, "priority_fee_p50": priority_fee(last.get("cu_price_p50") or 0),
                     "tip_floor": last.get("tip_floor"), "tip_used": last.get("tip_used"),
                     "costs": {m: vars(c) for m, c in method_costs(last.get("cu_price_p50") or 0, last.get("tip_used") or K.JITO_MIN_TIP).items()}},
        "survival": surv,
        "routes": sorted(({"route": k, **v} for k, v in routes.items()), key=lambda r: -r["n"])[:25],
        "sessions": [{k: s.get(k) for k in ("at", "seconds", "quotes", "refused", "candidates", "opportunities", "best")}
                     for s in sessions[-48:]],
        "recent": [{k: c.get(k) for k in ("found_at", "found_slot", "token", "buy_dex", "sell_dex", "gross", "rechecks", "tip", "cu_price")}
                   for c in opps[-20:]][::-1],
        "gate": live_gate(evidence={"landing": INSUFFICIENT, "survival": verdict}),
        "account": account_view,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--seconds", type=float, default=K.SESSION_S)
    ap.add_argument("--out", default=".cache/mev_out/snapshot.json")
    a = ap.parse_args()
    op, sp = state_path("mev", "opportunities.jsonl"), state_path("mev", "sessions.jsonl")
    opps, sessions = _read_jsonl(op), _read_jsonl(sp)
    if not a.snapshot:
        q = clients.Quotes()
        last_price = account.load().get("sol_usd")
        price = clients.sol_usd(q) or last_price
        s = session(q, a.seconds, size=account.trade_lamports(account.load(), price))
        price = price or clients.sol_usd(q)           # refused at the start: try once more at the end
        s["sol_usd"] = price
        new = s.pop("new")
        account.apply(new, price, s["at"])
        opps += new
        sessions.append(s)
        _write_jsonl(op, opps)
        _write_jsonl(sp, sessions)
        print(f"mev shadow: {s['candidates']} round trips, {s['opportunities']} opportunities, {s['quotes']} quotes "
              f"({s['refused']} refused); best {s['best']}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot(opps, sessions, account.view()), indent=1, default=str))


if __name__ == "__main__":
    main()
