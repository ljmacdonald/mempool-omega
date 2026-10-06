"""Prediction markets (PAPER ONLY): do favourites priced 55-70 cents, bought 1-7 days before the scheduled end, win
more often than their price says, after the real buying price and fees? (DECISIONS D89)

Research behind it (2,427 resolved Polymarket yes/no markets of $20k+, Sep 2024 - Sep 2026, decided at a time known in
advance - 1 or 7 days before the scheduled end - only while the market was still open, on a price traded in the 6 hours
before): favourites at 55-70c won about 81% of the time against 63% priced (+26% per $1 after a 1c cost, t = 3.2, in
both halves of the period, but only ~57 trades and one of 15 combinations looked at); favourites at 80-95c lost money;
all favourites together had no reliable edge. So this is tested forward on paper: every market that fits the rule is
paper-bought at its real ask plus fees, one per event (outcomes of one event move together), and judged when it resolves.

    python -m predict.run scan    # hourly: open markets, new paper trades, settlements -> .cache/predict_out/snapshot.json

Sources (public, no keys): Polymarket gamma API, Kalshi trade API v2. Kalshi is regulated in the US; Polymarket's main
site is not open to US residents.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
from datetime import datetime, timezone

import requests

from core.config import REPO_ROOT, state_path
from lab.run import clean

log = logging.getLogger("omega.predict")
POLY = "https://gamma-api.polymarket.com/markets"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/markets"
RULE_LO, RULE_HI = 0.55, 0.70          # the favourite's price band (bought at the ask)
WINDOW_D = (1.0, 7.0)                  # days before the scheduled end
MAX_SPREAD = 0.04
POLY_MIN_VOL, POLY_MIN_LIQ = 20_000.0, 5_000.0
KALSHI_MIN_VOL, KALSHI_MIN_24H = 20_000.0, 500.0   # contracts
POLY_FEE = 0.01                        # per contract, only where Polymarket charges a fee (estimate)
MIN_TRADES = 50
RESEARCH = {"markets": 2427, "period": "Sep 2024 - Sep 2026", "min_volume": 20000, "cost": 0.01, "rows": [
    {"band": "55-70c", "d1": {"n": 57, "won": 0.807, "price": 0.628, "ret": 0.2646}, "d7": {"n": 58, "won": 0.810, "price": 0.631, "ret": 0.26}},
    {"band": "70-80c", "d1": {"n": 31, "won": 0.839, "price": 0.738, "ret": 0.1221}, "d7": {"n": 48, "won": 0.75, "price": 0.754, "ret": -0.0166}},
    {"band": "80-90c", "d1": {"n": 34, "won": 0.794, "price": 0.859, "ret": -0.0896}, "d7": {"n": 56, "won": 0.893, "price": 0.858, "ret": 0.0317}},
    {"band": "90-95c", "d1": {"n": 49, "won": 0.837, "price": 0.929, "ret": -0.1087}, "d7": {"n": 70, "won": 0.914, "price": 0.932, "ret": -0.0303}},
    {"band": "95-99c", "d1": {"n": 113, "won": 0.965, "price": 0.975, "ret": -0.0216}, "d7": {"n": 222, "won": 0.982, "price": 0.977, "ret": -0.0056}},
    {"band": "all 55-95c", "d1": {"n": 171, "won": 0.819, "price": 0.78, "ret": 0.0614}, "d7": {"n": 232, "won": 0.849, "price": 0.802, "ret": 0.0601}}]}
_s = requests.Session()
_s.headers.update({"User-Agent": "mempool-omega research (paper only)"})


def get(url: str, params: dict | None = None, tries: int = 4):
    for i in range(tries):
        try:
            r = _s.get(url, params=params, timeout=40)
            if r.status_code == 429:
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:  # noqa: PERF203
            if i == tries - 1:
                raise
            log.debug("retry %s: %s", url, e)
            time.sleep(2 * (i + 1))
    return None


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _f(x, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


# ---------------------------------------------------------------------------------------------- one shape for both sites
def from_poly(m: dict) -> dict | None:
    """A Polymarket yes/no market in the common shape, or None."""
    try:
        if json.loads(m.get("outcomes") or "[]") != ["Yes", "No"]:
            return None
    except ValueError:
        return None
    bid, ask = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    end = _ts(m.get("endDate"))
    if not (0 < bid < ask < 1) or end is None or not m.get("acceptingOrders", True):
        return None
    ev = (m.get("events") or [{}])[0] or {}
    return {"venue": "polymarket", "id": str(m["id"]), "event": str(ev.get("id") or m["id"]), "q": m.get("question", ""),
            "yes_bid": bid, "yes_ask": ask, "end": end, "vol": _f(m.get("volumeNum")), "vol24": _f(m.get("volume24hr")),
            "liq": _f(m.get("liquidityNum")), "fee_pc": POLY_FEE if m.get("feesEnabled") else 0.0,
            "url": f"https://polymarket.com/event/{ev.get('slug') or m.get('slug', '')}"}


def from_kalshi(m: dict) -> dict | None:
    """A Kalshi yes/no market in the common shape, or None. Its scheduled end is the expected expiration (the event),
    not the close time, which can be days later."""
    if m.get("market_type", "binary") != "binary":
        return None
    bid, ask = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    end = _ts(m.get("expected_expiration_time")) or _ts(m.get("close_time"))
    if not (0 < bid < ask < 1) or end is None:
        return None
    # the rules sentence names the game or release ("If Seattle wins the SEA Seahawks vs DEN Broncos ... game ...")
    rule = (m.get("rules_primary") or "").split(", then the market resolves")[0].strip()
    sub = m.get("yes_sub_title") or ""
    q = (rule[3:] if rule.startswith("If ") else "") or (m.get("title", "") + (f" ({sub})" if sub and sub not in m.get("title", "") else ""))
    q = q[:1].upper() + q[1:]
    # one game has several Kalshi events (winner, spread, total: KXNFLGAME-26OCT11BALATL, KXNFLSPREAD-26OCT11BALATL, ...),
    # all moved by the same result: group them by the part after the series name
    ev = m.get("event_ticker") or m["ticker"]
    return {"venue": "kalshi", "id": m["ticker"], "event": ev.split("-", 1)[1] if "-" in ev else ev, "q": q,
            "yes_bid": bid, "yes_ask": ask, "end": end, "vol": _f(m.get("volume_fp")), "vol24": _f(m.get("volume_24h_fp")),
            "liq": None, "fee_pc": None, "url": f"https://kalshi.com/markets/{(m.get('event_ticker') or m['ticker']).split('-')[0].lower()}"}


def fee(row: dict, p: float) -> float:
    """Per-contract fee: Kalshi's published taker fee 0.07 * p * (1 - p); Polymarket's where it charges one."""
    return 0.07 * p * (1 - p) if row["venue"] == "kalshi" else (row["fee_pc"] or 0.0)


def favourite(row: dict) -> dict:
    """The likelier side by mid price, and what buying it really costs (its ask, plus the fee)."""
    mid = (row["yes_bid"] + row["yes_ask"]) / 2
    side = "yes" if mid >= 0.5 else "no"
    ask = row["yes_ask"] if side == "yes" else 1 - row["yes_bid"]
    return {"side": side, "mid": mid if side == "yes" else 1 - mid, "ask": ask, "fee": fee(row, ask), "spread": row["yes_ask"] - row["yes_bid"]}


def liquid(row: dict) -> bool:
    if row["yes_ask"] - row["yes_bid"] > MAX_SPREAD:
        return False
    if row["venue"] == "polymarket":
        return row["vol"] >= POLY_MIN_VOL and row["liq"] >= POLY_MIN_LIQ
    return row["vol"] >= KALSHI_MIN_VOL and row["vol24"] >= KALSHI_MIN_24H


def fits(row: dict, now: float) -> bool:
    days = (row["end"] - now) / 86400
    fv = favourite(row)
    return liquid(row) and WINDOW_D[0] <= days <= WINDOW_D[1] and RULE_LO <= fv["ask"] < RULE_HI


def pick(rows: list[dict], now: float) -> list[dict]:
    """Markets that fit the rule, one per event (the most traded), since outcomes of one event move together."""
    best: dict[str, dict] = {}
    for r in rows:
        if fits(r, now):
            k = f"{r['venue']}|{r['event']}"
            if k not in best or r["vol"] > best[k]["vol"]:
                best[k] = r
    return sorted(best.values(), key=lambda r: -r["vol"])


# ---------------------------------------------------------------------------------------------- the sites
def poly_open(now: float) -> list[dict]:
    out = []
    lo = datetime.fromtimestamp(now + 6 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = datetime.fromtimestamp(now + 8 * 86400, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for off in range(0, 3000, 100):                     # the API returns at most 100 a page
        b = get(POLY, {"closed": "false", "active": "true", "limit": 100, "offset": off, "volume_num_min": POLY_MIN_VOL / 4,
                       "end_date_min": lo, "end_date_max": hi}) or []
        out += [r for r in map(from_poly, b) if r]
        if len(b) < 100:
            break
    return out


def kalshi_open(now: float) -> list[dict]:
    out, cur = [], None
    for _ in range(12):
        p = {"status": "open", "limit": 1000, "min_close_ts": int(now + 6 * 3600), "max_close_ts": int(now + 12 * 86400), "mve_filter": "exclude"}
        if cur:
            p["cursor"] = cur
        j = get(KALSHI, p) or {}
        ms = j.get("markets") or []
        out += [r for r in map(from_kalshi, ms) if r]
        cur = j.get("cursor")
        if not cur or not ms:
            break
    return out


def settle_poly(pid: str) -> float | None:
    """1.0 if YES won, 0.0 if NO won, None while open (or unclear)."""
    m = get(f"{POLY}/{pid}")
    if not m or not m.get("closed"):
        return None
    try:
        fp = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except ValueError:
        return None
    return 1.0 if fp and fp[0] > 0.99 else 0.0 if fp and fp[0] < 0.01 else None


def settle_kalshi(ticker: str) -> float | None:
    m = (get(f"{KALSHI}/{ticker}") or {}).get("market") or {}
    if m.get("status") not in ("settled", "finalized", "determined"):
        return None
    return {"yes": 1.0, "no": 0.0}.get(m.get("result"))


# ---------------------------------------------------------------------------------------------- the paper book and its record
def open_position(row: dict, now: float) -> dict:
    fv = favourite(row)
    return {"key": f"{row['venue']}|{row['id']}", "venue": row["venue"], "id": row["id"], "event": row["event"], "q": row["q"],
            "url": row["url"], "side": fv["side"], "ask": round(fv["ask"], 4), "fee": round(fv["fee"], 4), "mid": round(fv["mid"], 4),
            "opened": int(now * 1000), "end": int(row["end"] * 1000), "days_left": round((row["end"] - now) / 86400, 2),
            "status": "open", "won": None, "net": None, "settled": None}


def close_position(p: dict, yes_won: float, now: float) -> dict:
    won = yes_won if p["side"] == "yes" else 1 - yes_won
    cost = p["ask"] + p["fee"]
    return {**p, "status": "settled", "won": int(won), "net": won / cost - 1, "settled": int(now * 1000)}


def record(book: list[dict]) -> dict:
    """Result per $1 after the ask and fees, against what the prices said; the label needs MIN_TRADES settled trades
    with a positive average and t >= 2 (Failed if the average is negative)."""
    done = [p for p in book if p["status"] == "settled"]
    out = {"open": sum(1 for p in book if p["status"] == "open"), "settled": len(done), "min_trades": MIN_TRADES}
    if done:
        nets = [p["net"] for p in done]
        avg = sum(nets) / len(nets)
        sd = (sum((x - avg) ** 2 for x in nets) / (len(nets) - 1)) ** 0.5 if len(nets) > 1 else 0.0
        out.update({"won": sum(p["won"] for p in done) / len(done), "price": sum(p["ask"] for p in done) / len(done),
                    "avg": avg, "total": sum(nets), "t": avg / (sd / math.sqrt(len(nets))) if sd > 0 else None,
                    "by_venue": {v: {"n": len(xs), "avg": sum(p["net"] for p in xs) / len(xs), "won": sum(p["won"] for p in xs) / len(xs)}
                                 for v in ("polymarket", "kalshi") if (xs := [p for p in done if p["venue"] == v])}})
    if len(done) < MIN_TRADES:
        return {**out, "level": "warn", "label": "Not proven yet",
                "text": f"{len(done)} paper trade(s) settled so far; a verdict needs {MIN_TRADES}."}
    if out["avg"] <= 0:
        return {**out, "level": "bad", "label": "Failed", "text": "Buying these favourites lost money after the real prices and fees."}
    if not ((out.get("t") or 0) >= 2):
        return {**out, "level": "warn", "label": "Not proven", "text": "Slightly positive, but not clearly more than luck."}
    return {**out, "level": "good", "label": "Held up", "text": "The favourites won clearly more often than their prices said, after fees."}


def _load(name: str, default):
    p = state_path("predict", name)
    try:
        return json.loads(p.read_text()) if p.exists() else default
    except ValueError:
        return default


def _save(name: str, obj) -> None:
    state_path("predict", name).write_text(json.dumps(clean(obj), indent=0))


def scan() -> dict:
    now = time.time()
    rows, errors = [], []
    for name, fn in (("polymarket", poly_open), ("kalshi", kalshi_open)):
        try:
            rows += fn(now)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")
            log.warning("%s: %s", name, e)
    book = _load("positions.json", [])
    held = {p["key"] for p in book}
    held_events = {f"{p['venue']}|{p['event']}" for p in book if p["status"] == "open"}
    picks = pick(rows, now)
    new = [open_position(r, now) for r in picks if f"{r['venue']}|{r['id']}" not in held and f"{r['venue']}|{r['event']}" not in held_events]
    book += new
    for i, p in enumerate(book):
        if p["status"] != "open" or now * 1000 < p["end"] - 3600e3:
            continue
        try:
            y = settle_poly(p["id"]) if p["venue"] == "polymarket" else settle_kalshi(p["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("settle %s: %s", p["key"], e)
            continue
        if y is not None:
            book[i] = close_position(p, y, now)
    _save("positions.json", book)
    live = {f"{r['venue']}|{r['id']}": r for r in rows}
    for p in book:
        if p["status"] == "open" and p["key"] in live:
            fv = favourite(live[p["key"]]) if live[p["key"]] else None
            p["now"] = fv["mid"] if fv and fv["side"] == p["side"] else (1 - fv["mid"]) if fv else None
    return snapshot(now, rows, picks, book, errors)


def snapshot(now: float, rows: list[dict], picks: list[dict], book: list[dict], errors: list[str]) -> dict:
    def view(r: dict) -> dict:
        fv = favourite(r)
        return {"venue": r["venue"], "id": r["id"], "q": r["q"], "url": r["url"], "side": fv["side"], "price": round(fv["mid"], 4),
                "ask": round(fv["ask"], 4), "fee": round(fv["fee"], 4), "spread": round(fv["spread"], 4), "vol": r["vol"],
                "vol24": r["vol24"], "end": int(r["end"] * 1000), "fits": fits(r, now)}
    liquid_rows = sorted((r for r in rows if liquid(r)), key=lambda r: -r["vol24"])
    snap = {"generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "rule": {"lo": RULE_LO, "hi": RULE_HI, "days": WINDOW_D,
            "max_spread": MAX_SPREAD}, "counts": {"scanned": len(rows), "liquid": len(liquid_rows), "fit": len(picks),
            "polymarket": sum(1 for r in rows if r["venue"] == "polymarket"), "kalshi": sum(1 for r in rows if r["venue"] == "kalshi")},
            "picks": [view(r) for r in picks[:30]], "markets": [view(r) for r in liquid_rows[:40]],
            "open": sorted((p for p in book if p["status"] == "open"), key=lambda p: p["end"])[:60],
            "recent": sorted((p for p in book if p["status"] == "settled"), key=lambda p: -p["settled"])[:30],
            "record": record(book), "research": RESEARCH, "errors": errors}
    out = REPO_ROOT / ".cache" / "predict_out" / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(clean(snap), allow_nan=False))
    return snap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if (argv[0] if argv else "scan") != "scan":
        print(__doc__)
        return 2
    s = scan()
    log.info("predict scan: %s, %d open, %d settled, %s", s["counts"], s["record"]["open"], s["record"]["settled"], s["record"]["label"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
