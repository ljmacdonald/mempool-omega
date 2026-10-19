"""Proven or testing only (D115), the market filter (D116) and the best idea right now (D117)."""
import csv
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parent.parent / "infra"
sys.path.insert(0, str(INFRA))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, INFRA / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load("summary")
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    import summary  # the module the three import
    monkeypatch.setattr(summary, "STATE", tmp_path)
    monkeypatch.setattr(summary, "read_data", lambda rel: None)
    P, M, B = _load("proof"), _load("market"), _load("best")
    for m in (P, M, B):
        monkeypatch.setattr(m, "S", summary)
    return tmp_path, P, M, B


def write_hist(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ts", "style", "status", "net_ret", "baseline_ret"])
        w.writeheader()
        w.writerows(rows)


def rows(n_scans, per_scan, ret, base, style="day", start=NOW - timedelta(days=30), noise=0.004):
    out = []
    for i in range(n_scans):
        t = start + timedelta(hours=i)
        for j in range(per_scan):
            e = noise * (1 if (i + j) % 2 else -1)
            out.append({"ts": t.isoformat(), "style": style, "status": "closed", "net_ret": ret + e, "baseline_ret": base})
    return out


def test_judge_needs_trades_profit_edge_and_a_recent_edge(mods):
    _, P, _, _ = mods
    assert P.judge(10, 0.01, 0.01, 5, None)[0] is False                    # too few
    assert "Lost money" in P.judge(100, -0.001, 0.01, 5, None)[1]          # an edge over random isn't enough
    assert "luck" in P.judge(100, 0.01, 0.002, 1.2, None)[1]               # profitable but not clearly better than random
    assert "faded" in P.judge(100, 0.01, 0.01, 3, {"n": 30, "avg": 0.01, "edge": -0.001, "t_edge": -0.5})[1]
    assert P.judge(100, 0.01, 0.01, 3, {"n": 30, "avg": 0.01, "edge": 0.01, "t_edge": 2})[0] is True


def test_scanner_style_proven_only_when_it_beats_random_and_makes_money(mods):
    state, P, _, _ = mods
    write_hist(state / "suggestions/history.csv",
               rows(40, 3, 0.012, 0.0, "day") + rows(40, 3, -0.004, -0.008, "short"))   # day: profit + edge; short: loses
    us = {u["id"]: u for u in P.scanner_units()}
    assert us["main:day"]["proven"] is True and us["main:day"]["hit"] > 0.5
    assert us["main:short"]["proven"] is False and "Lost money" in us["main:short"]["reason"]
    assert us["main:quick"]["proven"] is False and us["main:quick"]["n"] == 0


def test_t_is_over_scans_not_ideas(mods):
    state, P, _, _ = mods
    # 3 scans of 40 identical ideas: 120 ideas but only 3 independent looks
    write_hist(state / "suggestions/history.csv", rows(3, 40, 0.01, 0.0, "day", noise=0.0) + rows(1, 1, 0.01, 0.02, "day"))
    u = {u["id"]: u for u in P.scanner_units()}["main:day"]
    assert u["n"] == 121 and u["proven"] is False


def test_build_writes_sections_and_logs_only_changes(mods):
    state, P, _, _ = mods
    write_hist(state / "suggestions/history.csv", rows(40, 3, 0.012, 0.0, "day"))
    d = P.build(NOW)
    assert d["sections"]["main"]["status"] == "partly" and d["sections"]["main"]["proven_units"] == ["Day"]
    assert d["sections"]["predict"]["status"] == "testing"
    assert "main:day" in d["proven_ids"]
    n1 = len((state / "proof_log.csv").read_text().splitlines())
    P.build(NOW + timedelta(minutes=15))
    assert len((state / "proof_log.csv").read_text().splitlines()) == n1     # nothing changed, nothing logged


def btc_series(hours, f):
    t0 = int((NOW - timedelta(hours=hours)).timestamp() * 1000)
    return {t0 + i * 3600_000: f(i) for i in range(hours)}


def test_market_regime_uses_only_finished_hours(mods):
    _, _, M, _ = mods
    reg = M.regime(btc_series(200, lambda i: 100.0 if i < 190 else 90.0))
    assert reg[-1][1] is True                                             # below its 7-day average: falling
    assert M.falling_at(reg, reg[0][0] - timedelta(minutes=1)) is None   # nothing known before the first full week
    t_first_fall = next(k for k, f, _, _ in reg if f)
    assert M.falling_at(reg, t_first_fall - timedelta(seconds=1)) is False


def test_market_filter_stays_off_when_blocking_would_have_hurt(mods):
    state, _, M, _ = mods
    btc = btc_series(400, lambda i: 100.0 + (5 if (i // 48) % 2 else -5))  # alternating rising / falling stretches
    reg = M.regime(btc)
    hist = []
    for i, (k, fall, _, _) in enumerate(reg[::2]):
        hist.append({"ts": (k + timedelta(minutes=5)).isoformat(), "style": "day", "status": "closed",
                     "net_ret": (0.01 if fall else -0.01) + 0.002 * (i % 3 - 1), "baseline_ret": 0.0})   # better while falling
    write_hist(state / "suggestions/history.csv", hist)
    d = M.build(NOW, fetch=lambda: btc)
    assert d["test"]["enabled"] is False and "worse" in d["test"]["verdict"] and d["active"] is False
    for r in hist:                                                              # flip it: blocking now helps
        r["net_ret"] = -r["net_ret"]
    write_hist(state / "suggestions/history.csv", hist)
    d = M.build(NOW, fetch=lambda: btc)
    assert d["test"]["enabled"] is True
    assert d["active"] is (d["now"]["falling"] and not d["now"]["stale"])
    assert (state / "market" / "btc_1h.csv").exists()


def test_best_idea_only_from_proven_units_and_paused_crypto_buys(mods, monkeypatch):
    state, _, _, B = mods
    gen = NOW.isoformat()
    for style, coin, score in (("day", "AAA", 7.0), ("short", "BBB", 9.0)):
        (state / "suggestions").mkdir(parents=True, exist_ok=True)
        (state / f"suggestions/latest_{style}.json").write_text(json.dumps({"generated_at": gen, "ideas": [
            {"coin": coin, "symbol": coin + "USDT", "price_now": 1.0, "safety_exit": 0.9, "take_profit": 1.2, "exit_by": "x",
             "hold_text": "24 hours", "score": score, "grade": "Strong"}]}))
    proof = {"units": [{"id": "main:day", "proven": True, "name": "Exchange coins · Day", "n": 100, "avg": 0.01, "hit": 0.55},
                       {"id": "main:short", "proven": False}]}
    d = B.build(NOW, proof=proof, market={"active": False})
    assert d["idea"]["title"] == "Buy AAA" and d["idea"]["record"]["hit"] == 0.55      # BBB scored higher but isn't proven
    d = B.build(NOW, proof=proof, market={"active": True})
    assert d["idea"] is None and d["waiting"][0]["title"] == "Buy AAA" and "falling" in d["message"]
    d = B.build(NOW + timedelta(hours=3), proof=proof, market={"active": False})       # stale snapshots never count
    assert d["idea"] is None and d["message"].startswith("No idea passes")
    assert json.loads((state / "best.json").read_text())["idea"] is None
