"""Sniper lab (paper): exits fill honestly (a crash through the stop fills at the crash), launch limits keep every
scam test, and results compare passed tokens with rejected ones over the same hours."""
import numpy as np
import pandas as pd

from dex import security as S
from sniper import run as R


def candles(prices, start="2026-10-06 00:00", lows=None):
    idx = pd.date_range(start, periods=len(prices), freq="5min", tz="UTC")
    p = np.asarray(prices, float)
    lo = np.asarray(lows, float) if lows is not None else p * 0.99
    return pd.DataFrame({"open": p, "high": p * 1.01, "low": lo, "close": p}, index=idx)


def test_exits_take_profit_safety_exit_and_crash():
    t0 = pd.Timestamp("2026-10-06 00:00", tz="UTC")
    up = candles([1.0, 1.1, 1.3, 1.6, 1.7])
    assert R.simulate(up, 1.0, t0, R.PLANS["quick"]) == (1.3, "take profit")                 # jumped over the target: sold at the jump
    down = candles([1.0, 0.95, 0.84, 0.8])
    r, why = R.simulate(down, 1.0, t0, R.PLANS["quick"])
    assert why == "safety exit" and abs(r - 0.84) < 1e-9                                        # opened below the stop: sold at the open
    rug = candles([1.0, 1.0, 0.02], lows=[0.99, 0.99, 0.01])
    r, why = R.simulate(rug, 1.0, t0, R.PLANS["standard"])
    assert why == "crashed through the safety exit" and r == 0.01                        # filled at the crash, not the stop
    flat = candles([1.0] * 10)
    assert R.simulate(flat, 1.0, t0, R.PLANS["quick"])[1] == "time limit"
    assert R.simulate(flat, 1.0, t0 + pd.Timedelta(hours=5), R.PLANS["quick"]) is None   # nothing after the entry


def test_launch_limits_relax_only_size_age_and_activity():
    t = R.launch_thresholds()
    base = S.thresholds(None, "standard")
    relaxed = {k for k in base if k != "profile" and t[k] != base[k]}
    assert relaxed == {"min_liq", "min_age_days", "min_active_days", "min_buyers", "min_sellers", "min_sell_ratio", "min_holders"}
    f = {**S.empty_facts(), "goplus": True, "honeypot_src": True, "sim_ok": True, "owner_active": True, "mintable": True}
    m = R.market({"liq_real": 50_000, "age_min": 3, "tx_h24": {"buyers": 5, "sellers": 1}})
    a = S.assess(f, m, t, "bsc")
    assert a["verdict"] == "reject" and any(h["key"] == "owner_powers" for h in a["hard"])
    ok = {**f, "owner_active": False, "mintable": False, "open_source": True, "top10_pct": 0.2, "creator_pct": 0.0, "buy_tax": 0.0, "sell_tax": 0.0}
    assert S.assess(ok, m, t, "bsc")["verdict"] == "pass"


def test_results_pair_passed_with_rejected_on_the_same_network_and_hours():
    rows = []
    for i in range(40):
        t = str(pd.Timestamp("2026-10-01", tz="UTC") + pd.Timedelta(hours=i))
        rows.append({"id": f"p{i}", "chain": "bsc", "entry_t": t, "verdict": "pass", "status": "closed", "rug": False,
                     **{f"{p}_net": 0.05 + 0.01 * (i % 3) for p in R.PLANS}})
        rows.append({"id": f"r{i}", "chain": "bsc", "entry_t": t, "verdict": "reject", "status": "closed", "rug": i % 2 == 0,
                     **{f"{p}_net": -0.3 for p in R.PLANS}})
    h = pd.DataFrame(rows).reindex(columns=R.COLS)
    res = R.results(h)
    q = res["quick"]
    assert q["passed"]["n"] == 40 and q["rejected"]["n"] == 40
    assert abs(q["passed"]["random"] + 0.3) < 1e-9 and q["passed"]["edge"] > 0.3
    assert q["rug_passed"] == 0.0 and q["rug_rejected"] == 0.5
    assert q["level"] == "good"


def test_no_evidence_means_watch_only():
    res = R.results(pd.DataFrame(columns=R.COLS))
    j = R.judge(res, "standard", [], 0.0, 0.05)
    assert j["grade"] == "Avoid - watch only" and j["evidence"] == "Not proven yet"


def test_rejected_tokens_that_cant_be_sold_stay_out_of_the_comparison():
    """A token rejected because selling looks blocked only rises on paper; counting it as a 'bought anyway' winner
    would make the checks look worse than they are."""
    from sniper import run

    rows = []
    for i, (verdict, reason, net) in enumerate([("pass", "", -0.2), ("reject", "The creator/owner still holds 100%.", -0.1),
                                                ("reject", "Only 0 wallets sold against 5 that bought in 24 hours. X", 0.25),
                                                ("reject", "Honeypot: a test buy-and-sell failed", 0.25)]):
        rows.append({"chain": "bsc", "entry_t": f"2026-10-06T0{i}:00:00Z", "verdict": verdict, "reasons": reason, "status": "closed",
                     "rug": False, **{f"{p}_net": net for p in run.PLANS}})
    res = run.results(pd.DataFrame(rows))["quick"]
    assert res["unsellable"] == 2 and res["rejected"]["n"] == 1 and abs(res["rejected"]["avg"] + 0.1) < 1e-9


def test_unlocked_pool_or_unproven_sale_rejects_and_old_passes_are_rescored():
    """Since D91 an unlocked pool or an unproven sale rejects; earlier passes that carried those warnings count as
    rejections, so 'passed' only means passed under the current rules."""
    from sniper import run

    assert run.strict_rejections({"sim_ok": True, "lp_secured_pct": 0.95}) == []
    assert len(run.strict_rejections({"sim_ok": None, "lp_secured_pct": 0.95})) == 1
    assert len(run.strict_rejections({"sim_ok": False, "lp_secured_pct": None})) == 2
    h = pd.DataFrame([
        {"verdict": "pass", "rules": None, "warnings": "The pool's money isn't (fully) locked: it can be pulled."},
        {"verdict": "pass", "rules": None, "warnings": "No wallet has sold yet: the first sellers are usually the launch bots."},
        {"verdict": "pass", "rules": 2, "warnings": ""},
        {"verdict": "reject", "rules": None, "warnings": ""}])
    assert run.effective_verdict(h).tolist() == ["reject", "pass", "pass", "reject"]


def test_sniper_alerts_need_proof_under_the_current_rules():
    from sniper import run

    res = {"passed": {"n": 49}, "level": "good"}
    assert run.judge({"standard": {**res, "passed": {"n": 49, "hit": 0.6, "avg": 0.1, "avg_loss": -0.2}}}, "standard", [], 0, 0.02)["evidence"] == "Not proven yet"
    assert run.judge({"standard": {**res, "passed": {"n": 50, "hit": 0.6, "avg": 0.1, "avg_loss": -0.2}}}, "standard", [], 0, 0.02)["evidence"] == "Held up in paper tests"
