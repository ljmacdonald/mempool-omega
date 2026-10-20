"""Engine A (Hayes Recovery, D121): distress, regime, gates, supply, quality, confirmation. All data here is a labelled
simulated fixture and can never reach a live table."""
from datetime import date, timedelta

import pytest

from hayes import PAPER_TRADING
from hayes import regime as RG
from hayes import scoring as SC

D0 = date(2025, 1, 1)


def candles(path, start=D0, vol=6e6):
    """Simulated candles from a close path (high/low 1% around the close)."""
    return [{"d": (start + timedelta(days=i)).isoformat(), "o": p, "h": p * 1.01, "l": p * 0.99, "c": p, "v": vol} for i, p in enumerate(path)]


def test_paper_trading_is_hard_coded():
    assert PAPER_TRADING is True


# ------------------------------------------------------------------ distress
def crash_path(drop=0.85, rebound=0.2, trough_age=40):
    """300 flat days, a 100-day fall of `drop`, then `trough_age` days climbing `rebound` off the low."""
    pre = [100.0] * 300
    fall = [100 * (1 - drop * k / 100) for k in range(1, 101)]
    low = fall[-1]
    up = [low * (1 + rebound * k / trough_age) for k in range(1, trough_age + 1)]
    return pre + fall + up


def test_distress_band_uses_the_trough_not_todays_price():
    d = SC.distress(candles(crash_path(drop=0.85, rebound=0.4)))
    assert d["pass"], d["codes"]
    assert d["trough_dd"] == pytest.approx(0.15 * 0.99 / 1.01 - 1, rel=1e-3)
    assert d["recovery"] == pytest.approx(1.4 / 0.99 - 1, rel=1e-6) and d["trough_age"] == 40


@pytest.mark.parametrize("kw,code", [({"drop": 0.5}, "OUT_OF_BAND"), ({"drop": 0.97}, "OUT_OF_BAND"),
                                     ({"rebound": 1.0}, "RECOVERED_TOO_FAR"), ({"trough_age": 5}, "TROUGH_AGE")])
def test_distress_rejections(kw, code):
    assert code in SC.distress(candles(crash_path(**kw)))["codes"]


def test_distress_needs_complete_candles():
    c = candles(crash_path())
    del c[320:330]                                         # ten missing days after the high
    assert "CANDLE_GAP" in SC.distress(c)["codes"]


# ------------------------------------------------------------------ regime
def test_regime_units_and_publication_dates():
    walcl = RG.Series([("2025-03-05", 7_000_000), ("2025-06-04", 7_500_000)], 1)             # millions
    rrp = RG.Series([("2025-03-05", 100.0), ("2025-06-04", 100.0)], 1, RG.UNIT_TO_MILLIONS["RRPONTSYD"])   # billions
    tga = RG.Series([("2025-03-05", 800_000), ("2025-06-04", 800_000)], 1)
    nl, _ = RG.net_liquidity(walcl, rrp, tga, date(2025, 6, 5))
    assert nl == 7_500_000 - 100_000 - 800_000                                             # 100 billion = 100,000 million
    assert walcl.as_of(date(2025, 6, 4))[0] == 7_000_000                                   # not published until the next day
    assert walcl.as_of(date(2025, 6, 5))[0] == 7_500_000


def test_regime_states_and_stale_inputs():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(400)]
    rising = RG.Series([(d, 1000 + i) for i, d in enumerate(days)], 1)
    flat0 = RG.Series([(d, 0.0) for d in days], 1)
    btc_up = {d: 100 + i for i, d in enumerate(days)}
    on = RG.classify(date(2026, 1, 20), rising, flat0, flat0, rising, btc_up)
    assert on["state"] == "risk_on" and on["confirmations"] == 3
    btc_down = {d: 500 - i for i, d in enumerate(days)}
    assert RG.classify(date(2026, 1, 20), rising, flat0, flat0, rising, btc_down)["state"] == "neutral"
    old = RG.Series([(d, 1000 + i) for i, d in enumerate(days[:300])], 1)                   # stopped updating
    st = RG.classify(date(2026, 1, 20), old, flat0, flat0, rising, btc_up)
    assert st["state"] == "risk_off" and "WALCL" in st["stale"]


def test_regime_never_sees_the_future():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(400)]
    s = RG.Series([(d, 1.0) for d in days], 1)
    btc = {d: 100.0 for d in days}
    a = RG.classify(date(2025, 10, 1), s, s, s, s, btc)
    planted = dict(btc)
    planted.update({d: 1e9 for d in days if d > "2025-10-01"})                              # a planted future
    b = RG.classify(date(2025, 10, 1), s, s, s, s, planted)
    assert a == b


# ------------------------------------------------------------------ gates
GOOD = {"usdc_market": True, "history_days": 700, "is_stable_or_wrapped": False, "adv30": 6e6, "spread_bps": 10, "depth_2pct": 1e5,
        "circulating": 80, "max_supply": 100, "fdv": 120, "mcap": 100, "stale": False}


@pytest.mark.parametrize("change,code", [({"usdc_market": False}, "U1_MARKET"), ({"history_days": 300}, "U2_HISTORY"),
                                         ({"is_stable_or_wrapped": True}, "U3_TYPE"), ({"adv30": 1e6}, "U4_LIQUIDITY"),
                                         ({"spread_bps": None}, "U4_LIQUIDITY"), ({"circulating": 20}, "U5_DILUTION"),
                                         ({"fdv": 400}, "U5_DILUTION"), ({"stale": True}, "U6_FRESH"), ({"exploit_90d": True}, "U7_SECURITY"),
                                         ({"delisting": True}, "U8_DELIST"), ({"supply_codes": ["U10_SUPPLY_HARD_BLOCK"]}, "U10_SUPPLY"),
                                         ({"quality_codes": ["QUALITY_BELOW_MIN"]}, "U11_QUALITY_MIN")])
def test_each_gate_alone(change, code):
    assert SC.gates(GOOD) == []
    assert SC.gates({**GOOD, **change}) == [code]


def test_unknown_facts_fail_their_gate():
    assert "U3_TYPE" in SC.gates({**GOOD, "is_stable_or_wrapped": None})
    assert "U2_HISTORY" in SC.gates({**GOOD, "history_days": None})


# ------------------------------------------------------------------ supply pressure
def test_supply_cliff_linear_and_missing():
    today = "2026-01-01"
    cliff = [{"day": "2026-01-15", "qty": 1_000_000, "recipient": "investors"}]
    s = SC.supply_pressure(cliff, 1.0, 100_000, 100_000_000, today)
    assert s["a30"] == pytest.approx(5.0) and "U10_SUPPLY_HARD_BLOCK" in s["codes"]          # 1M x 50% / 100k a day
    linear = [{"day": (date(2026, 1, 1) + timedelta(days=k)).isoformat(), "qty": 1000, "recipient": "team"} for k in range(1, 91)]
    s2 = SC.supply_pressure(linear, 1.0, 100_000, 100_000_000, today)
    assert s2["a30"] == pytest.approx((30 * 1000 * 0.4 + 0.5 * 60 * 1000 * 0.4) / 100_000) and s2["codes"] == []
    assert SC.supply_pressure(None, 1.0, 1e5, 1e8, today)["codes"] == ["SCHEDULE_UNKNOWN"]
    big = [{"day": "2026-01-10", "qty": 6_000_000, "recipient": "treasury"}]
    assert "U10_SUPPLY_HARD_BLOCK" in SC.supply_pressure(big, 1.0, 1e9, 100_000_000, today)["codes"]   # 6% of float in 30 days


# ------------------------------------------------------------------ quality
def test_quality_rescales_missing_weights_and_defers():
    q = SC.quality(0.3, 0.8, 0.2, 50_000, 1.2, None, 5e6)
    assert q["score"] is not None and q["missing_share"] == pytest.approx(0.10) and "QUALITY_DEFERRED_MISSING" not in q["codes"]
    thin = SC.quality(0.3, 0.8, None, None, None, None, 5e6)
    assert "QUALITY_DEFERRED_MISSING" in thin["codes"]
    assert "FEES_TOO_LOW_OR_UNKNOWN" in SC.quality(0.3, 0.8, 0.2, 1, 1, 12, 500_000)["codes"]
    assert q["value_accrual"] is True and SC.quality(0.3, 0.8, 0.2, 0.0, 1, 12, 5e6)["value_accrual"] is False


# ------------------------------------------------------------------ confirmation
def test_confirmation_components_and_missing_perps():
    path = crash_path(drop=0.85, rebound=0.0, trough_age=40)[:-40]
    low = path[-1]
    tail = [low * 1.2, low * 1.1, low * 1.05, low * 1.15, low * 1.3, low * 1.2, low * 1.1, low * 1.08, low * 1.25, low * 1.4] * 3 + [low * 1.6]
    c = candles(path + tail, vol=6e6)
    for x in c[-7:]:
        x["v"] = 12e6                                      # volume doubled in the last week
    btc = {x["d"]: 100.0 for x in c}
    d = SC.distress(c)
    cf = SC.confirmation(c, btc, d["L_day"], d["L"])
    assert cf["components"]["higher_low"] and cf["components"]["structure_break"] and cf["components"]["volume_expansion"]
    assert not cf["perps"] and "derivatives_reset" not in cf["components"]
    assert cf["score"] == pytest.approx(100 * (20 + 20 + 20 + 15) / 75)                     # weights rescaled without perps
    with_perps = SC.confirmation(c, btc, d["L_day"], d["L"], {"oi_off_peak": 0.5, "funding": -0.0001, "spot_share_rising": False})
    assert with_perps["components"]["derivatives_reset"] and with_perps["score"] == pytest.approx(90.0)
