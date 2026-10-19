"""The market filter (DECISIONS D116): is the crypto market falling, and would pausing crypto buy ideas then help?

The rule, fixed before any test: the market is *falling* while Bitcoin's latest hourly close is below its average over
the last 7 days (168 hourly closes). While it is falling, crypto buy ideas would show "Waiting: market falling".

It is switched on only if it would have helped: across every finished Exchange coins, Small coins and DEX idea, the
ideas the filter lets through must have done better than those it would have blocked (t >= 2 over scans), and
better than all ideas taken together in both the older and the newer half of the record. The test is re-run with
every summary (the record keeps growing); the rule itself never changes. The first test (19 Oct 2026, 12,097 ideas
from 2-19 Oct) failed the other way round: ideas taken while Bitcoin was above its average did worse (-1.01% a
trade) than those taken below it (-0.25%), in all three sections and both halves, so the filter stays off.

Writes state/market.json; keeps Bitcoin's hourly closes in state/market/btc_1h.csv. Standard library only.
"""
from __future__ import annotations

import csv
import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import summary as S  # noqa: E402

BARS = 168
HIST = {"main": "suggestions/history.csv", "small": "suggestions/small_history.csv", "dex": "dex/history.csv"}
KLINES = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=1000"


def fetch_btc() -> dict[int, float]:
    """{hour open time in ms: close} for the last 1000 finished hours (the newest, still-open hour is dropped)."""
    with urllib.request.urlopen(KLINES, timeout=30) as r:
        rows = json.loads(r.read().decode())
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return {int(x[0]): float(x[4]) for x in rows if int(x[6]) < now_ms}


def load_btc(fetch=fetch_btc) -> dict[int, float]:
    p = S.STATE / "market" / "btc_1h.csv"
    have: dict[int, float] = {}
    try:
        with p.open(newline="") as f:
            have = {int(r["t"]): float(r["close"]) for r in csv.DictReader(f)}
    except (OSError, ValueError, KeyError):
        pass
    try:
        have.update(fetch())
    except Exception as e:  # noqa: BLE001  keep what we have
        print("market: no fresh Bitcoin prices:", e)
    if have:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "close"])
            for t in sorted(have):
                w.writerow([t, have[t]])
    return have


def regime(btc: dict[int, float]) -> list[tuple[datetime, bool, float, float]]:
    """[(time the close became known, falling?, close, 7-day average)] for every hour with a full week before it."""
    ts = sorted(btc)
    out, run = [], 0.0
    for i, t in enumerate(ts):
        run += btc[t]
        if i >= BARS:
            run -= btc[ts[i - BARS]]
        if i >= BARS - 1:
            avg = run / BARS
            known = datetime.fromtimestamp(t / 1000 + 3600, tz=timezone.utc)
            out.append((known, btc[t] < avg, btc[t], avg))
    return out


def falling_at(reg: list, t: datetime) -> bool | None:
    """The regime as known at time t (the last close finished by then)."""
    lo, hi = 0, len(reg)
    while lo < hi:
        mid = (lo + hi) // 2
        if reg[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return reg[lo - 1][1] if lo else None


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _t_diff(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3 or len(b) < 3:
        return None
    ma, mb = _mean(a), _mean(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se > 0 else None


def test(reg: list) -> dict:
    ideas = []
    for sec, rel in HIST.items():
        try:
            with (S.STATE / rel).open(newline="") as f:
                for r in csv.DictReader(f):
                    if r.get("status") != "closed" or r.get("net_ret") in (None, ""):
                        continue
                    t = S.parse_t(r["ts"])
                    fall = falling_at(reg, t) if t else None
                    if fall is not None:
                        ideas.append((t, sec, r["ts"], float(r["net_ret"]), fall))
        except OSError:
            continue
    ideas.sort()
    if len(ideas) < 2 * S.MIN_N:
        return {"n": len(ideas), "enabled": False, "verdict": "Too few finished crypto ideas to test the filter yet."}

    def part(xs):
        let, blk = [x[3] for x in xs if not x[4]], [x[3] for x in xs if x[4]]
        return {"n": len(xs), "all": _mean([x[3] for x in xs]), "allowed": _mean(let), "blocked": _mean(blk),
                "n_allowed": len(let), "n_blocked": len(blk)}

    scans: dict[tuple, list] = {}
    for x in ideas:
        scans.setdefault((x[1], x[2]), []).append(x)
    let = [_mean([y[3] for y in v]) for v in scans.values() if not v[0][4]]
    blk = [_mean([y[3] for y in v]) for v in scans.values() if v[0][4]]
    half = len(ideas) // 2
    whole, older, newer = part(ideas), part(ideas[:half]), part(ideas[half:])
    t = _t_diff(let, blk)

    def helps(p):
        return p["allowed"] is not None and p["blocked"] is not None and p["allowed"] > p["all"]

    enabled = bool(t is not None and t >= 2 and helps(older) and helps(newer))
    if enabled:
        verdict = (f"Switched on: ideas taken while Bitcoin was above its 7-day average did {whole['allowed']:+.2%} a trade, "
                   f"against {whole['blocked']:+.2%} while it was below, in both halves of the record.")
    elif whole["allowed"] is not None and whole["blocked"] is not None and whole["allowed"] < whole["blocked"]:
        verdict = (f"Tested and left off: it would have made results worse. Ideas taken while Bitcoin was above its 7-day average "
                   f"did {whole['allowed']:+.2%} a trade, those taken while it was below did {whole['blocked']:+.2%}.")
    else:
        verdict = (f"Tested and left off: the difference is not clear enough yet (above the average {whole['allowed']:+.2%}, "
                   f"below {whole['blocked']:+.2%} a trade).")
    return {**whole, "t": t, "older": older, "newer": newer, "enabled": enabled, "verdict": verdict,
            "from": str(ideas[0][0]), "to": str(ideas[-1][0])}


def build(now: datetime | None = None, fetch=fetch_btc) -> dict:
    now = now or S.now_utc()
    reg = regime(load_btc(fetch))
    cur = None
    if reg:
        known, fall, close, avg = reg[-1]
        since = known
        for k, f, _, _ in reversed(reg):
            if f != fall:
                break
            since = k
        cur = {"falling": fall, "btc": close, "avg7d": avg, "as_of": str(known), "since": str(since),
               "stale": now - known > timedelta(hours=3)}
    out = {"generated_at": now.isoformat(), "rule": "Bitcoin's latest hourly close below its 7-day average", "now": cur,
           "test": test(reg) if reg else {"enabled": False, "verdict": "No Bitcoin prices yet."}}
    out["active"] = bool(cur and cur["falling"] and not cur["stale"] and out["test"].get("enabled"))
    (S.STATE / "market.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    d = build()
    print(json.dumps({k: d[k] for k in ("now", "active")}, default=str), "|", d["test"].get("verdict"))
