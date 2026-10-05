/* Mempool Omega ICT page. Crypto setups are found live in this browser (site/ictengine.js on Binance candles);
   forex, gold and index futures come from the project's 15-minute snapshot. Chances come from the nightly backtest. */
(function () {
  "use strict";
  const E = window.OmegaEngine, I = window.OmegaICT;
  const DATA = window.OMEGA_ICT_DATA || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/data/ict/";
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const CRYPTO_COST = 0.0022, PRIOR_K = 10, MIN_PROVEN = 30;
  const BUCKETS = [["0-3", 0, 3], ["4-5", 4, 5], ["6-9", 6, 9]];
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
  const LIVE_AGE = 90000;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const state = { market: store.get("omega.ict.market", "crypto"), risk: store.get("omega.ict.risk", 10), snap: null, snapAt: 0,
    crypto: null, cryptoAt: 0, live: {}, trades: store.get("omega.ict.trades", []), charts: [], lastAction: {}, busy: false };
  const fmt = (x) => (!Number.isFinite(x) ? "–" : Math.abs(x) >= 1000 ? x.toLocaleString("en-US", { maximumFractionDigits: 2 }) : Math.abs(x) >= 10 ? x.toFixed(3) : Math.abs(x) >= 1 ? x.toFixed(4) : x.toPrecision(5));
  const pct = (x, d = 2) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const rr = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(2)}R` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toFixed(2)}` : "–");
  const nyTime = (ms) => new Date(ms).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });
  const local = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  async function getJSON(url, ms = 15000) {
    const c = new AbortController(); const tm = setTimeout(() => c.abort(), ms);
    try { const r = await fetch(url, { cache: "no-store", signal: c.signal }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); } finally { clearTimeout(tm); }
  }

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

  // ------------------------------------------------------------------ chance of the target first (same formula as ict/run.py judge)
  function bucketOf(n) { return BUCKETS.find(([, lo, hi]) => n >= lo && n <= hi)[0]; }
  function judge(s, st) {
    const b = ((st || {}).buckets || {})[bucketOf(s.count)] || {}; const n = b.filled || 0, w = b.wins || 0;
    const p0 = 1 / (1 + s.rr); const p = (w + p0 * PRIOR_K) / (n + PRIOR_K); const exp = p * s.rr - (1 - p) - s.cost_r;
    const grade = n < MIN_PROVEN ? "Unproven" : !(b.avg_r > 0) ? "Weak history" : b.t >= 1.5 ? "Held up in tests" : "Promising";
    return { prob: p, exp_r: exp, grade, bucket: bucketOf(s.count), hist_n: n, hist_avg_r: b.avg_r, hist_fill: b.fill_rate };
  }
  const gradeClass = (g) => (g === "Held up in tests" ? "good" : g === "Promising" ? "calm" : g === "Unproven" ? "warn" : "bad");

  // ------------------------------------------------------------------ data
  async function loadSnap(force) {
    if (!force && state.snap && Date.now() - state.snapAt < 60000) return state.snap;
    state.snap = await getJSON(DATA + "snapshot.json"); state.snapAt = Date.now(); return state.snap;
  }
  async function klines(sym, interval, limit) {
    const r = await getJSON(`${BINANCE}/klines?symbol=${sym}&interval=${interval}&limit=${limit}`);
    const step = interval === "15m" ? 900000 : 3600000; const now = Date.now();
    const rows = r.filter((k) => +k[0] + step <= now);          // closed candles only
    return { t: rows.map((k) => +k[0]), open: rows.map((k) => +k[1]), high: rows.map((k) => +k[2]), low: rows.map((k) => +k[3]), close: rows.map((k) => +k[4]) };
  }
  async function pool(items, n, fn) { const out = []; let i = 0; await Promise.all(Array.from({ length: n }, async () => { while (i < items.length) { const k = i++; try { out[k] = await fn(items[k]); } catch { out[k] = null; } } })); return out; }
  async function scanCrypto() {
    const syms = (state.snap?.classes?.crypto?.markets || []).map((m) => m.sym);
    const list = syms.length ? syms : ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT", "TRXUSDT", "BCHUSDT", "NEARUSDT", "SUIUSDT"];
    const c15 = {}, c1h = {};
    await pool(list, 6, async (s) => { [c15[s], c1h[s]] = await Promise.all([klines(s, "15m", 700), klines(s, "1h", 400)]); });
    const st = state.snap?.stats?.crypto; const out = [];
    for (const s of list) {
      const d = c15[s]; if (!d || d.t.length < 300) continue;
      const corr = c15[s === "BTCUSDT" ? "ETHUSDT" : "BTCUSDT"];
      const found = I.setups(d, c1h[s], corr, CRYPTO_COST, Math.max(0, d.t.length - 250));
      const n = d.t.length; const chart = { t: d.t.slice(-96), open: d.open.slice(-96), high: d.high.slice(-96), low: d.low.slice(-96), close: d.close.slice(-96) };
      for (const x of found) {
        if (x.status !== "pending" && x.status !== "active") continue;
        const fillT = x.fill >= 0 ? d.t[x.fill] : null;
        out.push({ ...x, id: `${s}|${x.side}|${x.t}`, sym: s, label: s.replace(/USDT$/, ""), fill_t: fillT, expires_t: x.t + I.P.fill_bars * 900000,
          close_by_t: (fillT || x.t) + I.P.hold_bars * 900000, chart, ...judge(x, st), lastBar: d.t[n - 1] });
      }
    }
    state.crypto = out; state.cryptoAt = Date.now();
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
  function currentSetups() {
    const now = Date.now();
    const raw = state.market === "crypto" && state.crypto ? state.crypto : (state.snap?.classes?.[state.market]?.setups || []);
    return raw.filter((s) => (s.status === "pending" ? now < s.expires_t : now < s.close_by_t))
      .sort((a, b) => (a.status !== b.status ? (a.status === "pending" ? -1 : 1) : b.exp_r - a.exp_r));
  }
  function statusText(s) {
    const snapNote = state.market === "crypto" && state.crypto ? "" : ` (as of the ${new Date(Date.parse(state.snap.generated_at)).toISOString().slice(11, 16)} UTC update)`;
    return s.status === "pending" ? `Waiting for the price to come back to ${fmt(s.entry)}. Order valid until ${local(s.expires_t)}${snapNote}.`
      : `Entry reached ${s.fill_t ? local(s.fill_t) : ""}: the trade is running to its target or stop, closing by ${local(s.close_by_t)}${snapNote}.`;
  }
  function priceFact(s) {
    const px = livePx(s.sym);
    if (!px) return `<div class="k">Price now</div><div class="v">–</div><div class="d muted">${state.market === "index" ? "no free live price: check your broker" : "waiting for the live price"}</div>`;
    const toEntry = s.side === "buy" ? px / s.entry - 1 : s.entry / px - 1;
    return `<div class="k">Price now (live)</div><div class="v">${fmt(px)}</div><div class="d muted">${s.status === "pending" ? `${pct(Math.abs(toEntry))} ${toEntry >= 0 ? "above" : "below"} the entry` : ""}</div>`;
  }
  function card(s, i) {
    const R = state.risk; const pos = R / s.risk_pct; const win = R * s.rr - R * s.cost_r, loss = -R - R * s.cost_r;
    const ticks = I.FACTORS.map((f) => `<li>${s.conf[f] ? '<span class="ok">✓</span>' : '<span class="na">✗</span>'}<span>${esc(FACTOR_TEXT[f][s.conf[f] ? 0 : 1])}</span></li>`).join("");
    const hist = Number.isFinite(s.hist_avg_r) ? `Past setups like this (${esc(s.bucket)} checklist items): ${s.hist_n} filled, average ${rr(s.hist_avg_r)} after costs.` : "Not enough past setups like this to judge yet.";
    const held = state.trades.some((t) => t.id === s.id);
    return `<article class="card">
      <div class="idea-head"><h2><span class="rank">${i + 1}.</span><span class="pill ${s.side === "buy" ? "good" : "bad"}" style="font-size:1rem">${s.side === "buy" ? "BUY" : "SELL"}</span> ${esc(s.label)}</h2>
        <div class="score"><b>${Math.round(s.prob * 100)}%</b><span class="muted">target first</span><span class="pill ${gradeClass(s.grade)}">${esc(s.grade)}</span></div></div>
      <p><b>${esc(statusText(s))}</b></p>
      <p class="small muted">Swept the ${esc(s.level)} at ${fmt(s.level_px)} (low ${fmt(s.sweep)}), then broke structure${s.killzone ? ` in the ${esc(s.killzone)} kill zone` : ""}. Fair value gap ${fmt(Math.min(s.fvg_bot, s.fvg_top))}–${fmt(Math.max(s.fvg_bot, s.fvg_top))}.</p>
      <div class="strip">
        <div class="fact"><div class="k">Entry (limit order)</div><div class="v">${fmt(s.entry)}</div><div class="d muted">middle of the gap</div></div>
        <div class="fact" data-px="${esc(s.id)}">${priceFact(s)}</div>
        <div class="fact"><div class="k">Target</div><div class="v">${fmt(s.target)}</div><div class="d up">${esc(s.target_name)}</div></div>
        <div class="fact"><div class="k">Stop</div><div class="v">${fmt(s.stop)}</div><div class="d down">${pct(-s.risk_pct)} from entry</div></div>
        <div class="fact"><div class="k">Reward : risk</div><div class="v">${s.rr.toFixed(1)} : 1</div><div class="d muted">costs ${s.cost_r.toFixed(2)}R</div></div>
      </div>
      <div class="chart" id="ict-chart-${i}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Entry</span><span><i class="l-tp"></i>Target</span><span><i class="l-sl"></i>Stop</span><span>Dotted: the fair value gap · 15-minute candles</span></div>
      <div class="facts">
        <div class="fact"><div class="k">Chance target first</div><div class="v">${Math.round(s.prob * 100)}%</div><div class="d muted">break-even needs ${Math.round(100 / (1 + s.rr))}%</div></div>
        <div class="fact"><div class="k">Expected, after costs</div><div class="v ${s.exp_r >= 0 ? "up" : "down"}">${rr(s.exp_r)}</div><div class="d muted">${usd(s.exp_r * R)} on $${R} risked</div></div>
        <div class="fact"><div class="k">Checklist</div><div class="v">${s.count} / 9</div></div>
      </div>
      <p class="small muted">${esc(hist)}</p>
      <details class="checks"><summary>ICT checklist: <span class="pill ${s.count >= 6 ? "good" : s.count >= 4 ? "calm" : "warn"}">${s.count} of 9</span></summary><ul>${ticks}</ul></details>
      <div class="calc"><h3>Risking $${R}</h3><div class="table-wrap"><table><tbody>
        <tr><td>Position size</td><td class="num">$${pos.toLocaleString("en-US", { maximumFractionDigits: 0 })}</td><td class="muted small">so the stop loses about $${R}</td></tr>
        <tr><td>If the target is reached</td><td class="num up">${usd(win)}</td><td class="muted small">after costs</td></tr>
        <tr><td>If the stop is hit</td><td class="num down">${usd(loss)}</td><td class="muted small">after costs</td></tr></tbody></table></div></div>
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
    for (const [p, c, title, style] of [[s.entry, "--calm", "Entry", 2], [s.target, "--good", "Target", 0], [s.stop, "--bad", "Stop", 0], [s.fvg_top, "--muted", "", 1], [s.fvg_bot, "--muted", "", 1]]) {
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
    el.className = `banner ${good ? "good" : o.avg_r > 0 ? "calm" : "bad"}`;
    el.innerHTML = `<b>Honest test result (${esc(state.snap.classes[state.market].label)}, last ${Math.round(st.days)} days):</b> ${o.filled} setups filled; ${Math.round((o.win_rate || 0) * 100)}% reached the target first; average ${rr(o.avg_r)} per trade after costs${good ? " (a positive edge in this period)" : o.avg_r > 0 ? ", positive but not clearly more than luck" : ", i.e. <b>these rules lost money</b> in this period"}. ${more} <a href="#tests" data-goto="tests">Details</a>.`;
  }
  function render() {
    for (const c of state.charts) { try { c.remove(); } catch { /* gone */ } } state.charts = [];
    honest();
    const list = currentSetups(); const pend = list.filter((s) => s.status === "pending").length;
    $("mood").className = `banner ${list.length ? "calm" : "warn"}`;
    $("mood").innerHTML = list.length ? `<b>${pend} setup${pend === 1 ? "" : "s"} waiting for entry</b>${list.length > pend ? `, ${list.length - pend} already running` : ""}. Sorted by expected result after costs. A setup is not a recommendation: check the grade and the test results above.`
      : "<b>No ICT setup right now.</b> They need a liquidity sweep, a structure shift and a fair value gap together, which is rare. New ones are checked every 15 minutes.";
    $("setups").innerHTML = `<div style="display:grid;gap:14px">${list.slice(0, 8).map(card).join("")}</div>`;
    list.slice(0, 8).forEach((s, i) => { const c = drawChart($(`ict-chart-${i}`), s.chart, s); if (c) state.charts.push(c); });
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
  }
  function setStatus(t) { $("status").textContent = t; }

  // ------------------------------------------------------------------ tests tab
  function renderTests() {
    const S = state.snap; if (!S) return;
    const row = (name, s) => `<tr><td>${esc(name)}</td><td class="num">${s.filled ?? 0}</td><td class="num">${Number.isFinite(s.fill_rate) ? Math.round(s.fill_rate * 100) + "%" : "–"}</td><td class="num">${Number.isFinite(s.win_rate) ? Math.round(s.win_rate * 100) + "%" : "–"}</td><td class="num ${s.avg_r > 0 ? "up" : "down"}">${rr(s.avg_r)}</td></tr>`;
    let html = "";
    for (const [k, c] of Object.entries(S.classes)) {
      const st = S.stats?.[k]; if (!st) continue;
      html += `<article class="card"><h3>${esc(c.label)}: last ${Math.round(st.days)} days of 15-minute prices</h3>
        <div class="table-wrap"><table><thead><tr><th>Setups</th><th class="num">Filled</th><th class="num">Fill rate</th><th class="num">Target first</th><th class="num">Average after costs</th></tr></thead><tbody>
        ${row("All", st.overall)}${BUCKETS.map(([b]) => row(`${b} checklist items`, st.buckets[b] || {})).join("")}</tbody></table></div>
        <details><summary>Does each checklist item help?</summary><div class="table-wrap"><table><thead><tr><th>Item</th><th class="num">With it</th><th class="num">Without it</th></tr></thead><tbody>
        ${I.FACTORS.map((f) => { const x = st.factors[f]; return `<tr><td>${esc(FACTOR_TEXT[f][0])}</td><td class="num ${x.with_r > 0 ? "up" : "down"}">${rr(x.with_r)} <span class="muted small">(${x.with_n})</span></td><td class="num ${x.without_r > 0 ? "up" : "down"}">${rr(x.without_r)} <span class="muted small">(${x.without_n})</span></td></tr>`; }).join("")}
        </tbody></table></div><p class="small muted">Average result per filled trade, after costs, with the number of trades in brackets. Small numbers mean little.</p></details></article>`;
    }
    const rec = S.record || {};
    html += `<article class="card"><h3>Live record (recorded as they appeared)</h3><div class="table-wrap"><table><thead><tr><th>Market</th><th class="num">Finished</th><th class="num">Target first</th><th class="num">Average after costs</th></tr></thead><tbody>
      ${Object.entries(S.classes).map(([k, c]) => { const r = rec[k] || {}; return `<tr><td>${esc(c.label)}</td><td class="num">${r.filled ?? 0}</td><td class="num">${Number.isFinite(r.win_rate) ? Math.round(r.win_rate * 100) + "%" : "–"}</td><td class="num">${rr(r.avg_r)}</td></tr>`; }).join("")}
      </tbody></table></div>${(rec.recent || []).length ? `<div class="table-wrap"><table><tbody>${rec.recent.map((r) => `<tr><td>${esc(local(r.t))}</td><td>${r.side.toUpperCase()} ${esc(r.label)}</td><td>${esc(r.status)}</td><td class="num ${r.r >= 0 ? "up" : "down"}">${rr(r.r)}</td></tr>`).join("")}</tbody></table></div>` : `<p class="small muted">No finished live setups yet.</p>`}
      <p class="small muted">"R" is the amount risked: +2R means twice the risk was won, −1R means the stop was hit. Costs: about 0.22% per round trip on crypto, the typical spread on forex and gold, about one tick plus commission on index futures.</p></article>`;
    $("tests").innerHTML = html;
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.ict.trades", state.trades); $("tradeCount").hidden = !state.trades.length; $("tradeCount").textContent = state.trades.length; }
  function tradeAdvice(t) {
    const now = Date.now(); const px = livePx(t.sym);
    const up = (x) => (t.side === "buy" ? x : -x);            // "higher is better" view for either side
    if (!t.filled_at) {
      if (px && up(px) <= up(t.entry)) { t.filled_at = now; saveTrades(); }
      else if (px && up(px) >= up(t.target)) return { action: "Missed: cancel your order", level: "warn", why: "The price reached the target without coming back to your entry. Cancel the limit order." };
      else if (now > t.expires_t) return { action: "Order expired: cancel it", level: "warn", why: "The setup's 4-hour window has passed without a fill. Cancel the limit order." };
      else if (!px) return { action: "Waiting for a live price", level: "calm", why: "The live price couldn't be confirmed, so this page can't tell whether your order filled. Check your broker or exchange." };
      else return { action: "Order waiting", level: "calm", why: `Your limit order at ${fmt(t.entry)} hasn't filled yet. It's valid until ${local(t.expires_t)}.` };
    }
    const view = (x) => (t.side === "buy" ? x : 1 / x);
    const tv = { entry: view(t.entry), take_profit: view(t.target), safety_exit: view(t.stop), exit_by: t.close_by_t };
    if (!px) return E.adviseNoPrice(tv, now);
    const a = E.advise(tv, view(px), now, null);
    if (a.action === "Take profit now") a.action = "Target reached: close it";
    if (a.action === "Exit now") a.action = "Stop reached: close it";
    return a;
  }
  function checkTrades() { for (const t of state.trades) notify(t, tradeAdvice(t)); }
  function renderTrades() {
    saveTrades();
    if (!state.trades.length) { $("trades").innerHTML = `<div class="banner calm">No ICT trades yet. Tap “I placed this order” on a setup.</div>`; return; }
    $("trades").innerHTML = `<div style="display:grid;gap:14px">${state.trades.map((t) => {
      const a = tradeAdvice(t); const px = livePx(t.sym); const R = t.risk;
      const res = t.filled_at && px ? ((t.side === "buy" ? px - t.entry : t.entry - px) / Math.abs(t.entry - t.stop) - t.cost_r) : NaN;
      return `<article class="card trade ${a.level}"><div class="idea-head"><h2>${t.side.toUpperCase()} ${esc(t.label)}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div><p>${esc(a.why)}</p>
        <div class="strip"><div class="fact"><div class="k">Entry</div><div class="v">${fmt(t.entry)}</div><div class="d muted">${t.filled_at ? `filled about ${local(t.filled_at)}` : "not filled yet"}</div></div>
        <div class="fact"><div class="k">Price now${px ? " (live)" : ""}</div><div class="v">${fmt(px)}</div><div class="d ${!Number.isFinite(res) ? "muted" : res >= 0 ? "up" : "down"}">${Number.isFinite(res) ? `${rr(res)} · ${usd(res * R)} on $${R} risked` : px ? "" : "not live right now"}</div></div>
        <div class="fact"><div class="k">Target</div><div class="v">${fmt(t.target)}</div></div><div class="fact"><div class="k">Stop</div><div class="v">${fmt(t.stop)}</div></div>
        <div class="fact"><div class="k">Close by</div><div class="v">${local(t.close_by_t)}</div></div></div>
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
    if (name === "tests") renderTests(); if (name === "trades") renderTrades();
    store.set("omega.ict.tab", name); if (location.hash !== `#${name}`) history.replaceState(null, "", `${location.pathname}#${name}`);
  }
  const press = () => { for (const b of document.querySelectorAll("#market button")) b.setAttribute("aria-pressed", String(b.dataset.market === state.market)); };
  document.addEventListener("click", (ev) => {
    const go = ev.target.closest("[data-goto]"); if (go) { ev.preventDefault(); selectTab(go.dataset.goto); }
    const take = ev.target.closest("[data-take]");
    if (take) {
      const s = currentSetups().find((x) => x.id === take.dataset.take);
      if (s) {
        state.trades.push({ id: s.id, sym: s.sym, label: s.label, class: state.market, side: s.side, entry: s.entry, stop: s.stop, target: s.target, cost_r: s.cost_r,
          expires_t: s.expires_t, close_by_t: s.status === "active" ? s.close_by_t : Date.now() + I.P.hold_bars * 900000, filled_at: s.status === "active" ? (s.fill_t || Date.now()) : null, risk: state.risk });
        saveTrades(); take.outerHTML = `<span class="pill good">Watching it. Open “My ICT trades”.</span>`;
      }
    }
    const done = ev.target.closest("[data-done]"); if (done) { state.trades = state.trades.filter((t) => t.id !== done.dataset.done); renderTrades(); }
  });
  for (const b of document.querySelectorAll("#market button")) b.addEventListener("click", () => { state.market = b.dataset.market; store.set("omega.ict.market", state.market); press(); paintZone(); render(); refresh(false); });
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refreshNow").addEventListener("click", () => refresh(true));
  $("risk").addEventListener("change", (e) => { state.risk = Math.max(1, +e.target.value || 10); store.set("omega.ict.risk", state.risk); render(); });
  $("notifyBtn").addEventListener("click", async () => { if (!("Notification" in window)) return; const p = await Notification.requestPermission(); $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off."; });
  window.addEventListener("omega-theme", () => { if (state.snap) render(); });

  $("risk").value = String(state.risk); press();
  const startTab = (location.hash || "").slice(1) || store.get("omega.ict.tab", "setups");
  selectTab(["setups", "tests", "trades", "guide"].includes(startTab) ? startTab : "setups");
  saveTrades(); paintZone(); refresh(false);
  setInterval(paintZone, 30000);
  setInterval(() => { if (!document.hidden) pollPrices(); }, 10000);
  setInterval(() => { if (!document.hidden) refresh(false); }, 300000);
})();
