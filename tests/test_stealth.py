"""Stealth accumulation (D120): swaps read from transfers, the day-by-day rules, and the paper trade. All addresses and
numbers are made-up test fixtures."""
from accum import stealth as ST

POOL, ROUTER, V4 = "0xpool", next(iter(ST.ROUTERS)), "0x000000000004444c5dc75cb358380d2e3de08a90"


def t(f, to, q):
    return {"from": f, "to": to, "qty": q}


def test_reading_swaps_from_transfers():
    assert ST.classify([t(POOL, "0xalice", 100)], {POOL}) == ({"0xalice": 100}, {})                     # bought
    assert ST.classify([t("0xbob", POOL, 50)], {POOL}) == ({}, {"0xbob": 50})                           # sold
    assert ST.classify([t(POOL, ROUTER, 70), t(ROUTER, "0xcarol", 70)], {POOL}) == ({"0xcarol": 70}, {})  # through a router
    assert ST.classify([t(V4, "0xdan", 9)], {V4}) == ({"0xdan": 9}, {})                                  # Uniswap v4's shared contract
    assert ST.classify([t("0xalice", "0xfriend", 500)], {POOL}) == ({}, {})                            # a transfer is never a buy


def test_liquidity_is_neither_a_buy_nor_a_sell():
    assert ST.liquidity_kind("addLiquidityETH(address token,uint256 amountTokenDesired)") == "add"
    assert ST.liquidity_kind("removeLiquidityWithPermit(address,address)") == "remove"
    assert ST.liquidity_kind("swapExactETHForTokens(uint256,address[],address,uint256)") is None
    st = {}
    ST.note_liquidity(st, None, "2026-01-01", "add", [t("0xwhale", POOL, 1000)], {POOL})
    assert st["lp"][0]["by"] == ["0xwhale"] and st["lp"][0]["qty"] == 1000


def day(i):
    from datetime import date, timedelta
    return (date(2026, 1, 1) + timedelta(days=i)).isoformat()


def run_days(buys_by_day, supply=1_000_000, price=1.0, quiet=0.0, n=40):
    """Feed days through the tracker; return the first signal (or None) and the day it fired."""
    tr = ST.Tracker()
    for i in range(n):
        tr.day = day(i)
        for addr, q, side in buys_by_day.get(i, []):
            tr.add(day(i), {addr: q} if side == "b" else {}, {addr: q} if side == "s" else {})
        tr.close_day()
        acc = tr.accumulators(supply, price)
        sig = ST.signal(acc, supply, quiet, day(i), tr.last_signal)
        if sig:
            return sig, i
    return None, None


def quiet_buying(wallets=3, per_buy=500, days=range(0, 30, 3), sells=0):
    out = {}
    for d in days:
        for w in range(wallets):
            out.setdefault(d, []).append((f"0xacc{w}", per_buy, "b"))
            if sells and d % 9 == 0:
                out[d].append((f"0xacc{w}", sells, "s"))
    return out


def test_small_chunks_over_many_days_make_a_signal_and_never_early():
    sig, when = run_days(quiet_buying())
    assert sig and sig["accumulators"] == 3 and sig["share"] >= ST.RULES["min_total_share"]
    assert when == 12                       # the day the 5th buy day completed (days 0,3,6,9,12): not before
    sig2, _ = run_days(quiet_buying(sells=100))                  # a few cover sales don't hide it
    assert sig2


def test_no_signal_for_one_big_buy_heavy_selling_a_pump_or_unknown_supply():
    assert run_days({1: [("0xbig", 20_000, "b")]})[0] is None                                   # one large purchase
    assert run_days(quiet_buying(sells=900))[0] is None                                          # sold most of it
    assert run_days(quiet_buying(), quiet=0.8)[0] is None                                        # already pumping
    assert run_days(quiet_buying(), supply=None)[0] is None                                      # no supply: no percentage
    assert run_days(quiet_buying(per_buy=20_000, wallets=1), supply=10_000_000)[0] is None       # chunks too big ($20k)


def test_cooldown_one_signal_per_60_days():
    tr = ST.Tracker()
    tr.last_signal = day(0)
    acc = [{"net": 10_000, "group": "a", "first_day": day(0), "buy_days": 6}]
    assert ST.signal(acc, 1_000_000, 0.0, day(30), tr.last_signal) is None
    assert ST.signal(acc, 1_000_000, 0.0, day(61), tr.last_signal)


def prices(path):
    return {day(i): p for i, p in enumerate(path)}


def test_paper_trade_exits_pump_stop_dump_and_time():
    up = ST.simulate(day(0), prices([1, 1, 1.2, 1.7]), {}, 100)
    assert up["reason"].startswith("take profit") and up["entry_day"] == day(1) and up["ret"] == 0.7 - ST.TRADE["cost"]
    down = ST.simulate(day(0), prices([1, 1, 0.9, 0.6]), {}, 100)
    assert down["reason"].startswith("safety exit")
    dump = ST.simulate(day(0), prices([1, 1, 1.1, 1.2]), {day(3): 30}, 100)
    assert dump["reason"] == "the accumulators started selling"
    flat = ST.simulate(day(0), prices([1] * 100), {}, 100)
    assert flat["reason"].startswith("held 90") and flat["days"] == 90
    assert ST.simulate(day(0), prices([1, 1, 1.05]), {}, 100)["reason"] == "still open"


def test_tracker_state_survives_a_save():
    tr = ST.Tracker()
    tr.day = day(0)
    tr.add(day(0), {"0xa": 5}, {})
    tr.close_day()
    again = ST.Tracker(tr.to_state())
    assert list(again.days) == ["0xa"] and again.days["0xa"][0][1] == 5
