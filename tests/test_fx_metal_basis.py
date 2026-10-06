"""Gold and silver live prices: the gap between the futures the ideas use and the round-the-clock contracts the page
reads is measured on the same finished candles, and an implausible gap is dropped."""
import pandas as pd

from fx import live


def frame(closes):
    idx = pd.date_range("2026-10-06 12:00", periods=len(closes), freq="15min", tz="UTC")
    return pd.DataFrame({"close": closes}, index=idx)


def test_basis_from_matching_finished_candles():
    fut = frame([61.6, 61.7, 61.8, 61.9, 62.0, 62.1, 99.0])          # the last candle is still forming: ignored
    perp = fut["close"] / 1.004
    out = live.metal_basis({"SI=F": fut}, perp_closes=lambda c: perp if c == "XAG_USDT" else perp.iloc[:0])
    assert abs(out["XAGUSD"]["basis"] - 1.004) < 1e-9
    assert "XAUUSD" not in out                                         # no gold data: no gap, so no live gold price


def test_implausible_gap_is_dropped():
    fut = frame([60.0] * 8)
    perp = fut["close"] / 1.10
    assert live.metal_basis({"SI=F": fut}, perp_closes=lambda c: perp) == {}
