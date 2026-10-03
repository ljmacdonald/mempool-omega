/* Mempool Omega browser engine: the same maths as scanner/*.py, run in your browser.
   Pure functions only (no DOM), so it can be tested in Node against the Python output. */
(function (root) {
  "use strict";
  const NaN_ = Number.NaN;
  const isNum = (x) => typeof x === "number" && Number.isFinite(x);

  // ---------------------------------------------------------------- pandas-equivalent helpers
  function diff(a, n = 1) { return a.map((v, i) => (i >= n ? v - a[i - n] : NaN_)); }
  function logArr(a) { return a.map((v) => Math.log(v)); }

  // pandas .ewm(alpha, min_periods, adjust=True, ignore_na=False).mean()
  function ewmMean(a, alpha, minP) {
    const d = 1 - alpha; let num = 0, den = 0, cnt = 0; const out = new Array(a.length);
    for (let i = 0; i < a.length; i++) {
      const x = a[i];
      if (isNum(x)) { num = x + d * num; den = 1 + d * den; cnt++; }
      else if (cnt > 0) { num *= d; den *= d; }
      out[i] = cnt >= minP && den > 0 ? num / den : NaN_;
    }
    return out;
  }

  // pandas .ewm(alpha, min_periods, adjust=True).std()  (bias-corrected)
  function ewmStd(a, alpha, minP) {
    const d = 1 - alpha; let sw = 0, sw2 = 0, swx = 0, swx2 = 0, cnt = 0; const out = new Array(a.length);
    for (let i = 0; i < a.length; i++) {
      const x = a[i];
      if (isNum(x)) { sw = 1 + d * sw; sw2 = 1 + d * d * sw2; swx = x + d * swx; swx2 = x * x + d * swx2; cnt++; }
      else if (cnt > 0) { sw *= d; sw2 *= d * d; swx *= d; swx2 *= d; }
      if (cnt < Math.max(minP, 2)) { out[i] = NaN_; continue; }
      const mean = swx / sw; let varB = swx2 / sw - mean * mean; if (varB < 0) varB = 0;
      const numr = sw * sw, denr = numr - sw2;
      out[i] = denr > 0 ? Math.sqrt(varB * numr / denr) : NaN_;
    }
    return out;
  }

  function rolling(a, w, minP, fn) {
    const out = new Array(a.length);
    for (let i = 0; i < a.length; i++) {
      const vals = [];
      for (let j = Math.max(0, i - w + 1); j <= i; j++) if (isNum(a[j])) vals.push(a[j]);
      out[i] = vals.length >= minP ? fn(vals) : NaN_;
    }
    return out;
  }
  const sum = (v) => v.reduce((s, x) => s + x, 0);
  const std = (v) => { if (v.length < 2) return NaN_; const m = sum(v) / v.length; return Math.sqrt(v.reduce((s, x) => s + (x - m) ** 2, 0) / (v.length - 1)); };
  const rSum = (a, w, minP = w) => rolling(a, w, minP, sum);
  const rStd = (a, w, minP = w) => rolling(a, w, minP, std);
  const rMax = (a, w, minP = w) => rolling(a, w, minP, (v) => Math.max(...v));
  const rMin = (a, w, minP = w) => rolling(a, w, minP, (v) => Math.min(...v));

  // ---------------------------------------------------------------- features (scanner/features.py)
  const FEATURES = ["ret_1b", "ret_4b", "ret_24b", "ret_72b", "ema24_dist", "ema72_dist", "atr_pct", "vol_ratio",
    "buy_pressure_6b", "buy_pressure_24b", "volume_surge", "rsi_14", "dist_high_168b", "dist_low_168b",
    "rel_strength_24b", "btc_ret_24b", "hour_sin", "hour_cos", "liquidity"];

  function rsi(close, n = 14) {
    const d = diff(close);
    const up = ewmMean(d.map((x) => (isNum(x) ? Math.max(x, 0) : NaN_)), 1 / n, n);
    const dn = ewmMean(d.map((x) => (isNum(x) ? Math.max(-x, 0) : NaN_)), 1 / n, n);
    return up.map((u, i) => { const rs = dn[i] === 0 ? NaN_ : u / dn[i]; return 100 - 100 / (1 + rs); });
  }

  function atrPct(c, n = 14) {
    const tr = c.close.map((_, i) => {
      const hl = c.high[i] - c.low[i];
      if (i === 0) return hl;
      const p = c.close[i - 1];
      return Math.max(hl, Math.abs(c.high[i] - p), Math.abs(c.low[i] - p));
    });
    return ewmMean(tr, 1 / n, n).map((v, i) => v / c.close[i]);
  }

  // c = {t:[ms], open, high, low, close, volume, qv, taker_buy_volume}; btc = same shape or null.
  // Returns the feature vector of the LAST candle (what the live ranking needs).
  function lastFeatures(c, btc) {
    const n = c.close.length, L = n - 1, close = c.close, lc = logArr(close);
    const f = {};
    for (const h of [1, 4, 24, 72]) f[`ret_${h}b`] = L >= h ? lc[L] - lc[L - h] : NaN_;
    const e24 = ewmMean(close, 2 / 25, 12), e72 = ewmMean(close, 2 / 73, 36);
    f.ema24_dist = close[L] / e24[L] - 1;
    f.ema72_dist = close[L] / e72[L] - 1;
    f.atr_pct = atrPct(c)[L];
    const r = diff(lc);
    f.vol_ratio = rStd(r, 24, 12)[L] / rStd(r, 168, 48)[L];
    for (const h of [6, 24]) f[`buy_pressure_${h}b`] = rSum(c.taker_buy_volume, h)[L] / rSum(c.volume, h)[L] - 0.5;
    f.volume_surge = rSum(c.qv, 24)[L] / (rSum(c.qv, 168, 72)[L] / 7);
    f.rsi_14 = rsi(close)[L];
    f.dist_high_168b = close[L] / rMax(c.high, 168, 48)[L] - 1;
    f.dist_low_168b = close[L] / rMin(c.low, 168, 48)[L] - 1;
    if (btc) {
      const idx = new Map(btc.t.map((t, i) => [t, i])); const bl = logArr(btc.close);
      const j = idx.get(c.t[L]);
      const b = j !== undefined && j >= 24 ? bl[j] - bl[j - 24] : NaN_;
      f.btc_ret_24b = b; f.rel_strength_24b = f.ret_24b - b;
    } else { f.btc_ret_24b = NaN_; f.rel_strength_24b = NaN_; }
    const dt = new Date(c.t[L]); const hours = dt.getUTCHours() + dt.getUTCMinutes() / 60;
    f.hour_sin = Math.sin(2 * Math.PI * hours / 24);
    f.hour_cos = Math.cos(2 * Math.PI * hours / 24);
    f.liquidity = Math.log10(Math.max(rSum(c.qv, 24)[L], 1));
    for (const k of FEATURES) if (!isNum(f[k])) f[k] = NaN_;
    return f;
  }

  function riskUnit(c, style) {
    const s = ewmStd(diff(logArr(c.close)), 2 / 49, 24);
    const v = s[s.length - 1] * Math.sqrt(style.horizon_bars);
    return Math.min(Math.max(isNum(v) ? v : style.min_risk, style.min_risk), style.max_risk);
  }

  // ---------------------------------------------------------------- LightGBM trees (scanner/export_web.py)
  function treeValue(nodes, x) {
    let i = 0;
    for (;;) {
      const n = nodes[i];
      if (n[0] === -1) return n[1];
      let v = x[n[0]]; const thr = n[1], defLeft = n[2], miss = n[3];
      let goLeft;
      if (miss === 2 && !isNum(v)) goLeft = defLeft === 1;                         // NaN -> default side
      else if (miss === 1 && (!isNum(v) || Math.abs(v) <= 1e-35)) goLeft = defLeft === 1;  // zero/NaN missing
      else { if (!isNum(v)) v = 0; goLeft = v <= thr; }                            // missing None: NaN as 0
      i = goLeft ? n[4] : n[5];
    }
  }
  function predict(model, feat) {
    const x = model.features.map((k) => feat[k]);
    let raw = 0;
    for (const t of model.trees) raw += treeValue(t, x);
    return 1 / (1 + Math.exp(-raw));
  }

  // ---------------------------------------------------------------- ranking (scanner/rank.py)
  function humanDuration(minutes) {
    minutes = Math.round(minutes);
    if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
    const h = minutes / 60;
    if (h < 48) { const hv = +h.toFixed(1); return `${hv} hour${hv === 1 ? "" : "s"}`; }
    return `${+(h / 24).toFixed(1)} days`;
  }
  const barsText = (style, n) => humanDuration(n * style.bar_minutes);
  const pct0 = (x) => `${Math.round(x * 100)}%`;
  const pct1 = (x) => `${(x * 100).toFixed(1)}%`;

  function riskLevel(ru, style) {
    const x = (ru - style.min_risk) / (style.max_risk - style.min_risk);
    return x < 0.15 ? "Low" : x < 0.35 ? "Medium" : x < 0.6 ? "High" : "Very high";
  }
  function grade(score, grades) { for (const [th, g] of grades) if (score >= th) return g; return "Avoid - watch only"; }
  const scoreFromR = (r) => 10 / (1 + Math.exp(-6 * r));
  const expectedR = (p, m, ru, cfg) => p * m.win_r - (1 - p) * m.loss_r - cfg.fee_r / (cfg.sl * ru);

  function redFlags(f, qv, style) {
    let pen = 0; const warn = [];
    if (f.ret_24b > Math.log(1.30) || f.ret_1b > Math.log(1.08)) {
      pen += 0.15;
      warn.push(`Already up ${pct0(Math.expm1(f.ret_24b))} in the last ${barsText(style, 24)}. Chasing coins that just spiked often ends in buying the top.`);
    }
    if (f.volume_surge > 4 && Math.abs(f.ret_24b) < 0.01) { pen += 0.10; warn.push("Trading activity is unusually high but the price isn't moving. That can be fake (wash) trading."); }
    if (qv < 10e6) { pen += 0.05; warn.push("Fairly thin trading (under $10M a day). Prices can jump around and are easier to push."); }
    if (f.rsi_14 > 80) { pen += 0.05; warn.push("Looks 'overheated' (RSI above 80). Short pullbacks are common after this."); }
    if (f.btc_ret_24b < Math.log(0.97)) warn.push(`Bitcoin fell more than 3% in the last ${barsText(style, 24)}. Most coins follow Bitcoin.`);
    return [pen, warn];
  }

  function reasons(f, style) {
    const out = [];
    if (f.buy_pressure_6b > 0.02) out.push([f.buy_pressure_6b * 20, `Buyers have been more eager than sellers over the last ${barsText(style, 6)} (${Math.round(50 + 100 * f.buy_pressure_6b)}% of trades were buys).`]);
    if (f.ema24_dist > 0 && f.ema72_dist > 0) out.push([0.8, `Price is above its average of the last ${barsText(style, 24)} and of the last ${barsText(style, 72)}, so the trend is up.`]);
    if (f.rel_strength_24b > 0.01) out.push([f.rel_strength_24b * 30, `It did ${pct1(Math.expm1(f.rel_strength_24b))} better than Bitcoin over the last ${barsText(style, 24)}.`]);
    if (f.volume_surge > 1.5) out.push([Math.min(f.volume_surge / 3, 1), `Trading activity is ${f.volume_surge.toFixed(1)}x higher than usual, so people are paying attention to it.`]);
    if (f.dist_low_168b < 0.03 && f.ret_4b > 0) out.push([0.6, `It is bouncing up from near its lowest price of the last ${barsText(style, 168)}.`]);
    if (f.rsi_14 >= 45 && f.rsi_14 <= 65) out.push([0.3, "Momentum is healthy: not overheated, not collapsing."]);
    if (f.dist_high_168b > -0.02) out.push([0.5, `It is close to its highest price of the last ${barsText(style, 168)}. A breakout is possible.`]);
    out.sort((a, b) => b[0] - a[0]);
    const r = out.slice(0, 3).map((x) => x[1]);
    return r.length ? r : ["The model sees a slightly better-than-usual pattern, with no single strong reason."];
  }

  // coins: [{symbol, candles, quoteVolume}]; returns {ideas, mood, scores}
  function rankCoins(coins, btc, model, style, cfg, bigMovers = {}, topN = 5) {
    const rows = [];
    for (const coin of coins) {
      const c = coin.candles; if (!c || c.close.length < 100) continue;
      const f = lastFeatures(c, btc); const ru = riskUnit(c, style); const p = predict(model, f);
      const [pen, warns] = redFlags(f, coin.quoteVolume || 0, style);
      const baseR = expectedR(p, model, ru, cfg); const adj = baseR - pen; const score = scoreFromR(adj);
      const close = c.close[c.close.length - 1];
      const madeAt = c.t[c.t.length - 1] + style.bar_minutes * 60000;
      const bm = bigMovers[coin.symbol] || {};
      rows.push({ symbol: coin.symbol, coin: coin.symbol.replace(/USDT$/, ""), style: style.key, p, baseR,
        score: Math.round(score * 10) / 10, rawScore: score, grade: grade(score, cfg.grades), chance_beats_market: p,
        risk_level: riskLevel(ru, style), risk_unit: ru, price_now: close,
        take_profit: close * (1 + cfg.pt * ru), take_profit_pct: cfg.pt * ru,
        safety_exit: close * (1 - cfg.sl * ru), safety_exit_pct: -cfg.sl * ru,
        hold_minutes: style.horizon_bars * style.bar_minutes, exit_by: madeAt + style.horizon_bars * style.bar_minutes * 60000,
        size_for_10usd_risk: 10 / (cfg.sl * ru), why: reasons(f, style), warnings: warns,
        week_up20_pct: bm.up_pct, week_down20_pct: bm.down_pct, candle_time: c.t[c.t.length - 1] });
    }
    const positive = rows.filter((r) => r.baseR > 0).length / Math.max(rows.length, 1);
    const mood = { label: positive > 0.6 ? "Favourable" : positive > 0.35 ? "Mixed" : "Unfavourable", share_positive: positive,
      coins: rows.length };
    const scores = Object.fromEntries(rows.map((r) => [r.symbol, r.rawScore]));
    rows.sort((a, b) => b.rawScore - a.rawScore);
    const ideas = rows.slice(0, topN).map((r, i) => ({ ...r, rank: i + 1 }));
    return { ideas, mood, scores };
  }

  // ---------------------------------------------------------------- universe filter (scanner/data.py)
  function selectUniverse(tickers, ucfg) {
    const excluded = new Set([...ucfg.exclude_stablecoins, ...ucfg.exclude_other, ...ucfg.exclude_stock_tokens]);
    const must = new Set(ucfg.always_include || []);
    const rows = tickers.filter((t) => t.symbol.endsWith("USDT")).map((t) => ({
      symbol: t.symbol, base: t.symbol.slice(0, -4), quoteVolume: +t.quoteVolume, lastPrice: +t.lastPrice,
      change: +t.priceChangePercent }))
      .filter((t) => /^[A-Z0-9]{2,15}$/.test(t.base) && !excluded.has(t.base) && !/(UP|DOWN|BULL|BEAR)$/.test(t.base))
      .filter((t) => t.quoteVolume >= ucfg.min_quote_volume_usd || must.has(t.base))
      .sort((a, b) => b.quoteVolume - a.quoteVolume);
    const top = rows.slice(0, ucfg.max_coins);
    for (const r of rows) if (must.has(r.base) && !top.includes(r)) top.push(r);
    return top;
  }

  // ---------------------------------------------------------------- trade monitor (scanner/live.py advise)
  function makeTrade(idea, entry, openedMs) {
    entry = entry || idea.price_now; openedMs = openedMs || Date.now();
    const ru = idea.risk_unit;
    return { id: `${idea.symbol}-${openedMs}`, symbol: idea.symbol, style: idea.style, entry,
      take_profit: entry * (1 + 2 * ru), safety_exit: entry * (1 - 1 * ru), opened: openedMs,
      exit_by: openedMs + idea.hold_minutes * 60000 };
  }

  function advise(t, price, nowMs, score) {
    nowMs = nowMs || Date.now();
    const leftMin = (t.exit_by - nowMs) / 60000;
    const pnl = price / t.entry - 1 - 0.002;
    let action, level, why;
    if (price >= t.take_profit) { action = "Take profit now"; level = "good"; why = "The price reached your take-profit level. Selling now locks in the gain."; }
    else if (price <= t.safety_exit) { action = "Exit now"; level = "bad"; why = "The price fell to your safety exit. Selling now keeps a small loss from becoming a big one."; }
    else if (leftMin <= 0) { action = "Time's up: sell now"; level = "warn"; why = "This idea had a time limit and it has passed. The expected move didn't happen in time."; }
    else if (isNum(score) && score < 4.5) { action = "Consider leaving early"; level = "warn"; why = `The reasons for this trade have faded (score now ${score.toFixed(1)}/10). Leaving early is reasonable; otherwise keep your safety exit.`; }
    else { action = "Hold"; level = "calm"; why = `Neither exit has been reached. Time left: ${humanDuration(leftMin)}.`; }
    const progress = Math.min(Math.max((price - t.safety_exit) / (t.take_profit - t.safety_exit), 0), 1);
    return { action, level, why, pnl, progress, leftMin: Math.max(leftMin, 0),
      toTp: t.take_profit / price - 1, toSl: t.safety_exit / price - 1 };
  }

  const api = { FEATURES, diff, ewmMean, ewmStd, rStd, lastFeatures, riskUnit, predict, rankCoins, selectUniverse,
    makeTrade, advise, humanDuration, scoreFromR, expectedR };
  root.OmegaEngine = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
