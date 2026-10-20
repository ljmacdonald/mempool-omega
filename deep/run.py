"""Weekly validation run for Engine A (Hayes Recovery, D121) and Engine B (Deep Distress, D122). PAPER ONLY.

Each engine must pass its own kill criteria on history before it may run a paper account (the specifications' rule).
This job re-runs every test on the latest data each week and publishes the reports; nothing is tuned on the results,
so a pass would be a genuine new pass, not a fitted one. All sources are free and keyless:
  Binance public data (every USDT coin, delisted ones included), Coinbase public data (USD/USDC coins Binance never
  listed, delisted ones included), FRED CSVs, DefiLlama stablecoin totals.

  python -m deep.run              # both engines; writes .cache/engines_out/{hayes,deep}.json
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from deep import backtest as BT
from deep import data as DD
from deep import research as DR
from deep.profile import fingerprint as fp_b
from deep.profile import load as load_b
from hayes import data as D
from hayes import research as R
from hayes.profile import fingerprint as fp_a
from hayes.profile import load as load_a

log = logging.getLogger("deep.run")
CACHE = Path(".cache/engines")
OUT = Path(".cache/engines_out")


def _gz_load(p: Path):
    try:
        return json.loads(gzip.decompress(p.read_bytes()))
    except (OSError, ValueError):
        return None


def _gz_save(p: Path, d) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(gzip.compress(json.dumps(d, separators=(",", ":")).encode()))


def inputs(now: datetime) -> dict:
    """Every coin either exchange lists (live or delisted), its daily candles, and the regime inputs."""
    known: dict[str, set[str]] = {}
    trading: set[str] = set()
    series: dict[str, list[dict]] = {}
    syms = D.binance_symbols()
    for s in syms:
        b = s["baseAsset"]
        if not R.eligible_symbol(b):
            continue
        known.setdefault(b, set()).add("binance")
        if s.get("status") == "TRADING":
            trading.add(b)
        p = CACHE / "bn" / f"{s['symbol']}.json.gz"
        have = _gz_load(p) or []
        if s.get("status") == "TRADING" or not have:          # delisted coins' history never changes: fetched once
            have = D.binance_daily(s["symbol"], have)
            _gz_save(p, have)
        if have:
            series[b] = D.to_candles(have)
    btc_k = D.binance_daily("BTCUSDT", _gz_load(CACHE / "bn" / "BTCUSDT.json.gz") or [])
    _gz_save(CACHE / "bn" / "BTCUSDT.json.gz", btc_k)
    prods: dict[str, dict] = {}
    for p in sorted(DD.coinbase_products(), key=lambda p: (p["quote_currency"] != "USD", p["id"])):
        b = p["base_currency"]
        if not R.eligible_symbol(b):
            continue
        known.setdefault(b, set()).add("coinbase")
        prods.setdefault(b, p)
        if p.get("status") == "online":
            trading.add(b)
    for b, p in prods.items():
        if b in series:
            continue                                          # Binance's history first; Coinbase fills the coins it lacks
        f = CACHE / "cb" / f"{b}.json.gz"
        have = _gz_load(f)
        if have is None or p.get("status") == "online":
            since = have[-1]["d"] if have else "2017-01-01"
            new = DD.coinbase_daily(p["id"], since, now)
            have = sorted({x["d"]: x for x in (have or []) + new}.values(), key=lambda x: x["d"])
            _gz_save(f, have)
        if have:
            series[b] = have
    for x in ("BTC",):
        series.pop(x, None)
        known.pop(x, None)
    fred = {k: D.fred(k) for k in ("WALCL", "RRPONTSYD", "WTREGEN")}
    return {"known": known, "trading": trading, "series": series, "btc": D.to_candles(btc_k), "fred": fred, "stables": D.stablecoins()}


def trim(es: dict, n: int = 60) -> dict:
    es = dict(es)
    es["samples"] = es.get("samples", [])[-n:]
    es.pop("events_by_coin", None)
    return es


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    t0 = time.time()
    now = datetime.now(timezone.utc)
    inp = inputs(now)
    log.info("inputs: %d known coins, %d with candles (%.0fs)", len(inp["known"]), len(inp["series"]), time.time() - t0)
    btc_close = {x["d"]: x["c"] for x in inp["btc"]}
    base = {"generated_at": now.isoformat(), "paper_only": True,
            "data": {"known_coins": len(inp["known"]), "coins_with_candles": len(inp["series"]),
                     "delisted_with_candles": sum(1 for c in inp["series"] if c not in inp["trading"]),
                     "regime_inputs": {k: (v[-1][0] if v else None) for k, v in inp["fred"].items()} |
                                      {"stablecoins": inp["stables"][-1][0] if inp["stables"] else None}}}
    OUT.mkdir(parents=True, exist_ok=True)
    for name, P, fp in (("hayes", load_a(), fp_a()), ("deep", load_b(), fp_b())):
        today = R.regime_fn(inp["fred"], inp["stables"], btc_close, P, full=True)(max(btc_close))
        out = {**base, "engine": P["engine"], "profile_version": P["profile_version"], "profile_fingerprint": fp, "regime_today": today}
        if name == "hayes":
            es = R.event_study(inp["series"], inp["trading"], inp["btc"], inp["fred"], inp["stables"], P)
            out |= {"event_study": trim(es), "kill": R.kill_a(es, P)}
        else:
            st = DR.study(inp["series"], inp["trading"], inp["known"], inp["btc"], inp["fred"], inp["stables"], P)
            out |= {"coverage": st["coverage"], "event_study": trim(st["event_study"]), "context_75_92": trim(st["context_75_92"], 0), "kill": st["kill"]}
            if st["kill"]["status"] == "paper":                # the backtest runs only after the event study passes
                trades = BT.trades_for(inp["series"], inp["trading"], btc_close, R.regime_fn(inp["fred"], inp["stables"], btc_close, P), P)
                main_bt = BT.run(trades, P)
                every = BT.run(trades, P, ("risk_on", "neutral", "risk_off"))
                out["backtest"] = {"main": main_bt, "every_regime": every, "kill": BT.kill(main_bt, P), "kill_every_regime": BT.kill(every, P),
                                   "btc_hold": {a: _hold(btc_close, a, b) for a, b in BT.WINDOWS},
                                   "trades": sorted(trades, key=lambda t: t["entry_day"])[-200:]}
                out["kill"]["backtest"] = out["backtest"]["kill"]
                out["kill"]["status"] = out["backtest"]["kill"]["status"]
        out["status"] = out["kill"]["status"]
        (OUT / f"{name}.json").write_text(json.dumps(out, separators=(",", ":"), default=str))
        log.info("%s: %s (%.0fs)", P["engine"], out["status"], time.time() - t0)


def _hold(close: dict, a: str, b: str) -> float | None:
    xs = [close[k] for k in sorted(close) if a <= k <= b]
    return xs[-1] / xs[0] - 1 if len(xs) > 1 else None


if __name__ == "__main__":
    main()
