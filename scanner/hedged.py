"""The hedged $100 Small-coin account (DECISIONS D111). Paper only.

The same trades as the $100 Small-coin challenge (scanner/challenge.py: same coins, times, sizes and exits), each
paired with a short position in the Bitcoin perpetual future, so that a fall in the whole crypto market is partly
offset. The question it answers: do the Small coins picks make money once the market's move is hedged out?

How each hedge is set, at the moment the coin is bought, from data before that moment only:
  * beta: how much the coin has moved with Bitcoin over the previous BETA_BARS hours (hourly returns), kept within
    BETA_CAP; the hedge is beta x the coin position (a coin that swings twice as hard as Bitcoin gets twice the short);
  * the short opens at Bitcoin's price at the coin's fill time and closes when the coin is sold, at that time's price;
  * costs: the perpetual future's taker fee and half its buy-sell gap, both ways, on the hedge's size; funding
    (realised OKX BTC rates while open) is received when positive and paid when negative.
Hedged value = the challenge's own value + the hedges' results (open hedges marked at the latest Bitcoin price, with
their closing costs already taken). Margin for the hedge is assumed to come from the account's idle cash; it isn't
otherwise locked here, which the page says.

Research before building (14 days of finished Small coins Day ideas): a 1x Bitcoin hedge took the average idea from
-0.78% to -0.49% (1.5x: -0.30%); small coins fell about twice as hard as Bitcoin, so a Bitcoin hedge only partly
offsets them. Built anyway, at the owner's request, as a forward test.
"""
from __future__ import annotations

import csv
import json

import numpy as np
import pandas as pd

from core.config import state_path
from core.log import get_logger

log = get_logger("scanner.hedged")
SRC = "suggestions/challenge_small"
DIR = "suggestions/challenge_small_hedged"
BETA_BARS = 240
BETA_CAP = (0.5, 2.5)
PERP_FEE = 0.0005          # OKX USDT perpetual taker fee, each way
PERP_HALF_SPREAD = 0.0001  # Bitcoin's future: about a 0.01% gap between buy and sell prices
BTC = "BTCUSDT"


def _p(name: str):
    p = state_path(DIR, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def beta(coin: pd.DataFrame | None, btc: pd.DataFrame, before: pd.Timestamp) -> float:
    """Slope of the coin's hourly returns on Bitcoin's, over the BETA_BARS hours before `before`; 1.0 if too little data."""
    if coin is None:
        return 1.0
    c = np.log(coin["close"][coin.index < before]).diff()
    b = np.log(btc["close"][btc.index < before]).diff()
    j = pd.concat([c, b], axis=1, keys=["c", "b"]).dropna().tail(BETA_BARS)
    if len(j) < 48 or j["b"].var() <= 0:
        return 1.0
    return float(np.clip(j["c"].cov(j["b"]) / j["b"].var(), *BETA_CAP))


def price_at(btc: pd.DataFrame, t: pd.Timestamp) -> float | None:
    """Bitcoin's price at time t: the close of the hourly candle ending at (or just before) t."""
    x = btc["close"][btc.index + pd.Timedelta(hours=1) <= t]
    return float(x.iloc[-1]) if len(x) else None


def funding_between(hist: list[dict], t0: pd.Timestamp, t1: pd.Timestamp) -> float:
    a, b = t0.value // 10**6, t1.value // 10**6
    return float(sum(x["rate"] for x in hist if a < x["t"] <= b))


def hedge_result(size: float, p0: float, p1: float, funding: float) -> float:
    """A short of `size` dollars of Bitcoin's future from p0 to p1: price result, funding, and both ways' costs."""
    return -size * (p1 / p0 - 1) + size * funding - size * 2 * (PERP_FEE + PERP_HALF_SPREAD)


def _read_csv(path) -> list[dict]:
    try:
        with path.open() as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def run(candles: dict, fetch=None, funding_hist: list[dict] | None = None, now: pd.Timestamp | None = None) -> dict:
    """Bring the hedge ledger up to date with the challenge's trades; write the hedged account and its value."""
    now = now or pd.Timestamp.now(tz="UTC")
    try:
        acc = json.loads(state_path(SRC, "account.json").read_text())
    except (OSError, ValueError):
        return {}
    trades = _read_csv(state_path(SRC, "trades.csv"))
    start = pd.Timestamp(acc.get("started_at") or now)
    need = int((now - start) / pd.Timedelta(hours=1)) + BETA_BARS + 50
    btc = candles.get(BTC)
    if fetch and (btc is None or btc.index[0] > start - pd.Timedelta(hours=BETA_BARS)):
        btc = fetch(BTC, "1h", min(need, 1000))
    if btc is None or not len(btc):
        return {}
    if funding_hist is None:
        try:
            from carry.run import _get
            funding_hist = [{"t": int(x["fundingTime"]), "rate": float(x.get("realizedRate") or x["fundingRate"])}
                            for x in _get("/api/v5/public/funding-rate-history", instId="BTC-USDT-SWAP", limit=100)]
        except Exception as e:  # noqa: BLE001
            log.warning("hedged: no funding history: %s", e)
            funding_hist = []
    try:
        ledger = json.loads(_p("hedges.json").read_text())
    except (OSError, ValueError):
        ledger = {}

    def coin_candles(symbol, since):
        df = candles.get(symbol)
        if (df is None or df.index[0] > since - pd.Timedelta(hours=BETA_BARS)) and fetch:
            hours = int((now - since) / pd.Timedelta(hours=1)) + BETA_BARS + 10
            try:
                df = fetch(symbol, "1h", min(hours, 1000))
            except Exception:  # noqa: BLE001
                df = None
        return df

    def open_hedge(key, symbol, amount, filled):
        t = pd.Timestamp(filled)
        p0 = price_at(btc, t)
        if p0 is None:
            return None
        b = beta(coin_candles(symbol, t), btc, t - pd.Timedelta(hours=1))
        ledger[key] = {"coin": symbol[:-4], "symbol": symbol, "opened": str(t), "beta": round(b, 3),
                       "size": round(b * amount, 4), "coin_amount": round(amount, 4), "btc_entry": p0, "status": "open"}
        return ledger[key]

    for r in trades:                                            # finished coin trades: close their hedges
        key = f"{r['symbol']}|{pd.Timestamp(r['opened'])}"
        h = ledger.get(key) or open_hedge(key, r["symbol"], float(r["amount"]), r["opened"])
        if not h or h["status"] == "closed":
            continue
        t1 = pd.Timestamp(r["closed"])
        p1 = price_at(btc, t1)
        if p1 is None:
            continue
        f = funding_between(funding_hist, pd.Timestamp(h["opened"]), t1)
        res = hedge_result(h["size"], h["btc_entry"], p1, f)
        h.update(status="closed", closed=str(t1), btc_exit=p1, funding=round(f, 6), result=round(res, 4),
                 coin_net=float(r["net"]), coin_ret=float(r["ret"]), btc_ret=round(p1 / h["btc_entry"] - 1, 5))
    open_keys = set()
    for pos in acc.get("positions", []):                        # coin trades still open: hedge marked at today's price
        if pos.get("status") != "open" or not pos.get("fill_t"):
            continue
        key = f"{pos['symbol']}|{pd.Timestamp(pos['fill_t'])}"
        open_keys.add(key)
        h = ledger.get(key) or open_hedge(key, pos["symbol"], float(pos["amount"]), pos["fill_t"])
        if h:
            p1 = float(btc["close"].iloc[-1])
            f = funding_between(funding_hist, pd.Timestamp(h["opened"]), now)
            h.update(mark=round(hedge_result(h["size"], h["btc_entry"], p1, f), 4), btc_now=p1, funding=round(f, 6))
    closed = [h for h in ledger.values() if h["status"] == "closed"]
    marks = [h.get("mark", 0.0) for k, h in ledger.items() if h["status"] == "open" and k in open_keys]
    hedge_total = sum(h["result"] for h in closed) + sum(marks)
    out = {"start": acc.get("start", 100.0), "unhedged": acc.get("equity"), "equity": (acc.get("equity") or 0) + hedge_total,
           "hedge_total": round(hedge_total, 4), "hedges_closed": len(closed), "hedges_open": len(marks), "trades": len(closed),
           "btc_now": float(btc["close"].iloc[-1]), "updated": str(now), "started_at": acc.get("started_at")}
    _p("hedges.json").write_text(json.dumps(ledger, indent=1, default=str))
    _p("account.json").write_text(json.dumps(out, indent=1, default=str))
    ep = _p("equity.csv")
    new = not ep.exists()
    with ep.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["t", "equity", "unhedged"])
        if new:
            w.writeheader()
        w.writerow({"t": str(now), "equity": round(out["equity"], 4), "unhedged": round(out["unhedged"] or 0, 4)})
    return out
