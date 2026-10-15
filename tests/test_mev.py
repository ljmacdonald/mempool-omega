"""MEV & transaction survival engine (D103): OFFLINE_TEST mode, no network."""
import json

import pytest

from core import config as core_config
from mev import adapters as A
from mev import clients, shadow
from mev import config as K
from mev import economics as E
from mev import policy as P
from mev import safety as S
from mev import survival as V

IN = K.TRADE_LAMPORTS


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "STATE_DIR", tmp_path)
    return tmp_path


# ------------------------------------------------------------------------------------------- economics
def test_each_cost_is_counted_once_and_a_reverted_bundle_costs_nothing():
    c = E.method_costs(cu_price_micro=10_000, tip=20_000)
    pf = E.priority_fee(10_000)
    assert pf == 6_000                                         # 600k CU x 10,000 micro-lamports
    assert c["rpc_plain"].on_success == c["rpc_plain"].on_revert == K.BASE_FEE + pf
    assert c["jito_bundle"].on_success == K.BASE_FEE + 20_000 and c["jito_bundle"].on_revert == 0
    assert c["jito_dontfront"].on_success == K.BASE_FEE + pf + 20_000   # same total budget, split 70/30
    assert all(x.on_miss == 0 for x in c.values())
    assert E.net_profit(IN, IN + 100_000, c["jito_bundle"]) == 100_000 - K.BASE_FEE - 20_000
    assert E.min_out(IN, c["rpc_plain"]) == IN + K.BASE_FEE + pf + K.MIN_PROFIT


def test_fee_bids_are_bounded():
    c = E.method_costs(cu_price_micro=10**9, tip=10**12)
    assert c["jito_bundle"].on_success == K.BASE_FEE + K.MAX_TIP_LAMPORTS
    assert c["rpc_plain"].on_success == K.BASE_FEE + E.priority_fee(K.MAX_CU_PRICE)
    assert E.method_costs(0, 0)["jito_bundle"].on_success == K.BASE_FEE + K.JITO_MIN_TIP
    assert E.tip_from_floor({"landed_tips_75th_percentile": 2.2e-05}) == 22_000
    assert E.tip_from_floor(None) == K.JITO_MIN_TIP


def test_ev_per_landed_attempt():
    c = E.method_costs(10_000, 20_000)
    assert E.ev_per_landed(0.5, 40_000, c["rpc_plain"]) == pytest.approx(0.5 * 40_000 - 0.5 * 11_000)
    assert E.ev_per_landed(None, 1, c["rpc_plain"]) is None


# ------------------------------------------------------------------------------------------- survival
def cand(i, out0, out1, delay=1):
    return {"found_at": f"2026-10-14T00:{i // 60:02d}:{i % 60:02d}+00:00", "amount_in": IN, "out": out0,
            "cu_price": 10_000, "tip": 20_000, "rechecks": [{"delay": delay, "out": out1}]}


def bundle(c):
    return E.method_costs(c["cu_price"], c["tip"])["jito_bundle"]


def test_insufficient_evidence_with_few_opportunities():
    cs = [cand(i, IN + 200_000, IN + 200_000) for i in range(10)]
    r = V.evaluate(cs, bundle, 1)
    assert r["verdict"] == V.INSUFFICIENT and r["landing"] == V.INSUFFICIENT


def test_opportunities_that_vanish_are_not_viable_and_ones_that_last_are_promising():
    gone = [cand(i, IN + 200_000, IN - 50_000) for i in range(40)]
    assert V.evaluate(gone, bundle, 1)["verdict"] == V.NOT_VIABLE
    kept = [cand(i, IN + 200_000, IN + 150_000) for i in range(40)]
    r = V.evaluate(kept, bundle, 1)
    assert r["verdict"] == V.PROMISING and r["test"]["n"] == 12 and r["ev_test"] > 0


def test_validation_is_time_ordered():
    # good early, bad late: the later check must catch it even though the early part looked fine
    cs = [cand(i, IN + 200_000, IN + 150_000) for i in range(28)] + [cand(28 + i, IN + 200_000, IN - 50_000) for i in range(12)]
    assert V.evaluate(list(reversed(cs)), bundle, 1)["verdict"] == V.NOT_VIABLE


def test_not_an_opportunity_for_this_method_is_skipped():
    c = cand(0, IN + 1_000, IN + 1_000)                         # doesn't clear any method's minimum output
    assert V.outcome(c, bundle(c), 1) is None
    assert V.wilson_low(0, 0) == 0 and 0 < V.wilson_low(9, 10) < 0.9


# ------------------------------------------------------------------------------------------- safety
def good_tx(**kw):
    tx = {"fee_payer": "Wallet111", "wallet_sol": 2 * K.LAMPORTS, "amount_in": IN, "input_mint": K.SOL,
          "output_mint": K.SOL, "min_out": IN + 50_000, "expected_costs": 25_000, "atomic": True,
          "instructions": [
              {"program": "ComputeBudget111111111111111111111111111111", "kind": "set_cu_limit", "units": 600_000},
              {"program": "ComputeBudget111111111111111111111111111111", "kind": "set_cu_price", "micro_lamports": 10_000},
              {"program": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4", "kind": "swap", "route": [K.SOL, K.TOKENS["JUP"], K.SOL],
               "accounts": [{"key": "jitodontfront111111111111111111111", "writable": False}]},
              {"program": "11111111111111111111111111111111", "kind": "transfer",
               "to": "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe", "lamports": 20_000}]}
    tx.update(kw)
    return tx


def test_a_correct_transaction_passes_every_check():
    assert S.inspect(good_tx())["ok"]


@pytest.mark.parametrize("change,needle", [
    ({"atomic": False}, "atomic"),
    ({"output_mint": K.TOKENS["JUP"]}, "round trip"),
    ({"input_mint": "SomeRandomMint"}, "allowlist"),
    ({"amount_in": 5 * K.LAMPORTS}, "max input"),
    ({"min_out": IN}, "minimum output"),
    ({"wallet_sol": IN + 10_000_000}, "SOL reserve"),
    ({"instructions": []}, "instructions"),
])
def test_bad_transactions_fail_closed(change, needle):
    with pytest.raises(S.Rejected, match=needle):
        S.inspect(good_tx(**change))


def test_instruction_level_rejections():
    for mutate, needle in [
        (lambda i: i.append({"program": "Unknown1111", "kind": "x"}), "program allowed"),
        (lambda i: i.__setitem__(3, {**i[3], "to": "AttackerWallet"}), "system transfer"),
        (lambda i: i.__setitem__(3, {**i[3], "lamports": K.MAX_TIP_LAMPORTS + 1}), "tip ceiling"),
        (lambda i: i.__setitem__(1, {**i[1], "micro_lamports": K.MAX_CU_PRICE + 1}), "fee ceiling"),
        (lambda i: i.__setitem__(2, {**i[2], "route": [K.SOL, "ScamMint", K.SOL]}), "swap route"),
        (lambda i: i.__setitem__(2, {**i[2], "accounts": [{"key": "jitodontfront1", "writable": True}]}), "read-only"),
        (lambda i: i.pop(0), "compute limit"),
    ]:
        tx = good_tx()
        mutate(tx["instructions"])
        with pytest.raises(S.Rejected, match=needle):
            S.inspect(tx)
    tx = good_tx()
    del tx["min_out"]
    with pytest.raises(S.Rejected, match="missing"):
        S.inspect(tx)


# ------------------------------------------------------------------------------------------- adapters and policy
def test_adapters_build_documented_requests_but_never_send():
    assert A.ADAPTERS["jito_bundle"].payload(["a", "b"])["method"] == "sendBundle"
    with pytest.raises(ValueError):
        A.ADAPTERS["jito_bundle"].payload(["x"] * 6)
    assert A.ADAPTERS["rpc_plain"].payload(["a"])["params"][1]["maxRetries"] == 0
    assert "bundleOnly=true" in A.ADAPTERS["jito_dontfront"].url
    for a in A.ADAPTERS.values():
        with pytest.raises(P.LiveTradingDisabled):
            a.send(["signed"])


def test_live_stays_locked_even_with_approval_and_evidence(state):
    (state / "mev").mkdir()
    (state / "mev" / "approval.json").write_text(json.dumps({f: "x" for f in P.APPROVAL_FIELDS}))
    gate = P.live_gate(evidence={"landing": "SUFFICIENT", "survival": "PROMISING_IN_SHADOW"},
                       tests_passed={t: True for t in P.ACCEPTANCE})
    assert not gate["allowed"]
    assert [c["name"] for c in gate["conditions"] if not c["ok"]] == ["A signer is configured"]


def test_hard_limits_are_independent_of_the_model():
    assert P.within_limits(IN, 0, 2 * K.LAMPORTS, 30_000) == (True, "ok")
    assert not P.within_limits(IN, K.DAILY_LOSS_LAMPORTS, 2 * K.LAMPORTS, 30_000)[0]
    assert not P.within_limits(IN, 0, IN + 1_000, 30_000)[0]


@pytest.mark.parametrize("sig,bundle_s,expired,unchanged,want", [
    ({"confirmationStatus": "confirmed", "err": None}, None, False, None, A.LANDED_OK),
    ({"confirmationStatus": "confirmed", "err": {"x": 1}}, None, False, None, A.LANDED_FAILED),
    ({"confirmationStatus": "processed", "err": None}, None, False, None, A.WAIT),
    (None, "Pending", False, None, A.WAIT),
    (None, "Landed", False, None, A.WAIT),                     # confirm on-chain before believing it
    (None, None, True, None, A.WAIT),                          # expired but wallet not checked: still unclear
    (None, None, True, False, A.WAIT),
    (None, "Failed", True, True, A.NOT_LANDED),
    (None, None, True, True, A.NOT_LANDED),
])
def test_reconciliation_never_guesses(sig, bundle_s, expired, unchanged, want):
    assert A.reconcile(sig, bundle_s, expired, unchanged) == want


# ------------------------------------------------------------------------------------------- a whole shadow session
class FakeQuotes:
    """JUP is cheap on Whirlpool and dear on Raydium CLMM at discovery; the gap closes after the first re-check."""

    def __init__(self):
        self.calls = self.refused = 0
        self.rechecks = 0

    def quote(self, i, o, amount, dex=None, paired=False):
        self.calls += 1
        if i == K.SOL:
            return {"out": amount * 3 // 10, "slot": 100 + self.calls, "at": 0, "route": [dex]}
        bonus = 300_000 if (dex == "Raydium CLMM" and self.calls < 40) else -200_000
        return {"out": amount * 10 // 3 + bonus, "slot": 100 + self.calls, "at": 0, "route": [dex]}


def test_session_records_opportunities_and_their_rechecks(monkeypatch, state):
    monkeypatch.setattr(K, "DELAYS_S", (0, 1))
    monkeypatch.setattr(clients, "priority_fees", lambda: {"p50": 10_000, "p75": 20_000, "share_paying": 0.2})
    monkeypatch.setattr(clients, "tip_floor", lambda: {"landed_tips_75th_percentile": 2e-05})
    s = shadow.session(FakeQuotes(), seconds=60, tokens={"JUP": K.TOKENS["JUP"]},
                       dexes=("Raydium CLMM", "Whirlpool"), passes=1)
    assert s["candidates"] == 2 and s["opportunities"] == 1
    c = s["new"][0]
    assert (c["buy_dex"], c["sell_dex"]) == ("Whirlpool", "Raydium CLMM") and c["submitted_at"] is None
    assert [r["delay"] for r in c["rechecks"]] == [0, 1] and all(r["out"] is not None for r in c["rechecks"])
    assert c["v"] == K.METHOD_VERSION and c["leg_gap_s"] is not None
    snap = shadow.snapshot(s["new"], [s])
    assert snap["mode"] == "SHADOW" and snap["counts"]["submitted"] == 0 and not snap["gate"]["allowed"]
    assert snap["verdict"] == V.INSUFFICIENT and snap["landing"] == V.INSUFFICIENT


def test_unpaired_opportunities_are_kept_but_not_judged():
    old = [dict(cand(i, IN + 200_000, IN + 150_000), token="USDT", buy_dex="A", sell_dex="B", gross=200_000)
           for i in range(40)]
    snap = shadow.snapshot(old, [])
    assert snap["counts"]["opportunities"] == 0 and snap["counts"]["opportunities_unpaired"] == 40
    assert snap["verdict"] == V.INSUFFICIENT


def test_a_paired_leg_skips_the_pause_but_owes_it_to_the_next_request(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(clients.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(clients.time, "sleep", lambda s: t.__setitem__(0, t[0] + s))
    monkeypatch.setattr(clients.time, "time", lambda: t[0])

    class R:
        status_code, headers = 200, {}

        def json(self):
            return {"outAmount": "5", "contextSlot": 1, "routePlan": []}

    class Sess:
        def get(self, *a, **k):
            return R()

    q = clients.Quotes(pace=1.0, session=Sess())
    q.quote("a", "b", 1)
    t0 = t[0]
    q.quote("b", "a", 1, paired=True)
    assert t[0] - t0 == pytest.approx(K.LEG_GAP_S)
    t1 = t[0]
    q.quote("a", "b", 1)
    assert t[0] - t1 == pytest.approx(2.0 - K.LEG_GAP_S)       # its own pause plus the one skipped
