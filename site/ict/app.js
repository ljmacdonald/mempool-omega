/* Mempool Omega ICT page. Crypto setups are found live in this browser (site/ictengine.js on Binance candles), with
   the Exchange coins page's fake-signal checks and exchange comparison (site/market.js); forex, gold and index futures
   come from the project's 15-minute snapshot. Every setup gets the same treatment as the other pages: warnings, news
   and manipulation checks, a score out of 10 and grade (switched off when the record says grades don't work), a risk
   level, the jumpiness adjustment and probation, a money calculator with leverage, and a track record vs random. */
(function () {
  "use strict";
  const E = window.OmegaEngine, I = window.OmegaICT, Q = window.OmegaQuality;
  const DATA = window.OMEGA_ICT_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/ict/";
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const CRYPTO_COST = 0.0022, PRIOR_K = 10, MIN_PROVEN = 30, LIVE_AGE = 90000, STOP_OUT = 0.5, CHECK_TOP = 6;
  const BUCKETS = [["0-3", 0, 3], ["4-5", 4, 5], ["6-9", 6, 9]];
  const GRADES = [[6.5, "Strong"], [5.6, "Moderate"], [5.0, "Weak"], [-1, "Avoid - watch only"]];
  const FACTOR_TEXT = {
    htf: ["1-hour structure agrees", "1-hour structure points the other way or is unclear"],
    discount: ["Entry in discount (lower half of the 24 h range)", "Entry not in discount"],
    ote: ["Gap inside the optimal trade entry zone (62–79%)", "Gap outside the optimal trade entry zone"],
    ob: ["Gap overlaps the order block", "No order block overlap"],
    killzone: ["Happened in a kill zone", "Outside the kill zones"],
    silver: ["Silver bullet hour", "Not a silver bullet hour"],
    smt: ["SMT divergence: the related market didn't confirm the sweep", "No SMT divergence"],
    major: ["Major liquidity swept", "Only a minor swing swept"],
    midnight: ["Entry on the right side of the New York midnight open", "Entry on the wrong side of the midnight open"],
  };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const state = { market: store.get("omega.ict.market", "crypto"), money: store.get("omega.ict.money", 100), lev: store.get("omega.ict.lev", 1),
    snap: null, snapAt: 0, crypto: null, cryptoMarkets: null, cryptoAt: 0, bySym1h: {}, uni: [], integrity: {}, checking: false,
    live: {}, trades: store.get("omega.ict.trades", []), charts: [], lastAction: {}, busy: false };
  const fmt = (x) => (!Number.isFinite(x) ? "–" : Math.abs(x) >= 1000 ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : Math.abs(x) >= 10 ? x.toFixed(3) : Math.abs(x) >= 1 ? x.toFixed(4) : x.toPrecision(5));
  const price = (x) => (Number.isFinite(x) ? `$${fmt(x)}` : "–");
  const pct = (x, d = 2) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const rr = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}R` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const nyTime = (ms) => new Date(ms).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const local = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const gradeOf = (sc) => GRADES.find(([th]) => sc >= th)[1];
  const riskClass = (r) => (r === "Low" ? "good" : r === "Medium" ? "calm" : r === "High" ? "warn" : "bad");
  const evidenceClass = (g) => (g === "Held up in tests" ? "good" : g === "Promising" ? "calm" : g === "Unproven" ? "warn" : "bad");
  async function getJSON(url, ms = 15000) {
    const c = new AbortController(); const tm = setTimeout(() => c.abort(), ms);
    try { const r = await fetch(url, { cache: "no-store", signal: c.signal }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); } finally { clearTimeout(tm); }
  }
  async function pool(items, n, fn) { const out = []; let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; try { out[k] = await fn(items[k]); } catch { out[k] = null; } } })); return out; }
  const M = window.OmegaMarket.create({ getJSON, store, setStatus: (t) => setStatus(t), adaptive: () => null, livePrice: (s) => livePx(s),
    pool, sleep, coinName: (s) => s.replace(/USDT$/, ""), price, usd, esc, BINANCE });
  const qual = () => (state.snap?.quality || {})[state.market] || null;
  const prob = () => (state.snap?.probation || {})[state.market] || null;

  // ------------------------------------------------------------------ sessions banner
  function paintZone() {
    const { mins } = I.nyParts(Date.now()); const wd = new Date().toLocaleDateString("en-US", { timeZone: "America/New_York", weekday: "short" });
    $("clock").textContent = `NY ${nyTime(Date.now())}`;
    const zones = [["London open kill zone", 120, 300], ["New York open kill zone", 420, 600], ["London close kill zone", 600, 720]];
    const silver = [["3–4 am", 180, 240], ["10–11 am", 600, 660], ["2–3 pm", 840, 900]];
    const z = zones.find(([, a, b]) => mins >= a && mins < b); const sb = silver.find(([, a, b]) => mins >= a && mins < b);
    const next = zones.map(([n, a]) => [n, (a - mins + 1440) % 1440]).filter(([, d]) => d > 0).sort((x, y) => x[1] - y[1])[0];
    const weekend = state.market !== "crypto" && (wd === "Sat" || (wd === "Sun" && mins < 1080) || (wd === "Fri" && mins >= 1020));
    $("zone").className = `banner ${weekend ? "warn" : z ? "good" : "calm"}`;
    $("zone").innerHTML = weekend ? "<b>Forex and futures markets are closed for the weekend.</b> They reopen Sunday evening New York time."
      : z ? `<b>Now: ${esc(z[0])}</b>${sb ? ` · <b>silver bullet hour (${sb[0]} New York)</b>` : ""}. ICT setups formed now get the kill-zone tick.`
        : `Outside the kill zones. Next: <b>${esc(next[0])}</b> in ${Math.floor(next[1] / 60)} h ${next[1] % 60} min.`;
  }

  // ------------------------------------------------------------------ scoring (same as ict/run.py judge + market_checks news)
  function bucketOf(n) { return BUCKETS.find(([, lo, hi]) => n >= lo && n <= hi)[0]; }
  function judge(s, st, extraPen, vol, pr) {
    const b = ((st || {}).buckets || {})[bucketOf(s.count)] || {}; const n = b.filled || 0, w = b.wins || 0;
    const p0 = 1 / (1 + s.rr); const p = (w + p0 * PRIOR_K) / (n + PRIOR_K); const exp = p * s.rr - (1 - p) - s.cost_r;
    const evidence = n < MIN_PROVEN ? "Unproven" : !(b.avg_r > 0) ? "Weak history" : b.t >= 1.5 ? "Held up in tests" : "Promising";
    const pen = ((s.ctx || {}).pen || 0) + extraPen + (pr && pr.active ? pr.penalty || 0.1 : 0);
    const adj = E.adjustR(exp, s.risk_pct, vol) - pen; const sc = E.scoreFromR(adj);
    const rq = (st || {}).risk_q;
    const risk = !rq ? null : s.risk_pct < rq[0] ? "Low" : s.risk_pct < rq[1] ? "Medium" : s.risk_pct < rq[2] ? "High" : "Very high";
    return { prob: p, exp_r: exp, exp_r_adj: adj, score: sc, grade: gradeOf(sc), evidence, risk_level: risk, bucket: bucketOf(s.count), hist_n: n, hist_avg_r: b.avg_r, hist_fill: b.fill_rate };
  }
  function newsChecks(s) {
    const ev = state.snap?.events || []; const now = Date.now(); const before = (state.snap?.news_before_min || 30) * 60000;
    const closeBy = (s.fill_t || s.t) + I.P.hold_bars * 900000; const usdEv = ev.filter((e) => e.country === "USD");
    const soon = usdEv.find((e) => Date.parse(e.at) >= now && Date.parse(e.at) <= now + before);
    const inside = usdEv.find((e) => Date.parse(e.at) >= now && Date.parse(e.at) <= closeBy);
    const checks = [], hard = []; let pen = 0;
    if (soon) { hard.push(`${soon.title} (USD) in the next ${Math.round(before / 60000)} minutes: no new orders until it's out.`); checks.push({ key: "news_soon", ok: false, text: hard[0] }); }
    if (inside) { pen += 0.25; checks.push({ key: "news_window", ok: false, text: `High-impact news before this trade's time limit: ${inside.title} (USD) at ${local(Date.parse(inside.at))}. Big US announcements move crypto too, and prices can jump straight through stops.` }); }
    else checks.push({ key: "news_window", ok: true, text: "No high-impact US news before the trade's time limit." });
    return { checks, hard, pen };
  }
  function ctxText(c, s) {
    const dir = s.side === "buy" ? "up" : "down";
    if (c.key === "chasing") return c.ok ? `Not chasing: ${pct(c.move, 1)} in the trade's direction over 24 hours.` : `Already ${dir} ${pct(Math.abs(c.move), 1).replace("+", "")} in the last 24 hours (more than ${Math.round(c.limit * 1000) / 10}% for this market). Entering after a big run often means ${s.side === "buy" ? "buying the top" : "selling the bottom"}.`;
    if (c.key === "overheated") return c.ok ? `Momentum not overheated (RSI ${Math.round(c.rsi)} in the trade's direction).` : `Momentum is overheated in the trade's direction (RSI ${Math.round(c.rsi)}). Pullbacks are common after this.`;
    if (c.key === "reference") return c.ok ? `The reference coin (${s.sym === "BTCUSDT" ? "ETH" : "BTC"}) isn't moving against the trade.` : `The reference coin (${s.sym === "BTCUSDT" ? "ETH" : "BTC"}) moved ${pct(Math.abs(c.move), 1).replace("+", "")} against this trade in 24 hours. Most coins follow it.`;
    return c.text || c.key;
  }

  // ------------------------------------------------------------------ data
  async function loadSnap(force) {
    if (!force && state.snap && Date.now() - state.snapAt < 60000) return state.snap;
    state.snap = await getJSON(DATA + "snapshot.json"); state.snapAt = Date.now(); return state.snap;
  }
  async function klines(sym, interval, limit) {
    const r = await getJSON(`${BINANCE}/klines?symbol=${sym}&interval=${interval}&limit=${limit}`);
    const step = interval === "15m" ? 900000 : 3600000; const now = Date.now();
    const rows = r.filter((k) => +k[0] + step <= now);          // closed candles only
    return { t: rows.map((k) => +k[0]), open: rows.map((k) => +k[1]), high: rows.map((k) => +k[2]), low: rows.map((k) => +k[3]), close: rows.map((k) => +k[4]),
      volume: rows.map((k) => +k[5]), qv: rows.map((k) => +k[7]), n_trades: rows.map((k) => +k[8]), taker_buy_volume: rows.map((k) => +k[9]) };
  }
  async function scanCrypto() {
    const list = (state.snap?.classes?.crypto?.markets || []).map((m) => m.sym);
    const syms = list.length ? list : ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT"];
    const c15 = {}, c1h = {};
    const [tick] = await Promise.all([getJSON(`${BINANCE}/ticker/24hr?symbols=${encodeURIComponent(JSON.stringify(syms))}`).catch(() => []),
      pool(syms, 6, async (s) => { [c15[s], c1h[s]] = await Promise.all([klines(s, "15m", 700), klines(s, "1h", 400)]); })]);
    state.uni = (tick || []).map((t) => ({ symbol: t.symbol, lastPrice: +t.lastPrice, change: +t.priceChangePercent, quoteVolume: +t.quoteVolume }));
    state.bySym1h = c1h;
    const st = state.snap?.stats?.crypto; const q = (state.snap?.quality || {}).crypto; const pr = (state.snap?.probation || {}).crypto;
    const out = [], held = [], markets = [];
    for (const s of syms) {
      const d = c15[s]; if (!d || d.t.length < 300) continue;
      const n = d.t.length; const a1 = c1h[s] && c1h[s].t.length ? I.arrays(c1h[s]) : null;
      const bias = a1 ? I.htfBias(a1, [d.t[n - 1]]).slice(-1)[0] : 0;
      markets.push({ sym: s, label: s.replace(/USDT$/, ""), last: d.close[n - 1], bias, chg24: n > 97 ? d.close[n - 1] / d.close[n - 97] - 1 : null });
      const corr = c15[s === "BTCUSDT" ? "ETHUSDT" : "BTCUSDT"];
      const found = I.setups(d, c1h[s], corr, CRYPTO_COST, Math.max(0, n - 250), "crypto");
      const chart = { t: d.t.slice(-96), open: d.open.slice(-96), high: d.high.slice(-96), low: d.low.slice(-96), close: d.close.slice(-96) };
      for (const x of found) {
        if (x.status !== "pending" && x.status !== "active") continue;
        const fillT = x.fill >= 0 ? d.t[x.fill] : null;
        const base = { ...x, id: `${s}|${x.side}|${x.t}`, sym: s, label: s.replace(/USDT$/, ""), fill_t: fillT, expires_t: x.t + I.P.fill_bars * 900000,
          close_by_t: (fillT || x.t) + I.P.hold_bars * 900000, chart };
        const mc = newsChecks(base);
        const row = { ...base, checks: mc.checks, hard: mc.hard, mkt_pen: mc.pen, ...judge(base, st, mc.pen, q && q.vol, pr) };
        (mc.hard.length && x.status === "pending" ? held : out).push(row);
      }
    }
    state.crypto = out; state.cryptoHeld = held; state.cryptoMarkets = markets; state.cryptoAt = Date.now();
  }
  // fake-signal checks (order books read 3 times, trades, other exchanges) on the best few coin setups, as on the Exchange coins page
  async function checkCrypto() {
    if (state.checking || !state.crypto || !state.crypto.length) return;
    state.checking = true;
    try {
      const syms = [...new Set(currentSetups().map((s) => s.sym))].slice(0, CHECK_TOP).filter((s) => !state.integrity[s] || Date.now() - state.integrity[s].at > 600000);
      if (!syms.length) return;
      const btc = state.bySym1h.BTCUSDT;
      const ideas = syms.map((s) => ({ symbol: s, features: state.bySym1h[s] && btc ? E.lastFeatures(state.bySym1h[s], btc) : null }));
      const res = await M.integrityFor(ideas, state.bySym1h, state.uni);
      for (const s of syms) state.integrity[s] = { ...(res[s] || null), at: Date.now() };
    } catch { /* checks unavailable: shown as such */ } finally { state.checking = false; }
    if (state.market === "crypto") render();
  }
  async function pollPrices() {
    if (document.hidden) return;
    try {
      if (state.market === "crypto" || state.trades.some((t) => t.class === "crypto")) {
        const all = await getJSON(`${BINANCE}/ticker/price`); const now = Date.now();
        const want = new Set((state.snap?.classes?.crypto?.markets || []).map((m) => m.sym).concat(state.trades.map((t) => t.sym)));
        for (const p of all) if (want.has(p.symbol)) state.live[p.symbol] = { price: +p.price, at: now };
      }
      if (state.market === "fx" || state.trades.some((t) => t.class === "fx")) {
        const [cb, gold] = await Promise.allSettled([getJSON("https://api.coinbase.com/v2/exchange-rates?currency=USD"), getJSON(`${BINANCE}/ticker/price?symbol=PAXGUSDT`)]);
        const now = Date.now(); const markets = state.snap?.classes?.fx?.markets || [];
        if (cb.status === "fulfilled") {
          const r = cb.value.data.rates;
          for (const m of markets) {
            if (m.sym === "XAUUSD") continue;
            const b = m.sym.slice(0, 3), q = m.sym.slice(3); const rb = b === "USD" ? 1 : +r[b], rq = q === "USD" ? 1 : +r[q]; const px = rq / rb;
            if (px > 0 && Math.abs(px / m.last - 1) < 0.015) state.live[m.sym] = { price: px, at: now };   // far from the last candle: a bad quote, ignored
          }
        }
        const g = markets.find((m) => m.sym === "XAUUSD");
        if (gold.status === "fulfilled" && g && Math.abs(+gold.value.price / g.last - 1) < 0.02) state.live.XAUUSD = { price: +gold.value.price, at: now };
      }
    } catch { /* next round */ }
    paintPrices(); checkTrades();
  }
  const livePx = (sym) => E.freshPrice(state.live[sym], LIVE_AGE);

  // ------------------------------------------------------------------ setups
  function withIntegrity(s) {
    const it = state.market === "crypto" ? state.integrity[s.sym] : null;
    if (!it || !Number.isFinite(it.penalty) || !it.penalty) return s;
    const adj = s.exp_r_adj - it.penalty; const sc = E.scoreFromR(adj);
    return { ...s, exp_r_adj: adj, score: sc, grade: gradeOf(sc) };
  }
  function currentSetups() {
    const now = Date.now();
    const raw = state.market === "crypto" && state.crypto ? state.crypto : (state.snap?.classes?.[state.market]?.setups || []);
    return raw.filter((s) => (s.status === "pending" ? now < s.expires_t : now < s.close_by_t)).map(withIntegrity)
      .sort((a, b) => (a.status !== b.status ? (a.status === "pending" ? -1 : 1) : b.score - a.score)).map((s, i) => ({ ...s, rank: i + 1 }));
  }
  const heldBack = () => (state.market === "crypto" && state.crypto ? state.cryptoHeld || [] : state.snap?.classes?.[state.market]?.held_back || []);
  const marketsNow = () => (state.market === "crypto" && state.cryptoMarkets ? state.cryptoMarkets : state.snap?.classes?.[state.market]?.markets || []);
  const fromSnapshot = () => !(state.market === "crypto" && state.crypto);
  function statusText(s) {
    const snapNote = fromSnapshot() ? ` (as of the ${new Date(Date.parse(state.snap.generated_at)).toISOString().slice(11, 16)} UTC update)` : "";
    return s.status === "pending" ? `Waiting for the price to come back to ${fmt(s.entry)} (a limit order). Valid until ${local(s.expires_t)}${snapNote}.`
      : `Entry reached ${s.fill_t ? local(s.fill_t) : ""}: the trade is running to its take profit or safety exit${snapNote}.`;
  }
  function priceFact(s) {
    const px = livePx(s.sym);
    if (!px) return `<div class="k">Price now</div><div class="v">–</div><div class="d muted">${state.market === "index" ? "no free live price: check your broker" : "waiting for the live price"}</div>`;
    const since = s.side === "buy" ? px / s.suggested - 1 : s.suggested / px - 1;
    return `<div class="k">Price now (live)</div><div class="v">${fmt(px)}</div><div class="d ${since >= 0 ? "up" : "down"}">${pct(since)} for this idea since suggested</div>`;
  }
  function why(s) {
    const out = [`Swept the ${s.level} at ${fmt(s.level_px)} (to ${fmt(s.sweep)}) and closed back ${s.side === "buy" ? "above" : "below"} it: the stop hunt ICT looks for.`,
      `Then broke the last swing ${s.side === "buy" ? "high" : "low"} with a strong candle${s.killzone ? `, in the ${s.killzone} kill zone` : ""}, leaving a fair value gap at ${fmt(Math.min(s.fvg_bot, s.fvg_top))}–${fmt(Math.max(s.fvg_bot, s.fvg_top))}.`,
      `Take profit at the ${s.target_name}, ${s.rr.toFixed(1)} times the risk away.`];
    for (const f of I.FACTORS) if (s.conf[f] && f !== "killzone" && f !== "major") out.push(FACTOR_TEXT[f][0] + ".");
    return out;
  }
  function calcHTML(s) {
    const amt = state.money, lev = state.lev, pos = amt * lev; const costPct = s.cost_r * s.risk_pct;
    const res = (exitPx) => { const mv = s.side === "buy" ? exitPx / s.entry - 1 : s.entry / exitPx - 1; return pos * mv - pos * costPct; };
    const row = (label, x) => { const v = res(x); return `<tr><td>${label}</td><td class="num">${fmt(x)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v)}</td><td class="num muted">${pct(v / amt, 1)}</td></tr>`; };
    const px = livePx(s.sym); const stopOut = STOP_OUT / lev;
    return `<div class="calc"><h3>Profit calculator: $${amt.toLocaleString()} of your money, ${lev === 1 ? "no leverage" : `${lev}x leverage`} (a $${pos.toLocaleString()} position)</h3>
      <div class="table-wrap"><table><tbody>${row("If it reaches the take profit", s.target)}${row("If it hits the safety exit", s.stop)}
      ${px && s.status === "active" ? row("If you closed right away (live price)", px) : `<tr><td>If you closed right away</td><td colspan="3" class="muted">${s.status === "active" ? "shown with a live price" : "not filled yet: the order waits at the entry price"}</td></tr>`}</tbody></table></div>
      ${lev > 1 ? `<div class="banner ${s.risk_pct >= stopOut ? "bad" : "warn"}">At ${lev}x, a move of about ${pct(stopOut, 1).replace("+", "")} against you loses half your money and most platforms close the trade.${s.risk_pct >= stopOut ? " That's <b>before</b> the safety exit: lower the leverage." : ""}</div>` : ""}
      <p class="small muted">After costs (about ${pct(costPct, 3).replace("+", "")} of the position). To risk exactly $10 at the safety exit, use a $${Math.round(10 / s.risk_pct).toLocaleString()} position. Change "Your money" and "Leverage" at the top.</p></div>`;
  }
  function card(s, i) {
    const allChecks = [...((s.ctx || {}).checks || []).map((c) => ({ ok: c.ok, text: ctxText(c, s) })), ...(s.checks || [])];
    const bad = allChecks.filter((c) => c.ok === false);
    const pr = prob(); const warns = bad.map((c) => c.text).concat(pr && pr.active ? [`ICT on this market is on probation: its last ${pr.n} live setups averaged ${rr(pr.avg_r)}, worse than coin-flip entries with the same stops (${rr(pr.random_avg_r)}), so scores are lowered.`] : []);
    const held = state.trades.some((t) => t.id === s.id); const isCrypto = state.market === "crypto";
    const ticks = I.FACTORS.map((f) => `<li>${s.conf[f] ? '<span class="ok">✓</span>' : '<span class="na">✗</span>'}<span>${esc(FACTOR_TEXT[f][s.conf[f] ? 0 : 1])}</span></li>`).join("");
    const it = isCrypto ? state.integrity[s.sym] : null;
    return `<article class="card">
      <div class="idea-head"><h2><span class="rank">${s.rank}.</span><span class="pill ${s.side === "buy" ? "good" : "bad"}" style="font-size:1rem">${s.side === "buy" ? "BUY" : "SELL"}</span> ${esc(s.label)}</h2>
        <div class="score"><b>${s.score.toFixed(1)}</b><span class="muted">/ 10</span><span class="pill ${Q.gradeClassFor(s.grade, qual())}">${esc(Q.gradeWord(s.grade, qual(), s.rank))}</span>${s.risk_level ? `<span class="pill ${riskClass(s.risk_level)}">Risk: ${esc(s.risk_level)}</span>` : ""}</div></div>
      <p><b>${esc(statusText(s))}</b></p>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${local(s.t + 900000)}</div><div class="d muted">at ${fmt(s.suggested)}</div></div>
        <div class="fact" data-px="${esc(s.id)}">${priceFact(s)}</div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${fmt(s.target)}</div><div class="d up">${pct(Math.abs(s.target / s.entry - 1))} from entry</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${fmt(s.stop)}</div><div class="d down">${pct(-s.risk_pct)} from entry</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(s.close_by_t)}</div><div class="d muted">${nyTime(s.close_by_t)} New York</div></div>
      </div>
      <div class="chart" id="ict-chart-${i}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Entry</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span><span>Dotted: the fair value gap · 15-minute candles</span></div>
      <div class="facts">
        <div class="fact"><div class="k">Entry (limit order)</div><div class="v">${fmt(s.entry)}</div><div class="d muted">middle of the gap</div></div>
        <div class="fact"><div class="k">Chance target first</div><div class="v">${Math.round(s.prob * 100)}%</div><div class="d muted">break-even needs ${Math.round(100 / (1 + s.rr))}%</div></div>
        <div class="fact"><div class="k">Expected, after costs</div><div class="v ${s.exp_r_adj >= 0 ? "up" : "down"}">${rr(s.exp_r_adj)}</div><div class="d muted">per $1 risked, after warnings</div></div>
        <div class="fact"><div class="k">Reward : risk</div><div class="v">${s.rr.toFixed(1)} : 1</div><div class="d muted">costs ${s.cost_r.toFixed(2)}R</div></div>
        <div class="fact"><div class="k">Test evidence</div><div class="v"><span class="pill ${evidenceClass(s.evidence)}">${esc(s.evidence)}</span></div><div class="d muted">${s.hist_n} similar past fills</div></div>
      </div>
      <div><h3>Why it was picked</h3><ul class="why">${why(s).map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${warns.length ? `<div class="warnings">${warns.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      <details class="checks"><summary>Market checks (news${state.market !== "crypto" ? ", rate fixes, thin hours" : ""}, chasing, momentum${isCrypto ? ", reference coin" : ""}): <span class="pill ${bad.length ? "warn" : "good"}">${allChecks.length - bad.length} clean${bad.length ? `, ${bad.length} warning${bad.length > 1 ? "s" : ""}` : ""}</span></summary>
        <ul>${allChecks.map((c) => `<li>${c.ok ? '<span class="ok">✓</span>' : '<span class="bad">✗</span>'}<span>${esc(c.text)}</span></li>`).join("")}</ul></details>
      ${isCrypto ? M.checksBlock(it && Number.isFinite(it.checked) ? it : null, !it && state.checking) : ""}
      <details class="checks"><summary>ICT checklist: <span class="pill ${s.count >= 6 ? "good" : s.count >= 4 ? "calm" : "warn"}">${s.count} of 9</span></summary><ul>${ticks}</ul></details>
      ${calcHTML(s)}
      ${isCrypto ? (s.side === "buy" ? `<details class="venues" data-id="${esc(s.id)}"><summary>Where to buy it cheapest: actual money you'd take out after all fees</summary><div class="venues-out"></div></details>`
        : `<p class="small muted">Selling short needs margin or futures, which this page doesn't compare. On spot exchanges a sell setup only means: if you hold this coin, consider selling.</p>`) : ""}
      <div class="actions">${held ? `<span class="pill calm">You're watching this setup</span>` : `<button class="btn primary" type="button" data-take="${esc(s.id)}">I placed this order: watch it for me</button>`}</div>
    </article>`;
  }
  function drawChart(el, ch, s) {
    if (!el || !ch || !window.LightweightCharts) { if (el) el.innerHTML = `<p class="small muted" style="padding:12px">Chart unavailable.</p>`; return null; }
    const chart = LightweightCharts.createChart(el, { autoSize: true, layout: { background: { color: cssVar("--surface") }, textColor: cssVar("--muted"), fontFamily: cssVar("--f-num") },
      grid: { vertLines: { color: cssVar("--sunken") }, horzLines: { color: cssVar("--sunken") } }, rightPriceScale: { borderColor: cssVar("--line") },
      timeScale: { borderColor: cssVar("--line"), timeVisible: true, secondsVisible: false }, crosshair: { mode: 0 }, handleScroll: false, handleScale: false });
    const last = ch.close[ch.close.length - 1]; const prec = last >= 1000 ? 2 : last >= 10 ? 3 : last >= 1 ? 4 : 6;
    const ser = chart.addCandlestickSeries({ upColor: cssVar("--good"), downColor: cssVar("--bad"), borderVisible: false, wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad"), priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    ser.setData(ch.t.map((t, i) => ({ time: t / 1000, open: ch.open[i], high: ch.high[i], low: ch.low[i], close: ch.close[i] })));
    for (const [p, c, title, style] of [[s.entry, "--calm", "Entry", 2], [s.target, "--good", "Take profit", 0], [s.stop, "--bad", "Safety exit", 0], [s.fvg_top, "--muted", "", 1], [s.fvg_bot, "--muted", "", 1]]) {
      ser.createPriceLine({ price: p, color: cssVar(c), lineWidth: title ? 2 : 1, lineStyle: style, axisLabelVisible: !!title, title });
    }
    const mt = Math.floor(s.t / 900000) * 900;
    if (ch.t.length && mt >= ch.t[0] / 1000) ser.setMarkers([{ time: Math.min(mt, ch.t[ch.t.length - 1] / 1000), position: s.side === "buy" ? "belowBar" : "aboveBar", color: cssVar("--calm"), shape: s.side === "buy" ? "arrowUp" : "arrowDown", text: "Structure shift" }]);
    chart.timeScale().fitContent();
    return chart;
  }
  function honest() {
    const st = state.snap?.stats?.[state.market]; const el = $("honest");
    if (!st) { el.className = "banner warn"; el.innerHTML = "The backtest hasn't run yet, so chances can't be measured. Treat every setup as unproven."; return; }
    const o = st.overall; const bs = st.buckets; const good = o.avg_r > 0 && o.t >= 1.5;
    const hi = bs["6-9"] || {}, lo = bs["0-3"] || {}; const more = Number.isFinite(hi.avg_r) && Number.isFinite(lo.avg_r) && hi.filled >= 15 && lo.filled >= 15 ? (hi.avg_r > lo.avg_r ? "Setups with more checklist items did better." : "More checklist items did <b>not</b> lead to better results.") : "";
    const vsRandom = Number.isFinite(o.random_avg_r) ? ` Coin-flip entries with the same stops and targets: ${rr(o.random_avg_r)}, so ICT did <b>${o.avg_r > o.random_avg_r ? "better" : "worse"} than random</b>.` : "";
    el.className = `banner ${good ? "good" : o.avg_r > 0 ? "calm" : "bad"}`;
    el.innerHTML = `<b>Honest test result (${esc(state.snap.classes[state.market].label)}, last ${Math.round(st.days)} days):</b> ${o.filled} setups filled; ${Math.round((o.win_rate || 0) * 100)}% reached the target first; average ${rr(o.avg_r)} per trade after costs${good ? " (a positive edge in this period)" : o.avg_r > 0 ? ", positive but not clearly more than luck" : ", i.e. <b>these rules lost money</b> in this period"}.${vsRandom} ${more} <a href="#/record" data-goto="record">Details</a>.`;
  }
  function mood() {
    const ms = marketsNow(); const up = ms.filter((m) => m.bias > 0).length, down = ms.filter((m) => m.bias < 0).length;
    const list = currentSetups(); const pend = list.filter((s) => s.status === "pending").length; const hb = heldBack();
    const label = up > down * 1.5 ? "Rising" : down > up * 1.5 ? "Falling" : "Mixed";
    $("mood").className = `banner ${list.length ? "calm" : "warn"}`;
    $("mood").innerHTML = `<b>Market mood: ${label}.</b> ${up} of ${ms.length} markets' 1-hour structure points up, ${down} down. ` +
      (list.length ? `<b>${pend} setup${pend === 1 ? "" : "s"} waiting for entry</b>${list.length > pend ? `, ${list.length - pend} already running` : ""}, sorted by score. A setup is not a recommendation: check its grade, warnings and the test results above.`
        : "<b>No ICT setup right now.</b> They need a liquidity sweep, a structure shift and a fair value gap together, which is rare. New ones are checked every 15 minutes.") +
      (hb.length ? ` ${hb.length} more held back: high-impact news is due within ${state.snap?.news_before_min || 30} minutes.` : "");
  }
  function render() {
    for (const c of state.charts) { try { c.remove(); } catch { /* gone */ } } state.charts = [];
    honest(); mood();
    const list = currentSetups().slice(0, 8);
    $("setups").innerHTML = `${Q.ideasBanner(qual())}<div style="display:grid;gap:14px">${list.map(card).join("")}</div>`;
    list.forEach((s, i) => { const c = drawChart($(`ict-chart-${i}`), s.chart, s); if (c) state.charts.push(c); });
    if (!$("tab-movers").hidden) renderMovers();
  }
  function paintPrices() { for (const s of currentSetups()) { const el = document.querySelector(`[data-px="${CSS.escape(s.id)}"]`); if (el) el.innerHTML = priceFact(s); } if (!$("tab-trades").hidden) renderTrades(); }

  async function refresh(force) {
    if (state.busy) return; state.busy = true; $("refreshNow").disabled = true;
    try {
      setStatus("Loading…");
      try { await loadSnap(force); } catch (e) { if (!state.snap) { $("honest").className = "banner warn"; $("honest").textContent = `Couldn't load the test results (${e.message || e}).`; } }
      if (state.market === "crypto") {
        setStatus("Finding ICT setups on 15 coins, live…");
        try { await scanCrypto(); } catch { state.crypto = null; }
      }
      render(); await pollPrices();
      const age = state.snap ? Math.round((Date.now() - Date.parse(state.snap.generated_at)) / 60000) : null;
      setStatus(state.market === "crypto" && state.crypto ? `Live: checked ${new Date(state.cryptoAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · re-checks every 5 min` : age !== null ? `From the update ${age} min ago` : "No data yet");
    } finally { state.busy = false; $("refreshNow").disabled = false; }
    if (state.market === "crypto") checkCrypto();
  }
  function setStatus(t) { $("status").textContent = t; }

  // ------------------------------------------------------------------ movers
  function renderMovers() {
    const ms = marketsNow().filter((m) => Number.isFinite(m.chg24)).sort((a, b) => Math.abs(b.chg24) - Math.abs(a.chg24)).slice(0, 12);
    const sets = currentSetups();
    $("movers").innerHTML = ms.length ? `<div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">24 h change</th><th>1-hour structure</th><th style="min-width:16em">ICT setup now?</th></tr></thead><tbody>
      ${ms.map((m) => { const s = sets.find((x) => x.sym === m.sym);
        return `<tr><td><b>${esc(m.label)}</b></td><td class="num ${m.chg24 >= 0 ? "up" : "down"}">${pct(m.chg24)}</td><td>${m.bias > 0 ? "Up" : m.bias < 0 ? "Down" : "Unclear"}</td>
          <td class="small" style="white-space:normal">${s ? `${s.side === "buy" ? "Buy" : "Sell"} setup, #${s.rank}, score ${s.score.toFixed(1)}` : "No setup: it needs a liquidity sweep, then a structure shift with a fair value gap. A big move alone isn't one."}</td></tr>`; }).join("")}
      </tbody></table></div><p class="small muted">Information only. A market that just moved a lot is not a reason to trade it.</p>` : `<div class="banner calm">No market data yet.</div>`;
  }

  // ------------------------------------------------------------------ track record tab
  function renderRecord() {
    const S = state.snap; if (!S) return;
    const row = (name, s) => `<tr><td>${esc(name)}</td><td class="num">${s.filled ?? 0}</td><td class="num">${Number.isFinite(s.fill_rate) ? Math.round(s.fill_rate * 100) + "%" : "–"}</td><td class="num">${Number.isFinite(s.win_rate) ? Math.round(s.win_rate * 100) + "%" : "–"}</td><td class="num ${s.avg_r > 0 ? "up" : "down"}">${rr(s.avg_r)}</td><td class="num">${rr(s.random_avg_r)}</td></tr>`;
    let html = "";
    const k = state.market; const c = S.classes[k]; const st = S.stats?.[k];
    if (st) {
      html += `<article class="card"><h3>${esc(c.label)}: tested on the last ${Math.round(st.days)} days of 15-minute prices</h3>
        <div class="table-wrap"><table><thead><tr><th>Setups</th><th class="num">Filled</th><th class="num">Fill rate</th><th class="num">Target first</th><th class="num">Average after costs</th><th class="num">Random entry</th></tr></thead><tbody>
        ${row("All", st.overall)}${BUCKETS.map(([b]) => row(`${b} checklist items`, st.buckets[b] || {})).join("")}</tbody></table></div>
        <p class="small muted">"Random entry": entering at the same moment in a random direction with the same stop distance, reward:risk and costs. If ICT doesn't beat it, the setups aren't adding value.</p>
        <details><summary>Does each checklist item help?</summary><div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">With it</th><th class="num">Without it</th></tr></thead><tbody>
        ${I.FACTORS.map((f) => { const x = st.factors[f]; return `<tr><td>${esc(FACTOR_TEXT[f][0])}</td><td class="num ${x.with_r > 0 ? "up" : "down"}">${rr(x.with_r)} <span class="muted small">(${x.with_n})</span></td><td class="num ${x.without_r > 0 ? "up" : "down"}">${rr(x.without_r)} <span class="muted small">(${x.without_n})</span></td></tr>`; }).join("")}
        </tbody></table></div></details></article>`;
    }
    const rec = (S.record || {})[k] || {}; const pr = (S.probation || {})[k] || {};
    html += `<article class="card"><h3>Live record: ${esc(c.label)} (recorded as setups appeared)</h3>
      <div class="table-wrap"><table><thead><tr><th>Finished</th><th class="num">Target first</th><th class="num">Average after costs</th><th class="num">Random entry</th></tr></thead><tbody>
      <tr><td class="num">${rec.filled ?? 0}</td><td class="num">${Number.isFinite(rec.win_rate) ? Math.round(rec.win_rate * 100) + "%" : "–"}</td><td class="num">${rr(rec.avg_r)}</td><td class="num">${rr(rec.random_avg_r)} ${Number.isFinite(rec.avg_r) && Number.isFinite(rec.random_avg_r) ? (rec.avg_r > rec.random_avg_r ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>') : ""}</td></tr></tbody></table></div>
      ${pr.active ? `<div class="banner warn"><b>On probation:</b> the live setups did worse than random entries, so their scores are lowered by ${pr.penalty}R until that changes.</div>` : `<p class="small muted">Probation starts if, over ${MIN_PROVEN} or more finished live setups, ICT does worse than random entries.</p>`}
      ${Q.gradeTable(qual())}
      ${((S.record || {}).recent || []).length ? `<h3>Latest finished setups (all markets)</h3><div class="table-wrap"><table><tbody>${S.record.recent.map((r) => `<tr><td>${esc(local(r.t))}</td><td>${r.side.toUpperCase()} ${esc(r.label)}</td><td>${esc(r.status)}</td><td class="num ${r.r >= 0 ? "up" : "down"}">${rr(r.r)}</td></tr>`).join("")}</tbody></table></div>` : ""}
      <p class="small muted">"R" is the amount risked: +2R means twice the risk was won, −1R means the safety exit was hit.</p></article>`;
    $("record").innerHTML = html;
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.ict.trades", state.trades); $("tradeCount").hidden = !state.trades.length; $("tradeCount").textContent = state.trades.length; }
  function tradeAdvice(t) {
    const now = Date.now(); const px = livePx(t.sym);
    const up = (x) => (t.side === "buy" ? x : -x);
    if (!t.filled_at) {
      if (px && up(px) <= up(t.entry)) { t.filled_at = now; saveTrades(); }
      else if (px && up(px) >= up(t.target)) return { action: "Missed: cancel your order", level: "warn", why: "The price reached the take profit without coming back to your entry. Cancel the limit order." };
      else if (now > t.expires_t) return { action: "Order expired: cancel it", level: "warn", why: "The setup's 4-hour window has passed without a fill. Cancel the limit order." };
      else if (!px) return { action: "Waiting for a live price", level: "calm", why: "The live price couldn't be confirmed, so this page can't tell whether your order filled. Check your broker or exchange." };
      else return { action: "Order waiting", level: "calm", why: `Your limit order at ${fmt(t.entry)} hasn't filled yet. It's valid until ${local(t.expires_t)}.` };
    }
    const view = (x) => (t.side === "buy" ? x : 1 / x);
    const tv = { entry: view(t.entry), take_profit: view(t.target), safety_exit: view(t.stop), exit_by: t.close_by_t };
    if (!px) return E.adviseNoPrice(tv, now);
    const a = E.advise(tv, view(px), now, null);
    if (a.action === "Take profit now") a.action = "Take profit reached: close it";
    if (a.action === "Exit now") a.action = "Safety exit reached: close it";
    return a;
  }
  function checkTrades() { for (const t of state.trades) notify(t, tradeAdvice(t)); }
  function renderTrades() {
    saveTrades();
    if (!state.trades.length) { $("trades").innerHTML = `<div class="banner calm">No ICT trades yet. Tap “I placed this order” on a setup.</div>`; return; }
    $("trades").innerHTML = `<div style="display:grid;gap:14px">${state.trades.map((t) => {
      const a = tradeAdvice(t); const px = livePx(t.sym); const pos = (t.money || 100) * (t.lev || 1);
      const mv = t.filled_at && px ? (t.side === "buy" ? px / t.entry - 1 : t.entry / px - 1) : NaN; const v = pos * mv - pos * (t.cost_r * Math.abs(t.entry - t.stop) / t.entry);
      return `<article class="card trade ${a.level}"><div class="idea-head"><h2>${t.side.toUpperCase()} ${esc(t.label)}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div><p>${esc(a.why)}</p>
        <div class="strip"><div class="fact"><div class="k">Entry</div><div class="v">${fmt(t.entry)}</div><div class="d muted">${t.filled_at ? `filled about ${local(t.filled_at)}` : "not filled yet"}</div></div>
        <div class="fact"><div class="k">Price now${px ? " (live)" : ""}</div><div class="v">${fmt(px)}</div><div class="d ${!Number.isFinite(mv) ? "muted" : v >= 0 ? "up" : "down"}">${Number.isFinite(mv) ? `${usd(v)} on $${(t.money || 100).toLocaleString()} at ${t.lev || 1}x` : px ? "" : "not live right now"}</div></div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${fmt(t.target)}</div></div><div class="fact"><div class="k">Safety exit at</div><div class="v">${fmt(t.stop)}</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(t.close_by_t)}</div></div></div>
        <div class="actions"><button class="btn" type="button" data-done="${esc(t.id)}">Remove</button></div></article>`; }).join("")}</div>
      <p class="small muted">Fills are judged from live prices checked every 10–60 seconds, so a brief wick to your entry may be missed: your broker's order status is the real answer.</p>`;
  }
  function notify(t, a) {
    const prev = state.lastAction[t.id]; state.lastAction[t.id] = a.action;
    if (!prev || prev === a.action || ["Hold", "Order waiting", "Waiting for a live price"].includes(a.action)) return;
    document.title = `${a.action}: ${t.label} · Mempool Omega`;
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`${t.label}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "record") renderRecord(); if (name === "trades") renderTrades(); if (name === "movers") renderMovers();
    store.set("omega.ict.tab", name); if (location.hash !== `#/${name}`) history.replaceState(null, "", `${location.pathname}#/${name}`);
  }
  const press = () => { for (const b of document.querySelectorAll("#market button")) b.setAttribute("aria-pressed", String(b.dataset.market === state.market)); };
  document.addEventListener("click", (ev) => {
    const go = ev.target.closest("[data-goto]"); if (go) { ev.preventDefault(); selectTab(go.dataset.goto); }
    const take = ev.target.closest("[data-take]");
    if (take) {
      const s = currentSetups().find((x) => x.id === take.dataset.take);
      if (s) {
        state.trades.push({ id: s.id, sym: s.sym, label: s.label, class: state.market, side: s.side, entry: s.entry, stop: s.stop, target: s.target, cost_r: s.cost_r,
          expires_t: s.expires_t, close_by_t: s.status === "active" ? s.close_by_t : Date.now() + I.P.hold_bars * 900000, filled_at: s.status === "active" ? (s.fill_t || Date.now()) : null,
          money: state.money, lev: state.lev });
        saveTrades(); take.outerHTML = `<span class="pill good">Watching it. Open “My ICT trades”.</span>`;
      }
    }
    const done = ev.target.closest("[data-done]"); if (done) { state.trades = state.trades.filter((t) => t.id !== done.dataset.done); renderTrades(); }
  });
  document.addEventListener("toggle", async (ev) => {
    const d = ev.target; if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("venues") || !d.open) return;
    const s = currentSetups().find((x) => x.id === d.dataset.id); if (!s) return;
    const box = d.querySelector(".venues-out"); box.innerHTML = `<p class="small muted">Checking exchanges and decentralised exchanges for $${(state.money * state.lev).toLocaleString()}…</p>`;
    try { box.innerHTML = M.venueTable(await M.compareVenues({ symbol: s.sym, price_now: s.suggested, anchor: { take_profit: s.target, safety_exit: s.stop } }, state.money * state.lev)); }
    catch { box.innerHTML = `<p class="small muted">Couldn't load prices from other exchanges right now.</p>`; }
  }, true);
  for (const b of document.querySelectorAll("#market button")) b.addEventListener("click", () => { state.market = b.dataset.market; store.set("omega.ict.market", state.market); press(); paintZone(); render(); refresh(false); if (!$("tab-record").hidden) renderRecord(); });
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refreshNow").addEventListener("click", () => refresh(true));
  $("money").addEventListener("change", (e) => { state.money = Math.max(1, +e.target.value || 100); store.set("omega.ict.money", state.money); render(); });
  $("lev").addEventListener("change", (e) => { state.lev = +e.target.value || 1; store.set("omega.ict.lev", state.lev); render(); });
  $("notifyBtn").addEventListener("click", async () => { if (!("Notification" in window)) return; const p = await Notification.requestPermission(); $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off."; });
  window.addEventListener("omega-theme", () => { if (state.snap) render(); });

  $("money").value = String(state.money); $("lev").value = String(state.lev); press();
  const startTab = (location.hash || "").replace(/^#\/?/, "") || store.get("omega.ict.tab", "setups");
  selectTab(["setups", "movers", "record", "trades", "guide"].includes(startTab) ? startTab : startTab === "tests" ? "record" : "setups");
  saveTrades(); paintZone(); refresh(false);
  setInterval(paintZone, 30000);
  setInterval(() => { if (!document.hidden) pollPrices(); }, 10000);
  setInterval(() => { if (!document.hidden) refresh(false); }, 300000);
})();
