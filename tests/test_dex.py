"""DEX scanner: scam defences against a scammer who knows the rules, all-in costs, and JS/Python agreement."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dex import security as S
from dex.costs import dex_cost

ROOT = Path(__file__).resolve().parent.parent
RAW = json.loads((ROOT / "tests" / "fixtures" / "dex" / "raw.json").read_text())
T = S.thresholds(None)


def clean_evm() -> dict:
    """A token that passes everything (renounced, verified, sell test ok, many real sellers)."""
    f = S.empty_facts()
    f.update(goplus=True, honeypot_src=True, sim_ok=True, honeypot=False, buy_tax=0.0, sell_tax=0.0, open_source=True,
             holder_count=20000, top10_pct=0.18, creator_pct=0.0, lp_secured_pct=None)
    return f


def clean_market(**kw) -> dict:
    m = {"liq_real": 2_000_000, "age_days": 120, "active_days": 14, "buyers_h24": 900, "sellers_h24": 800,
         "buys_h24": 3000, "sells_h24": 2800, "vol_h24": 3_000_000, "liq_change_6h": 0.0, "liq_change_24h": 0.01,
         "liq_change_72h": 0.05, "boosted": False, "copycat": False, "impersonator": False, "rules_changed": False}
    m.update(kw)
    return m


def keys(a):
    return {h["key"] for h in a["hard"]}


def test_clean_token_passes():
    a = S.assess(clean_evm(), clean_market(), T, "ethereum")
    assert a["verdict"] == "pass", a["hard"]


@pytest.mark.parametrize("change,market,expect", [
    ({"sim_ok": False}, {}, "honeypot"),
    ({"honeypot": True}, {}, "honeypot"),
    # simulator whitelisted: the test sells fine but real buyers can't -> few real sellers
    ({}, {"buyers_h24": 900, "sellers_h24": 150}, "sell_block"),
    ({}, {"buyers_h24": 150, "sellers_h24": 140}, "few_traders"),
    ({"sell_tax": 0.08}, {}, "tax"),
    ({"tax_modifiable": True, "owner_active": True}, {}, "tax_modifiable"),
    ({"blacklist": True, "owner_active": True}, {}, "owner_powers"),
    ({"mintable": True, "owner_active": True}, {}, "owner_powers"),
    ({"hidden_owner": True, "owner_active": True}, {}, "hidden_owner"),
    ({"proxy": True}, {}, "upgradeable"),
    ({"open_source": False}, {}, "closed_source"),
    ({"same_creator_honeypots": 2}, {}, "serial_scammer"),
    ({"goplus": False}, {}, "unverified"),            # fail closed
    ({"honeypot_src": None}, {}, "unverified"),       # sell test missing on a chain that has one
    ({}, {"liq_real": 450_000}, "liquidity"),
    ({}, {"age_days": 6}, "young"),
    ({}, {"age_days": 400, "active_days": 3}, "revived"),          # old dormant token "revived" for a pump
    ({}, {"liq_change_6h": -0.3}, "liq_pull"),                     # rug in progress
    ({}, {"liq_change_72h": 3.5}, "liq_added"),                    # liquidity parked to pass the filter
    ({"top10_pct": 0.62}, {}, "concentrated"),
    ({"creator_pct": 0.3}, {}, "creator"),
    ({"lp_top_unlocked_eoa_pct": 0.8, "lp_secured_pct": 0.1}, {}, "lp_owner"),
    ({"lp_unlock_soon": True}, {}, "lp_unlock"),
    ({"insiders": 40}, {}, "insiders"),
    ({}, {"copycat": True}, "copycat"),
    ({}, {"rules_changed": True}, "rules_changed"),
])
def test_each_scammer_trick_is_rejected(change, market, expect):
    f = clean_evm()
    f.update(change)
    a = S.assess(f, clean_market(**market), T, "bsc")
    assert a["verdict"] == "reject" and expect in keys(a), (expect, a["hard"])


def test_no_simulation_means_stricter_seller_rules():
    """A scammer may pick a pool type the simulator can't test; then more real sellers are required."""
    f = S.add_honeypot_is(clean_evm(), {"_unsupported": True})
    f["sim_ok"] = None
    assert f["honeypot_src"] == "unsupported"
    assert S.assess(f, clean_market(sellers_h24=150, buyers_h24=300), T, "bsc")["verdict"] == "reject"
    assert S.assess(f, clean_market(sellers_h24=400, buyers_h24=800), T, "bsc")["verdict"] == "pass"
    assert S.assess(clean_evm(), clean_market(sellers_h24=150, buyers_h24=300), T, "bsc")["verdict"] == "pass"


def test_ethereum_needs_fewer_traders_because_each_trade_costs_gas():
    m = clean_market(buyers_h24=166, sellers_h24=242)      # PEPE's main pool on a real day
    assert S.assess(clean_evm(), m, T, "ethereum")["verdict"] == "pass"
    assert "few_traders" in keys(S.assess(clean_evm(), m, T, "bsc"))


def test_powers_are_harmless_once_ownership_is_renounced():
    """PEPE-like: blacklist/pause code exists but nobody owns the contract any more."""
    f = S.from_goplus_evm(RAW["goplus_evm_pepe"], [], 0)
    f = S.add_honeypot_is(f, RAW["honeypot_pepe"])
    assert f["blacklist"] and f["pausable"] and not f["owner_active"]
    a = S.assess(f, clean_market(), T, "ethereum")
    assert not keys(a) & {"owner_powers", "tax_modifiable", "honeypot"}


@pytest.mark.parametrize("change,expect", [
    ({"freeze_authority": True}, "authorities"), ({"mint_authority": True}, "authorities"),
    ({"permanent_delegate": True}, "authorities"), ({"transfer_hook": True}, "authorities"),
    ({"rugcheck_src": None}, "unverified"), ({"rugged": True}, "rugged"),
])
def test_solana_specific_traps(change, expect):
    f = S.from_goplus_sol(RAW["goplus_sol_jup"])
    f = S.add_rugcheck(f, RAW["rugcheck_jup"])
    assert S.assess(f, clean_market(), T, "solana")["verdict"] == "pass"
    f.update(change)
    assert expect in keys(S.assess(f, clean_market(), T, "solana"))


def test_cheap_fake_signals_cost_points():
    base = S.assess(clean_evm(), clean_market(), T, "ethereum")["penalty"]
    for m in ({"boosted": True}, {"buys_h24": 30000, "sells_h24": 30000}, {"vol_h24": 60_000_000},
              {"liq_change_72h": 1.5}):
        a = S.assess(clean_evm(), clean_market(**m), T, "ethereum")
        assert a["verdict"] == "pass" and a["penalty"] > base, m


def test_higher_risk_profile_loosens_size_and_age_but_never_the_scam_tests():
    young = clean_market(liq_real=150_000, age_days=6, active_days=5, buyers_h24=150, sellers_h24=110)
    assert S.assess(clean_evm(), young, T, "bsc")["verdict"] == "reject"
    assert S.assess(clean_evm(), young, S.thresholds(None, "risky"), "bsc")["verdict"] == "pass"
    for change in ({"sim_ok": False}, {"tax_modifiable": True, "owner_active": True}, {"proxy": True},
                   {"mintable": True, "owner_active": True}, {"goplus": False}):
        f = clean_evm()
        f.update(change)
        assert S.assess(f, young, S.thresholds(None, "risky"), "bsc")["verdict"] == "reject", change
    for k in S.BASE:      # nothing about scams differs between the two profiles
        if k not in ("min_liq", "min_age_days", "min_active_days", "min_buyers", "min_sellers", "min_holders", "max_top10"):
            assert S.RISKY[k] == S.BASE[k], k


def test_secret_limits_only_ever_get_stricter():
    for seed in range(300):
        t = S.thresholds(seed)
        for k, v in S.BASE.items():
            assert (v <= t[k] + 1e-9) if k.startswith("min_") else (t[k] <= v + 1e-9), (seed, k)
    assert S.thresholds(1)["min_liq"] != S.thresholds(2)["min_liq"]


def test_fingerprint_changes_when_rules_change():
    f = clean_evm()
    g = dict(f, sell_tax=0.04)
    assert S.fingerprint(f) != S.fingerprint(g)


def test_costs_add_up_and_break_even_nets_zero():
    c = dex_cost(500, 2.0, 1_000_000, 0.003, 0.01, 0.02, 1.0, 0.005, 0.005)
    assert abs(c["at"](c["break_even"])["net"]) < 1e-6
    assert c["at"](2.0)["net"] < 0 and c["break_even"] > 2.0
    k = c["at"](2.4)["costs"]
    assert all(v >= 0 for v in k.values()) and k["tax"] > 0 and k["sniper"] > 0
    # bigger orders in the same pool pay more impact
    assert dex_cost(50_000, 2, 1e6, .003, 0, 0, 1, .005, .005)["round_trip"] > c["round_trip"] - 0.03


def test_sandwich_bots_only_attack_swaps_worth_attacking():
    from dex.costs import mev_share

    small = dex_cost(100, 1.0, 5_000_000, 0.003, 0, 0, 0.1, 0.005, 0.0)        # $100 in a deep pool: not worth it
    big = dex_cost(50_000, 1.0, 5_000_000, 0.003, 0, 0, 0.1, 0.005, 0.0)      # $50k: fully worth sandwiching
    assert small["at"](1.0)["costs"]["mev"] == 0 and big["at"](1.0)["costs"]["mev"] > 0
    assert mev_share(1000, 0.0001, 1e6) == 1.0          # very low-fee pools are cheap to attack
    protected = dex_cost(50_000, 1.0, 5_000_000, 0.003, 0, 0, 0.1, 0.0, 0.0)
    assert protected["at"](1.1)["net"] > big["at"](1.1)["net"]
    assert abs(big["at"](big["break_even"])["net"]) < 1e-6


def test_discovery_filters_fake_liquidity_impersonators_and_copycats(monkeypatch):
    from dex import live

    def pool(chain, token, sym, quote, reserve, name=None, buyers=900):
        return {"chain": chain, "pool": "P" + token, "dex": "uniswap_v2", "name": f"{sym} / X", "base_symbol": sym,
                "base_name": name or sym, "token": token, "quote_symbol": "WETH", "quote": quote, "price": 1.0,
                "quote_price": 3000.0, "reserve_usd": reserve, "created": "2025-01-01T00:00:00Z", "fee_pct": float("nan"),
                "tx_h24": {"buys": 3000, "sells": 2900, "buyers": buyers, "sellers": 800}, "tx_h1": {}, "vol_h24": 1e6}

    weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    fake_quote = "0xscammerstoken"
    pools = [pool("ethereum", "0xgood", "GOOD", weth, 3e6), pool("ethereum", "0xfakeliq", "RICH", fake_quote, 7e8),
             pool("ethereum", "0xusdc", "USDC", weth, 2e6), pool("ethereum", "0xcopy", "GOOD", weth, 6e5)]
    monkeypatch.setattr(live, "gt_pools", lambda chain, sort, pages, dex=None: pools if chain == "ethereum" else [])
    monkeypatch.setattr(live, "ds_boosted", lambda: set())
    cands, rejected, _ = live.discover()
    by = {c["token"]: c for c in cands}
    assert "0xfakeliq" not in by                       # paired with the scammer's own token: never counted
    assert "0xusdc" not in by and any("major coin" in " ".join(r["reasons"]) for r in rejected)
    assert by["0xgood"]["copycat"] is False and by["0xcopy"]["copycat"] is True


def test_runup_and_sniper_learning(tmp_path, monkeypatch):
    from core import config
    from dex import improve

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    n = 120
    h = pd.DataFrame({"ts": [str(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i)) for i in range(n)],
                      "style": "dex_short", "symbol": [f"solana:T{i}" for i in range(n)], "status": "closed",
                      "net_ret": 0.0, "baseline_ret": 0.0, "first_hour_runup": 0.03, "first_hour_runup_base": 0.01})
    (tmp_path / "dex").mkdir()
    h.to_csv(tmp_path / "dex" / "history.csv", index=False)
    s = improve.sniper_costs(h)
    assert s["solana"] > 0.005 and s["ethereum"] == 0.005      # learned only where there is evidence


# ------------------------------------------------------------------------------- JavaScript agreement
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_dex_engine_matches_python(tmp_path):
    rng = np.random.default_rng(3)
    cases = []
    base_f = [S.add_honeypot_is(S.from_goplus_evm(RAW["goplus_evm_pepe"], [], 1.7e9), RAW["honeypot_pepe"]),
              S.from_goplus_evm(RAW["goplus_evm_robinhood"], [], 1.7e9),
              S.add_rugcheck(S.from_goplus_sol(RAW["goplus_sol_jup"]), RAW["rugcheck_jup"])]
    flags = [k for k, v in S.empty_facts().items() if isinstance(v, bool)]
    for i in range(400):
        f = dict(base_f[i % 3])
        for k in rng.choice(flags, size=rng.integers(0, 3), replace=False):
            f[str(k)] = bool(rng.integers(0, 2))
        f["top10_pct"] = float(rng.uniform(0, 0.8))
        f["sell_tax"] = float(rng.choice([0, 0.01, 0.06]))
        m = clean_market(liq_real=float(rng.uniform(3e5, 5e6)), age_days=float(rng.uniform(2, 300)),
                         active_days=int(rng.integers(0, 15)), sellers_h24=int(rng.integers(0, 900)),
                         liq_change_6h=float(rng.uniform(-0.4, 0.1)), liq_change_72h=float(rng.uniform(-0.2, 4)),
                         boosted=bool(rng.integers(0, 2)), buys_h24=int(rng.integers(100, 30000)))
        seed = int(rng.integers(0, 2**32))
        chain = ["ethereum", "robinhood", "solana"][i % 3]
        prof = "risky" if i % 5 == 0 else "standard"
        a = S.assess(f, m, S.thresholds(seed, prof), chain)
        cases.append({"f": f, "m": m, "seed": seed, "prof": prof, "chain": chain, "hard": sorted(keys(a)), "pen": a["penalty"],
                      "checks": [[c_["key"], c_["ok"]] for c_ in a["checks"]]})
    norm = {"pepe": S.add_honeypot_is(S.from_goplus_evm(RAW["goplus_evm_pepe"], [], 1.7e9), RAW["honeypot_pepe"]),
            "rh": S.from_goplus_evm(RAW["goplus_evm_robinhood"], [], 1.7e9),
            "jup": S.add_rugcheck(S.from_goplus_sol(RAW["goplus_sol_jup"]), RAW["rugcheck_jup"])}
    costs = []
    for _ in range(200):
        args = [float(rng.uniform(10, 20000)), float(rng.uniform(1e-6, 50)), float(rng.uniform(1e5, 2e7)),
                float(rng.choice([0.0001, 0.0025, 0.003, 0.01])), float(rng.choice([0, 0.02])),
                float(rng.choice([0, 0.03])), float(rng.uniform(0.01, 5)), float(rng.choice([0, 0.002, 0.005])),
                float(rng.uniform(0, 0.02))]
        c = dex_cost(*args)
        costs.append({"args": args, "rt": c["round_trip"], "be": c["break_even"] if np.isfinite(c["break_even"]) else None,
                      "net": c["at"](args[1] * 1.1)["net"]})
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"cases": cases, "raw": RAW, "norm": norm, "costs": costs,
                              "thresholds": {f"{s}|{pr}": S.thresholds(s, pr) for s in (0, 1, 99, 4294967295)
                                             for pr in ("standard", "risky")}}))
    js = """
const X = require(process.argv[1]); const fx = require(process.argv[2]); let bad = [];
const near = (a, b) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
for (const [s, t] of Object.entries(fx.thresholds)) { const [seed, prof] = s.split('|'); const j = X.thresholds(+seed, prof); for (const k of Object.keys(t)) if (typeof t[k] === 'number' ? !near(j[k], t[k]) : j[k] !== t[k]) bad.push(['th', s, k]); }
const raw = fx.raw;
const js = { pepe: X.addHoneypotIs(X.fromGoplusEvm(raw.goplus_evm_pepe, [], 1.7e9), raw.honeypot_pepe),
  rh: X.fromGoplusEvm(raw.goplus_evm_robinhood, [], 1.7e9), jup: X.addRugcheck(X.fromGoplusSol(raw.goplus_sol_jup), raw.rugcheck_jup) };
for (const [n, f] of Object.entries(fx.norm)) for (const k of Object.keys(f)) {
  const a = js[n][k], b = f[k];
  if (typeof b === 'number' ? !near(a, b) : a !== b) bad.push(['norm', n, k, a, b]); }
for (const c of fx.cases) {
  const a = X.assess(c.f, c.m, X.thresholds(c.seed, c.prof), c.chain);
  const hard = [...new Set(a.hard.map((h) => h.key))].sort();
  if (JSON.stringify(hard) !== JSON.stringify([...new Set(c.hard)].sort())) bad.push(['hard', hard, c.hard]);
  if (!near(a.penalty, c.pen)) bad.push(['pen', a.penalty, c.pen]);
  if (JSON.stringify(a.checks.map((x) => [x.key, x.ok])) !== JSON.stringify(c.checks)) bad.push(['checks', a.checks.map((x) => [x.key, x.ok]), c.checks]);
}
for (const c of fx.costs) { const r = X.dexCost(...c.args);
  if (!near(r.round_trip, c.rt) || !(near(r.break_even, c.be) || (!isFinite(r.break_even) && c.be === null)) || !near(r.at(c.args[1] * 1.1).net, c.net)) bad.push(['cost', c.args]); }
console.log(JSON.stringify(bad.slice(0, 5)));"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "site" / "dexengine.js"), str(fx)], capture_output=True,
                         text=True, timeout=120)
    assert out.stdout.strip() == "[]", (out.stdout[:2000], out.stderr[:2000])


def test_candle_store_downloads_history_once_then_only_tops_up(tmp_path, monkeypatch):
    from dex import cache

    monkeypatch.setenv("OMEGA_DEX_CACHE", str(tmp_path))
    end = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
    calls = []

    def fake_fetch(chain, pool, n):
        calls.append(n)
        idx = pd.date_range(end=end, periods=n, freq="1h")
        return pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "qv": 1e4, "volume": 1e4,
                             "taker_buy_volume": 5e3}, index=idx)

    a = cache.candles("solana", "POOL", 300, fake_fetch)
    assert len(a) == 300 and calls == [cache.FULL_FETCH]
    b = cache.candles("solana", "POOL", 300, fake_fetch)          # nothing new: no request at all
    assert len(b) == 300 and len(calls) == 1
    old = cache.load("solana", "POOL")
    old.iloc[:-5].to_parquet(cache._path("solana", "POOL"))       # 5 hours behind
    cache.candles("solana", "POOL", 300, fake_fetch)
    assert calls[-1] < 10                                         # only the missing candles
    assert "solana:POOL" in cache.all_cached()


def test_snapshot_json_never_contains_nan():
    from dex.live import clean

    out = json.dumps(clean({"a": float("nan"), "b": [np.float64("inf"), np.int64(3)], "c": {"d": np.bool_(True)}}),
                     allow_nan=False)
    assert out == '{"a": null, "b": [null, 3], "c": {"d": true}}'


def test_discovery_survives_a_token_whose_pools_carry_different_names(monkeypatch):
    """A token's pools can be labelled differently ("Jupiter USD" vs "JupUSD"); the copycat check must read names
    from the same pool it ranks by (crashed the hourly scan with KeyError before)."""
    from dex import live

    weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"

    def pool(pid, token, sym, name, reserve):
        return {"chain": "ethereum", "pool": pid, "dex": "uniswap_v2", "name": f"{sym} / WETH", "base_symbol": sym,
                "base_name": name, "token": token, "quote_symbol": "WETH", "quote": weth, "price": 1.0,
                "quote_price": 3000.0, "reserve_usd": reserve, "created": "2025-01-01T00:00:00Z", "fee_pct": float("nan"),
                "tx_h24": {"buys": 3000, "sells": 2900, "buyers": 900, "sellers": 800}, "tx_h1": {}, "vol_h24": 1e6}

    pools = [pool("P1", "0xjup", "JUPUSD", "JupUSD", 1e5), pool("P2", "0xjup", "JUPUSD", "Jupiter USD", 2e6)]
    monkeypatch.setattr(live, "gt_pools", lambda chain, sort, pages, dex=None: pools if chain == "ethereum" else [])
    monkeypatch.setattr(live, "ds_boosted", lambda: set())
    cands, _, _ = live.discover()
    assert [c["copycat"] for c in cands if c["token"] == "0xjup"] == [False]
