"""Best idea right now (DECISIONS D117): the one card on the home page.

From every section's current ideas, only those from a *proven* unit (infra/proof.py) are considered; crypto buy ideas
are held back while the market filter is on and the market is falling (infra/market.py). The highest-scoring one left
is the card; when none is left the card says so ("No idea passes our tests right now. Sitting in cash."), which is an
honest and common answer. Ideas are taken from the sections' own published snapshots, only while those are fresh.

Writes state/best.json, read by the home page and by the alerts (which only ever alert on proven ideas).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary as S  # noqa: E402

FRESH_MIN = 45
SKIP_GRADES = {"Avoid - watch only", "Weak"}


def _ms(x) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(x) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return S.parse_t(x)


def _fresh(gen, now: datetime, minutes: int = FRESH_MIN) -> bool:
    t = S.parse_t(gen)
    return bool(t and now - t <= timedelta(minutes=minutes))


def _c(unit, section, path, title, side, crypto, price, stop, target, exit_text, score, grade, as_of, extra=None) -> dict:
    return {"unit": unit, "section": section, "path": path, "title": title, "side": side, "crypto": crypto, "price": price,
            "stop": stop, "target": target, "exit": exit_text, "score": score, "grade": grade, "as_of": str(as_of) if as_of else None,
            **(extra or {})}


def scanner(now: datetime) -> list[dict]:
    out = []
    for sec, path, prefix in (("main", "coins/", ""), ("small", "small/", "small_")):
        for style in ("quick", "short", "day"):
            d = S.read_state(f"suggestions/{prefix}latest_{style}.json") or {}
            if not _fresh(d.get("generated_at"), now, 90):
                continue
            for x in d.get("ideas") or []:
                out.append(_c(f"{sec}:{style}", sec, path, f"Buy {x.get('coin') or x.get('symbol')}", "buy", True, x.get("price_now"),
                              x.get("safety_exit"), x.get("take_profit"), f"Sell by {x.get('exit_by')} ({x.get('hold_text')}) at the latest.",
                              x.get("score"), x.get("grade"), d.get("generated_at")))
    for sec, path in (("stocks", "stocks/"), ("fx", "fx/")):
        d = S.read_data(f"{sec}/snapshot.json") or {}
        if not _fresh(d.get("generated_at"), now):
            continue
        for styles in (d.get("lists") or {}).values():
            for style, rows in (styles or {}).items():
                for x in rows or []:
                    if x.get("hard"):
                        continue
                    side = x.get("side") or "buy"
                    px = x.get("price")
                    name = x.get("label") or x.get("symbol")
                    tp = x.get("take_profit") or (px * (1 + x["take_profit_pct"]) if px and x.get("take_profit_pct") is not None else None)
                    sl = x.get("safety_exit") or (px * (1 + x["safety_exit_pct"]) if px and x.get("safety_exit_pct") is not None else None)
                    out.append(_c(f"{sec}:{style}", sec, path, f"{side.capitalize()} {name}", side, False, px, sl, tp,
                                  "Close it by the end of its holding time (shown on the page).", x.get("score"), x.get("grade"),
                                  d.get("generated_at")))
    return out


def lab(now: datetime) -> list[dict]:
    d = S.read_data("lab/snapshot.json") or {}
    if not _fresh(d.get("generated_at"), now):
        return []
    return [_c(f"lab:{x.get('strategy')}", "lab", "lab/", f"{'Buy' if x.get('side', 1) > 0 else 'Sell'} {x.get('label')}",
               "buy" if x.get("side", 1) > 0 else "sell", str(x.get("sym", "")).endswith("USDT"), x.get("last") or x.get("entry"),
               x.get("stop"), x.get("target"), x.get("exit_rule"), x.get("score"), x.get("grade"), _ms(x.get("asof")),
               {"strategy": x.get("name")}) for x in d.get("ideas") or [] if x.get("fresh", True)]


def forming(now: datetime) -> list[dict]:
    """Connors RSI(2) signals forming before today's close: 'likely buy at the close' (the order goes in near 4 pm New York)."""
    d = S.read_data("lab/snapshot.json") or {}
    if not _fresh(d.get("generated_at"), now):
        return []
    return [{"unit": "lab:rsi2", "section": "lab", "path": "lab/", "title": f"Likely buy {f.get('label')} at today's close",
             "text": f"Connors RSI(2): the 2-day RSI is {round(f.get('rsi2') or 0)} if it closed now. The order goes in during the "
                     "last minutes before 4 pm New York time.", "as_of": str(_ms(f.get("asof")))}
            for f in d.get("forming") or [] if f.get("signal")]


def listings(now: datetime) -> list[dict]:
    d = S.read_data("listings/snapshot.json") or {}
    if not _fresh(d.get("generated_at"), now, 90):
        return []
    return [_c(f"listings:{x.get('rule')}", "listings", "listings/", f"{'Buy' if x.get('side', 1) > 0 else 'Sell'} {x.get('base')}",
               "buy" if x.get("side", 1) > 0 else "sell", True, x.get("last") or x.get("entry"), x.get("stop"), None,
               x.get("rule_text"), x.get("score"), x.get("grade"), _ms(x.get("asof")), {"strategy": x.get("name"), "exchange": x.get("exchange")})
            for x in d.get("ideas") or []]


def ict(now: datetime) -> list[dict]:
    d = S.read_data("ict/snapshot.json") or {}
    if not _fresh(d.get("generated_at"), now):
        return []
    return [_c(f"ict:{k}", "ict", "ict/", f"{str(x.get('side')).capitalize()} {x.get('label')} (limit order at {x.get('entry')})",
               x.get("side"), k == "crypto", x.get("entry"), x.get("stop"), x.get("target"), "The page says when to cancel the order.",
               x.get("score"), x.get("grade"), d.get("generated_at"))
            for k, c in (d.get("classes") or {}).items() for x in c.get("setups") or []
            if x.get("status") == "pending" and not x.get("hard")]


def sniper(now: datetime) -> list[dict]:
    d = S.read_data("sniper/snapshot.json") or {}
    if not _fresh(d.get("generated_at"), now):
        return []
    return [_c("sniper", "sniper", "sniper/", f"Buy {x.get('symbol')}", "buy", True, x.get("entry") or x.get("last"), None, None,
               "Follow the exits on the Sniper lab page.", x.get("score"), x.get("grade"), d.get("generated_at"))
            for x in d.get("ideas") or []]


def build(now: datetime | None = None, proof: dict | None = None, market: dict | None = None) -> dict:
    now = now or S.now_utc()
    proof = proof if proof is not None else (S.read_state("proof.json") or {})
    market = market if market is not None else (S.read_state("market.json") or {})
    proven = {u["id"]: u for u in proof.get("units", []) if u.get("proven")}
    blocks = bool(market.get("active"))
    cands, errors = [], []
    for f in (scanner, lab, listings, ict, sniper):
        try:
            cands += f(now)
        except Exception as e:  # noqa: BLE001  one broken source never hides the others
            errors.append(f"{f.__name__}: {e}")
    ok = [c for c in cands if c["unit"] in proven and c.get("grade") not in SKIP_GRADES and c.get("score") is not None]
    waiting = [c for c in ok if blocks and c["crypto"] and c["side"] == "buy"]
    live = sorted((c for c in ok if c not in waiting), key=lambda c: -float(c["score"]))
    for c in live + waiting:
        u = proven[c["unit"]]
        c["record"] = {"name": u.get("name"), "n": u.get("n"), "avg": u.get("avg"), "hit": u.get("hit"), "source": u.get("source")}
    try:
        soon = [x for x in forming(now) if x["unit"] in proven]
    except Exception as e:  # noqa: BLE001
        soon = []
        errors.append(f"forming: {e}")
    idea = live[0] if live else None
    if idea:
        msg = "The strongest idea from a part of the product that has passed our honest tests."
    elif waiting:
        msg = "A proven idea is waiting: the market is falling, so crypto buy ideas are paused."
    else:
        msg = "No idea passes our tests right now. Sitting in cash."
    out = {"generated_at": now.isoformat(), "idea": idea, "also": live[1:4], "waiting": waiting[:3], "soon": soon, "message": msg,
           "considered": len(cands), "from_proven": len(ok), "proven_units": [u.get("name") for u in proven.values()],
           "market_filter": {"active": blocks, "enabled": bool((market.get("test") or {}).get("enabled")),
                             "falling": bool((market.get("now") or {}).get("falling"))}, "errors": errors}
    (S.STATE / "best.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    d = build()
    print(d["message"], "|", d["considered"], "ideas,", d["from_proven"], "from proven units", "|", (d["idea"] or {}).get("title"))
