"""The $100 funding-income account (DECISIONS D108). Paper only.

What it does, per coin in BASKET: buy the coin on OKX spot and, at the same moment, open an equal short position in
its USDT perpetual future at 1x (the futures margin equals the position, so the price can rise ~70% before the risk
rule closes it). Price moves cancel out between the two legs; what is left is the funding payment: every funding
time (usually 8-hourly) the short side receives the realised funding rate x the position when the rate is positive,
and pays it when negative.

Capital: $100 is split equally across the basket; each coin's share is half spot, half futures margin, so the funding
earned is on half the money.

Rules (fixed, decided before going live, from 3 months of OKX history, 13 Jul-14 Oct 2026):
  * enter a coin when its funding over the last 7 days added up to more than ENTER (0.05%);
  * leave it when that 7-day sum turns negative (the trade would start paying);
  * leave it at once if the futures leg's loss reaches RISK_STOP of its margin (a sharp rise);
  * a coin that was closed isn't reopened for COOLDOWN_H hours;
  * every entry and exit pays both legs' taker fees and half the buy-sell gap, at live prices.
The research behind it: holding through was +3% to +7% a year in funding for major coins (Bitcoin ~5%), while
switching between coins chasing the highest rate lost money to fees. One coin (WAL) went sharply negative.

    python -m carry.run            # hourly
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from core.config import state_path

OKX = "https://www.okx.com"
BASKET = ("BTC", "ETH", "SOL", "XRP", "DOGE")
START = 100.0
SPOT_FEE, PERP_FEE = 0.001, 0.0005     # OKX regular-tier taker fees
ENTER, EXIT = 0.0005, 0.0              # 7-day funding sum thresholds
RISK_STOP = 0.7                        # close if the short leg's loss reaches 70% of its margin
COOLDOWN_H = 24                        # a closed coin isn't reopened for a day (no paying fees twice in an hour)
DIR = "carry"


def _get(path: str, **params):
    for i in range(4):
        try:
            r = requests.get(OKX + path, params=params, timeout=15)
            d = r.json()
            if r.status_code == 200 and d.get("code") == "0":
                return d["data"]
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1 + i)
    raise RuntimeError(f"OKX {path} unavailable")


def market(coin: str) -> dict:
    """Live bid/ask for spot and perp, the current period's funding rate and the last 7 days of realised funding."""
    s = _get("/api/v5/market/ticker", instId=f"{coin}-USDT")[0]
    p = _get("/api/v5/market/ticker", instId=f"{coin}-USDT-SWAP")[0]
    fr = _get("/api/v5/public/funding-rate", instId=f"{coin}-USDT-SWAP")[0]
    hist = _get("/api/v5/public/funding-rate-history", instId=f"{coin}-USDT-SWAP", limit=100)
    return {"spot_bid": float(s["bidPx"]), "spot_ask": float(s["askPx"]), "perp_bid": float(p["bidPx"]),
            "perp_ask": float(p["askPx"]), "rate_now": float(fr["fundingRate"]),
            "next_funding": datetime.fromtimestamp(int(fr["fundingTime"]) / 1000, timezone.utc).isoformat(),
            "history": [{"t": int(x["fundingTime"]), "rate": float(x.get("realizedRate") or x["fundingRate"])} for x in hist]}


def sum_7d(history: list[dict], now_ms: int) -> float:
    return sum(x["rate"] for x in history if now_ms - x["t"] <= 7 * 86_400_000)


# --------------------------------------------------------------------------------------------- the account
def _p(name: str) -> Path:
    p = state_path(DIR, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict:
    try:
        return json.loads(_p("account.json").read_text())
    except (OSError, ValueError):
        return {"start": START, "cash": START, "equity": START, "positions": {}, "funding_total": 0.0,
                "fees_total": 0.0, "trades": 0, "started_at": datetime.now(timezone.utc).isoformat(), "updated": None}


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


def value(pos: dict, m: dict) -> float:
    """What the position would fetch if closed now (before closing fees): spot at the bid, futures gain/loss at the ask."""
    return pos["qty"] * m["spot_bid"] + pos["margin"] + pos["qty"] * (pos["perp_entry"] - m["perp_ask"])


def open_pos(acc: dict, coin: str, m: dict, budget: float, at: str) -> None:
    """Half the budget buys the coin (at the ask, plus fee); the other half is futures margin for an equal short."""
    spot_usd = budget / 2
    qty = spot_usd / (m["spot_ask"] * (1 + SPOT_FEE))
    perp_fee = qty * m["perp_bid"] * PERP_FEE
    margin = budget - spot_usd - perp_fee
    fees = spot_usd - qty * m["spot_ask"] + perp_fee
    acc["cash"] -= budget
    acc["fees_total"] += fees
    acc["trades"] += 1
    acc["positions"][coin] = {"qty": qty, "spot_entry": m["spot_ask"], "perp_entry": m["perp_bid"], "margin": margin,
                              "opened": at, "funding": 0.0, "last_funding_t": int(datetime.fromisoformat(at).timestamp() * 1000)}
    _row("trades.csv", {"time": at, "coin": coin, "action": "open", "spot_price": m["spot_ask"], "perp_price": m["perp_bid"],
                        "qty": round(qty, 8), "usd": round(budget, 4), "fees_usd": round(fees, 4), "funding_usd": "", "why": ""})
    _log(f"Opened {coin}: bought {qty:.6g} {coin} at ${m['spot_ask']:,.6g} and sold the same amount of the {coin} "
         f"future at ${m['perp_bid']:,.6g} (${budget:,.2f} in all, ${fees:.4f} of fees and gaps). Price moves now cancel "
         f"out; it earns the funding rate, now {m['rate_now'] * 100:+.4f}% per period.", "open", at)


def close_pos(acc: dict, coin: str, m: dict, at: str, why: str) -> None:
    pos = acc["positions"].pop(coin)
    acc.setdefault("cooldown", {})[coin] = (datetime.fromisoformat(at) + timedelta(hours=COOLDOWN_H)).isoformat()
    proceeds = pos["qty"] * m["spot_bid"] * (1 - SPOT_FEE)
    perp_pnl = pos["qty"] * (pos["perp_entry"] - m["perp_ask"]) - pos["qty"] * m["perp_ask"] * PERP_FEE
    back = proceeds + pos["margin"] + perp_pnl
    fees = pos["qty"] * m["spot_bid"] * SPOT_FEE + pos["qty"] * m["perp_ask"] * PERP_FEE
    acc["cash"] += back
    acc["fees_total"] += fees
    acc["trades"] += 1
    _row("trades.csv", {"time": at, "coin": coin, "action": "close", "spot_price": m["spot_bid"], "perp_price": m["perp_ask"],
                        "qty": round(pos["qty"], 8), "usd": round(back, 4), "fees_usd": round(fees, 4),
                        "funding_usd": round(pos["funding"], 4), "why": why})
    _log(f"Closed {coin} ({why}): sold the coin at ${m['spot_bid']:,.6g} and bought back the future at "
         f"${m['perp_ask']:,.6g}; ${back:,.2f} came back, after ${pos['funding']:+.4f} of funding collected while open.",
         "close", at)


def step(acc: dict, markets: dict[str, dict], now: datetime | None = None) -> dict:
    """One hourly update: collect funding, apply the risk rule, then exits and entries by the 7-day rule."""
    now = now or datetime.now(timezone.utc)
    at, now_ms = now.isoformat(), int(now.timestamp() * 1000)
    for coin, pos in list(acc["positions"].items()):
        m = markets.get(coin)
        if not m:
            continue
        for f in sorted(m["history"], key=lambda x: x["t"]):     # funding settled since the last update
            if f["t"] > pos["last_funding_t"] and f["t"] <= now_ms:
                amt = pos["qty"] * (m["perp_bid"] + m["perp_ask"]) / 2 * f["rate"]   # short side receives a positive rate
                pos["funding"] += amt
                acc["cash"] += amt
                acc["funding_total"] += amt
                pos["last_funding_t"] = f["t"]
                ft = datetime.fromtimestamp(f["t"] / 1000, timezone.utc).isoformat()
                _row("funding.csv", {"time": ft, "coin": coin, "rate": f["rate"], "usd": round(amt, 6)})
                _log(f"Funding on {coin}: {f['rate'] * 100:+.4f}% on ${pos['qty'] * m['spot_bid']:,.2f} = "
                     f"{'+' if amt >= 0 else '−'}${abs(amt):.5f}.", "funding", ft)
        if pos["qty"] * (m["perp_ask"] - pos["perp_entry"]) >= RISK_STOP * pos["margin"]:
            close_pos(acc, coin, m, at, f"risk rule: the futures loss reached {RISK_STOP:.0%} of its margin")
        elif sum_7d(m["history"], now_ms) < EXIT:
            close_pos(acc, coin, m, at, "its 7-day funding turned negative")
    budget_each = acc["start"] / len(BASKET)
    for coin in BASKET:
        m = markets.get(coin)
        if not m or coin in acc["positions"] or acc.get("cooldown", {}).get(coin, "") > at:
            continue
        if sum_7d(m["history"], now_ms) > ENTER and acc["cash"] >= budget_each * 0.5:
            open_pos(acc, coin, m, min(budget_each, acc["cash"]), at)
    for c, p in acc["positions"].items():           # a coin whose prices didn't load keeps its last known value
        if c in markets:
            p["last_value"] = value(p, markets[c])
    acc["equity"] = acc["cash"] + sum(p.get("last_value", p["margin"] * 2) for p in acc["positions"].values())
    acc["updated"] = at
    _p("account.json").write_text(json.dumps(acc, indent=1))
    _row("equity.csv", {"t": at, "equity": round(acc["equity"], 4)})
    return acc


def view(acc: dict, markets: dict[str, dict]) -> dict:
    def read(name):
        try:
            with _p(name).open() as f:
                return list(csv.DictReader(f))
        except OSError:
            return []
    try:
        log = json.loads(_p("log.json").read_text())[:40]
    except (OSError, ValueError):
        log = []
    now_ms = int(time.time() * 1000)
    funding = read("funding.csv")
    by_day: dict[str, float] = {}
    for r in funding:
        by_day[r["time"][:10]] = by_day.get(r["time"][:10], 0.0) + float(r["usd"])
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "account": {k: v for k, v in acc.items()},
            "coins": [{"coin": c, "rate_now": m["rate_now"], "next_funding": m["next_funding"],
                       "sum_7d": sum_7d(m["history"], now_ms), "held": c in acc["positions"],
                       "per_year": sum_7d(m["history"], now_ms) * 365 / 7,
                       "basis": m["perp_bid"] / m["spot_ask"] - 1} for c, m in markets.items()],
            "trades": read("trades.csv")[-40:][::-1], "funding_by_day": sorted(by_day.items(), reverse=True)[:60],
            "equity_curve": [(r["t"], float(r["equity"])) for r in read("equity.csv")][-24 * 60:], "log": log,
            "rules": {"basket": BASKET, "enter_7d": ENTER, "exit_7d": EXIT, "risk_stop": RISK_STOP,
                      "spot_fee": SPOT_FEE, "perp_fee": PERP_FEE}}


def main() -> None:
    markets = {}
    for c in BASKET:
        try:
            markets[c] = market(c)
        except Exception as e:  # noqa: BLE001
            print(f"carry: {c} skipped: {e}")
    acc = step(load(), markets)
    out = Path(".cache/carry_out/snapshot.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(view(acc, markets), indent=1, default=str))
    print(f"carry: equity ${acc['equity']:.4f}, holding {sorted(acc['positions'])}, funding ${acc['funding_total']:+.5f}")


if __name__ == "__main__":
    main()
