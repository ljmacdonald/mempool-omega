"""Engine B's catalyst gate (D122): catalysts detected from measured data, never typed in (the owner's requirement:
no manual work). Each catalyst carries the date the data first showed it and expires after expiry_days, so a backtest
only ever sees a catalyst from its effective date. Pure functions.

  fee_switch_to_holders  holders revenue (DefiLlama: buybacks, burns, staker share) starts after 60+ days of none
  buyback_or_burn        holders revenue over 30 days is at least 1.5x what it was 90 days earlier
  emissions_cut          the daily unlock rate falls to half or less of its rate 90 days earlier (DefiLlama schedules)
  overhang_resolved      the largest scheduled unlock in the token's schedule has passed (within overhang_days)
Product relaunches and resolved legal or exploit overhangs have no reliable free source: not detected (stated).
"""
from __future__ import annotations

from datetime import date, timedelta

from deep.profile import load


def _sum(series: dict[str, float], end: date, days: int) -> float | None:
    vals = [series.get((end - timedelta(days=k)).isoformat()) for k in range(days)]
    known = [v for v in vals if v is not None]
    return sum(known) if len(known) >= days * 0.8 else None      # too many missing days: unknown, not zero


def detect(as_of: date, holders: dict[str, float] | None, unlocked: dict[str, float] | None, profile: dict | None = None) -> list[dict]:
    """Catalysts live on `as_of` from daily holders revenue ({day: usd}) and cumulative unlocked supply ({day: qty}),
    using only days up to as_of."""
    P = (profile or load())["catalyst"]
    exp = P["expiry_days"]
    out = []
    if holders:
        for back in range(exp):                                   # the most recent start date still unexpired
            d = as_of - timedelta(days=back)
            now30, before = _sum(holders, d, 30), _sum(holders, d - timedelta(days=30), 60)
            prev30 = _sum(holders, d - timedelta(days=1), 30)
            if now30 and before == 0 and not prev30:
                out.append({"type": "fee_switch_to_holders", "effective": d.isoformat(), "evidence": f"holders revenue began: ${now30:,.0f} in 30 days after 60 days of none"})
                break
        for back in range(exp):
            d = as_of - timedelta(days=back)
            a, b = _sum(holders, d, 30), _sum(holders, d - timedelta(days=90), 30)
            a1, b1 = _sum(holders, d - timedelta(days=1), 30), _sum(holders, d - timedelta(days=91), 30)
            if a and b and a >= 1.5 * b and not (a1 and b1 and a1 >= 1.5 * b1):
                out.append({"type": "buyback_or_burn", "effective": d.isoformat(), "evidence": f"holders revenue ${a:,.0f} in 30 days, {a / b:.1f}x the level 90 days earlier"})
                break
    if unlocked:
        def rate(d: date) -> float | None:
            x, y = unlocked.get(d.isoformat()), unlocked.get((d - timedelta(days=30)).isoformat())
            return (x - y) / 30 if x is not None and y is not None else None
        for back in range(exp):
            d = as_of - timedelta(days=back)
            r, r0 = rate(d), rate(d - timedelta(days=90))
            r1, r10 = rate(d - timedelta(days=1)), rate(d - timedelta(days=91))
            if r is not None and r0 and r0 > 0 and r <= P["emissions_cut_ratio"] * r0 and not (r1 is not None and r10 and r1 <= P["emissions_cut_ratio"] * r10):
                out.append({"type": "emissions_cut", "effective": d.isoformat(), "evidence": f"daily unlocks fell to {r:,.0f} from {r0:,.0f} 90 days earlier"})
                break
        days = sorted(unlocked)
        jumps = [(days[i], unlocked[days[i]] - unlocked[days[i - 1]]) for i in range(1, len(days))]
        if jumps:
            big_day, big = max(jumps, key=lambda x: x[1])
            if big > 0 and 0 <= (as_of - date.fromisoformat(big_day)).days <= P["overhang_days"]:
                out.append({"type": "overhang_resolved", "effective": big_day, "evidence": f"the largest scheduled unlock ({big:,.0f} tokens) has passed"})
    live = []
    for c in out:
        c["expires"] = (date.fromisoformat(c["effective"]) + timedelta(days=exp)).isoformat()
        if c["effective"] <= as_of.isoformat() < c["expires"]:
            live.append(c)
    return live
