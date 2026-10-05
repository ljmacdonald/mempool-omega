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

  // Learned from the track record (scanner/quality.py vol_adjust): a jumpy coin's expected R is lowered when jumpy coins
  // have done worse than their scores said. No change without enough history.
  function adjustR(r, ru, adj) {
    if (!adj || !adj.slope || !(ru > 0)) return r;
    return r + adj.slope * (Math.log(ru) - adj.mean) / adj.sd;
  }

  // coins: [{symbol, candles, quoteVolume}]; returns {ideas, mood, scores}
  function rankCoins(coins, btc, model, style, cfg, bigMovers = {}, topN = 5, adaptive = null) {
    const rows = [];
    for (const coin of coins) {
      const c = coin.candles; if (!c || c.close.length < 100) continue;
      const f = lastFeatures(c, btc); const ru = riskUnit(c, style); const p = predict(model, f);
      let [pen, warns] = redFlags(f, coin.quoteVolume || 0, style);
      const [apen, awarn] = adaptivePenalty(f, adaptive); pen += apen; warns = warns.concat(awarn);
      const baseR = adjustR(expectedR(p, model, ru, cfg), ru, adaptive && adaptive.vol); const adj = baseR - pen; const score = scoreFromR(adj);
      const close = c.close[c.close.length - 1];
      const low24 = Math.min(...c.low.slice(-24));
      const [stop, target] = declutterExits(close, close * (1 - cfg.sl * ru), close * (1 + cfg.pt * ru), low24, ru, cfg.sl);
      const madeAt = c.t[c.t.length - 1] + style.bar_minutes * 60000;
      const bm = bigMovers[coin.symbol] || {};
      rows.push({ symbol: coin.symbol, coin: coin.symbol.replace(/USDT$/, ""), style: style.key, p, baseR, adjR: adj,
        score: Math.round(score * 10) / 10, rawScore: score, grade: grade(score, cfg.grades), chance_beats_market: p,
        risk_level: riskLevel(ru, style), risk_unit: ru, price_now: close,
        take_profit: target, take_profit_pct: target / close - 1,
        safety_exit: stop, safety_exit_pct: stop / close - 1, features: f,
        hold_minutes: style.horizon_bars * style.bar_minutes, exit_by: madeAt + style.horizon_bars * style.bar_minutes * 60000,
        size_for_10usd_risk: 10 / (1 - stop / close), why: reasons(f, style), warnings: warns,
        week_up20_pct: bm.up_pct, week_down20_pct: bm.down_pct, candle_time: c.t[c.t.length - 1] });
    }
    const positive = rows.filter((r) => r.baseR > 0).length / Math.max(rows.length, 1);
    const mood = { label: positive > 0.6 ? "Favourable" : positive > 0.35 ? "Mixed" : "Unfavourable", share_positive: positive,
      coins: rows.length };
    const scores = Object.fromEntries(rows.map((r) => [r.symbol, r.rawScore]));
    rows.sort((a, b) => b.rawScore - a.rawScore);
    const ideas = rows.slice(0, topN).map((r, i) => ({ ...r, rank: i + 1 }));
    const all = rows.map((r, i) => ({ symbol: r.symbol, score: r.rawScore, place: i + 1, warnings: r.warnings }));
    return { ideas, mood, scores, all };
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

  // "Small coins" page (scanner/data.py select_small_universe): the next tier after the main universe
  function selectSmallUniverse(tickers, ucfg) {
    const main = new Set(selectUniverse(tickers, ucfg).map((r) => r.symbol));
    const excluded = new Set([...ucfg.exclude_stablecoins, ...ucfg.exclude_other, ...ucfg.exclude_stock_tokens]);
    return tickers.filter((t) => t.symbol.endsWith("USDT")).map((t) => ({
      symbol: t.symbol, base: t.symbol.slice(0, -4), quoteVolume: +t.quoteVolume, lastPrice: +t.lastPrice, change: +t.priceChangePercent }))
      .filter((t) => /^[A-Z0-9]{2,15}$/.test(t.base) && !excluded.has(t.base) && !/(UP|DOWN|BULL|BEAR)$/.test(t.base))
      .filter((t) => !main.has(t.symbol) && t.quoteVolume >= ucfg.small_min_quote_volume_usd)
      .sort((a, b) => b.quoteVolume - a.quoteVolume).slice(0, ucfg.small_max_coins);
  }
  // How often (last ~90 days) the coin rose / fell 20%+ at some point within a week (scanner/features.py big_mover_stats)
  function bigMoverStats(daily, threshold = 0.20, window = 7) {
    const c = daily.close, hi = daily.high, lo = daily.low, n = c.length;
    if (n < window + 10) return { up_pct: NaN_, down_pct: NaN_, days: n };
    let ups = 0, downs = 0, cnt = 0;
    for (let i = 0; i < n - window; i++) {
      let mh = -Infinity, ml = Infinity;
      for (let j = i + 1; j < i + 1 + window; j++) { mh = Math.max(mh, hi[j]); ml = Math.min(ml, lo[j]); }
      ups += mh / c[i] - 1 >= threshold ? 1 : 0; downs += 1 - ml / c[i] >= threshold ? 1 : 0; cnt++;
    }
    return { up_pct: ups / cnt, down_pct: downs / cnt, days: n };
  }

  // ---------------------------------------------------------------- trade monitor (scanner/live.py advise)
  function makeTrade(idea, entry, openedMs) {
    entry = entry || idea.price_now; openedMs = openedMs || Date.now();
    const ru = idea.risk_unit;
    const tpPct = isNum(idea.take_profit_pct) ? idea.take_profit_pct : 2 * ru;
    const slPct = isNum(idea.safety_exit_pct) ? idea.safety_exit_pct : -ru;
    return { id: `${idea.symbol}-${openedMs}`, symbol: idea.symbol, style: idea.style, entry,
      take_profit: entry * (1 + tpPct), safety_exit: entry * (1 + slPct), opened: openedMs,
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

  // ---------------------------------------------------------------- freshness: an old price is never shown or used as "now"
  // A price counts as live only if it was confirmed by the source within LIVE_MAX_AGE_MS. Without one, pages show no
  // "price now", no "if you sold right away" and no hold / take-profit / exit advice (only the time limit, which
  // doesn't depend on the price).
  const LIVE_MAX_AGE_MS = 60000;
  function freshPrice(rec, maxAgeMs, nowMs) {
    if (!rec || !isNum(rec.price) || !(rec.price > 0) || !isNum(rec.at)) return null;
    return (nowMs || Date.now()) - rec.at <= (maxAgeMs || LIVE_MAX_AGE_MS) ? rec.price : null;
  }
  function adviseNoPrice(t, nowMs) {
    const leftMin = (t.exit_by - (nowMs || Date.now())) / 60000;
    const base = { pnl: NaN, progress: NaN, leftMin: Math.max(leftMin, 0), toTp: NaN, toSl: NaN, stale: true };
    if (leftMin <= 0) return { ...base, action: "Time's up: sell now", level: "warn", why: "This idea had a time limit and it has passed. (No live price right now, so check the price where you bought before selling.)" };
    return { ...base, action: "Waiting for a live price", level: "calm", why: "The live price couldn't be confirmed in the last minute, so this page won't tell you to hold or sell yet. Your take profit and safety exit still apply: check the price where you bought." };
  }

  // Only an older price is available (e.g. the last 15-minute update): never "Hold", only "check now" when that older
  // price had already crossed an exit. `when` says when that price was taken (e.g. "14:35 New York").
  function adviseAsOf(t, px, when, nowMs) {
    if (!isNum(px)) return adviseNoPrice(t, nowMs);
    const a = advise(t, px, nowMs, null); const nb = adviseNoPrice(t, nowMs);
    if (a.action === "Take profit now") return { ...nb, level: "good", action: "Check now: take profit reached", why: `At the last update (${when}) the price had reached your take-profit level. Check the live price where you bought and sell if it's still there.` };
    if (a.action === "Exit now") return { ...nb, level: "bad", action: "Check now: safety exit reached", why: `At the last update (${when}) the price had fallen to your safety exit. Check the live price where you bought and sell if it's still there.` };
    if (nb.action !== "Waiting for a live price") return nb;
    return { ...nb, why: `No live price here. At the last update (${when}) neither exit had been reached. Check the live price where you bought.` };
  }

  // ---------------------------------------------------------------- fake-signal checks (scanner/integrity.py)
  // The code is public, so every limit is jittered +/-15% from a private seed that rotates hourly (see ADVERSARY.md).
  const PENALTY = { walls: 0.15, thin_book: 0.05, wash: 0.10, impact: 0.10, venues: 0.15, venues_none: 0.03, whale: 0.05, engineered: 0.10 };
  const MAX_PENALTY = Object.entries(PENALTY).filter(([k]) => k !== "venues_none").reduce((s2, [, v]) => s2 + v, 0);
  const BASE = { wall_mult: 5, wall_share: 0.30, wall_keep: 0.50, thin_depth: 50000, thin_spread: 0.003, wash_share: 0.02,
    impact_surge: 2.5, impact_ratio: 0.45, venue_dev: 0.015, venue_chg: 8, whale_ratio: 4, eng_surge: 3, eng_bp: 0.05 };
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a) >>> 0;
      t = (((t + (Math.imul(t ^ (t >>> 7), 61 | t) >>> 0)) >>> 0) ^ t) >>> 0;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function thresholds(seed) {
    if (seed === null || seed === undefined) return { ...BASE };
    const rnd = mulberry32(seed); const t = {};
    for (const k of Object.keys(BASE)) t[k] = BASE[k] * (0.85 + 0.30 * rnd());
    return t;
  }
  const snapshotGaps = (rnd) => [2 + 3 * rnd(), 4 + 6 * rnd()];
  const median = (v) => { const a = v.filter(isNum).slice().sort((x, y) => x - y); if (!a.length) return NaN_; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  const nanmean = (v) => { const a = v.filter(isNum); return a.length ? sum(a) / a.length : NaN_; };
  const money = (x) => `$${Math.round(x).toLocaleString("en-US")}`;
  const near = (levels, mid, band = 0.02) => levels.filter(([p]) => Math.abs(p / mid - 1) <= band);

  function checkWalls(books, t = BASE) {
    const b1 = books[0]; const mid = (b1.bids[0][0] + b1.asks[0][0]) / 2;
    const lv = near(b1.bids, mid).concat(near(b1.asks, mid));
    if (lv.length < 5 || books.length < 2) return { key: "walls", ok: null, text: "Not enough orders near the price to check for fake walls." };
    const notional = lv.map(([p, q]) => p * q); const med = median(notional); const total = sum(notional);
    const walls = lv.map(([p, q], i) => [p, q, notional[i]]).filter((w) => w[2] > t.wall_mult * med && w[2] > 1000);
    if (!walls.length) return { key: "walls", ok: true, text: "No suspicious giant orders near the price (no sign of spoofing)." };
    const wsum = sum(walls.map((w) => w[2])); let kept = 1;
    for (const later of books.slice(1)) {
      const after = new Map(later.bids.concat(later.asks).map(([p, q]) => [p, q]));
      kept = Math.min(kept, sum(walls.map(([p, q, n]) => n * Math.min(after.get(p) || 0, q) / q)) / wsum);
    }
    const share = wsum / total;
    if (share > t.wall_share && kept < t.wall_keep) return { key: "walls", ok: false, text: `Large orders (${Math.round(share * 100)}% of the nearby order book) disappeared between our order-book checks. That's spoofing: fake orders shown to trick other traders.` };
    return { key: "walls", ok: true, text: `Big orders near the price stayed in place across ${books.length} checks (they look real).` };
  }
  function checkThinBook(b, t = BASE) {
    const mid = (b.bids[0][0] + b.asks[0][0]) / 2;
    const depth = sum(near(b.bids, mid).concat(near(b.asks, mid)).map(([p, q]) => p * q));
    const spread = (b.asks[0][0] - b.bids[0][0]) / mid;
    if (depth < t.thin_depth || spread > t.thin_spread) return { key: "thin_book", ok: false, text: `Only about ${money(depth)} of orders within 2% of the price (spread ${(spread * 100).toFixed(2)}%). One large trade can push it around.` };
    return { key: "thin_book", ok: true, text: `About ${money(depth)} of orders within 2% of the price. Healthy depth.` };
  }
  function checkWash(trades, t = BASE) {
    if (trades.length < 100) return { key: "wash", ok: null, text: "Too few recent trades to check for fake volume." };
    let pairs = 0;
    for (let i = 1; i < trades.length; i++) { const a = trades[i - 1], b = trades[i]; if (b.qty === a.qty && b.buyer_maker !== a.buyer_maker && b.time - a.time <= 2000) pairs++; }
    const share = pairs / (trades.length - 1);
    if (share > t.wash_share) return { key: "wash", ok: false, text: `${(share * 100).toFixed(1)}% of recent trades were back-and-forth trades of the exact same size within 2 seconds. That's typical of fake (wash) trading to make a coin look busy.` };
    return { key: "wash", ok: true, text: "Recent trades look natural (no back-and-forth fake trading)." };
  }
  function checkImpact(c, t = BASE) {
    if (!c || c.close.length < 60) return { key: "impact", ok: null, text: "Not enough history to compare volume with price movement." };
    const imp = c.qv.map((q, i) => (q > 0 ? Math.abs(Math.log(c.close[i] / c.open[i])) / Math.sqrt(q) : NaN_));   // square-root law
    const usualQv = median(c.qv.slice(-168)); const usualImp = median(imp.slice(-168));
    const surge = usualQv > 0 ? nanmean(c.qv.slice(-6)) / usualQv : NaN_;
    const ratio = usualImp > 0 ? median(imp.slice(-6)) / usualImp : NaN_;
    if (isNum(surge) && isNum(ratio) && surge > t.impact_surge && ratio < t.impact_ratio) return { key: "impact", ok: false, text: `Trading volume is ${surge.toFixed(1)}x normal, but the price reacts only ${Math.round(ratio * 100)}% as much as that much trading normally moves it. Real buying moves prices; this looks like fake volume.` };
    return { key: "impact", ok: true, text: "The price reacts normally to the amount being traded." };
  }
  function checkVenues(price, changePct, others, t = BASE) {
    if (!others || !others.length) return { key: "venues", ok: null, penalty: PENALTY.venues_none, text: "Not traded on the other exchanges checked (OKX, Gate.io), so the move can't be confirmed." };
    const dev = Math.max(...others.map((o) => Math.abs(o.price / price - 1)));
    const chgDiff = Math.abs(changePct - median(others.map((o) => o.change_pct)));
    if (dev > t.venue_dev || chgDiff > t.venue_chg) return { key: "venues", ok: false, text: `The price or daily move on Binance differs from other exchanges (up to ${(dev * 100).toFixed(1)}% apart, ${Math.round(chgDiff)} points different over 24 hours). It may be pushed on one exchange only.` };
    return { key: "venues", ok: true, text: `Other exchanges confirm the price (${others.length} checked).` };
  }
  function checkWhale(c, t = BASE) {
    if (!c || !c.n_trades || c.close.length < 60) return { key: "whale", ok: null, text: "Not enough history to check trade sizes." };
    const avg = c.qv.map((q, i) => (c.n_trades[i] > 0 ? q / c.n_trades[i] : NaN_));
    const recent = nanmean(avg.slice(-6)); const usual = median(avg.slice(-168)); const ratio = usual > 0 ? recent / usual : NaN_;
    if (isNum(ratio) && ratio > t.whale_ratio) return { key: "whale", ok: false, text: `Recent trades are ${Math.round(ratio)}x bigger than usual. A few very large players are behind the activity (a whale or a coordinated push).` };
    return { key: "whale", ok: true, text: "Activity comes from many normal-sized trades." };
  }
  function checkEngineered(f, t = BASE) {
    const cheap = (f.volume_surge > t.eng_surge) || (f.buy_pressure_6b > t.eng_bp);
    const lasting = f.ret_72b > 0 && f.ema72_dist > 0;
    if (cheap && !lasting) return { key: "engineered", ok: false, text: "The case rests on signals that are cheap to fake (a burst of volume or buying) with no lasting price trend behind it. Manipulators build exactly this look to attract buyers." };
    return { key: "engineered", ok: true, text: cheap ? "The setup is backed by a longer-lasting trend, not just a short burst of activity." : "No artificial-looking burst of activity." };
  }
  function combineChecks(checks, penalties) {
    const pt = { ...PENALTY, ...(penalties || {}) };   // learned weights from the nightly self-tuning
    let pen = 0;
    for (const c of checks) { if (c.ok === false) pen += pt[c.key]; else if (c.ok === null) pen += c.penalty || 0; }
    const known = checks.filter((c) => c.ok !== null);
    return { checks, penalty: Math.round(pen * 1e6) / 1e6, passed: known.filter((c) => c.ok).length, checked: known.length,
      trust: Math.round(Math.max(0, 1 - pen / MAX_PENALTY) * 1000) / 1000 };
  }
  function applyIntegrity(ideas, results, topN, grades) {
    for (const d of ideas) {
      const r = results[d.symbol]; d.integrity = r || null;
      if (r) { d.adjR = d.adjR - r.penalty; d.rawScore = scoreFromR(d.adjR); d.score = Math.round(d.rawScore * 10) / 10; d.grade = grade(d.rawScore, grades); }
    }
    ideas.sort((a, b) => b.rawScore - a.rawScore);
    return ideas.slice(0, topN).map((d, i) => ({ ...d, rank: i + 1 }));
  }

  function applyProbation(ideas, adaptive, styleKey, grades) {
    const pr = ((adaptive || {}).probation || {})[styleKey] || {};
    if (pr.active) {
      for (const d of ideas) {
        d.adjR -= pr.penalty || 0.1; d.rawScore = scoreFromR(d.adjR); d.score = Math.round(d.rawScore * 10) / 10; d.grade = grade(d.rawScore, grades);
        d.warnings = d.warnings.concat([`This speed is on probation: its last ${pr.n} ideas did worse than picking coins at random, so its scores are lowered.`]);
      }
      ideas.sort((a, b) => b.rawScore - a.rawScore);
    }
    return ideas.map((d, i) => ({ ...d, rank: i + 1 }));
  }

  // ---------------------------------------------------------------- defences (scanner/defence.py)
  const roundStep = (price) => 0.5 * Math.pow(10, Math.floor(Math.log10(price)));
  function declutterExits(entry, stop, target, lowRecent, ru, slMult = 1) {
    const floor = entry * (1 - slMult * ru * 1.25); let s2 = stop;
    const step = roundStep(s2); const rn = Math.round(s2 / step) * step;
    if (rn > 0 && Math.abs(s2 / rn - 1) <= 0.002) s2 = rn * (1 - 0.003);
    if (isNum(lowRecent) && lowRecent > 0 && lowRecent * (1 - 0.002) <= s2 && s2 <= lowRecent * (1 + 0.004)) s2 = lowRecent * (1 - 0.004);
    s2 = Math.max(s2, floor);
    let g = target; const gstep = roundStep(g); const grn = Math.ceil(g / gstep) * gstep;
    if (grn > entry && grn / g - 1 >= 0 && grn / g - 1 <= 0.002) g = grn * (1 - 0.002);
    return [s2, g];
  }
  function adaptivePenalty(f, adaptive) {
    let pen = 0; const warn = []; const ru = (adaptive || {}).runup || {}, su = (adaptive || {}).surge || {};
    if (ru.penalty > 0 && ru.cut !== null && ru.cut !== undefined && f.ret_24b >= ru.cut) { pen += ru.penalty; warn.push("In our own track record, coins that had already run up like this tended to drop right after being suggested (a sign someone sells into followers). Score lowered."); }
    if (su.penalty > 0 && su.cut !== null && su.cut !== undefined && f.volume_surge >= su.cut) { pen += su.penalty; warn.push("In our own track record, ideas with a volume burst like this tended to reverse after being suggested. Score lowered."); }
    return [pen, warn];
  }

  // ---------------------------------------------------------------- real costs & where to buy
  // Standard entry-level spot fees (verify on your account; VIP tiers and token discounts are lower).
  // withdraw_usd = typical fee to withdraw stablecoins on the cheapest network, i.e. to take money out.
  const VENUES = {
    binance: { name: "Binance", taker: 0.0010, maker: 0.0010, withdraw_usd: 1.0 },
    okx: { name: "OKX", taker: 0.0010, maker: 0.0008, withdraw_usd: 1.0 },
    bitget: { name: "Bitget", taker: 0.0010, maker: 0.0010, withdraw_usd: 1.0 },
    gate: { name: "Gate.io", taker: 0.0020, maker: 0.0020, withdraw_usd: 1.0 },
    htx: { name: "HTX", taker: 0.0020, maker: 0.0020, withdraw_usd: 1.0 },
    kraken: { name: "Kraken", taker: 0.0040, maker: 0.0025, withdraw_usd: 2.5 },
    coinbase: { name: "Coinbase Advanced", taker: 0.0060, maker: 0.0040, withdraw_usd: 1.0 },
  };
  // DEX swap fee by exchange (pool tiers vary; this is the common default) and sandwich (MEV) exposure by chain:
  // without protection a sandwich bot can take up to your slippage tolerance; private RPCs avoid the public mempool.
  const DEX_FEE = { uniswap: 0.003, pancakeswap: 0.0025, sushiswap: 0.003, aerodrome: 0.003, velodrome: 0.003, raydium: 0.0025, orca: 0.003, meteora: 0.003, traderjoe: 0.003, camelot: 0.003 };
  const CHAINS = {
    ethereum: { name: "Ethereum", mev: 0.005, gasUnits: 150000, native: "ETH", protect: "Flashbots Protect or MEV Blocker" },
    bsc: { name: "BNB Chain", mev: 0.005, gasUnits: 150000, native: "BNB", protect: "a private RPC (e.g. bloXroute or 48 Club)" },
    base: { name: "Base", mev: 0.001, gasUnits: 150000, native: "ETH", protect: "a wallet with MEV protection" },
    arbitrum: { name: "Arbitrum", mev: 0.001, gasUnits: 300000, native: "ETH", protect: "a wallet with MEV protection" },
    solana: { name: "Solana", mev: 0.003, gasUsd: 0.02, protect: "a wallet with MEV protection (e.g. Jito-protected)" },
  };

  // Walk an order book. buy: spend `quote` dollars into asks. sell: sell `qty` coins into bids.
  function walkBuy(asks, quote) {
    let left = quote, coins = 0;
    for (const [p, q] of asks) { const can = p * q; const use = Math.min(left, can); coins += use / p; left -= use; if (left <= 1e-9) break; }
    const spent = quote - left; return { avg: coins > 0 ? spent / coins : NaN_, coins, filled: left <= 1e-6 * quote };
  }
  function walkSell(bids, qty) {
    let left = qty, got = 0;
    for (const [p, q] of bids) { const use = Math.min(left, q); got += use * p; left -= use; if (left <= 1e-12) break; }
    const sold = qty - left; return { avg: sold > 0 ? got / sold : NaN_, filled: left <= 1e-9 * qty };
  }
  // Net money you can take out from a CEX if you buy now with `amount` and sell at `exitPrice` (refMid = the
  // reference market price the exit levels are based on; the venue's own price is scaled to it).
  function cexNet(o) {
    const { amount, asks, bids, taker, withdraw_usd, refMid } = o;
    const venueMid = (asks[0][0] + bids[0][0]) / 2;
    const b = walkBuy(asks, amount);
    const coins = b.coins * (1 - taker);
    const s = walkSell(bids, coins);
    const sellSlip = s.avg / bids[0][0];                       // < 1: how far your sell eats into the book
    const at = (exitRef) => coins * exitRef * (venueMid / refMid) * sellSlip * (1 - taker) - amount - withdraw_usd;
    const buySlipPct = b.avg / asks[0][0] - 1;
    const breakEven = (amount + withdraw_usd) / (coins * sellSlip * (1 - taker)) * (refMid / venueMid);
    return { buyAvg: b.avg, coins, buySlipPct, sellSlipPct: 1 - sellSlip, spreadPct: asks[0][0] / bids[0][0] - 1,
      filled: b.filled && s.filled, netAt: at, breakEven, fees: amount * taker * 2, withdraw: withdraw_usd };
  }
  // Same for a DEX pool: swap fee + price impact (constant-product approximation) + gas + sandwich risk.
  function dexNet(o) {
    const { amount, priceUsd, liqUsd, swapFee, gasUsd, mev, refMid } = o;
    const half = Math.max(liqUsd / 2, 1);
    const impBuy = Math.min(amount / half, 0.5);
    const coins = Math.max(amount - gasUsd, 0) * (1 - swapFee) * (1 - impBuy) * (1 - mev) / priceUsd;
    const impSell = Math.min(coins * priceUsd / half, 0.5);
    const at = (exitRef) => coins * exitRef * (priceUsd / refMid) * (1 - swapFee) * (1 - impSell) * (1 - mev) - gasUsd - amount;
    const breakEven = (amount + gasUsd) / (coins * (1 - swapFee) * (1 - impSell) * (1 - mev)) * (refMid / priceUsd);
    return { coins, impBuy, impSell, netAt: at, breakEven, gasTotal: 2 * gasUsd, mevCost: mev * amount * 2 };
  }

  const api = { FEATURES, applyProbation, VENUES, DEX_FEE, CHAINS, walkBuy, walkSell, cexNet, dexNet, checkWalls, checkThinBook, checkWash, checkImpact, checkVenues, checkWhale, checkEngineered, combineChecks, applyIntegrity,
    mulberry32, thresholds, snapshotGaps, declutterExits, adaptivePenalty, BASE, diff, ewmMean, ewmStd, rStd, lastFeatures, riskUnit, predict, rankCoins, selectUniverse,
    makeTrade, advise, adviseNoPrice, adviseAsOf, adjustR, freshPrice, LIVE_MAX_AGE_MS, humanDuration, scoreFromR, expectedR, selectSmallUniverse, bigMoverStats };
  root.OmegaEngine = api;
  if (typeof module !== "undefined") module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
