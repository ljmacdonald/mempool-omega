"""Scoring, admission routes and the watchlist's states (D119). Pure functions over the evidence.

The score is a provisional 100-point research ranking (config.WEIGHTS), NOT a probability that a token will rise.
Missing data earns no points; every component is shown with the numbers behind it. Confidence is separate from the
score and uses plain categories with stated criteria. Hard disqualifications override the score.

Admission never needs a price rise: a token can be added while its price is flat or falling. The first
qualification's time and evidence are frozen and never moved.
"""
from __future__ import annotations

from accum import config as K

STATES = ("investigating", "accumulation_detected", "persistent_accumulation", "concentrated_ownership_risk",
          "accumulation_weakening", "distribution_detected", "data_insufficient", "disqualified", "archived")
LABEL = {"investigating": "Investigating", "accumulation_detected": "Accumulation detected",
         "persistent_accumulation": "Persistent accumulation", "concentrated_ownership_risk": "Concentrated ownership risk",
         "accumulation_weakening": "Accumulation weakening", "distribution_detected": "Distribution detected",
         "data_insufficient": "Data insufficient", "disqualified": "Disqualified", "archived": "Archived"}
ACTIVE = {"accumulation_detected", "persistent_accumulation", "concentrated_ownership_risk"}


def clip01(x):
    return max(0.0, min(1.0, x))


def score(m: dict) -> dict:
    """m: the token's evidence (windows w7..w90, buyer-relative list, price, flags). Returns points per component."""
    W = K.WEIGHTS
    w7, w14, w30 = m["w"].get(7) or {}, m["w"].get(14) or {}, m["w"].get(30) or {}
    comp = {}
    s7 = w7.get("share")
    comp["verified"] = (W["verified"] * clip01(s7 / (2 * K.ROUTE_A["min_share"])) * clip01(w7.get("coverage", 0) / K.ROUTE_A["min_coverage"])
                        if s7 and s7 > 0 else 0.0, f"7-day net verified buying {fmt_share(s7)} of circulating supply, "
                        f"swaps seen for {pct(w7.get('coverage'))} of the time")
    mult = [b["multiple"] for b in m.get("buyers", []) if b.get("multiple")]
    new = [b for b in m.get("buyers", []) if b.get("label") == "newly observed holder" and (b.get("share") or 0) >= K.ROUTE_B["min_share"]]
    best = max(mult) if mult else None
    br = 20 if best and best >= 5 else 14 if best and best >= 2 else 8 if best and best >= 1 else 12 if new else 0
    comp["buyer_relative"] = (W["buyer_relative"] * br / 20, f"largest buyer bought {best:.1f}x what it held before" if best
                              else "a new holder bought a material amount" if new else "no buyer-relative evidence (balances unavailable or small)")
    full = [n for n in K.WINDOWS if (m["w"].get(n) or {}).get("complete")]
    pos = [n for n in full if ((m["w"][n] or {}).get("net_qty") or 0) > 0]
    comp["persistence"] = (W["persistence"] * len(pos) / len(K.WINDOWS), f"net buying in {len(pos)} of {len(full)} fully observed windows "
                           f"(of {len(K.WINDOWS)}: 7-90 days)")
    longest = next((m["w"][n] for n in (90, 60, 30, 14) if (m["w"].get(n) or {}).get("complete")), w30)
    sl = longest.get("share")
    comp["supply_share"] = (W["supply_share"] * clip01(sl / 0.005) if sl and sl > 0 else 0.0,
                            f"{fmt_share(sl)} of circulating supply over the longest fully observed window")
    vl = w14.get("vs_liquidity")
    comp["liquidity"] = (W["liquidity"] * clip01(vl / 0.25) if vl and vl > 0 else 0.0, f"14-day net buying = {pct(vl)} of the pool's liquidity")
    na, nd = w14.get("acc_wallets", 0), w14.get("dist_wallets", 0)
    part = clip01(na / K.ROUTE_C["min_wallets"]) * clip01((na / max(nd, 1)) / (2 * K.ROUTE_C["min_ratio"])) if na else 0.0
    comp["participation"] = (W["participation"] * part, f"{na} addresses net-bought $1k+ in 14 days, {nd} net-sold (addresses, not people)")
    ch = (m.get("price") or {}).get("change")
    comp["price"] = (W["price"] if ch is not None and -0.2 <= ch <= 0.1 else 0.0 if ch is None or ch >= K.REPRICING else W["price"] * 0.4,
                     f"14-day price change {pct(ch)}" + (" (already repricing)" if ch is not None and ch >= K.REPRICING else ""))
    q = (0.5 if m.get("circ") else 0.0) + 0.5 * clip01(w7.get("coverage", 0))
    comp["quality"] = (W["quality"] * q, f"supply {'known' if m.get('circ') else 'unknown'}, swap coverage {pct(w7.get('coverage'))}")
    total = sum(v[0] for v in comp.values())
    return {"version": K.SCORE_VERSION, "total": round(total, 1), "parts": {k: {"points": round(v[0], 1), "max": W[k], "why": v[1]} for k, v in comp.items()}}


def confidence(m: dict) -> tuple[str, str]:
    w7 = m["w"].get(7) or {}
    days = max((n for n in K.WINDOWS if (m["w"].get(n) or {}).get("complete")), default=0)
    known_bal = any(b.get("now") is not None for b in m.get("buyers", []))
    if w7.get("coverage", 0) >= 0.9 and days >= 30 and m.get("circ") and known_bal:
        return "High", "swaps seen 90%+ of the time, 30+ days observed, supply and buyers' balances known"
    if w7.get("coverage", 0) >= 0.7 and days >= 7 and m.get("circ"):
        return "Medium", "swaps seen 70%+ of the time, at least a week observed, supply known"
    return "Low", "short or patchy observation, or supply unknown"


def routes(m: dict) -> list[dict]:
    """Which admission routes this evidence passes (each with its own rule)."""
    out = []
    a = m["w"].get(K.ROUTE_A["window"]) or {}
    if (a.get("share") or 0) >= K.ROUTE_A["min_share"] and (a.get("net_usd") or 0) >= K.ROUTE_A["min_usd"] and \
            a.get("buy_days", 0) >= K.ROUTE_A["min_buy_days"] and a.get("coverage", 0) >= K.ROUTE_A["min_coverage"]:
        out.append({"route": "A", "name": "Verified acquisition", "why": f"{fmt_share(a['share'])} of supply (${a['net_usd']:,.0f}) net bought "
                    f"in swaps over 7 days, buying on {a['buy_days']} days"})
    b = m["w"].get(K.ROUTE_B["window"]) or {}
    for x in m.get("buyers", []):
        if (x.get("share") or 0) >= K.ROUTE_B["min_share"] and (x.get("net_usd") or 0) >= K.ROUTE_B["min_usd"] and \
                ((x.get("multiple") or 0) >= K.ROUTE_B["min_multiple"] or x.get("label") == "newly observed holder") and not x.get("exchange"):
            out.append({"route": "B", "name": "Concentrated accumulation", "why": f"one address net bought {fmt_share(x['share'])} of supply "
                        f"(${x['net_usd']:,.0f}) in 14 days: {x['label']}"})
            break
    c = m["w"].get(K.ROUTE_C["window"]) or b
    if c.get("acc_wallets", 0) >= K.ROUTE_C["min_wallets"] and (c.get("share") or 0) >= K.ROUTE_C["min_share"] and \
            c["acc_wallets"] / max(c.get("dist_wallets", 0), 1) >= K.ROUTE_C["min_ratio"]:
        out.append({"route": "C", "name": "Distributed accumulation", "why": f"{c['acc_wallets']} addresses net bought against "
                    f"{c.get('dist_wallets', 0)} net sellers over 14 days, {fmt_share(c['share'])} of supply"})
    return out


def hard_fail(m: dict) -> str | None:
    if m.get("reserve") is not None and m["reserve"] < K.MIN_LIQUIDITY_KEEP:
        return f"liquidity fell to ${m['reserve']:,.0f} (under ${K.MIN_LIQUIDITY_KEEP:,}): not executable"
    return None


def flags(m: dict) -> list[str]:
    out = []
    w14 = m["w"].get(14) or {}
    if (w14.get("top3_share_of_buying") or 0) >= 0.6:
        out.append("Concentrated: three addresses did 60%+ of the net buying")
    if (m.get("price") or {}).get("repricing"):
        out.append("Already repricing: up 30%+ in 14 days")
    if any(x.get("exchange") for x in m.get("buyers", [])):
        out.append("A top buyer is an exchange custody wallet (customers' coins, not one investor)")
    if (m["w"].get(7) or {}).get("coverage", 1) < 0.8:
        out.append("Gaps in swap coverage: busy pool or missed polls")
    if m.get("to_exchange"):
        out.append(f"{len(m['to_exchange'])} transfer(s) from accumulating addresses to an exchange (possible sale, not proof)")
    if m.get("transfers_in"):
        out.append(f"{len(m['transfers_in'])} transfer(s) into accumulating addresses (not counted as buying)")
    if not m.get("circ"):
        out.append("Circulating supply unknown: no supply percentages")
    return out


def next_state(entry: dict | None, m: dict, rts: list[dict], sc: dict, now_iso: str) -> tuple[str | None, str]:
    """The new primary state (None: not on the watchlist and nothing to add) and the reason."""
    cur = (entry or {}).get("state")
    w7 = m["w"].get(7) or {}
    fail = hard_fail(m)
    if fail:
        return ("disqualified", fail) if entry else (None, fail)
    if w7.get("covered_days", 0) < 3:
        return ("data_insufficient", f"only {w7.get('covered_days', 0)} days of swaps observed in the last 7") if entry else (None, "")
    if cur in (None, "investigating", "data_insufficient", "archived", "disqualified"):
        if rts:
            conc = (m["w"].get(14) or {}).get("top3_share_of_buying") or 0
            st = "concentrated_ownership_risk" if conc >= 0.6 and len(rts) == 1 and rts[0]["route"] == "B" else "accumulation_detected"
            return st, "qualified by route " + ", ".join(r["route"] for r in rts) + ": " + rts[0]["why"]
        if cur is None and sc["total"] >= 35:
            return "investigating", f"score {sc['total']:.0f} but no admission route passed yet"
        if cur == "investigating" and entry.get("since") and days_between(entry["since"], now_iso) >= K.ARCHIVE_D:
            return "archived", f"no route passed in {K.ARCHIVE_D} days"
        return cur, ""
    # on the watchlist: distribution, weakening, persistence
    if m.get("distribution"):
        return "distribution_detected", m["distribution"]
    det7 = ((entry.get("detection") or {}).get("w7_net_qty")) or 0
    now7 = w7.get("net_qty") or 0
    if cur in ACTIVE and (now7 <= 0 or (det7 > 0 and now7 < K.WEAKENING * det7)):
        return "accumulation_weakening", f"7-day net buying {now7:,.0f} tokens, under {K.WEAKENING:.0%} of the {det7:,.0f} at detection"
    if cur in ("accumulation_weakening", "distribution_detected"):
        if rts and now7 > 0:
            return "accumulation_detected", "qualified again: " + rts[0]["why"]
        if days_between(entry.get("since") or now_iso, now_iso) >= K.ARCHIVE_D:
            return "archived", f"no renewed accumulation in {K.ARCHIVE_D} days"
        return cur, ""
    weeks = m.get("weekly") or []
    if cur == "accumulation_detected" and len([x for x in weeks if x and x > 0]) >= 3 and \
            all(((m["w"].get(n) or {}).get("net_qty") or 0) > 0 for n in (7, 14, 30)) and (m["w"].get(30) or {}).get("complete"):
        return "persistent_accumulation", "net buying in 3+ of the last 4 weeks and in every 7/14/30-day window"
    return cur, ""


def days_between(a: str, b: str) -> float:
    from accum.evidence import parse
    return (parse(b) - parse(a)).total_seconds() / 86400


def pct(x) -> str:
    return "–" if x is None else f"{x * 100:+.1f}%" if abs(x) < 10 else f"{x * 100:+.0f}%"


def fmt_share(x) -> str:
    return "unknown share" if x is None else f"{x * 100:.3f}%"
