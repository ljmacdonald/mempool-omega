"""The $100 arbitrage paper account (DECISIONS D105): trades the Arbitrage lab's own opportunities, virtually.

It starts with $100 and every hourly session sizes its round trips at the account's whole value in SOL (scanning
quotes at that size, so price impact is the account's own). For each opportunity it acts the way a fast but not
professional trader could:

  * it "sends" at the first re-check (about 1 second after discovery: this engine's fastest reaction) as an atomic
    Jito bundle with the tip used for that session;
  * if the round trip at that moment still clears the minimum output, the bundle executes: the buy and the sell happen
    in the same transaction (entry and exit share one time and slot), and the account gains the re-checked output
    minus the base fee and the tip;
  * if the gap has closed, the bundle reverts and costs nothing: recorded as "missed".

What this assumes, shown on the page: every bundle sent lands (unmeasured, since nothing is really sent), and the
re-checked quote is what the chain would have paid. Profit is converted to dollars at the SOL price of that session.
Nothing here is a realised result.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone

from core.config import state_path
from mev import config as K
from mev.economics import method_costs, min_out

START_USD = 100.0
METHOD = "jito_bundle"
DIR = "mev/account"


def _p(name):
    p = state_path(DIR, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    try:
        return json.loads(_p("account.json").read_text())
    except (OSError, ValueError):
        return {"start": START_USD, "equity": START_USD, "trades": 0, "missed": 0, "fees_usd": 0.0,
                "started_at": datetime.now(timezone.utc).isoformat(), "updated": None, "done_ids": []}


def trade_lamports(acc: dict, sol_usd: float | None) -> int:
    """The account's whole value in SOL (capped by the hard input limit). Falls back to TRADE_LAMPORTS."""
    if not sol_usd or sol_usd <= 0:
        return K.TRADE_LAMPORTS
    return int(min(acc["equity"] / sol_usd * K.LAMPORTS, K.MAX_INPUT_LAMPORTS))


def _log(text: str, kind: str, at: str) -> None:
    p = _p("log.json")
    try:
        log = json.loads(p.read_text())
    except (OSError, ValueError):
        log = []
    log.insert(0, {"t": at, "kind": kind, "text": text})
    p.write_text(json.dumps(log[:3000], indent=1))


def _row(name: str, row: dict) -> None:
    p = _p(name)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def apply(opps: list[dict], sol_usd: float | None, session_at: str) -> dict:
    """Trade or miss each new opportunity in time order; then record the account's value for this session."""
    acc = load()
    done = set(acc.get("done_ids", []))
    for c in sorted(opps, key=lambda c: c["found_at"]):
        if c["id"] in done or c.get("v", 1) < K.METHOD_VERSION or (c.get("leg_gap_s") or 99) > K.MAX_LEG_GAP_S:
            continue
        done.add(c["id"])
        r = next((x for x in c.get("rechecks", []) if x["delay"] == K.DELAYS_S[0]), None)
        if r is None or r.get("out") is None or not sol_usd:
            continue                                   # never re-checked: no decision possible, nothing recorded
        costs = method_costs(c["cu_price"], c["tip"])[METHOD]
        at = (datetime.fromisoformat(c["found_at"]) + timedelta(seconds=r.get("elapsed_s") or 0)).isoformat()
        usd = lambda lam: lam / K.LAMPORTS * sol_usd  # noqa: E731
        size = usd(c["amount_in"])
        route = f"{c['token']}: buy on {c['buy_dex']}, sell on {c['sell_dex']}"
        if r["out"] >= min_out(c["amount_in"], costs):
            profit = r["out"] - c["amount_in"] - costs.on_success
            acc["equity"] += usd(profit)
            acc["trades"] += 1
            acc["fees_usd"] += usd(costs.on_success)
            _row("trades.csv", {"time": at, "slot": r.get("slot"), "result": "traded", "token": c["token"],
                                "buy_dex": c["buy_dex"], "sell_dex": c["sell_dex"], "size_usd": round(size, 2),
                                "seen_gain_usd": round(usd(c["out"] - c["amount_in"]), 4),
                                "profit_usd": round(usd(profit), 4), "costs_usd": round(usd(costs.on_success), 4),
                                "equity": round(acc["equity"], 4)})
            _log(f"Traded {route}, ${size:,.2f} in one Jito bundle at {at[11:19]} UTC (slot {r.get('slot')}): bought and "
                 f"sold in the same transaction, {usd(profit):+.4f} dollars after ${usd(costs.on_success):.4f} of fees and "
                 f"tip. Account ${acc['equity']:,.2f}.", "trade", at)
        else:
            acc["missed"] += 1
            _row("trades.csv", {"time": at, "slot": r.get("slot"), "result": "missed", "token": c["token"],
                                "buy_dex": c["buy_dex"], "sell_dex": c["sell_dex"], "size_usd": round(size, 2),
                                "seen_gain_usd": round(usd(c["out"] - c["amount_in"]), 4), "profit_usd": 0.0,
                                "costs_usd": 0.0, "equity": round(acc["equity"], 4)})
            _log(f"Missed {route}: a {usd(c['out'] - c['amount_in']):+.4f} dollar gap was seen, but {r.get('elapsed_s')} s "
                 f"later it no longer covered the costs, so the bundle would have reverted (no cost).", "missed", at)
    acc["done_ids"] = sorted(done)[-5000:]
    acc["updated"] = session_at
    acc["sol_usd"] = sol_usd
    _p("account.json").write_text(json.dumps(acc, indent=1))
    _row("equity.csv", {"t": session_at, "equity": round(acc["equity"], 4)})
    if not opps:
        _log("Searched for an hour's worth of round trips: none paid after costs, so no trade (the account stays in "
             "cash).", "check", session_at)
    return acc


def daily(rows: list[dict]) -> list[dict]:
    """Per UTC day: trades, misses, profit."""
    out: dict[str, dict] = {}
    for r in rows:
        d = out.setdefault(r["time"][:10], {"day": r["time"][:10], "traded": 0, "missed": 0, "profit_usd": 0.0})
        d[r["result"]] = d.get(r["result"], 0) + 1
        d["profit_usd"] += float(r["profit_usd"] or 0)
    return sorted(out.values(), key=lambda d: d["day"], reverse=True)


def view() -> dict:
    """Everything the page shows about the account."""
    acc = load()
    try:
        with _p("trades.csv").open() as f:
            rows = list(csv.DictReader(f))
    except OSError:
        rows = []
    try:
        with _p("equity.csv").open() as f:
            eq = [(r["t"], float(r["equity"])) for r in csv.DictReader(f)]
    except OSError:
        eq = []
    try:
        log = json.loads(_p("log.json").read_text())[:40]
    except (OSError, ValueError):
        log = []
    return {"start": acc["start"], "equity": acc["equity"], "trades": acc["trades"], "missed": acc["missed"],
            "fees_usd": acc["fees_usd"], "started_at": acc["started_at"], "updated": acc["updated"],
            "sol_usd": acc.get("sol_usd"), "method": METHOD, "daily": daily(rows)[:30], "recent": rows[-40:][::-1],
            "equity_curve": eq[-24 * 30:], "log": log}
