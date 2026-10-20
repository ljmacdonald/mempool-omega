"""Engine B (D122): catalysts from measured data, the coverage check, the exit plan and the kill criteria."""
from datetime import date, timedelta

from deep import backtest as BT
from deep import catalyst as CT
from deep import coverage as CV
from deep import research as DR
from deep.profile import load

P = load()
D0 = date(2024, 1, 1)


def days(n, start=D0):
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def test_fee_switch_is_dated_from_the_first_day_of_holder_revenue():
    ds = days(200)
    holders = {d: (0.0 if i < 120 else 1000.0) for i, d in enumerate(ds)}
    live = CT.detect(date.fromisoformat(ds[-1]), holders, None, P)
    sw = [c for c in live if c["type"] == "fee_switch_to_holders"]
    assert sw and sw[0]["effective"] == ds[120]
    assert not CT.detect(date.fromisoformat(ds[119]), holders, None, P)            # never seen before it happened


def test_unknown_days_are_not_zero():
    ds = days(200)
    holders = {d: 1000.0 for i, d in enumerate(ds) if i >= 120}                     # no data before: not "none"
    assert not [c for c in CT.detect(date.fromisoformat(ds[-1]), holders, None, P) if c["type"] == "fee_switch_to_holders"]


def test_emissions_cut_and_overhang():
    ds = days(260)
    unlocked, q = {}, 0.0
    for i, d in enumerate(ds):
        q += 100.0 if i < 150 else 20.0
        if i == 200:
            q += 50_000
        unlocked[d] = q
    live = {c["type"] for c in CT.detect(date.fromisoformat(ds[210]), None, unlocked, P)}
    assert "overhang_resolved" in live and "emissions_cut" in live


def test_coverage_estimates_missing_entries_and_labels_low_coverage():
    known = {c: {"binance"} for c in "ABCDEFGHIJ"}
    rep = CV.report(known, set("ABCDEFGH"), {"A": 2, "B": 2}, P)                    # 4 found in 8 coins: ~1 missing
    assert rep["entries_estimated_missing"] == 1.0 and rep["reliable"] and abs(rep["ratio"] - 0.8) < 1e-9
    rep = CV.report(known, set("AB"), {"A": 1}, P)
    assert not rep["reliable"] and rep["label"].startswith("UNRELIABLE")


def candles(prices):
    return [{"d": d, "o": p, "h": p, "l": p, "c": p, "v": 5e6} for d, p in zip(days(len(prices)), prices)]


def test_ladder_sells_in_steps_and_trailing_stop_closes_the_rest():
    c = candles([1.0, 1.5, 2.1, 2.5, 4.1, 3.0, 2.3])
    t = BT.simulate(c, 0, 0.8, True, lambda d: "risk_on", P)
    # 50% at 2x, 15% at 4x, the other 35% on the 40% trail below the 4.1 close: 0.5x2 + 0.15x4 + 0.35x2.3, less costs
    assert t["reason"] == "trailing_stop" and 2.3 < t["multiple"] < 2.405


def test_disaster_stop_under_the_trough():
    c = candles([1.0, 0.9, 0.7, 0.5])
    t = BT.simulate(c, 0, 0.8, True, lambda d: "risk_on", P)
    assert t["reason"] == "disaster_stop" and t["multiple"] < 0.61


def test_delisted_coin_exits_at_its_last_close():
    c = candles([1.0, 1.1, 0.95])
    t = BT.simulate(c, 0, 0.5, False, lambda d: "risk_on", P)
    assert t["reason"] == "delisted" and t["multiple"] < 0.95


def test_backtest_keeps_only_allowed_regimes_and_limits():
    tr = [{"symbol": f"S{i}", "entry_day": f"2023-01-{i + 1:02d}", "exit_day": "2023-06-01", "regime": "risk_on" if i % 2 else "neutral",
           "multiple": 1.1, "pnl": 5.0, "reason": "trailing_stop"} for i in range(20)]
    out = BT.run(tr, P)
    assert out["trades"] == P["portfolio"]["max_positions"]
    assert all(t["regime"] == "risk_on" for t in tr[1::2])
    k = BT.kill(out, P)
    assert not k["positive_windows"]["pass"]                                       # only one window has trades


def test_kill_b_needs_every_criterion():
    cov = {"reliable": True, "ratio": 0.9, "min_ratio": 0.8}
    es = {"entry": {"all": {"delisted_or_fell_80": 0.2, "reached_3x": 0.2, "n": 50, "n_full_year": 40}}, "regime_inputs_ok": True}
    assert DR.kill_b(cov, es, P)["status"] == "paper"
    es["entry"]["all"]["reached_3x"] = 0.1
    assert DR.kill_b(cov, es, P)["status"] == "halted"
    es["entry"]["all"]["reached_3x"] = 0.2
    assert DR.kill_b({**cov, "reliable": False}, es, P)["status"] == "halted"
