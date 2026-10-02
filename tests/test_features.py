import numpy as np
import pandas as pd

from core.schema import ALL_RAW
from features.engine import FEATURE_NAMES, build_features
from features.hawkes import HawkesParams, fit_hawkes, intensity_from_counts
from features.microstructure import book_features, book_ofi, depth_persistence, trade_ofi


def test_synthetic_has_all_columns(raw):
    assert set(ALL_RAW) <= set(raw.columns)
    assert raw.index.is_monotonic_increasing and raw.index.tz is not None


def test_feature_count_and_names(raw):
    f = build_features(raw)
    assert list(f.columns) == FEATURE_NAMES
    assert len(FEATURE_NAMES) >= 20


def test_no_lookahead(raw):
    """Changing the FUTURE must not change features up to t."""
    t = 1800
    f1 = build_features(raw)
    mod = raw.copy()
    mod.iloc[t + 1:, mod.columns.get_loc("close")] *= 1.2
    mod.iloc[t + 1:, mod.columns.get_loc("volume")] *= 3
    f2 = build_features(mod)
    pd.testing.assert_frame_equal(f1.iloc[: t + 1], f2.iloc[: t + 1])


def test_trade_ofi_bounds():
    v = pd.Series([10.0, 10.0, 0.0])
    b = pd.Series([10.0, 0.0, 0.0])
    o = trade_ofi(v, b)
    assert o.iloc[0] == 1 and o.iloc[1] == -1 and np.isnan(o.iloc[2])


def _book(bid, ask, qb=1.0, qa=1.0, ts=0.0):
    return {"ts": ts, "bids": [(bid - i, qb) for i in range(5)], "asks": [(ask + i, qa) for i in range(5)]}


def test_book_ofi_sign():
    up = [_book(100 + i, 101 + i, ts=i) for i in range(10)]
    assert book_ofi(up) > 0
    down = [_book(100 - i, 101 - i, ts=i) for i in range(10)]
    assert book_ofi(down) < 0


def test_depth_persistence_detects_pulled_walls():
    honest, spoof = [], []
    bids = [(99.0 - i, 1.0) for i in range(5)]
    for i in range(10):
        asks = [(101.0 - 0.1 * i + j, 1.0) for j in range(5)]  # price drifts down toward the bids
        honest.append({"ts": i, "bids": bids, "asks": asks})
        # spoof walls flicker: placed, then pulled as soon as price leans on them
        spoof.append({"ts": i, "bids": bids if i % 2 == 0 else [(p, 0.01) for p, _ in bids], "asks": asks})
    assert depth_persistence(honest, lag=1) > 0.9 > 0.1 > depth_persistence(spoof, lag=1)
    feats = book_features(honest)
    assert {"bid_depth", "ask_depth", "book_ofi", "spread_bps"} <= set(feats)


def test_hawkes_fit_recovers_excitation():
    from core.synthetic import _hawkes_events

    counts = _hawkes_events(6000, mu=0.1, alpha=0.5, beta=1.0, rng=np.random.default_rng(0))
    p = fit_hawkes(counts)
    assert 0.2 < p.alpha < 0.8 and p.branching_ratio < 1
    lam = intensity_from_counts(counts, p)
    assert lam.shape == counts.shape and (lam > 0).all()
    assert HawkesParams(0.1, 0.0, 1.0).branching_ratio == 0
