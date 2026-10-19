"""Proven or Testing only (DECISIONS D115): which parts of the product may put an idea in front of people.

Every section keeps running and recording on paper, but only a *proven* unit (a section's trading style, a Strategy
lab strategy, a New listings rule...) may reach the home page's "Best idea right now" card and the trade alerts. All
other ideas are shown greyed as "Testing only".

Proven means the same three things everywhere, after every cost:
  1. enough finished trades (MIN_N), and they made money;
  2. they beat the same trades taken at random by more than luck (paired edge, t >= 2);
  3. they still did in the most recent part (the last third in time, or after publication for the Strategy lab and New
     listings): edge > 0 with t >= 1, and still made money.
The Strategy lab and New listings already run this exact test on long histories ("Held up"); they are proven when that
test holds AND their own forward record since going live is not clearly failing. Live forward records (Exchange
coins, Small coins, DEX, US stocks, Forex, ICT, Prediction markets, Whales, Sniper) are judged on what they recorded.
Ideas made within one scan are not independent, so their t is taken over scans (each scan's average), not ideas.

Run by the summary job; writes state/proof.json. A status changes only when the numbers do; each change is logged in
state/proof_log.csv with the numbers behind it.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary as S  # noqa: E402

MIN_N = 30
MIN_SCANS = 20
LATE_MIN = 10

SCANNER = [  # section key, page, name, history files, {style: label}
    ("main", "coins/", "Exchange coins", ["suggestions/history.csv"],
     {"quick": "Quick", "short": "Short", "day": "Day"}),
    ("small", "small/", "Small coins", ["suggestions/small_history.csv"],
     {"quick": "Quick", "short": "Short", "day": "Day"}),
    ("dex", "dex/", "DEX tokens", ["dex/history.csv"], {"dex_short": "Short", "dex_day": "Day"}),
    ("stocks", "stocks/", "US stocks", ["stocks/history_large.csv", "stocks/history_volatile.csv"],
     {"stk_today": "Today", "stk_days": "Days"}),
    ("fx", "fx/", "Forex & gold", ["fx/history.csv"], {"fx_today": "Today", "fx_days": "Days"}),
]
CRYPTO = {"main", "small", "dex", "sniper", "listings", "whales", "accum"}


def tstat(xs: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m / math.sqrt(v / n) if v > 0 else None


def judge(n: int, avg, edge, t_edge, late: dict | None, label_late: str = "the most recent third") -> tuple[bool, str]:
    """(proven, plain-English reason), the same order of checks as the Strategy lab's verdict."""
    if not n or n < MIN_N or avg is None:
        return False, f"Too few finished trades to judge ({n or 0}; at least {MIN_N} needed)."
    if not avg > 0:
        return False, "Lost money after costs."
    if edge is None or t_edge is None or t_edge < 2:
        if edge is not None and edge <= 0:
            return False, "Made money only because the market went up: random picks did as well or better."
        return False, "Made money after costs, but not clearly better than random picks: could be luck."
    if late is not None and not (late.get("n", 0) >= LATE_MIN and (late.get("avg") or 0) > 0 and (late.get("edge") or 0) > 0
                                 and (late.get("t_edge") or 0) >= 1):
        return False, f"Beat random picks earlier, but not clearly in {label_late}: the edge seems to have faded."
    return True, "Made money after costs, beat random picks by more than luck, and still does recently."


def _rows(rel: str) -> list[dict]:
    try:
        with (S.STATE / rel).open(newline="") as f:
            return [r for r in csv.DictReader(f) if r.get("status") == "closed" and r.get("net_ret") not in (None, "")]
    except OSError:
        return []


def _stats(rows: list[dict]) -> dict:
    """Per-idea averages; t of the edge over scans (one average per scan time)."""
    if not rows:
        return {"n": 0, "avg": None, "hit": None, "edge": None, "t_edge": None, "scans": 0}
    net = [float(r["net_ret"]) for r in rows]
    edges = [float(r["net_ret"]) - float(r.get("baseline_ret") or 0) for r in rows]
    by: dict[str, list[float]] = {}
    for r, e in zip(rows, edges):
        by.setdefault(r["ts"], []).append(e)
    per_scan = [sum(v) / len(v) for v in by.values()]
    return {"n": len(rows), "avg": sum(net) / len(net), "hit": sum(x > 0 for x in net) / len(net), "edge": sum(edges) / len(edges), "t_edge": tstat(per_scan),
            "scans": len(per_scan)}


def scanner_units() -> list[dict]:
    out = []
    for key, path, name, files, styles in SCANNER:
        rows = [r for f in files for r in _rows(f)]
        for style, label in styles.items():
            rs = sorted((r for r in rows if r.get("style") == style), key=lambda r: r["ts"])
            a = _stats(rs)
            cut = rs[len(rs) * 2 // 3]["ts"] if len(rs) >= 3 else None
            late = _stats([r for r in rs if cut and r["ts"] >= cut])
            ok, why = judge(a["n"], a["avg"], a["edge"], a["t_edge"], late)
            if ok and a["scans"] < MIN_SCANS:
                ok, why = False, f"Too few separate scans to judge ({a['scans']}; at least {MIN_SCANS} needed)."
            out.append({"id": f"{key}:{style}", "section": key, "path": path, "name": f"{name} · {label}",
                        "proven": ok, "reason": why, "n": a["n"], "avg": a["avg"], "hit": a["hit"], "edge": a["edge"], "t_edge": a["t_edge"],
                        "late": {k: late[k] for k in ("n", "avg", "edge", "t_edge")}, "unit": "%", "source": "forward record"})
    return out


def _forward_failing(rec: dict | None) -> bool:
    """A strategy's live record since going live is clearly failing: 30+ trades and losing money."""
    return bool(rec and (rec.get("n") or 0) >= MIN_N and rec.get("avg") is not None and rec["avg"] < 0)


def lab_units() -> list[dict]:
    st = S.read_state("lab/stats.json") or {}
    snap = S.read_data("lab/snapshot.json") or {}
    names = {k: (v or {}).get("name", k) for k, v in (snap.get("strategies") or {}).items()}
    rec = snap.get("record") or {}
    out = []
    for k, v in (st.get("strategies") or {}).items():
        a = v.get("all") or {}
        ok = v.get("level") == "good" and not _forward_failing(rec.get(k))
        why = v.get("verdict") or ""
        if v.get("level") == "good" and not ok:
            why = "Held up on history, but its live record since going live is losing money."
        out.append({"id": f"lab:{k}", "section": "lab", "path": "lab/", "name": f"Strategy lab · {names.get(k, k)}", "proven": ok,
                    "reason": why, "n": a.get("n"), "avg": a.get("avg"), "hit": a.get("hit"), "edge": a.get("edge"), "t_edge": a.get("t_edge"),
                    "late": {x: (v.get("late") or {}).get(x) for x in ("n", "avg", "edge", "t_edge")}, "unit": "%",
                    "source": "history test",
                    "live": rec.get(k)})
    return out


def listings_units() -> list[dict]:
    snap = S.read_data("listings/snapshot.json") or {}
    rules = (snap.get("research") or {}).get("rules") or {}
    rec = snap.get("record") or {}
    out = []
    for k, v in rules.items():
        a = ((v.get("holds") or {}).get(str(v.get("chosen"))) or {}).get("all") or {}
        late = ((v.get("holds") or {}).get(str(v.get("chosen"))) or {}).get("late") or {}
        ok = v.get("level") == "good" and not _forward_failing(rec.get(k))
        why = v.get("verdict") or ""
        if v.get("level") == "good" and not ok:
            why = "Held up on history, but its live record since going live is losing money."
        out.append({"id": f"listings:{k}", "section": "listings", "path": "listings/", "name": f"New listings · {v.get('name', k)}",
                    "proven": ok, "reason": why, "n": a.get("n"), "avg": a.get("avg"), "hit": a.get("hit"), "edge": a.get("edge"), "t_edge": a.get("t_edge"),
                    "late": {x: late.get(x) for x in ("n", "avg", "edge", "t_edge")}, "unit": "%", "source": "history test",
                    "live": rec.get(k)})
    return out


def ict_units() -> list[dict]:
    snap = S.read_data("ict/snapshot.json") or {}
    out = []
    for k, v in (snap.get("record") or {}).items():
        if not isinstance(v, dict):
            continue
        n, avg, rnd, t = v.get("filled") or 0, v.get("avg_r"), v.get("random_avg_r"), v.get("t")
        edge = (avg - rnd) if avg is not None and rnd is not None else None
        ok, why = judge(n, avg, edge, t if edge is not None and edge >= 0.1 else None, None)   # t is of the R per trade
        out.append({"id": f"ict:{k}", "section": "ict", "path": "ict/", "name": f"ICT setups · {k.capitalize()}", "proven": ok,
                    "reason": why.replace("random picks", "random entries"), "n": n, "avg": avg, "edge": edge, "t_edge": t,
                    "late": None, "unit": "R", "source": "forward record"})
    return out


def other_units() -> list[dict]:
    out = []
    s = S.read_data("sniper/snapshot.json") or {}
    r = ((s.get("results") or {}).get(s.get("plan") or "standard") or {}).get("passed") or {}
    ok = bool(s.get("proven"))
    out.append({"id": "sniper", "section": "sniper", "path": "sniper/", "name": "Sniper lab", "proven": ok,
                "reason": "Passed its own proof test." if ok else
                f"Not proven yet: {r.get('n') or 0} finished trades of the {s.get('proof_n') or MIN_N} its test needs, or they lost money.",
                "n": r.get("n"), "avg": r.get("avg"), "edge": (r["avg"] - r["random"]) if r.get("avg") is not None and r.get("random") is not None else None,
                "t_edge": None, "late": None, "unit": "%", "source": "forward record"})
    s = S.read_data("predict/snapshot.json") or {}
    r = s.get("record") or {}
    n, avg, t = r.get("settled") or 0, r.get("avg"), r.get("t")
    ok, why = judge(n, avg, avg, t, None)
    out.append({"id": "predict", "section": "predict", "path": "predict/", "name": "Prediction markets", "proven": ok,
                "reason": why.replace("random picks", "break-even"), "n": n, "avg": avg, "edge": avg, "t_edge": t, "late": None,
                "unit": "%", "source": "forward record"})
    s = S.read_data("whales/snapshot.json") or {}
    v = s.get("verdict") or {}
    days, lead, rnd = v.get("days") or 0, v.get("lead"), v.get("rand")
    ok = days >= 30 and (lead or 0) > 0 and (lead or 0) > (rnd or 0)
    out.append({"id": "whales", "section": "whales", "path": "whales/", "name": "Whale tracker", "proven": ok,
                "reason": "Copying the whales made money and beat random picks over 30+ days." if ok else
                (v.get("text") or "Not enough days yet.") if days < 30 else "Copying the whales lost money or did no better than random picks.",
                "n": days, "avg": lead, "edge": (lead - rnd) if lead is not None and rnd is not None else None, "t_edge": None,
                "late": None, "unit": "%", "source": "forward record (days)"})
    s = S.read_data("accum/snapshot.json") or {}                # Accumulation lab (D119): two separate parts
    f30 = ((s.get("forward") or {}).get("summary") or {}).get("30") or {}
    ok, why = judge(f30.get("n") or 0, f30.get("avg"), f30.get("edge"), f30.get("t_edge"), None)
    out.append({"id": "accum:watchlist", "section": "accum", "path": "accum/", "name": "Accumulation lab · Watchlist",
                "proven": ok, "reason": why.replace("trades", "detections judged 30 days later").replace("random picks", "the other tokens watched"),
                "n": f30.get("n") or 0, "avg": f30.get("avg"), "hit": f30.get("hit"), "edge": f30.get("edge"), "t_edge": f30.get("t_edge"),
                "late": None, "unit": "%", "source": "forward record (30 days after each detection)"})
    b = ((s.get("backtest") or {}).get("results") or {}).get("90") or {}
    a = b.get("all") or {}
    out.append({"id": "accum:recovery", "section": "accum", "path": "accum/", "name": "Accumulation lab · Distressed recovery",
                "proven": b.get("level") == "good", "reason": b.get("verdict") or "Backtest not run yet.", "n": a.get("n"),
                "avg": a.get("avg"), "hit": a.get("hit"), "edge": a.get("edge"), "t_edge": a.get("t_edge"),
                "late": {x: (b.get("late") or {}).get(x) for x in ("n", "avg", "edge", "t_edge")}, "unit": "%",
                "source": "history test, 90 days after each signal (delisted coins included)"})
    return out


SECTION_NAMES = {"main": "Exchange coins", "small": "Small coins", "dex": "DEX tokens", "stocks": "US stocks", "fx": "Forex & gold",
                 "ict": "ICT setups", "lab": "Strategy lab", "listings": "New listings", "sniper": "Sniper lab",
                 "predict": "Prediction markets", "whales": "Whale tracker", "accum": "Accumulation lab"}


def build(now: datetime | None = None) -> dict:
    now = now or S.now_utc()
    units = []
    for f in (scanner_units, lab_units, listings_units, ict_units, other_units):
        try:
            units += f()
        except Exception as e:  # noqa: BLE001  one broken source never hides the others
            units.append({"id": f.__name__, "section": f.__name__, "proven": False, "reason": f"Could not be checked: {e}"})
    sections = {}
    for key, name in SECTION_NAMES.items():
        us = [u for u in units if u.get("section") == key]
        pr = [u["name"].split(" · ", 1)[-1] for u in us if u.get("proven")]
        status = "proven" if us and len(pr) == len(us) else "partly" if pr else "testing"
        sections[key] = {"name": name, "status": status, "proven_units": pr, "units": len(us),
                         "crypto": key in CRYPTO}
    out = {"generated_at": now.isoformat(), "rule": {"min_n": MIN_N, "t_edge": 2.0, "late_t_edge": 1.0},
           "units": units, "sections": sections, "proven_ids": [u["id"] for u in units if u.get("proven")]}
    log_changes(out, now)
    (S.STATE / "proof.json").write_text(json.dumps(out, indent=1, default=str))
    return out


def log_changes(new: dict, now: datetime) -> None:
    old = {u["id"]: u.get("proven") for u in (S.read_state("proof.json") or {}).get("units", [])}
    p = S.STATE / "proof_log.csv"
    first = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "id", "status", "n", "avg", "edge", "t_edge", "reason"])
        if first:
            w.writeheader()
        for u in new["units"]:
            if u["id"] in old and old[u["id"]] == u.get("proven"):
                continue
            w.writerow({"t": now.isoformat(), "id": u["id"], "status": "proven" if u.get("proven") else "testing",
                        "n": u.get("n"), "avg": u.get("avg"), "edge": u.get("edge"), "t_edge": u.get("t_edge"), "reason": u.get("reason")})


if __name__ == "__main__":
    d = build()
    for u in d["units"]:
        print(("PROVEN " if u.get("proven") else "testing"), u["id"], "|", u.get("reason"))
