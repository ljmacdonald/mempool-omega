"""Prediction markets (paper): the rule picks favourites at 55-70c bought at the real ask, 1-7 days before the
scheduled end, liquid only, one per event; trades settle after fees; the label needs 50 settled trades and an edge."""
import json

from predict import run as P

NOW = 1_800_000_000.0


def poly(i, bid, ask, days, vol=50_000, liq=10_000, ev="e1", fees=False):
    return P.from_poly({"id": i, "question": f"Q{i}", "outcomes": json.dumps(["Yes", "No"]), "bestBid": bid, "bestAsk": ask,
                        "endDate": __import__("datetime").datetime.fromtimestamp(NOW + days * 86400, __import__("datetime").timezone.utc).isoformat(),
                        "volumeNum": vol, "volume24hr": 1000, "liquidityNum": liq, "feesEnabled": fees, "events": [{"id": ev, "slug": "s"}]})


def test_favourite_side_and_real_cost():
    r = poly("1", 0.36, 0.38, 3)                       # NO is the favourite: buying it costs 1 - YES bid
    fv = P.favourite(r)
    assert fv["side"] == "no" and abs(fv["ask"] - 0.64) < 1e-9 and fv["fee"] == 0
    k = {**r, "venue": "kalshi"}
    assert abs(P.fee(k, 0.6) - 0.07 * 0.6 * 0.4) < 1e-12


def test_rule_band_window_liquidity_and_one_per_event():
    rows = [poly("a", 0.60, 0.62, 3, ev="x"), poly("b", 0.58, 0.60, 3, vol=90_000, ev="x"),   # same event: the most traded
            poly("c", 0.80, 0.82, 3, ev="y"),                                                   # too expensive
            poly("d", 0.60, 0.62, 0.5, ev="z"),                                                 # too close to the end
            poly("e", 0.60, 0.62, 9, ev="w"),                                                   # too early
            poly("f", 0.55, 0.62, 3, ev="v"),                                                   # spread too wide
            poly("g", 0.60, 0.62, 3, vol=5_000, ev="u")]                                        # too thin
    got = P.pick(rows, NOW)
    assert [r["id"] for r in got] == ["b"]


def test_settlement_after_fees_and_label():
    p = P.open_position({**poly("1", 0.60, 0.62, 3), "venue": "kalshi", "fee_pc": None}, NOW)
    cost = 0.62 + 0.07 * 0.62 * 0.38
    w = P.close_position(p, 1.0, NOW + 86400)
    assert w["won"] == 1 and abs(w["net"] - (1 / cost - 1)) < 1e-3
    lost = P.close_position(p, 0.0, NOW + 86400)
    assert lost["won"] == 0 and abs(lost["net"] + 1) < 1e-9
    assert P.record([w] * 10)["label"] == "Not proven yet"
    assert P.record([w, lost] * 30)["label"] == "Failed"              # ~50% wins at 64c: loses
    mixed = [w] * 50 + [lost] * 8                                      # 86% at 64c: clearly ahead
    assert P.record(mixed)["label"] == "Held up"
