"""Grade honesty: results per $1 risked, the automatic grade check and the jumpiness adjustment (Python = JS)."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scanner import quality as Q

ROOT = Path(__file__).resolve().parent.parent


def _hist(n_each=30, high_better=True, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for g, score, mu in (("Strong", 7.0, 0.3), ("Moderate", 6.0, 0.2), ("Weak", 5.2, -0.1), ("Avoid - watch only", 4.0, -0.2)):
        mu = mu if high_better else -mu
        for _ in range(n_each):
            ru = rng.uniform(0.01, 0.05)
            rows.append({"grade": g, "score": score + rng.normal(0, 0.1), "risk_unit": ru, "sl_pct": -ru,
                         "net_ret": (mu + rng.normal(0, 0.3)) * ru, "baseline_ret": 0.0})
    return pd.DataFrame(rows)


def test_results_are_per_dollar_risked():
    c = pd.DataFrame({"net_ret": [0.04, -0.01], "sl_pct": [-0.02, np.nan], "risk_unit": [0.5, 0.01]})
    assert list(Q.outcome_r(c)) == pytest.approx([2.0, -1.0])


def test_grade_check():
    assert Q.grade_check(_hist(high_better=True))["reliable"] is True
    bad = Q.grade_check(_hist(high_better=False))
    assert bad["reliable"] is False and "no better" in bad["reason"]
    assert Q.grade_check(_hist(n_each=5))["reliable"] is None
    assert Q.grade_check(pd.DataFrame())["reliable"] is None


def test_grade_table_order_and_values():
    t = Q.grade_table(_hist())
    assert [r["grade"] for r in t] == Q.GRADE_ORDER
    assert t[0]["avg_r"] > t[-1]["avg_r"] and t[0]["ideas"] == 30


def test_jumpy_coins_that_lose_get_marked_down():
    rng = np.random.default_rng(1)
    ru = np.exp(rng.uniform(np.log(0.01), np.log(0.1), 300))
    z = (np.log(ru) - np.log(ru).mean()) / np.log(ru).std()
    c = pd.DataFrame({"grade": "Strong", "score": 6, "risk_unit": ru, "sl_pct": -ru,
                      "net_ret": (-0.4 * z + rng.normal(0, 0.2, 300)) * ru})
    adj = Q.vol_adjust(c)
    assert adj["slope"] < -0.2 and adj["n"] == 300
    assert Q.adjust_r(0.1, 0.1, adj) < 0.1 < Q.adjust_r(0.1, 0.01, adj)
    assert Q.vol_adjust(c.head(10))["slope"] == 0.0          # too few ideas: no adjustment
    assert Q.adjust_r(0.1, 0.05, None) == 0.1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_adjustment_and_grade_words_match():
    adj = {"n": 200, "mean": float(np.log(0.03)), "sd": 0.5, "slope": -0.2}
    js = r"""
    require(process.argv[1]); require(process.argv[2]);
    const E = globalThis.OmegaEngine, Q = globalThis.OmegaQuality; const adj = JSON.parse(process.argv[3]);
    const bad = { check: { reliable: false, reason: "x" } }, ok = { check: { reliable: true } };
    console.log(JSON.stringify([E.adjustR(0.1, 0.06, adj), E.adjustR(0.1, 0.06, null), Q.gradeWord("Strong", bad, 2),
      Q.gradeWord("Strong", ok, 2), Q.gradeWord("Strong", null, 2), Q.ideasBanner(ok), Q.ideasBanner(bad).includes("switched off")]));"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "site" / "engine.js"), str(ROOT / "site" / "quality.js"), json.dumps(adj)],
                         capture_output=True, text=True, check=True)
    r = json.loads(out.stdout)
    assert r[0] == pytest.approx(Q.adjust_r(0.1, 0.06, adj)) and r[1] == 0.1
    assert r[2:6] == ["Ranked #2", "Strong", "Strong", ""] and r[6] is True


def test_grade_tables_grouped_by_market():
    h = _hist()
    h["symbol"] = ["solana:x" if i % 2 else "ethereum:y" for i in range(len(h))]
    q = Q.quality(h, lambda s: {"solana": "Solana", "ethereum": "Ethereum"}[s.split(":")[0]], ["Solana", "Ethereum"])
    assert [g["name"] for g in q["groups"]] == ["Solana", "Ethereum"]
    assert sum(r["ideas"] for g in q["groups"] for r in g["by_grade"]) == len(h)
    assert all(g["check"]["reliable"] is True for g in q["groups"])          # 30 high and 30 low ideas per market
    assert "groups" not in Q.quality(h)
