"""The website re-implements the scanner maths in JavaScript (site/engine.js). This test checks that the
JavaScript gives the same features, probabilities and exit levels as Python. Skipped if Node is missing."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scanner.export_web import export_model
from scanner.features import coin_features
from scanner.model import ScannerModel, build_dataset, risk_unit
from scanner.styles import STYLES

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _candles(n, seed):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    c = 10 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[c[0], c[:-1]]
    v = rng.gamma(3, 100, n)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.003, "low": np.minimum(o, c) * 0.997, "close": c,
                         "volume": v, "qv": v * c, "taker_buy_volume": v * rng.uniform(0.4, 0.6, n)}, index=idx)


def test_js_engine_matches_python(tmp_path):
    style = STYLES["day"]
    candles = {f"C{i}USDT": _candles(600, i) for i in range(6)}
    candles["BTCUSDT"] = _candles(600, 99)
    m = ScannerModel(style="day").fit(build_dataset(candles, style), n_rounds=30, folds=3)
    model = export_model(m)
    btc = candles["BTCUSDT"].iloc[-240:]
    coins = []
    for s, df in candles.items():
        df = df.iloc[-240:]
        f = coin_features(df, btc).iloc[[-1]]
        coins.append({"symbol": s, "p": float(m.predict(f)[0]), "risk_unit": float(risk_unit(df, style).iloc[-1]),
                      "features": {k: (None if np.isnan(v) else float(v)) for k, v in f.iloc[0].items()},
                      "candles": {"t": [int(x.value // 10**6) for x in df.index],
                                  **{k: df[k].tolist() for k in df.columns}}})
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"model": model, "style": style.__dict__, "coins": coins}))
    js = """
const E = require(process.argv[1]); const fx = require(process.argv[2]);
const btc = fx.coins.find((c) => c.symbol === 'BTCUSDT').candles; let worst = 0;
for (const c of fx.coins) {
  const f = E.lastFeatures(c.candles, btc);
  for (const k of E.FEATURES) { const b = c.features[k], a = f[k];
    if (b === null) { if (Number.isFinite(a)) throw new Error('NaN mismatch ' + k); continue; }
    worst = Math.max(worst, Math.abs(a - b) / Math.max(Math.abs(b), 1e-9)); }
  worst = Math.max(worst, Math.abs(E.predict(fx.model, f) - c.p));
  worst = Math.max(worst, Math.abs(E.riskUnit(c.candles, fx.style) - c.risk_unit) / c.risk_unit);
}
console.log(worst);"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "site" / "engine.js"), str(fx)], capture_output=True, text=True,
                         check=True)
    assert float(out.stdout.strip()) < 1e-6


def test_js_defences_match_python(tmp_path):
    from scanner import integrity as I
    from scanner.defence import adaptive_penalty, declutter_exits

    rng = np.random.default_rng(1)
    book = {"bids": [(100 - i * 0.1, 5.0) for i in range(60)], "asks": [(100.1 + i * 0.1, 5.0) for i in range(60)]}
    walled = {"bids": [(p, 400.0 if i in (3, 4) else q) for i, (p, q) in enumerate(book["bids"])], "asks": book["asks"]}
    thin = {"bids": [(100 - i, 0.1) for i in range(20)], "asks": [(101 + i, 0.1) for i in range(20)]}
    normal = [{"price": 100.0, "qty": float(rng.integers(1, 10_000)) / 100, "buyer_maker": bool(rng.random() < .5),
               "time": 1000 * i} for i in range(500)]
    wash = []
    for i in range(250):
        wash += [{"price": 100.0, "qty": 7.0, "buyer_maker": True, "time": 3000 * i},
                 {"price": 100.0, "qty": 7.0, "buyer_maker": False, "time": 3000 * i + 500}]
    base = _candles(240, 5)
    whale = base.assign(n_trades=100.0)
    whale.iloc[-6:, whale.columns.get_loc("n_trades")] = 1.0
    calm = base.assign(n_trades=100.0)
    fake_vol = calm.copy()
    fake_vol.iloc[-6:, fake_vol.columns.get_loc("qv")] *= 8
    fake_vol.iloc[-6:, fake_vol.columns.get_loc("close")] = fake_vol["open"].iloc[-6:] * 1.0001
    seeds = [None, 1, 123456789, 4294967295]
    cases = {
        "walls_ok": ("walls", [[book, book, book]]), "walls_held_then_pulled": ("walls", [[walled, walled, book]]),
        "thin_ok": ("thin_book", [book]), "thin_bad": ("thin_book", [thin]),
        "wash_ok": ("wash", [normal]), "wash_bad": ("wash", [wash]),
        "venues_ok": ("venues", [100.0, 5.0, [{"price": 100.2, "change_pct": 4.0}]]),
        "venues_bad": ("venues", [100.0, 30.0, [{"price": 100.2, "change_pct": 2.0}]]),
        "venues_none": ("venues", [100.0, 5.0, []]),
        "eng_bad": ("engineered", [{"volume_surge": 5, "buy_pressure_6b": 0.09, "ret_72b": -0.04, "ema72_dist": -0.01}]),
        "eng_ok": ("engineered", [{"volume_surge": 5, "buy_pressure_6b": 0.09, "ret_72b": 0.06, "ema72_dist": 0.02}]),
    }
    candle_cases = {"whale_bad": ("whale", whale), "whale_ok": ("whale", calm), "impact_bad": ("impact", fake_vol),
                    "impact_ok": ("impact", calm)}
    fn = {"walls": I.check_walls, "thin_book": I.check_thin_book, "wash": I.check_wash, "venues": I.check_venues,
          "engineered": I.check_engineered, "whale": I.check_whale, "impact": I.check_impact}
    expected = {}
    for sd in seeds:
        t = I.thresholds(sd)
        for k, (f, args) in cases.items():
            expected[f"{sd}:{k}"] = fn[f](*args, t)["ok"]
        for k, (f, df) in candle_cases.items():
            expected[f"{sd}:{k}"] = fn[f](df, t)["ok"]
    assert expected["None:walls_held_then_pulled"] is False and expected["None:impact_bad"] is False
    assert expected["None:wash_bad"] is False and expected["None:eng_bad"] is False and expected["None:thin_bad"] is False
    rnd_py = [I.mulberry32(s)() for s in (0, 1, 2 ** 31, 4294967295)]
    thr_py = {str(sd): I.thresholds(sd) for sd in seeds}
    ex_inputs = [[2.10, 2.10 * (1 - 0.0476), 2.10 * (1 + 2 * 0.0476), None, 0.0476],
                 [10.0, 9.70, 10.6, 9.69, 0.03], [1.0, 0.97, 1.4990, None, 0.03], [0.01243, 0.01144, 0.01442, 0.0112, 0.08]]
    ex_py = [list(declutter_exits(a, b, c, np.nan if d is None else d, e)) for a, b, c, d, e in ex_inputs]
    adaptive = {"runup": {"cut": 0.1, "penalty": 0.2}, "surge": {"cut": 3.0, "penalty": 0.05}}
    ad_inputs = [{"ret_24b": 0.2, "volume_surge": 1}, {"ret_24b": 0.0, "volume_surge": 4}, {"ret_24b": 0.0, "volume_surge": 1}]
    ad_py = [adaptive_penalty(f, adaptive)[0] for f in ad_inputs]

    def cj(df):
        return {"t": [0] * len(df), **{k: df[k].tolist() for k in df.columns}}

    fx = tmp_path / "ix.json"
    fx.write_text(json.dumps({"cases": cases, "candles": {k: [f, cj(df)] for k, (f, df) in candle_cases.items()},
                              "seeds": seeds, "ex_inputs": ex_inputs, "adaptive": adaptive, "ad_inputs": ad_inputs}))
    js = """
const E = require(process.argv[1]); const fx = require(process.argv[2]);
const fn = {walls: E.checkWalls, thin_book: E.checkThinBook, wash: E.checkWash, venues: E.checkVenues,
  engineered: E.checkEngineered, whale: E.checkWhale, impact: E.checkImpact};
const got = {};
for (const sd of fx.seeds) { const t = E.thresholds(sd); const K = sd === null ? 'None' : String(sd);
  for (const [k, [f, args]] of Object.entries(fx.cases)) got[`${K}:${k}`] = fn[f](...args, t).ok;
  for (const [k, [f, c]] of Object.entries(fx.candles)) got[`${K}:${k}`] = fn[f](c, t).ok; }
const rnd = [0, 1, 2 ** 31, 4294967295].map((s) => E.mulberry32(s)());
const thr = Object.fromEntries(fx.seeds.map((sd) => [sd === null ? 'None' : String(sd), E.thresholds(sd)]));
const ex = fx.ex_inputs.map(([a, b, c, d, e]) => E.declutterExits(a, b, c, d === null ? NaN : d, e));
const ad = fx.ad_inputs.map((f) => E.adaptivePenalty(f, fx.adaptive)[0]);
console.log(JSON.stringify({got, rnd, thr, ex, ad}));"""
    out = json.loads(subprocess.run(["node", "-e", js, str(ROOT / "site" / "engine.js"), str(fx)],
                                    capture_output=True, text=True, check=True).stdout)
    assert out["got"] == {k: v for k, v in expected.items()}
    np.testing.assert_allclose(out["rnd"], rnd_py, rtol=0, atol=0)
    for sd in thr_py:
        for k, v in thr_py[sd].items():
            assert abs(out["thr"][sd][k] - v) < 1e-12 * max(1, abs(v))
    np.testing.assert_allclose(np.array(out["ex"]), np.array(ex_py), rtol=1e-12)
    np.testing.assert_allclose(out["ad"], ad_py)
