"""Shadow test of the Small-coin-trained Day model (D107)."""
import json

import numpy as np
import pandas as pd
import pytest

from core import config as core_config
from scanner import shadow


def _candles(n=700, seed=0, drift=0.0, vol=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = 10 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, vol / 2, n)))
    lo = np.minimum(o, c) * (1 - abs(rng.normal(0, vol / 2, n)))
    v = rng.gamma(3, 100, n)
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v, "qv": v * c,
                         "taker_buy_volume": v * rng.uniform(0.4, 0.6, n)}, index=idx)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(core_config, "STATE_DIR", tmp_path)
    (tmp_path / "suggestions").mkdir()
    monkeypatch.setattr(shadow, "big_movers", lambda: {})
    return tmp_path


def market(n=700):
    """Prices up to the last full hour, so ideas aren't treated as too old to settle."""
    c = {f"C{i}USDT": _candles(n=n, seed=i, drift=0.0005 * (i % 3 - 1)) for i in range(8)}
    c["BTCUSDT"] = _candles(n=n, seed=99)
    idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1), periods=n, freq="1h")
    return {s: df.set_axis(idx) for s, df in c.items()}


def universe(c):
    syms = [s for s in c if s != "BTCUSDT"]
    return pd.DataFrame({"symbol": syms, "quoteVolume": [5e6] * len(syms)})


def test_trains_on_first_use_records_its_own_top_10_and_settles_them(state, monkeypatch):
    full = market()
    monkeypatch.setattr(shadow, "load_all", lambda syms, n, interval: {s: full[s] for s in syms if s in full})
    early = {s: df.iloc[:640] for s, df in full.items()}
    out = shadow.run(early, universe(full))
    assert (state / "models" / "small_shadow" / "scanner_day_model.txt").exists()
    h = pd.read_csv(state / shadow.HIST)
    assert len(h) == 8 and (h["status"] == "open").all() and h["style"].eq("day").all()   # all 8 coins rank (top 10)
    assert out["verdict"] == "early" and out["challenger"]["n"] == 0
    shadow.run(full, universe(full))                       # 60 hours later: the 24-hour ideas have finished
    h = pd.read_csv(state / shadow.HIST)
    closed = h[h["status"] == "closed"]
    assert len(closed) == 8 and closed["baseline_ret"].notna().all() and (h["status"] == "open").sum() == 8
    board = json.loads((state / shadow.BOARD).read_text())
    assert board["challenger"]["n"] == 8 and board["since"] == h["ts"].min() and board["model"]["config"] == "small_shadow"


def rows(n, ret, base, ts="2026-10-16 00:00:00+00:00", style="day"):
    return pd.DataFrame({"ts": [ts] * n, "style": style, "symbol": [f"S{i}USDT" for i in range(n)], "status": "closed",
                         "net_ret": ret, "baseline_ret": base, "grade": "Strong", "score": 7.0, "outcome": "time_limit"})


@pytest.mark.parametrize("ch,live,want", [
    ((0.004, 0.0), (0.001, 0.0), "challenger_better"),
    ((0.001, 0.0), (0.004, 0.0), "no_better"),
    ((-0.002, -0.001), (-0.004, -0.001), "no_better"),       # better than live, but no better than random picks
])
def test_verdict_needs_enough_ideas_and_must_beat_live_and_random(state, ch, live, want):
    rows(shadow.SWITCH_N, *ch).to_csv(state / shadow.HIST, index=False)
    pd.concat([rows(shadow.SWITCH_N, *live), rows(5, 0.5, 0.0, ts="2026-10-01 00:00:00+00:00"),   # before the test began
               rows(5, 0.5, 0.0, style="quick")]).to_csv(state / shadow.LIVE_HIST, index=False)
    out = shadow.compare()
    assert out["verdict"] == want and out["live"]["n"] == shadow.SWITCH_N
    rows(shadow.SWITCH_N - 1, *ch).to_csv(state / shadow.HIST, index=False)
    assert shadow.compare()["verdict"] == "early"
