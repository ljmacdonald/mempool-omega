"""ICT setups: the rules on hand-made prices, buy/sell symmetry, outcomes, and JavaScript = Python on real data."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ict import engine as I

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "ict" / "btc_eth.json"


def _df(d):
    idx = pd.to_datetime(d["t"], unit="ms", utc=True)
    return pd.DataFrame({k: d[k] for k in ("open", "high", "low", "close")}, index=idx).astype(float)


def _fixture():
    d = json.loads(FIX.read_text())
    return _df(d["b15"]), _df(d["b1"]), _df(d["e15"])


def _textbook(target_hit=True):
    """Previous day 100-110, then today: a swing high at 106, a sweep under the 100 previous-day low that closes back
    above it, a strong rally through 106 leaving a gap, a dip into the gap, then a run to the previous day high."""
    t0 = pd.Timestamp("2026-03-02 05:00", tz="UTC")          # midnight New York (EST) on Monday
    rows = []
    def bar(o, h, lo, c):
        rows.append((o, h, lo, c))
    for i in range(96):                                        # Monday: ranges 100-110
        x = 105 + 4 * np.sin(i / 6)
        bar(x, x + 0.6, x - 0.6, x + 0.1)
    rows[10] = (105, 110, 104.5, 105)                          # day high 110
    rows[50] = (101, 101.2, 100, 100.8)                        # day low 100
    for i in range(20):                                        # Tuesday: drift 104 -> 102 with a swing high at 106
        x = 104 - i * 0.1
        bar(x, x + 0.4, x - 0.4, x - 0.05)
    rows[96 + 5] = (104, 106, 103.8, 104.2)
    bar(102, 102.2, 99.2, 100.6)                              # sweep below 100, closes back above
    bar(100.6, 101.4, 100.4, 101.2)
    bar(101.2, 103.6, 101.1, 103.5)                           # displacement
    bar(103.5, 106.8, 102.9, 106.6)                           # breaks 106 -> gap between 101.4 and 102.9
    bar(106.6, 106.9, 102.0, 102.5)                           # dips into the gap (fills ~102.15)
    for i in range(12):
        x = 102.5 + i * (0.8 if target_hit else 0.05)
        bar(x, x + 0.5, x - 0.2, x + 0.4)
    idx = pd.date_range(t0, periods=len(rows), freq="15min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_textbook_buy_setup_is_found_and_wins():
    df = _textbook()
    s = [x for x in I.setups(df) if x["side"] == "buy"]
    assert s, "the textbook setup was not detected"
    s = s[-1]
    assert s["level"] == "previous day low" and s["sweep"] == pytest.approx(99.2)
    assert s["fvg_bot"] == pytest.approx(101.4) and s["fvg_top"] == pytest.approx(102.9)
    assert s["entry"] == pytest.approx(102.15) and s["stop"] < 99.2
    assert s["rr"] >= 2 and s["conf"]["major"] and s["status"] == "win" and s["r"] > 0


def test_sell_is_the_mirror_of_buy():
    df = _textbook()
    up = pd.DataFrame({"open": -df["open"], "high": -df["low"], "low": -df["high"], "close": -df["close"]}, index=df.index)
    b = [x for x in I.setups(df) if x["side"] == "buy"][-1]
    s = [x for x in I.setups(up) if x["side"] == "sell"][-1]
    assert s["entry"] == pytest.approx(-b["entry"]) and s["stop"] == pytest.approx(-b["stop"])
    assert s["target"] == pytest.approx(-b["target"]) and s["level"] == "previous day high"
    assert s["status"] == b["status"] and s["r"] == pytest.approx(b["r"])


def test_outcomes_are_conservative():
    a = {"h": np.array([10, 10, 10.0, 12.5, 12.5]), "l": np.array([9, 9, 9.0, 7.5, 9.0]), "c": np.array([9.5] * 5)}
    s = {"m": 1, "entry": 10.0, "stop": 8.0, "target": 12.0, "rr": 1.0, "cost_r": 0.1}
    r = I.simulate(a, s, {"fill_bars": 2, "hold_bars": 5})
    assert r["status"] == "loss" and r["r"] == pytest.approx(-1.1)     # stop and target in the same candle -> loss
    a["l"][3] = 9.5
    assert I.simulate(a, s, {"fill_bars": 2, "hold_bars": 5})["status"] == "win"
    a2 = {"h": np.array([10, 10, 12.5, 12.5]), "l": np.array([9, 9, 10.5, 10.5]), "c": np.array([9.5] * 4)}
    assert I.simulate(a2, s, {"fill_bars": 2})["status"] == "missed"   # target reached before the entry


def test_real_data_sanity():
    b15, b1, e15 = _fixture()
    S = I.setups(b15, b1, e15, cost=0.0022)
    assert len(S) >= 5
    for s in S:
        assert s["rr"] >= 2 - 1e-9 and s["risk_pct"] >= 0.0022 - 1e-12
        if s["side"] == "buy":
            assert s["stop"] < s["sweep"] <= s["level_px"] and s["entry"] < s["target"]
        else:
            assert s["stop"] > s["sweep"] >= s["level_px"] and s["entry"] > s["target"]
        assert s["status"] in {"win", "loss", "time", "missed", "expired", "pending", "active"}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_javascript_matches_python():
    b15, b1, e15 = _fixture()
    py = I.setups(b15, b1, e15, cost=0.0022)
    js = r"""
    const I = require(process.argv[1]); const d = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
    console.log(JSON.stringify(I.setups(d.b15, d.b1, d.e15, 0.0022)));"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "site" / "ictengine.js"), str(FIX)], capture_output=True, text=True, check=True)
    jsr = json.loads(out.stdout)
    assert len(jsr) == len(py) and len(py) > 0
    for a, b in zip(py, jsr):
        for k in ("m", "s", "side", "level", "target_name", "status", "count", "killzone"):
            assert a[k] == b[k], (k, a, b)
        for k in ("entry", "stop", "target", "rr", "r", "cost_r"):
            assert a[k] == pytest.approx(b[k], rel=1e-9, abs=1e-9), (k, a, b)
        assert a["conf"] == b["conf"]
        assert a["suggested"] == pytest.approx(b["suggested"], rel=1e-12)
        assert a["ctx"]["pen"] == pytest.approx(b["ctx"]["pen"])
        assert [(c["key"], c["ok"]) for c in a["ctx"]["checks"]] == [(c["key"], c["ok"]) for c in b["ctx"]["checks"]]


def test_context_warnings_and_random_baseline():
    n = 200
    up = np.linspace(100, 130, n)                                  # about +14% over the last day: chasing for a buy
    a = {"o": up, "h": up + 0.1, "l": up - 0.1, "c": up, "t": np.arange(n), "day": np.zeros(n, int), "mins": np.zeros(n, int)}
    cx = I.context(a, n - 1, "crypto")
    assert {c["key"]: c["ok"] for c in cx["checks"]} == {"chasing": False, "overheated": False}
    assert cx["pen"] == pytest.approx(0.20)
    sx = I.context(I.flip(a), n - 1, "crypto")                    # a sell into the same rally is not chasing
    assert {c["key"]: c["ok"] for c in sx["checks"]} == {"chasing": True, "overheated": True} and sx["pen"] == 0
    flat = np.full(n, 100.0)
    b = {"o": flat, "h": flat + 0.05, "l": flat - 0.05, "c": flat}
    assert I.random_baseline(b, 10, 0.01, 2.0, 0.1, hold=50) == pytest.approx(-0.1)    # nothing happens: costs only
    assert np.isnan(I.random_baseline(b, n - 5, 0.01, 2.0, 0.1, hold=50))


def test_research_split_summary_and_judging():
    from ict import research as RS
    rows = []
    for i in range(90):
        for v in ("h1", "h1_daily"):
            good = v == "h1_daily"
            rows.append({"sym": "ES", "class": "index", "variant": v, "t": i, "status": "win" if (good and i % 2) else "loss",
                         "r": (2.0 if (good and i % 2) else -1.0), "base_r": -0.2})
    RS.split(rows)
    assert {r["split"] for r in rows if r["t"] < 59} == {"train"} and {r["split"] for r in rows if r["t"] > 60} == {"test"}
    classes = {"index": {"label": "x", "variants": {v: {"train": RS.summary([r for r in rows if r["variant"] == v and r["split"] == "train"]),
                                                       "test": RS.summary([r for r in rows if r["variant"] == v and r["split"] == "test"])}
                                                   for v in ("h1", "h1_daily")}}}
    RS.judge(classes)
    c = classes["index"]
    assert c["chosen"] == "h1_daily" and c["passed"] is True and "held up" in c["verdict"]
    s = c["variants"]["h1"]["test"]
    assert s["avg_r"] == -1.0 and s["edge"] == pytest.approx(-0.8)
