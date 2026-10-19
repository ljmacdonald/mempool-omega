"""The Accumulation lab (D119): evidence, watchlist, paper account, forward evaluation, recovery backtest.
Every number here is a clearly made-up test fixture, never shown on the site."""
from datetime import date, datetime, timedelta, timezone

import pytest

from accum import config as K
from accum import evidence as E
from accum import forward as F
from accum import paper as P
from accum import recovery as R
from accum import watch as W

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
TODAY = NOW.date()


def swap(day, wallet, side, qty, usd=1000.0, tx=None, h=10, price=1.0):
    ts = f"{day}T{h:02d}:00:00Z"
    return {"ts": ts, "tx": tx or f"{wallet}{day}{side}{qty}{h}", "wallet": wallet, "side": side, "qty": qty, "usd": usd,
            "price": price, "anomalous": False}


def days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def build(trades, coverage=1.0, ndays=30):
    ds, ws = E.daily(trades)
    cov = {days_ago(i): coverage for i in range(ndays)}
    return ds, ws, cov


# ------------------------------------------------------------------ evidence (spec tests 1-10)
def test_purchase_increases_and_sale_reduces_net_acquisition():
    ds, ws, cov = build([swap(days_ago(1), "A", "buy", 1000)])
    m = E.window_metrics(ds, ws, cov, TODAY, 7, 1_000_000, 500_000)
    assert m["net_qty"] == 1000 and m["share"] == pytest.approx(0.001)
    ds, ws, cov = build([swap(days_ago(1), "A", "buy", 1000), swap(days_ago(2), "B", "sell", 400)])
    assert E.window_metrics(ds, ws, cov, TODAY, 7, 1_000_000, 500_000)["net_qty"] == 600


def test_transfers_never_count_as_buying_and_exchange_moves_are_only_warnings():
    transfers = [{"tx": "t1", "from": "0xfriend", "to": "0xwhale", "qty": 5000},
                 {"tx": "swap1", "from": "0xpool", "to": "0xwhale", "qty": 100},
                 {"tx": "t2", "from": "0xwhale", "to": "0xbinance", "qty": 300}]
    c = E.classify_transfers(transfers, {"swap1"}, {"0xbinance": "Binance"}, {"0xwhale"})
    assert [t["tx"] for t in c["transfers_in"]] == ["t1"]                  # the swap leg is not a transfer
    assert c["to_exchange"][0]["exchange"] == "Binance"
    ds, _, _ = build([swap(days_ago(1), "0xwhale", "buy", 100, tx="swap1")])
    assert ds[days_ago(1)]["buy_qty"] == 100                                # only the swap is buying, never the 5,000 transferred


def test_missing_supply_gives_no_percentage_and_zero_holdings_no_division():
    ds, ws, cov = build([swap(days_ago(1), "A", "buy", 1000)])
    m = E.window_metrics(ds, ws, cov, TODAY, 7, None, None)
    assert m["share"] is None and m["vs_liquidity"] is None
    assert E.circulating({"market_cap_usd": None, "price_usd": 1})[0] is None
    assert E.buyer_relative(1000, 1000)["label"] == "newly observed holder"   # held nothing before: no made-up multiple
    assert E.buyer_relative(1000, None)["label"] == "reference balance unavailable"
    r = E.buyer_relative(1000, 1500)
    assert r["multiple"] == pytest.approx(2.0)


def test_missing_history_never_shows_persistence():
    ds, ws, _ = build([swap(days_ago(i), "A", "buy", 100) for i in range(5)])
    cov = {days_ago(i): 1.0 for i in range(5)}                                 # only five days observed
    m = {"w": {n: E.window_metrics(ds, ws, cov, TODAY, n, 1e6, 1e6) for n in K.WINDOWS}, "buyers": [], "price": {}, "circ": 1e6}
    assert not m["w"][7]["complete"] and m["w"][7]["covered_days"] == 5
    assert W.score(m)["parts"]["persistence"]["points"] == 0


def test_duplicate_swaps_are_counted_once():
    t = [swap(days_ago(1), "A", "buy", 10, tx="x")]
    merged, added = E.merge_trades(t, t + [swap(days_ago(1), "B", "buy", 5, tx="y")])
    assert added == 1 and len(merged) == 2


def test_round_trips_are_left_out_but_frequent_small_buyers_are_kept():
    t = [swap(days_ago(1), "CHUNKS", "buy", 10, h=1 + i % 20, tx=f"b{i}") for i in range(40)]      # 40 small buys: stealth
    t += [swap(days_ago(1), "RT", "buy", 100, tx="r1"), swap(days_ago(1), "RT", "sell", 95, tx="r2")]
    t += [swap(days_ago(1), "REAL", "buy", 50, tx="z")]
    ds, ws = E.daily(t)
    assert ds[days_ago(1)]["buy_qty"] == 450 and ds[days_ago(1)]["bot_usd"] > 0


def test_same_ticker_on_two_chains_stays_two_tokens(tmp_path):
    from accum.run import key_of
    assert key_of("solana", "ABC") != key_of("eth", "0xabc") and key_of("solana", "ABC") == "solana:ABC"


def test_coverage_marks_gaps_when_the_feed_was_full():
    poll = NOW
    full = [swap(TODAY.isoformat(), f"w{i}", "buy", 1, h=11) for i in range(300)]
    a, b = E.poll_interval(poll, poll - timedelta(hours=3), full)
    assert a == E.parse(full[0]["ts"])                                         # anything before the oldest swap may be missing
    a2, _ = E.poll_interval(poll, poll - timedelta(hours=3), full[:10])
    assert a2 == poll - timedelta(hours=3)
    cov = E.coverage_by_day([(NOW - timedelta(hours=6), NOW)])
    assert cov[TODAY.isoformat()] == pytest.approx(0.25)


# ------------------------------------------------------------------ watchlist (spec tests 11-18)
def evidence(net_per_day=3000, days=10, price_change=0.0, circ=1_000_000, reserve=2_000_000, wallets=20, sellers=2):
    t = []
    for i in range(days):
        for j in range(wallets):
            t.append(swap(days_ago(i), f"W{j}", "buy", net_per_day / wallets, usd=net_per_day / wallets * 10, tx=f"{i}-{j}"))
        for j in range(sellers):
            t.append(swap(days_ago(i), f"S{j}", "sell", 1, usd=1000, tx=f"{i}-s{j}"))
    ds, ws = E.daily(t)
    cov = {days_ago(i): 1.0 for i in range(days)}
    m = {"w": {n: E.window_metrics(ds, ws, cov, TODAY, n, circ, reserve) for n in K.WINDOWS}, "weekly": E.weekly_nets(ds, TODAY),
         "price": {"change": price_change, "repricing": price_change >= K.REPRICING}, "circ": circ, "reserve": reserve, "buyers": []}
    return m


@pytest.mark.parametrize("change", [0.0, -0.12])
def test_qualifies_with_a_flat_or_falling_price(change):
    m = evidence(price_change=change)
    rts = W.routes(m)
    assert {r["route"] for r in rts} >= {"A", "C"}
    st, why = W.next_state(None, m, rts, W.score(m), NOW.isoformat())
    assert st == "accumulation_detected" and "route" in why


def test_no_evidence_no_entry_and_insufficient_data_is_explicit():
    m = evidence(net_per_day=0.001, wallets=1, sellers=0)
    assert W.next_state(None, m, W.routes(m), W.score(m), NOW.isoformat())[0] is None
    thin = evidence(days=2)
    entry = {"state": "accumulation_detected", "since": NOW.isoformat()}
    assert W.next_state(entry, thin, W.routes(thin), W.score(thin), NOW.isoformat())[0] == "data_insufficient"


def test_weakening_keeps_the_entry_and_distribution_needs_evidence():
    entry = {"state": "accumulation_detected", "since": NOW.isoformat(), "detection": {"w7_net_qty": 21000}}
    weak = evidence(net_per_day=200)
    st, why = W.next_state(entry, weak, W.routes(weak), W.score(weak), NOW.isoformat())
    assert st == "accumulation_weakening" and "at detection" in why
    m = evidence()
    m["distribution"] = "the addresses buying at detection have since sold 60% of what they had bought"
    assert W.next_state(entry, m, W.routes(m), W.score(m), NOW.isoformat())[0] == "distribution_detected"


def test_score_is_explained_versioned_and_missing_data_earns_nothing():
    m = evidence(circ=None)
    sc = W.score(m)
    assert sc["version"] == K.SCORE_VERSION and set(sc["parts"]) == set(K.WEIGHTS)
    assert sc["parts"]["verified"]["points"] == 0 and sc["parts"]["supply_share"]["points"] == 0
    assert all("why" in p for p in sc["parts"].values())
    assert sum(K.WEIGHTS.values()) == 100


def test_liquidity_collapse_disqualifies_whatever_the_score():
    m = evidence(reserve=50_000)
    entry = {"state": "persistent_accumulation", "since": NOW.isoformat()}
    assert W.next_state(entry, m, W.routes(m), W.score(m), NOW.isoformat())[0] == "disqualified"


# ------------------------------------------------------------------ paper account (spec tests 19-25)
def cand(**kw):
    c = {"key": "solana:T", "chain": "solana", "symbol": "T", "state": "accumulation_detected", "price": 1.0, "price_at": NOW,
         "reserve": 2_000_000, "low14": 0.92, "repricing": False}
    c.update(kw)
    return c


def test_paper_trade_sizes_by_risk_and_updates_usdc():
    acc = P.new_account()
    assert acc["live_trading"] is False
    p = P.plan(cand(), acc, {}, True, NOW)
    assert p["eligible"] and p["stop"] == pytest.approx(0.92 * 0.98)
    assert p["notional"] <= K.MAX_POSITION * 100 + 1e-9 and p["risk"] <= K.RISK_PER_TRADE * 100 + 1e-9
    assert p["target"] == pytest.approx(1 + 2 * (1 - p["stop"]))
    pos = P.buy(acc, cand(), p, NOW)
    assert acc["cash"] == pytest.approx(100 - p["notional"] - (p["notional"] * K.DEX_FEE + 0.01))
    tr = P.sell(acc, pos, 1.1, 2_000_000, "test", NOW)
    assert tr["pnl"] == pytest.approx(acc["cash"] - 100) and tr["fees"] > 0
    assert P.mark(acc, {})["equity"] == pytest.approx(acc["cash"])


def test_no_trade_without_a_defensible_stop_fresh_price_or_peg():
    acc = P.new_account()
    assert "no defensible stop" in P.plan(cand(low14=0.999), acc, {}, True, NOW)["why"]
    assert "no defensible invalidation" in P.plan(cand(low14=None), acc, {}, True, NOW)["why"]
    assert "stale" in P.plan(cand(price_at=NOW - timedelta(hours=5)), acc, {}, True, NOW)["why"]
    assert "peg" in P.plan(cand(), acc, {}, False, NOW)["why"]
    assert "repricing" in P.plan(cand(repricing=True), acc, {}, True, NOW)["why"]
    assert "not an accumulation state" in P.plan(cand(state="investigating"), acc, {}, True, NOW)["why"]


def test_total_open_risk_is_capped():
    acc = P.new_account()
    n = 0
    for i in range(12):
        p = P.plan(cand(key=f"solana:{i}", low14=0.80), acc, {}, True, NOW)
        if not p["eligible"]:
            assert "open risk" in p["why"] or "cash" in p["why"]
            break
        P.buy(acc, cand(key=f"solana:{i}"), p, NOW)
        n += 1
    assert P.open_risk(acc) <= K.MAX_OPEN_RISK * 100 + 1e-6 and n >= 1


def test_ethereum_network_fee_can_make_a_tiny_trade_ineligible():
    p = P.plan(cand(chain="eth"), P.new_account(), {}, True, NOW)
    assert not p["eligible"] and "costs" in p["why"]


def test_exits_on_stop_target_state_and_time():
    acc = P.new_account()
    p = P.plan(cand(), acc, {}, True, NOW)
    P.buy(acc, cand(), p, NOW)
    out = P.manage(acc, {"solana:T": {"state": "accumulation_detected"}}, {"solana:T": 0.5}, {}, {"solana:T": 2e6}, NOW)
    assert out[0]["reason"].startswith("safety exit") and out[0]["exit"] < p["stop"]       # a gap fills at the worse price
    P.buy(acc, cand(), P.plan(cand(), acc, {}, True, NOW), NOW)
    out = P.manage(acc, {"solana:T": {"state": "distribution_detected"}}, {"solana:T": 1.0}, {}, {"solana:T": 2e6}, NOW)
    assert "distribution" in out[0]["reason"]


# ------------------------------------------------------------------ forward evaluation and backtest (spec tests 26-29)
def candles_from(prices, start=NOW - timedelta(days=40)):
    d0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    return [{"t": int((d0 + timedelta(days=i)).timestamp()), "o": p, "h": p * 1.01, "l": p * 0.99, "c": p} for i, p in enumerate(prices)]


def test_forward_uses_only_prices_after_detection_and_compares_with_random():
    at = (NOW - timedelta(days=20)).isoformat()
    cs = {"solana:T": candles_from([1.0] * 21 + [1.5] * 20), "solana:O": candles_from([1.0] * 41), "eth:X": candles_from([3.0] * 41)}
    rows = F.evaluate([{"key": "solana:T", "chain": "solana", "at": at, "price": 1.0}], cs, {"solana:T": "solana", "solana:O": "solana", "eth:X": "eth"}, NOW)
    r7 = next(r for r in rows if r["horizon"] == 7)
    assert r7["n_random"] == 1                                                  # only the same chain is the yardstick
    assert r7["ret"] == pytest.approx(0.5 - 2 * K.DEX_FEE) and r7["random"] == pytest.approx(-2 * K.DEX_FEE)
    assert {r["horizon"] for r in rows} == {7, 14}                              # 30+ days haven't passed yet
    gone = F.evaluate([{"key": "solana:Z", "chain": "solana", "at": at, "price": 1.0}], cs, {"solana:Z": "solana"}, NOW)
    assert gone and all(r["status"] == "no price" for r in gone)               # vanished tokens are kept, not dropped


def kl(prices, start_ms=1_598_918_400_000, vol=2e6):          # 1 Sep 2020, 00:00 UTC (Binance days start at midnight)
    return [[start_ms + i * 86_400_000, p, p, p, p, 1, start_ms + (i + 1) * 86_400_000 - 1, vol] for i, p in enumerate(prices)]


def test_recovery_backtest_has_no_lookahead_keeps_delisted_coins_and_is_reproducible():
    n = 1000
    crash = [100.0] * 400 + [10.0] * 340 + [10 * 1.004 ** k for k in range(260)]   # fell 90%, then climbs slowly off its low
    dead = [100.0] * 400 + [10.0] * 340 + [10 * 1.004 ** k for k in range(60)]     # the same, then delisted
    flat = [50.0] * n
    series = {"CRASHUSDT": kl(crash), "DEADUSDT": kl(dead), "FLATUSDT": kl(flat)}
    rows = R.bt_signals(series, kl(flat))
    assert rows and {r["sym"] for r in rows} >= {"CRASHUSDT"}
    first = min(r["t"] for r in rows)
    i = (first - 1_598_918_400_000) // 86_400_000
    assert i >= K.REC_HISTORY_D                                        # never before two years of history
    d = [r for r in rows if r["sym"] == "DEADUSDT"]
    assert all(r["delisted_before"][180] for r in d)
    assert R.bt_signals(series, kl(flat)) == rows                     # same inputs, same result
    assert R.bt_summary(rows, 30)["verdict"].startswith("Too few")


def test_recovery_screen_and_hard_exclusions():
    base = {"symbol": "abc", "ath_change_percentage": -85.0, "market_cap": 50e6, "total_volume": 2e6, "ath_date": "2024-01-01"}
    assert R.screen(base)[0]
    assert not R.screen({**base, "symbol": "usdc"})[0]
    assert not R.screen({**base, "ath_change_percentage": -40.0})[0]
    assert not R.screen({**base, "total_volume": 1e5})[0]
    sc = R.score({**base, "circulating_supply": 5e8, "max_supply": 1e9}, None, None, None)
    assert sc["parts"]["catalysts"]["points"] == 0 and "not measured" in sc["parts"]["catalysts"]["why"]
    assert sc["parts"]["usage"]["points"] == 0 and sum(K.REC_WEIGHTS.values()) == 100


def test_live_trading_does_not_exist():
    import inspect

    import accum.paper as paper_mod
    src = inspect.getsource(paper_mod)
    assert "PAPER ONLY" in src and P.new_account()["live_trading"] is False
    assert not any(x in src for x in ("private_key", "sign_transaction", "sendTransaction"))


def test_empty_state_first_run_window(tmp_path):
    m = E.window_metrics({}, [], {}, date(2026, 10, 19), 7, None, None)
    assert m["net_qty"] is None and m["covered_days"] == 0 and m["coverage"] == 0
