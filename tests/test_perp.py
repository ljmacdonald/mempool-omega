"""Perp DEX lab (D123): accounting, the cost model, data checks, gates, and ANEE's adversarial tests AT-01..AT-12.
Everything offline: fixtures only."""
from decimal import Decimal as D

import pytest

from perp import costs as CO
from perp import directional as DI
from perp import gates as GT
from perp import lp as LP
from perp import money as M
from perp import paper as PA
from perp.carry import hourly_carry
from perp.live import integrity_ok

# ETH/USD [ETH-USDC] settings as GMX published them on 2026-10-22 (30-decimal values already converted)
S = {"fee_pos": D("0.0004"), "fee_neg": D("0.0006"), "impact_pos": D("3.6575776315667269025792E-8"),
     "impact_neg": D("4.3890931578800724508672E-8"), "impact_exp_pos": D("1.618749894686302441991865106432"),
     "impact_exp_neg": D("1.618749894686302441991865106432"), "max_impact_neg": D("0.005"),
     "swap_fee_pos": D("0.0005"), "swap_fee_neg": D("0.0007")}


def ctx(**kw):
    c = {"settings": S, "oi_long": D(10_266_789), "oi_short": D(8_674_879), "funding_ps_long": D("5.4e-9"),
         "funding_ps_short": D("-6.2e-9"), "borrow_ps_long": D("2.2e-9"), "borrow_ps_short": D(0), "execution_fee_usd": D("0.31")}
    c.update(kw)
    return c


# ------------------------------------------------------------------ accounting
def test_net_is_gross_minus_every_cost_and_income_counts_as_negative_cost():
    t = M.Trade("x", "account", "ETH", 1, D(100), "2026-01-01")
    t.gross = M.price_pnl(1, 100, 2000, 2020)               # +1.00
    t.add_cost("open_fee", D("0.06"))
    t.add_cost("funding", D("-0.02"))                        # received
    assert t.gross == D("1.00") and t.net == D("0.96")
    assert [s["step"] for s in M.waterfall([t])] == ["Gross price result", "Open fee", "Funding", "Net result"]


def test_missing_cost_is_never_zero():
    with pytest.raises(ValueError):
        M.net(D(1), {"open_fee": None})
    with pytest.raises(ValueError):
        M.Trade("x", "a", "ETH", 1, D(1), "t").add_cost("not_a_cost", 1)


def test_decimal_money_has_no_float_drift():
    assert sum((M.d(0.1) for _ in range(10)), M.ZERO) == D(1)


def test_spec_example_net_pnl():
    """ANEE 6.2's illustration: $50, 1% move, 0.06% each way, $0.04 impact, $0.02 funding+borrow, $0.10 network."""
    t = M.Trade("x", "a", "ETH", 1, D(50), "t")
    t.gross = M.price_pnl(1, 50, 100, 101)
    for k, v in (("open_fee", "0.03"), ("close_fee", "0.03"), ("price_impact", "0.04"), ("funding", "0.02"), ("execution_fee", "0.10")):
        t.add_cost(k, D(v))
    assert t.net == D("0.28")


# ------------------------------------------------------------------ cost model
def test_impact_matches_a_real_gmx_order():
    """A real $285,686 long decrease on 2026-10-22: GMX reported +$116.4 impact; the formula with the balance in
    tokens at the oracle price gives about +$110 (open interest had moved a little in between)."""
    imp = CO.gmx_impact(D(10_266_789), D(8_674_879), 1, D(-285_686), S)
    assert 100 < imp < 125


def test_impact_signs_and_size_effect():
    small = CO.gmx_impact(D(10e6), D(9e6), 1, D(10_000), S)
    big = CO.gmx_impact(D(10e6), D(9e6), 1, D(1_000_000), S)
    assert small < 0 and big < 0 and big / 1_000_000 < small / 10_000      # worsening the balance costs more per $ at size
    assert CO.gmx_impact(D(10e6), D(9e6), -1, D(10_000), S) > 0             # improving it earns a rebate


def test_round_trip_items_and_scenarios():
    est = CO.estimate_costs("gmx", ctx(), 1, D(1000), 24)
    it = est["items"]
    assert it["open_fee"] == D("0.6") and it["execution_fee"] == D("0.62")
    assert est["adverse"] >= 2 * sum(v for v in it.values() if v > 0)
    assert est["extreme"] > est["adverse"] > est["base"]


def test_at10_missing_fee_parameters_fail_closed():
    with pytest.raises(CO.NotTradeable):
        CO.estimate_costs("gmx", ctx(settings={**S, "fee_neg": None, "fee_pos": None}), 1, D(1000), 24)
    with pytest.raises(CO.NotTradeable):
        CO.estimate_costs("gmx", ctx(execution_fee_usd=None), 1, D(1000), 24)


def test_at07_thin_book_cannot_fill():
    book = {"bids": [("99", "1")], "asks": [("101", "1")]}
    assert CO.book_slippage(book, 1, D(50), D(100)) == D(50) * D("0.01")
    assert CO.book_slippage(book, 1, D(500), D(100)) is None                  # deeper than the book: not fillable


def test_hourly_carry_signs():
    g = {"fund_long": D("1e-9"), "fund_short": D("-1e-9"), "borrow_long": D("1e-9"), "borrow_short": D(0)}
    long_c = hourly_carry(g, D("0.0001"), 1)                 # long GMX pays 2e-9/s, short HL receives 0.01%/h
    assert long_c == D("0.0001") - D("2e-9") * 3600
    assert hourly_carry(g, D("0.0001"), -1) == -D("0.0001") + D("1e-9") * 3600


# ------------------------------------------------------------------ data checks
def test_pool_price_glitches_and_level_changes():
    gm = {f"2026-01-{i:02d}": D("1.00") + D(i) / 1000 for i in range(1, 21)}
    gm["2026-01-05"] = D("5")                                 # one-off glitch
    gm["2026-01-09"] = D(0)                                   # impossible
    good, bad = LP.clean(gm)
    assert set(bad) == {"2026-01-05", "2026-01-09"} and "2026-01-06" in good
    lvl = {f"2026-02-{i:02d}": (D(1) if i < 10 else D(3)) for i in range(1, 20)}
    good, bad = LP.clean(lvl)
    assert bad == ["2026-02-10"] and "2026-02-11" in good   # confirmed level change: no return across the jump


def test_at01_price_integrity():
    assert integrity_ok(D(2000), D(2010)) and not integrity_ok(D(2000), D(2100))


# ------------------------------------------------------------------ gates
def test_gates_incomplete_until_enough_evidence_and_fail_on_drawdown():
    r = GT.evaluate({"complete": 1.0, "nets": [0.01, -0.005] * 10, "drawdown": 0.02, "stressed_mean": 0.001})
    assert r["status"] == "testing" and r["gates"]["profit"]["status"] == "incomplete"
    r = GT.evaluate({"complete": 1.0, "nets": [0.01] * 10, "drawdown": 0.4, "stressed_mean": 0.001})
    assert r["gates"]["drawdown"]["status"] == "fail" and r["status"] == "failed"


def test_gate3_needs_a_positive_lower_bound():
    import random
    rnd = random.Random(1)
    noisy = [rnd.gauss(0.0005, 0.02) for _ in range(600)]     # positive mean, interval spans zero
    r = GT.evaluate({"complete": 1.0, "nets": noisy, "drawdown": 0.05, "stressed_mean": 0.0001})
    assert r["gates"]["evidence"]["pass"] and r["gates"]["profit"]["status"] == "fail"
    steady = [0.002, 0.001] * 300
    r = GT.evaluate({"complete": 1.0, "nets": steady, "drawdown": 0.01, "stressed_mean": 0.001,
                     "shadow": {"days": 40, "trades": 150, "cost_ok": 0.97}})
    assert r["status"] == "passed: review only"


def test_bootstrap_interval_contains_the_mean():
    xs = [0.001 * ((i * 37) % 11 - 5) for i in range(400)]
    lo, hi = GT.block_bootstrap_ci(xs)
    assert lo < sum(xs) / len(xs) < hi


# ------------------------------------------------------------------ directional simulation (AT-03, AT-05, AT-06, AT-09, AT-12)
def _bars(prices, t0=1_700_000_000_000, step=3_600_000):
    out = []
    for i, (o, h, lo, c) in enumerate(prices):
        out.append([t0 + i * step, o, h, lo, c, 1.0, t0 + (i + 1) * step - 1, 1e6])
    return out


def flat(ts):
    return {"fund_long": D(0), "fund_short": D(0), "borrow_long": D(0), "borrow_short": D(0)}


def _market(rates):
    return {"name": "ETH", "settings": S, "oi": (D(10e6), D(9e6)), "exec_fee": D("0.31"), "rate_at": rates}


def test_at03_at06_next_bar_entry_and_gap_through_stop():
    prices = [(100, 101, 99, 100)] * 20 + [(100, 100.5, 99.5, 100), (100, 100.2, 99.8, 100), (95, 95, 94, 94.5)]
    bars = _bars(prices)
    trades, _ = DI.run_trades(bars, [(20, 1)], _market(flat), D(10_000), "trend", "reference")
    t = trades[0]
    assert t.opened == M.iso(bars[21][0] // 1000)                         # filled at the NEXT bar's open (AT-03)
    assert t.reason == "gap through the stop" and t.gross < 0           # exits at the worse open, not the stop (AT-06)


def test_at05_funding_follows_the_actual_path():
    prices = [(100, 101, 99, 100)] * 20 + [(100, 100.5, 99.5, 100)] * 6 + [(100, 120, 100, 120)]
    bars = _bars(prices)

    def flip(ts):
        return {"fund_long": D("1e-6") if ts < bars[23][0] // 1000 else D("-1e-6"), "fund_short": D(0),
                "borrow_long": D(0), "borrow_short": D(0)}
    trades, _ = DI.run_trades(bars, [(20, 1)], _market(flip), D(10_000), "trend", "reference")
    t = trades[0]

    def const(ts):
        return {"fund_long": D("1e-6"), "fund_short": D(0), "borrow_long": D(0), "borrow_short": D(0)}
    t2, _ = DI.run_trades(bars, [(20, 1)], _market(const), D(10_000), "trend", "reference")
    assert t.costs["funding"] < t2[0].costs["funding"]                 # the flip mid-hold reduced the funding paid


def test_small_account_cannot_fund_the_fixed_costs():
    prices = [(100, 101, 99, 100)] * 30
    trades, rej = DI.run_trades(_bars(prices), [(20, 1)], _market(flat), D(12.5), "trend", "account")
    assert not trades and "risk budget" in rej[0]["why"]


def test_at09_one_position_per_side_across_correlated_markets():
    a = M.Trade("trend", "r", "ETH", 1, D(1), "2026-01-01T00:00", "2026-01-02T00:00")
    b = M.Trade("trend", "r", "BTC", 1, D(1), "2026-01-01T05:00", "2026-01-01T09:00")
    c = M.Trade("trend", "r", "SOL", -1, D(1), "2026-01-01T05:00", "2026-01-01T09:00")
    assert DI.limit_group([a, b, c]) == [a, c]


def test_at12_dependence_on_the_largest_winners_is_reported():
    from perp.report import summarize
    ts = []
    for i in range(20):
        t = M.Trade("x", "r", "ETH", 1, D(100), f"2026-01-{i + 1:02d}", f"2026-01-{i + 1:02d}")
        t.gross = D(50) if i < 3 else D(-1)
        ts.append(t)
    s = summarize(ts, D(1000))
    assert s["mean"] > 0 and s["without_top5"] < 0


# ------------------------------------------------------------------ risk gate and kill switches (AT-02, AT-04, AT-08)
def test_at04_capacity_and_at08_kill_switch_block_new_positions():
    assert PA.capacity_ok(D(1000), D(1_000_000)) and not PA.capacity_ok(D(20_000), D(1_000_000))
    assert not PA.capacity_ok(D(10), None)
    acc = {"cash": "25", "open": [], "paused": None, "day_start": "25"}
    snap = {"kill": ["USDC price 0.97 outside 1% of $1"], "contexts": {}}
    assert PA.risk_block(acc, snap, 0, D(1), D(25)).startswith("kill switch")
    assert PA.risk_block(acc, {"kill": [], "contexts": {}}, 0, D(6), D(25)) == "margin limit (20% of the sleeve) reached"


def test_at02_cost_spike_turns_the_conservative_edge_negative():
    ok = CO.estimate_costs("gmx", ctx(), 1, D(1000), 24)
    spike = CO.estimate_costs("gmx", ctx(execution_fee_usd=D(20)), 1, D(1000), 24)
    assert spike["adverse"] > 40 > ok["adverse"]


def test_no_live_trading_path_exists():
    import pathlib
    src = " ".join(p.read_text() for p in pathlib.Path("perp").glob("*.py"))
    for word in ("private_key", "sign_transaction", "send_raw_transaction", "eth_sendRawTransaction", "Account.from_key"):
        assert word not in src
    import perp
    assert perp.PAPER_ONLY is True


def test_pool_entry_costs_are_counted_once():
    acc = {"cash": "25", "open": [], "closed": [], "paused": None, "day_start": "25"}
    snap = {"contexts": {}, "kill": [], "exec_fee_usd": D("0.31")}
    costs = {"deposit_fee": D("0.0056"), "execution_fee": D("0.31"), "gas": D("0.02")}
    pos = PA.open_pos(acc, "account", "lp", "POOL", 0, D(8), D("1.25"), 0, {}, costs, {"gm_now": "1.25", "swap_fee": "0.0007"})
    assert PA.equity(acc, snap) == D(25) - sum(costs.values())
    tr = PA.close(acc, pos, snap, 3600, "test")
    assert D(acc["cash"]) == D(25) + M.d(tr["net"])
