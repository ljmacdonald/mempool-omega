"""Outlook on probation: signals add up as documented, calls are recorded and judged after costs, and the label
stays "Not proven" until there is enough live evidence."""
from whales import outlook as O


def test_signals_add_up():
    up = [100 + i for i in range(60)]                       # rising: above its average, higher than 14 days ago
    s = O.signals(up, 0.0)
    assert (s["trend"], s["mom"], s["crowd"], s["lean"]) == (1, 1, 0, 1)
    s = O.signals(up, 0.50)                                 # crowded longs pull it back to no clear lean? 1+1-1 = 1
    assert s["crowd"] == -1 and s["lean"] == 0
    down = list(reversed(up))
    assert O.signals(down, -0.20)["lean"] == 0              # 1 (crowded shorts) - 1 - 1 = -1
    assert O.signals(down, 0.0)["lean"] == -1
    assert O.signals(up[:30], 0.0) is None                  # not enough history


def test_calls_recorded_and_judged():
    store = {}
    load = lambda n, d: store.get(n, d)                     # noqa: E731
    save = lambda n, o: store.__setitem__(n, o)             # noqa: E731
    closes = [100 + i for i in range(60)]

    def post(body):
        if body["type"] == "candleSnapshot":
            return [{"t": body["req"]["endTime"] - (61 - i) * 86_400_000, "c": str(c)} for i, c in enumerate(closes)]
        return [{"fundingRate": "0.00001"}]
    t0 = 1_760_000_000_000
    out = O.refresh(post, load, save, ["ETH"], {"ETH": 1}, {"ETH": 100.0}, t0)
    assert out["coins"]["ETH"]["lean"] == 1 and len(store["outlook_calls.json"]) == 1
    O.refresh(post, load, save, ["ETH"], {"ETH": 1}, {"ETH": 100.0}, t0 + 3600_000)      # same day: no second call
    assert len(store["outlook_calls.json"]) == 1
    out = O.refresh(post, load, save, ["ETH"], {"ETH": 1}, {"ETH": 110.0}, t0 + 7 * 86_400_000)
    first = store["outlook_calls.json"][0]
    assert abs(first["net"] - (0.10 - O.COST_RT)) < 1e-9
    assert out["record"]["label"] == "Not proven" and out["record"]["judged"] == 1


def test_label_needs_a_month_and_an_edge():
    good = [{"date": f"2026-01-{d:02d}" if d <= 31 else f"2026-02-{d - 31:02d}", "coin": c, "lean": 1, "net": 0.02 + 0.001 * (d % 3), "whales": 1}
            for d in range(1, 41) for c in ("A", "B")]
    assert O.record(good)["label"] == "Held up"
    assert O.record(good[:20])["label"] == "Not proven"
    bad = [x | {"net": -0.01} for x in good]
    assert O.record(bad)["label"] == "Failed"
