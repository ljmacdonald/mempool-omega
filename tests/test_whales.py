"""Whale tracker (paper): the mirror copies whole positions in proportion to each whale's account, pays funding and
costs on changes, caps leverage; changes are detected; selection skips bots and small accounts; verdict is strict."""
import pandas as pd

from whales import run as W


def lb(addr, acct, pnl_m, vlm_m, vlm_w=1.0, pnl_all=1.0):
    return {"ethAddress": addr, "accountValue": str(acct), "displayName": None,
            "windowPerformances": [["day", {"pnl": "0", "roi": "0", "vlm": "0"}], ["week", {"pnl": "0", "roi": "0", "vlm": str(vlm_w)}],
                                   ["month", {"pnl": str(pnl_m), "roi": "9", "vlm": str(vlm_m)}], ["allTime", {"pnl": str(pnl_all), "roi": "0", "vlm": "1"}]]}


def test_selection_skips_small_accounts_and_bots_and_ranks_on_profit_over_account():
    rows = [lb("big_good", 2e6, 1e6, 1e7), lb("small", 5e5, 4e5, 1e6), lb("bot", 2e6, 2e6, 1e9),
            lb("big_ok", 4e6, 1e6, 1e7), lb("loser", 2e6, -1e5, 1e7), lb("idle", 2e6, 1e6, 1e7, vlm_w=0)]
    lead, rnd = W.choose(rows, 1)
    assert [w["addr"] for w in lead] == ["big_good", "big_ok"]            # 50% beats 25%; leaderboard ROI ignored
    assert {w["addr"] for w in rnd} <= {"loser"}


def test_exposure_is_proportional_and_capped():
    mids = {"BTC": 100.0, "ETH": 10.0}
    books = {"a": {"acct": 1000.0, "pos": {"BTC": {"szi": 20.0}}},          # 2x long BTC
             "b": {"acct": 1000.0, "pos": {"ETH": {"szi": -100.0}}}}        # 1x short ETH
    e = W.exposure(books, mids)
    assert abs(e["BTC"] - 1.0) < 1e-9 and abs(e["ETH"] + 0.5) < 1e-9
    huge = {"a": {"acct": 100.0, "pos": {"BTC": {"szi": 100.0}}}}           # 100x
    assert abs(sum(abs(v) for v in W.exposure(huge, mids).values()) - W.MAX_GROSS) < 1e-9


def test_mirror_step_prices_funding_and_costs():
    eq = W.step(1.0, {"BTC": 1.0}, {"BTC": 1.0}, {"BTC": 100.0}, {"BTC": 110.0}, {"BTC": 0.0}, 1.0)
    assert abs(eq - 1.10) < 1e-12
    eq = W.step(1.0, {"BTC": 1.0}, {"BTC": 1.0}, {"BTC": 100.0}, {"BTC": 100.0}, {"BTC": 0.001}, 2.0)
    assert abs(eq - 0.998) < 1e-12                                           # longs pay funding
    eq = W.step(1.0, {}, {"BTC": 2.0}, {"BTC": 100.0}, {"BTC": 100.0}, {}, 0.25)
    assert abs(eq - (1 - 2 * W.COST_SIDE)) < 1e-12                           # opening costs


def test_changes_detected():
    mids = {"BTC": 100.0, "ETH": 10.0, "SOL": 5.0}
    old = {"a": {"acct": 1, "pos": {"BTC": {"szi": 1.0, "entry": 90}, "ETH": {"szi": 10.0, "entry": 9}}}}
    new = {"a": {"acct": 1, "pos": {"BTC": {"szi": -1.0, "entry": 100}, "SOL": {"szi": 5.0, "entry": 5}}}}
    ch = W.changes(old, new, mids)
    kinds = {(x["coin"], x["kind"]) for x in ch}
    assert kinds == {("BTC", "flipped"), ("ETH", "closed"), ("SOL", "opened")}
    moved = {x["coin"]: x["moved"] for x in ch}
    assert moved == {"BTC": 200.0, "ETH": 100.0, "SOL": 25.0}      # a flip moves both sides


def test_verdict_needs_a_month_and_an_edge():
    t = [1_700_000_000_000 + i * 86_400_000 for i in range(40)]
    short = pd.DataFrame({"t": t[:5], "cohort": "c", "eq_lead": 1.0, "eq_rand": 1.0, "eq_btc": 1.0})
    assert W.verdict(short)["level"] == "warn"
    lead, e = [], 1.0
    for i in range(40):                                                     # +1% a day give or take 0.2%
        lead.append(e)
        e *= 1.012 if i % 2 else 1.008
    good = pd.DataFrame({"t": t, "cohort": "c", "eq_lead": lead, "eq_rand": [1.0] * 40, "eq_btc": [1.0] * 40})
    assert W.verdict(good)["level"] == "good"
    bad = good.assign(eq_lead=[1.0 - 0.001 * i for i in range(40)])
    assert W.verdict(bad)["level"] == "bad"
