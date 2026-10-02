import numpy as np

from asi.anomaly import benford_score, cusum_score, hurst_rs, round_size_share
from asi.redteam import ATTACKS, apply_attack
from asi.trust import TrustContext, compute_trust, manipulation_premium_bps
from asi.wallets import build_graph, graph_cleanliness, wash_cycle_share
from core.synthetic import synthetic_trade_sizes, synthetic_transfers
from features.engine import build_features


def test_benford_flags_wash_sizes():
    assert benford_score(synthetic_trade_sizes(3000, wash=False)) > benford_score(synthetic_trade_sizes(3000, wash=True))
    assert round_size_share(np.array([1.0, 0.5, 2.0])) == 1.0


def test_hurst_random_walk_increments():
    h = hurst_rs(np.random.default_rng(0).normal(size=4000))
    assert 0.35 < h < 0.65


def test_cusum_detects_shift():
    import pandas as pd

    x = pd.Series(np.r_[np.random.default_rng(1).normal(0, 1, 300), np.random.default_rng(2).normal(4, 1, 10)])
    assert cusum_score(x) == 1.0


def test_wallet_wash_ring_lowers_cleanliness():
    clean = synthetic_transfers(300, seed=1)
    dirty = synthetic_transfers(300, seed=1, wash_ring=True)
    assert wash_cycle_share(build_graph(dirty)) > wash_cycle_share(build_graph(clean))
    assert graph_cleanliness(dirty) < graph_cleanliness(clean)


def test_premium_monotone():
    assert manipulation_premium_bps(0.2) > manipulation_premium_bps(0.8) > 0


def test_trust_in_unit_interval(raw):
    f = build_features(raw)
    t, diag = compute_trust(f, TrustContext())
    v = t.to_numpy()
    v = v[np.isfinite(v)]
    assert v.min() >= 0 and v.max() <= 1
    assert "consensus" in diag


def test_spoof_attack_lowers_book_trust(raw):
    att, pos = apply_attack(raw, "spoofing", rate=0.02, seed=0)
    f0, f1 = build_features(raw), build_features(att)
    t0, _ = compute_trust(f0)
    t1, _ = compute_trust(f1)
    ts = att.index[pos]
    assert t1.loc[ts, "ofi_book"].mean() < t0.loc[ts, "ofi_book"].mean() - 0.2


def test_all_attacks_apply(raw):
    for name in ATTACKS:
        att, pos = apply_attack(raw, name, rate=0.01, seed=1)
        assert len(pos) > 0 and len(att) == len(raw)
        assert (att["high"] >= att["low"]).all()
