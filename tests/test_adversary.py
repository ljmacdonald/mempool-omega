"""Adversary tests: a manipulator who has read our (public) code and tunes tricks to slip past the original
checks. Each test shows the evasion would beat a naive version of the defence, and that the hardened
defence still catches it. See docs/ADVERSARY.md."""
import numpy as np
import pandas as pd

from scanner import integrity as I
from scanner.defence import adaptive_penalty, declutter_exits, round_step, update_adaptive


def _book(wall_qty=None):
    bids = [(100 - i * 0.1, 5.0) for i in range(60)]
    if wall_qty:
        bids[3] = (bids[3][0], wall_qty)
        bids[4] = (bids[4][0], wall_qty)
    return {"bids": bids, "asks": [(100.1 + i * 0.1, 5.0) for i in range(60)]}


def test_spoof_wall_held_past_a_fixed_window_is_still_caught():
    """Evasion: the attacker knows we re-check after ~3 s, so keeps the wall up until then and pulls it later."""
    walled, pulled = _book(400.0), _book()
    naive = I.check_walls([walled, walled])                 # a 2-snapshot check sees a 'stable' wall
    hardened = I.check_walls([walled, walled, pulled])      # our third snapshot comes at a random later time
    assert naive["ok"] is True and hardened["ok"] is False


def test_secret_thresholds_differ_per_seed_and_stay_near_base():
    a, b = I.thresholds(1), I.thresholds(2)
    assert a != b
    for k, v in I.BASE.items():
        assert 0.85 * v <= a[k] <= 1.15 * v
    assert I.thresholds(None) == I.BASE
    g = I.snapshot_gaps(I.mulberry32(7))
    assert 2 <= g[0] <= 5 and 4 <= g[1] <= 10


def _candles(n=240, surge_qv=False, impact=1.0, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    o = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    move = rng.normal(0, 0.01, n)
    qv = rng.uniform(0.8, 1.2, n) * 1e6
    if surge_qv:
        qv[-6:] *= 6
        move[-6:] *= impact
    c = o * np.exp(move)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998, "close": c,
                         "volume": qv / c, "qv": qv, "taker_buy_volume": qv / c / 2, "n_trades": qv / 1000}, index=idx)


def test_wash_trading_with_random_sizes_and_timing_is_caught_by_price_impact():
    """Evasion: random trade sizes and timings defeat the 'identical back-and-forth' signature..."""
    rng = np.random.default_rng(3)
    trades = [{"price": 100.0, "qty": float(rng.uniform(0.1, 9)), "buyer_maker": bool(rng.random() < .5),
               "time": int(i * 1000 + rng.integers(0, 4000))} for i in range(500)]
    assert I.check_wash(trades)["ok"] is True
    # ...but 6x the usual money traded while the price barely moves cannot be disguised.
    assert I.check_impact(_candles(surge_qv=True, impact=0.05))["ok"] is False
    assert I.check_impact(_candles(surge_qv=True, impact=2.5))["ok"] is True   # real: 6x money -> ~sqrt(6)x move


def test_engineered_setup_without_lasting_trend_is_flagged():
    bait = {"volume_surge": 5.0, "buy_pressure_6b": 0.09, "ret_72b": -0.04, "ema72_dist": -0.01}
    real = {"volume_surge": 5.0, "buy_pressure_6b": 0.09, "ret_72b": 0.06, "ema72_dist": 0.02}
    assert I.check_engineered(bait)["ok"] is False and I.check_engineered(real)["ok"] is True


def test_stops_are_moved_off_round_numbers_and_recent_lows():
    entry, ru = 2.10, 0.0476                              # naive stop = 2.10 * (1 - 0.0476) = 2.0000 (round!)
    stop, target = declutter_exits(entry, entry * (1 - ru), entry * (1 + 2 * ru), np.nan, ru)
    assert stop < 2.0 * (1 - 0.0025) and stop >= entry * (1 - 1.25 * ru)
    stop2, _ = declutter_exits(10.0, 9.70, 10.6, 9.69, 0.03)   # stop sits just above the recent low
    assert stop2 < 9.69
    _, tgt = declutter_exits(1.0, 0.97, 1.4990, np.nan, 0.03)  # target a hair under 1.5: sellers queue at 1.5
    assert tgt <= 1.4990 and round_step(1.5) == 0.5


def test_bait_monitor_learns_when_our_ideas_are_used_against_users(tmp_path, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    rng = np.random.default_rng(0)
    n = 120
    pre = rng.uniform(-0.05, 0.25, n)
    net = np.where(pre > 0.15, -0.03, 0.002) + rng.normal(0, 0.005, n)   # big run-ups dump after we suggest them
    h = pd.DataFrame({"ts": [str(pd.Timestamp("2026-01-01", tz="UTC") + pd.Timedelta(hours=i)) for i in range(n)],
                      "style": "day", "symbol": "XUSDT", "grade": "Strong", "status": "closed", "outcome": "time_limit",
                      "net_ret": net, "baseline_ret": 0.0, "pre_ret": pre, "vol_surge": rng.uniform(0.5, 3, n),
                      "risk_unit": 0.03, "score": 7.0})
    (tmp_path / "suggestions").mkdir()
    h.to_csv(tmp_path / "suggestions" / "history.csv", index=False)
    adaptive = update_adaptive(min_n=30)
    assert adaptive["runup"]["penalty"] > 0 and adaptive["surge"]["penalty"] == 0
    pen, warn = adaptive_penalty({"ret_24b": 0.2, "volume_surge": 1.0}, adaptive)
    assert pen > 0 and warn
    assert adaptive_penalty({"ret_24b": 0.0, "volume_surge": 1.0}, adaptive)[0] == 0
