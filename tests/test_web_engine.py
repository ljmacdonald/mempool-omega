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


def test_js_integrity_checks_match_python(tmp_path):
    from scanner import integrity as I

    rng = np.random.default_rng(1)
    book = {"bids": [(100 - i * 0.1, 5.0) for i in range(60)], "asks": [(100.1 + i * 0.1, 5.0) for i in range(60)]}
    spoof1 = {"bids": [(p, 400.0 if i in (3, 4) else q) for i, (p, q) in enumerate(book["bids"])], "asks": book["asks"]}
    thin = {"bids": [(100 - i, 0.1) for i in range(20)], "asks": [(101 + i, 0.1) for i in range(20)]}
    normal_trades = [{"price": 100.0, "qty": float(rng.integers(1, 10_000)) / 100, "buyer_maker": bool(rng.random() < .5),
                      "time": 1000 * i} for i in range(500)]
    wash_trades = []
    for i in range(250):
        wash_trades += [{"price": 100.0, "qty": 7.0, "buyer_maker": True, "time": 3000 * i},
                        {"price": 100.0, "qty": 7.0, "buyer_maker": False, "time": 3000 * i + 500}]
    c = _candles(240, 5)
    whale = c.copy()
    whale["n_trades"] = 100.0
    whale.iloc[-6:, whale.columns.get_loc("n_trades")] = 1.0
    calm = c.assign(n_trades=100.0)
    cases = {
        "walls_ok": ("walls", [book, book]), "walls_spoof": ("walls", [spoof1, book]),
        "thin_ok": ("thin_book", [book]), "thin_bad": ("thin_book", [thin]),
        "wash_ok": ("wash", [normal_trades]), "wash_bad": ("wash", [wash_trades]),
        "venues_ok": ("venues", [100.0, 5.0, [{"price": 100.2, "change_pct": 4.0}]]),
        "venues_bad": ("venues", [100.0, 30.0, [{"price": 100.2, "change_pct": 2.0}]]),
        "venues_none": ("venues", [100.0, 5.0, []]),
    }
    fn = {"walls": I.check_walls, "thin_book": I.check_thin_book, "wash": I.check_wash, "venues": I.check_venues}
    expected = {k: fn[f](*args)["ok"] for k, (f, args) in cases.items()}
    for name, df in (("whale_bad", whale), ("whale_ok", calm)):
        expected[name] = I.check_whale(df)["ok"]
    assert expected["walls_spoof"] is False and expected["thin_bad"] is False and expected["wash_bad"] is False
    assert expected["venues_bad"] is False and expected["whale_bad"] is False and expected["walls_ok"] is True

    def cj(df):
        return {"t": [0] * len(df), **{k: df[k].tolist() for k in df.columns}}

    fx = tmp_path / "ix.json"
    fx.write_text(json.dumps({"cases": cases, "whale_bad": cj(whale), "whale_ok": cj(calm), "expected": expected}))
    js = """
const E = require(process.argv[1]); const fx = require(process.argv[2]);
const fn = {walls: E.checkWalls, thin_book: E.checkThinBook, wash: E.checkWash, venues: E.checkVenues};
const got = {};
for (const [k, [f, args]] of Object.entries(fx.cases)) got[k] = fn[f](...args).ok;
got.whale_bad = E.checkWhale(fx.whale_bad).ok; got.whale_ok = E.checkWhale(fx.whale_ok).ok;
console.log(JSON.stringify(got));"""
    out = subprocess.run(["node", "-e", js, str(ROOT / "site" / "engine.js"), str(fx)], capture_output=True, text=True,
                         check=True)
    assert json.loads(out.stdout) == expected
    py = I.combine([I.check_walls(spoof1, book), I.check_wash(wash_trades), I.check_venues(100.0, 5.0, [])])
    assert py["penalty"] == round(0.15 + 0.10 + 0.03, 6)
