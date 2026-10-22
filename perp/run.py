"""Perp DEX lab jobs (D123). PAPER ONLY: nothing here can sign or send an order.

  python -m perp.run              hourly: live snapshot -> data checks -> kill switches -> each strategy's paper step
                                  on the $100 and $10,000 books -> decision log -> .cache/perp_out/snapshot.json
  python -m perp.run --research   daily: every history test, benchmark, the execution-gap study and the gates
                                  -> .cache/perp_out/research.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from perp import history as HI
from perp import paper as PA
from perp import sources as SRC
from perp.config import fingerprint, load
from perp.money import ZERO, d, waterfall

log = logging.getLogger("perp.run")
OUT = Path(".cache/perp_out")


def _bars_binance(coin: str, interval: str, n: int, now: float) -> list[list]:
    step = {"1h": 3_600_000, "15m": 900_000}[interval]
    ks = SRC.binance_klines(f"{coin}USDT", interval, int(now * 1000) - n * step)
    return [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), k[6], float(k[7])]
            for k in ks if k[6] < now * 1000]


def hourly(now: float | None = None) -> dict:
    now = now or time.time()
    from perp import live as LV
    snap = LV.snapshot(now)
    st = PA.load_state(now)
    if st.get("config") != fingerprint():
        st.setdefault("config_changes", []).append({"t": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                                                    "from": st.get("config"), "to": fingerprint()})
        st["config"] = fingerprint()
    bars = {}
    P = load()["directional"]
    for coin, c in snap["contexts"].items():
        bars[("bn1h", coin)] = _bars_binance(coin, "1h", P["atr_floor_window"] + P["ema"] + 30, now)
        bars[("bn15m", coin)] = _bars_binance(coin, "15m", 200, now)
        g = HI.gmx_candles(c["name"].split("/")[0], "1h")
        bars[("gmx", coin)] = g
        bars[("hl", coin)] = HI.hl_candles_1h(c["hl_coin"]) if c.get("hl_coin") else {}
    lp_in = None
    probe = HI.gm_prices(snap["contexts"]["ETH"]["address"]) if "ETH" in snap["contexts"] else {}
    latest = max(probe) if probe else None
    held = any(p for b in st["books"].values() for p in b["lp"]["open"])
    if latest and (st.get("lp_day") != latest or held):
        lp_in = PA.lp_live_inputs(snap) if st.get("lp_day") != latest else _held_prices(st)
    rows = PA.step(st, snap, now, lp_in, bars)
    PA.log_decisions(rows, now, st)
    PA.save_state(st)
    out = page(st, snap, rows, now)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "snapshot.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
    return out


def _held_prices(st: dict) -> dict:
    """Live pool prices for the pools held (between daily re-rankings)."""
    gm_now = {}
    names = {p["market"] for b in st["books"].values() for p in b["lp"]["open"]}
    for m in HI.markets():
        if m["name"] in names:
            raw = SRC.gmx_market_settings(m["address"]) or {}
            if raw.get("poolValue") and raw.get("marketTokenSupply"):
                gm_now[m["name"]] = (Decimal(raw["poolValue"]) / Decimal(10) ** 30) / (Decimal(raw["marketTokenSupply"]) / Decimal(10) ** 18)
    return {"pools": [], "days": [st["lp_day"]], "gm_now": gm_now, "only_marks": True}


def page(st: dict, snap: dict, rows: list[dict], now: float) -> dict:
    """What the pages show: per strategy and book, equity, positions, closed trades, the cost waterfall and drift,
    the decision log summary, and the shadow-gate inputs."""
    from perp.money import Trade
    books = {}
    for book, accs in st["books"].items():
        books[book] = {}
        for strat, acc in accs.items():
            closed = acc["closed"]
            ts = []
            for t in closed:
                tr = Trade(t["strategy"], book, t["market"], t["side"], d(t["notional"]), t["opened"], t["closed"])
                tr.gross, tr.costs = d(t["gross"]), {k: d(v) for k, v in t["costs"].items()}
                ts.append(tr)
            eq = PA.equity(acc, snap)
            books[book][strat] = {"start": acc["start"], "equity": str(eq), "net": str(eq - d(acc["start"])),
                                  "paused": acc["paused"], "open": [{k: v for k, v in p.items() if k not in ("est",)} for p in acc["open"]],
                                  "closed": closed[-40:][::-1], "n_closed": len(closed), "curve": acc["equity"][-24 * 30:],
                                  "waterfall": waterfall(ts) if ts else [],
                                  "costs_total": str(sum((sum(t.costs.values(), ZERO) for t in ts), ZERO))}
    recent = rows[-400:]
    counts: dict = {}
    for r in rows:
        k = (r["strategy"], r["decision"], r.get("why", "")[:80])
        counts[k] = counts.get(k, 0) + 1
    return {"generated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(), "paper_only": True,
            "started": st["started"], "config": st["config"], "books": books,
            "markets": {k: {"name": c["name"], "oracle": c["oracle"], "oracle_age_s": c["oracle_age_s"],
                            "oi_long": c["oi_long"], "oi_short": c["oi_short"],
                            "funding_long_annual": float(c["funding_ps_long"] * 31_536_000) if c["funding_ps_long"] is not None else None,
                            "funding_short_annual": float(c["funding_ps_short"] * 31_536_000) if c["funding_ps_short"] is not None else None,
                            "borrow_long_annual": float(c["borrow_ps_long"] * 31_536_000) if c["borrow_ps_long"] is not None else None,
                            "borrow_short_annual": float(c["borrow_ps_short"] * 31_536_000) if c["borrow_ps_short"] is not None else None,
                            "hl_mid": c["hl"]["mid"] if c.get("hl") else None,
                            "hl_funding_annual": float(c["hl"]["funding_hourly"] * 8760) if c.get("hl") and c["hl"]["funding_hourly"] is not None else None,
                            "quality": snap["quality"][k]} for k, c in snap["contexts"].items()},
            "kill": snap["kill"], "kills": st.get("kills", [])[-20:], "exec_fee_usd": snap["exec_fee_usd"], "usdc": snap["usdc"],
            "decisions": recent[::-1][:150],
            "decision_counts": [{"strategy": k[0], "decision": k[1], "why": k[2], "n": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])],
            "shadow": {b: PA.shadow_stats(st, b) for b in st["books"]},
            "sources": SRC.HEALTH}


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if "--research" in argv:
        from perp import research as RS
        out = RS.run()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "research.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
        log.info("research done in %ss", out.get("seconds"))
        return 0
    hourly()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
