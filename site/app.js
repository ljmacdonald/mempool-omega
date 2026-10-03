/* Mempool Omega web app: runs entirely in your browser.
   Live prices and candles: Binance public market data (REST + WebSocket). Cross-checks: OKX and Gate.io.
   Models, track record and practice account: this project's public repository (updated by GitHub Actions). */
(function () {
  "use strict";
  const E = window.OmegaEngine;
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const WS = "wss://data-stream.binance.vision/stream?streams=";
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const FEE = 0.001;                     // 0.1 % exchange fee each time you buy or sell
  const INTERVAL_MS = { "5m": 300000, "15m": 900000, "1h": 3600000 };
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };

  const state = {
    style: store.get("omega.style", "short"), refresh: store.get("omega.refresh", 300),
    amount: store.get("omega.amount", 100),
    cfg: null, models: {}, bigMovers: {}, universe: null, universeAt: 0, venues: null, venuesAt: 0,
    lastScan: {}, scanning: false, timer: null, nextAt: 0,
    trades: store.get("omega.trades", []), lastAction: {},
    anchors: store.get("omega.sugg", {}), earlier: store.get("omega.sugg.earlier", []),
    live: {}, charts: {}, ws: null, wsKey: "", wsOk: false, pollTimer: null, adaptive: null, adaptiveAt: 0, lowStreak: {},
  };

  // ------------------------------------------------------------------ formatting
  const price = (x) => (!Number.isFinite(x) ? "–" : x >= 1000 ? `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}` :
    x >= 1 ? `$${x.toFixed(x >= 100 ? 2 : 4)}` : `$${x.toPrecision(4)}`);
  const pct = (x, d = 1) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const hhmm = (ms) => new Date(ms).toISOString().slice(11, 16);
  const local = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const localDay = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const gradeClass = (g) => (g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad");
  const riskClass = (r) => (r === "Low" ? "good" : r === "Medium" ? "calm" : r === "High" ? "warn" : "bad");
  const coinName = (s) => s.replace(/USDT$/, "");
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  // money if you put `amount` dollars in at `entry` and sell at `exit` (0.1 % fee on both sides)
  const outcome = (amount, entry, exit) => amount * (1 - FEE) / entry * exit * (1 - FEE) - amount;

  // ------------------------------------------------------------------ data access
  async function getJSON(url, timeout = 15000) {
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeout);
    try { const r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
    finally { clearTimeout(t); }
  }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function loadConfig() {
    if (!state.cfg) state.cfg = await getJSON(REPO + "web/config.json");
    if (!Object.keys(state.bigMovers).length) { try { state.bigMovers = await getJSON(REPO + "reports/scanner_bigmovers.json"); } catch { /* optional */ } }
  }
  async function loadModel(style) {
    if (!state.models[style]) state.models[style] = await getJSON(REPO + `web/model_${style}.json`, 30000);
    return state.models[style];
  }
  async function loadUniverse() {
    if (state.universe && Date.now() - state.universeAt < 10 * 60000) return state.universe;
    const tickers = await getJSON(`${BINANCE}/ticker/24hr`);
    state.universe = E.selectUniverse(tickers, state.cfg.universe); state.universeAt = Date.now();
    return state.universe;
  }
  async function candles(symbol, interval, n) {
    const rows = await getJSON(`${BINANCE}/klines?symbol=${symbol}&interval=${interval}&limit=${n + 1}`);
    const step = INTERVAL_MS[interval];
    const c = { t: [], open: [], high: [], low: [], close: [], volume: [], qv: [], taker_buy_volume: [], n_trades: [], live: null };
    for (const r of rows.filter((r) => r[0] + step <= Date.now()).slice(-n)) {
      c.t.push(r[0]); c.open.push(+r[1]); c.high.push(+r[2]); c.low.push(+r[3]); c.close.push(+r[4]);
      c.volume.push(+r[5]); c.qv.push(+r[7]); c.n_trades.push(+r[8]); c.taker_buy_volume.push(+r[9]);
    }
    const last = rows[rows.length - 1];
    if (last && last[0] + step > Date.now()) c.live = { t: last[0], open: +last[1], high: +last[2], low: +last[3], close: +last[4] };
    return c;
  }
  async function pool(items, size, fn) {
    const out = new Array(items.length); let i = 0;
    await Promise.all(Array.from({ length: size }, async () => {
      while (i < items.length) { const k = i++; try { out[k] = await fn(items[k]); } catch { out[k] = null; } }
    }));
    return out;
  }

  // ------------------------------------------------------------------ fake-signal checks (live data)
  async function venues() {
    if (state.venues && Date.now() - state.venuesAt < 60000) return state.venues;
    const out = {};
    const add = (base, v) => { (out[base] = out[base] || []).push(v); };
    try {
      const okx = await getJSON("https://www.okx.com/api/v5/market/tickers?instType=SPOT");
      for (const t of okx.data || []) if (t.instId.endsWith("-USDT") && +t.open24h > 0 && +t.last > 0) add(t.instId.slice(0, -5), { venue: "OKX", price: +t.last, change_pct: (+t.last / +t.open24h - 1) * 100 });
    } catch { /* OKX unavailable */ }
    try {
      const gate = await getJSON("https://api.gateio.ws/api/v4/spot/tickers");
      for (const t of gate) if (t.currency_pair.endsWith("_USDT") && +t.last > 0 && t.change_percentage !== "" && t.change_percentage != null) add(t.currency_pair.slice(0, -5), { venue: "Gate.io", price: +t.last, change_pct: +t.change_percentage });
    } catch { /* Gate unavailable */ }
    state.venues = out; state.venuesAt = Date.now();
    return out;
  }
  const book = async (s) => { const d = await getJSON(`${BINANCE}/depth?symbol=${s}&limit=100`); return { bids: d.bids.map(([p, q]) => [+p, +q]), asks: d.asks.map(([p, q]) => [+p, +q]) }; };
  const trades = async (s) => (await getJSON(`${BINANCE}/trades?symbol=${s}&limit=1000`)).map((t) => ({ price: +t.price, qty: +t.qty, buyer_maker: !!t.isBuyerMaker, time: +t.time }));

  // Private per-device seed, rotated every hour: thresholds and snapshot timing are unknowable from outside.
  function deviceSeed() {
    let s0 = store.get("omega.seed", null);
    if (!Number.isInteger(s0)) { const a = new Uint32Array(1); crypto.getRandomValues(a); s0 = a[0]; store.set("omega.seed", s0); }
    return (s0 ^ Math.floor(Date.now() / 3600000)) >>> 0;
  }
  async function integrityFor(ideas, bySym, uni) {
    const symbols = ideas.map((d) => d.symbol);
    const seed = deviceSeed(); const t = E.thresholds(seed); const gaps = E.snapshotGaps(E.mulberry32((seed ^ 0x9E3779B9) >>> 0));
    const [ven, b1] = await Promise.all([venues(), pool(symbols, 6, book)]);
    const snaps = [b1];
    for (let g = 0; g < gaps.length; g++) {
      setStatus(`Fake-signal checks: order-book snapshot ${g + 2} of 3…`);
      await sleep(gaps[g] * 1000);
      snaps.push(await pool(symbols, 6, book));
    }
    const tr = await pool(symbols, 6, trades);
    const u = Object.fromEntries(uni.map((x) => [x.symbol, x]));
    const out = {};
    symbols.forEach((s, i) => {
      const checks = [];
      const books = snaps.map((sn) => sn[i]).filter((b) => b && b.bids.length && b.asks.length);
      if (books.length >= 2) checks.push(E.checkWalls(books, t), E.checkThinBook(books[0], t));
      if (tr[i]) checks.push(E.checkWash(tr[i], t));
      if (bySym[s]) checks.push(E.checkImpact(bySym[s], t));
      if (u[s]) checks.push(E.checkVenues(u[s].lastPrice, u[s].change, ven[coinName(s)] || [], t));
      if (bySym[s]) checks.push(E.checkWhale(bySym[s], t));
      if (ideas[i].features) checks.push(E.checkEngineered(ideas[i].features, t));
      out[s] = E.combineChecks(checks);
    });
    return out;
  }
  async function loadAdaptive() {
    if (state.adaptive && Date.now() - state.adaptiveAt < 3600000) return state.adaptive;
    try { state.adaptive = await getJSON(REPO + "web/adaptive.json"); } catch { state.adaptive = null; }
    state.adaptiveAt = Date.now();
    return state.adaptive;
  }

  // ------------------------------------------------------------------ scanning
  async function scan(styleKey) {
    await loadConfig();
    const style = state.cfg.styles[styleKey];
    const [model, uni] = await Promise.all([loadModel(styleKey), loadUniverse()]);
    const syms = uni.map((u) => u.symbol);
    if (!syms.includes("BTCUSDT")) syms.push("BTCUSDT");
    const data = await pool(syms, 8, (s) => candles(s, style.interval, style.live_bars));
    const bySym = Object.fromEntries(syms.map((s, i) => [s, data[i]]));
    const coins = uni.map((u) => ({ symbol: u.symbol, quoteVolume: u.quoteVolume, candles: bySym[u.symbol] })).filter((c) => c.candles);
    if (coins.length < 10) throw new Error("not enough market data came back");
    const res = E.rankCoins(coins, bySym.BTCUSDT, model, style, state.cfg, state.bigMovers, 10, await loadAdaptive());
    setStatus("Running fake-signal checks on the best 10 candidates…");
    let checks = {};
    try { checks = await integrityFor(res.ideas, bySym, uni); } catch { /* checks unavailable */ }
    res.ideas = E.applyIntegrity(res.ideas, checks, 5, state.cfg.grades);
    res.candles = bySym; res.at = Date.now(); res.style = styleKey;
    state.lastScan[styleKey] = res;
    return res;
  }

  async function refreshIdeas() {
    if (state.scanning) return;
    state.scanning = true; $("refreshNow").disabled = true;
    setStatus("Fetching live prices and re-ranking about 60 coins…");
    try {
      const res = await scan(state.style);
      anchorIdeas(res);
      renderIdeas(res);
      setStatus(`Updated ${local(res.at)} · ${res.mood.coins} coins checked`);
    } catch (e) {
      setStatus("Couldn't reach live prices. Showing the latest hourly ideas instead.");
      await renderHourlyFallback(e);
    } finally {
      state.scanning = false; $("refreshNow").disabled = false; schedule(); connectLive();
    }
  }

  function schedule() {
    clearTimeout(state.timer);
    if (state.refresh > 0) { state.nextAt = Date.now() + state.refresh * 1000; state.timer = setTimeout(refreshIdeas, state.refresh * 1000); }
    else state.nextAt = 0;
  }
  function setStatus(text) { $("status").dataset.base = text; paintStatus(); }
  function paintStatus() {
    const base = $("status").dataset.base || "";
    let extra = "";
    if (state.nextAt && !state.scanning) { const s = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000)); extra = ` · next in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
    $("status").textContent = base + extra;
    $("clock").textContent = `UTC ${new Date().toISOString().slice(11, 16)}`;
  }

  // ------------------------------------------------------------------ suggestion anchors
  // A coin's "suggestion" starts the first time it enters this speed's top 5 on this device. Its price,
  // take-profit, safety exit and sell-by time stay fixed from that moment, so you can see how it has done since.
  function anchorIdeas(res) {
    const now = Date.now(); const keep = {};
    for (const d of res.ideas) {
      const k = `${res.style}:${d.symbol}`; let a = state.anchors[k];
      if (!a || now > a.exit_by) {
        a = { symbol: d.symbol, style: res.style, at: now, price: d.price_now, take_profit: d.take_profit, safety_exit: d.safety_exit,
          exit_by: now + d.hold_minutes * 60000, risk_unit: d.risk_unit, score: d.score };
      }
      keep[k] = a; d.anchor = a;
    }
    for (const [k, a] of Object.entries(state.anchors)) {
      if (k.startsWith(`${res.style}:`) && !keep[k]) state.earlier.unshift({ ...a, left: now });
      else if (!k.startsWith(`${res.style}:`)) keep[k] = a;
    }
    state.earlier = state.earlier.filter((e) => now - e.at < 48 * 3600000).slice(0, 40);
    state.anchors = keep; store.set("omega.sugg", keep); store.set("omega.sugg.earlier", state.earlier);
  }

  // ------------------------------------------------------------------ charts
  function makeChart(el, c, lines, markerMs, interval) {
    if (!window.LightweightCharts || !el) { if (el) el.innerHTML = `<p class="small muted" style="padding:12px">The chart library couldn't load on this network.</p>`; return null; }
    const chart = LightweightCharts.createChart(el, {
      autoSize: true,
      layout: { background: { color: cssVar("--surface") }, textColor: cssVar("--muted"), fontFamily: cssVar("--f-num") },
      grid: { vertLines: { color: cssVar("--sunken") }, horzLines: { color: cssVar("--sunken") } },
      rightPriceScale: { borderColor: cssVar("--line") }, timeScale: { borderColor: cssVar("--line"), timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }, handleScroll: false, handleScale: false,
    });
    const series = chart.addCandlestickSeries({ upColor: cssVar("--good"), downColor: cssVar("--bad"), borderVisible: false,
      wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad"), priceFormat: { type: "price", precision: precisionFor(c.close[c.close.length - 1]), minMove: Math.pow(10, -precisionFor(c.close[c.close.length - 1])) } });
    const n = Math.min(c.t.length, 96);
    const bars = [];
    for (let i = c.t.length - n; i < c.t.length; i++) bars.push({ time: c.t[i] / 1000, open: c.open[i], high: c.high[i], low: c.low[i], close: c.close[i] });
    if (c.live) bars.push({ time: c.live.t / 1000, open: c.live.open, high: c.live.high, low: c.live.low, close: c.live.close });
    series.setData(bars);
    for (const l of lines) series.createPriceLine({ price: l.price, color: cssVar(l.color), lineWidth: 2, lineStyle: l.dashed ? 2 : 0, axisLabelVisible: true, title: l.title });
    if (markerMs) {
      const step = INTERVAL_MS[interval]; const mt = Math.floor(markerMs / step) * step / 1000;
      if (bars.length && mt >= bars[0].time) series.setMarkers([{ time: Math.min(mt, bars[bars.length - 1].time), position: "belowBar", color: cssVar("--calm"), shape: "arrowUp", text: "Suggested" }]);
    }
    chart.timeScale().fitContent();
    return { chart, series, lastTime: bars.length ? bars[bars.length - 1].time : 0 };
  }
  function precisionFor(p) { return p >= 100 ? 2 : p >= 1 ? 4 : Math.min(8, Math.max(4, 3 - Math.floor(Math.log10(p)))); }
  function dropCharts(prefix) { for (const k of Object.keys(state.charts)) if (k.startsWith(prefix)) { try { state.charts[k].chart.remove(); } catch { /* gone */ } delete state.charts[k]; } }

  // ------------------------------------------------------------------ live updates (WebSocket, polling fallback)
  function liveTargets() {
    const t = {};
    const res = state.lastScan[state.style];
    if (res) for (const d of res.ideas) t[`${d.symbol}|${state.cfg.styles[state.style].interval}`] = true;
    for (const tr of state.trades) t[`${tr.symbol}|${state.cfg ? state.cfg.styles[tr.style].interval : "15m"}`] = true;
    return Object.keys(t).map((k) => { const [symbol, interval] = k.split("|"); return { symbol, interval }; });
  }
  function connectLive() {
    if (!state.cfg) return;
    const targets = liveTargets(); const key = targets.map((t) => `${t.symbol}@${t.interval}`).sort().join(",");
    if (key === state.wsKey && state.ws && state.ws.readyState <= 1) return;
    state.wsKey = key;
    try { if (state.ws) state.ws.close(); } catch { /* ignore */ }
    clearInterval(state.pollTimer);
    if (!targets.length) return;
    const url = WS + targets.map((t) => `${t.symbol.toLowerCase()}@kline_${t.interval}`).join("/");
    try {
      const ws = new WebSocket(url); state.ws = ws;
      ws.onopen = () => { state.wsOk = true; paintLiveBadges(); };
      ws.onmessage = (ev) => { try { const m = JSON.parse(ev.data); const k = m.data && m.data.k; if (k) onTick(k.s, k.i, { t: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c }); } catch { /* ignore */ } };
      ws.onclose = ws.onerror = () => { if (state.ws === ws) { state.wsOk = false; paintLiveBadges(); startPolling(); } };
    } catch { startPolling(); }
  }
  function startPolling() {
    clearInterval(state.pollTimer);
    state.pollTimer = setInterval(async () => {
      for (const t of liveTargets()) {
        try { const r = await getJSON(`${BINANCE}/klines?symbol=${t.symbol}&interval=${t.interval}&limit=1`); const k = r[0]; onTick(t.symbol, t.interval, { t: k[0], open: +k[1], high: +k[2], low: +k[3], close: +k[4] }); } catch { /* next round */ }
      }
    }, 10000);
  }
  function onTick(symbol, interval, bar) {
    state.live[symbol] = { price: bar.close, at: Date.now() };
    for (const [key, ch] of Object.entries(state.charts)) {
      if (!key.endsWith(`|${symbol}|${interval}`) || !ch) continue;
      const time = bar.t / 1000;
      if (time >= ch.lastTime) { try { ch.series.update({ time, open: bar.open, high: bar.high, low: bar.low, close: bar.close }); ch.lastTime = time; } catch { /* chart gone */ } }
    }
    for (const el of document.querySelectorAll(`[data-live="${symbol}"]`)) el.textContent = price(bar.close);
    for (const el of document.querySelectorAll(`[data-since="${symbol}"]`)) {
      const ch = bar.close / +el.dataset.ref - 1; el.textContent = `${pct(ch, 2)} since suggested`; el.className = `d ${ch >= 0 ? "up" : "down"}`;
    }
    for (const el of document.querySelectorAll(`.calc[data-sym="${symbol}"]`)) updateCalc(el);
  }
  function paintLiveBadges() { for (const el of document.querySelectorAll(".livebadge")) { el.classList.toggle("stale", !state.wsOk); el.textContent = state.wsOk ? "Live" : "Updates every 10 s"; } }

  // ------------------------------------------------------------------ calculator
  function calcBlock(sym, entry, tp, sl, amount, label) {
    return `<div class="calc" data-sym="${esc(sym)}" data-entry="${entry}" data-tp="${tp}" data-sl="${sl}">
      <h3>${esc(label)}</h3>
      <div class="row">
        <label class="inline" for="amt-${esc(sym)}-${label.length}">I put in $<input class="amt" id="amt-${esc(sym)}-${label.length}" type="number" min="1" step="any" value="${amount}"></label>
        <label class="inline" for="tgt-${esc(sym)}-${label.length}">and sell at $<input class="tgt" id="tgt-${esc(sym)}-${label.length}" type="number" min="0" step="any" placeholder="${(+tp).toPrecision(5)}"></label>
      </div>
      <div class="table-wrap"><table><tbody class="calc-out"></tbody></table></div>
      <p class="small muted">Includes the 0.1% exchange fee when you buy and again when you sell. Prices move; these are what-ifs, not promises.</p>
    </div>`;
  }
  function updateCalc(el) {
    const amt = +el.querySelector(".amt").value || 0; const entry = +el.dataset.entry, tp = +el.dataset.tp, sl = +el.dataset.sl;
    const tgt = +el.querySelector(".tgt").value; const now = state.live[el.dataset.sym]?.price;
    const qty = amt * (1 - FEE) / entry;
    const row = (label, px, extra = "") => { const v = outcome(amt, entry, px); return `<tr><td>${label}${extra}</td><td class="num">${price(px)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v)}</td><td class="num muted">${pct(v / amt, 1)}</td></tr>`; };
    let html = `<tr><td colspan="4" class="muted">You'd get about <b class="num">${qty.toPrecision(5)}</b> ${esc(coinName(el.dataset.sym))} for $${amt.toLocaleString()} at ${price(entry)}.</td></tr>`;
    html += row("If it reaches the take profit", tp);
    html += row("If it hits the safety exit", sl);
    if (Number.isFinite(now)) html += row("If you sold right now", now);
    if (tgt > 0) html += row("If you sell at your price", tgt);
    el.querySelector(".calc-out").innerHTML = html;
  }

  // ------------------------------------------------------------------ rendering: ideas
  function checksBlock(it) {
    if (!it) return `<p class="small muted">Fake-signal checks couldn't run for this coin right now.</p>`;
    const icon = (ok) => (ok === true ? `<span class="ok">✓</span>` : ok === false ? `<span class="bad">✗</span>` : `<span class="na">–</span>`);
    const label = it.checked ? `${it.passed} of ${it.checked} checks passed` : "Checks unavailable";
    const cls = it.penalty === 0 ? "good" : it.penalty < 0.1 ? "calm" : "warn";
    return `<details class="checks"><summary>Fake-signal check: <span class="pill ${cls}">${label}</span></summary>
      <ul>${it.checks.map((c) => `<li>${icon(c.ok)}<span>${esc(c.text)}</span></li>`).join("")}</ul>
      <p class="small muted">Checks: spoofing (fake orders that vanish), thin order book, fake back-and-forth trading, whether other exchanges (OKX, Gate.io) confirm the price, and whale-sized trades. Failed checks lower the score.</p></details>`;
  }

  function ideaCard(d, hourly) {
    const a = d.anchor || { at: Date.parse(d.suggested_at || "") || Date.now(), price: d.price_now, take_profit: d.take_profit, safety_exit: d.safety_exit, exit_by: d.exit_by };
    const held = state.trades.some((t) => t.symbol === d.symbol && t.style === d.style);
    const nowPx = state.live[d.symbol]?.price ?? d.price_now;
    const since = nowPx / a.price - 1;
    const already = nowPx >= a.take_profit ? `<div class="banner good">Since it was suggested, the price already reached the take-profit level.</div>` :
      nowPx <= a.safety_exit ? `<div class="banner bad">Since it was suggested, the price already fell to the safety exit. Don't buy it now.</div>` : "";
    const bm = Number.isFinite(d.week_up20_pct) ? `<p class="small muted">Last 90 days: it rose 20%+ within a week ${Math.round(d.week_up20_pct * 100)}% of the time and fell 20%+ ${Math.round(d.week_down20_pct * 100)}% of the time.</p>` : "";
    const chartId = `chart-idea-${d.style}-${d.symbol}`;
    return `<article class="card">
      <div class="idea-head">
        <h2><span class="rank">${d.rank}.</span>${esc(d.coin)}</h2>
        <div class="score"><b>${d.score.toFixed(1)}</b><span class="muted">/ 10</span>
          <span class="pill ${gradeClass(d.grade)}">${esc(d.grade)}</span>
          <span class="pill ${riskClass(d.risk_level)}">Risk: ${esc(d.risk_level)}</span></div>
      </div>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${localDay(a.at)}</div><div class="d muted">at ${price(a.price)}</div></div>
        <div class="fact"><div class="k">Price now</div><div class="v" data-live="${esc(d.symbol)}">${price(nowPx)}</div><div class="d ${since >= 0 ? "up" : "down"}" data-since="${esc(d.symbol)}" data-ref="${a.price}">${pct(since, 2)} since suggested</div>${hourly ? "" : '<div class="livebadge live small muted">Live</div>'}</div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${price(a.take_profit)}</div><div class="d up">${pct(a.take_profit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(a.safety_exit)}</div><div class="d down">${pct(a.safety_exit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(a.exit_by)}</div><div class="d muted">${hhmm(a.exit_by)} UTC</div></div>
      </div>
      ${already}
      ${hourly ? "" : `<div class="chart" id="${chartId}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Price when suggested</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span><span>Candles: ${esc(state.cfg.styles[d.style].interval)} each, moving live</span></div>`}
      <div class="facts">
        <div class="fact"><div class="k">Beats the market</div><div class="v">${Math.round(d.chance_beats_market * 100)}%</div><div class="d muted">chance</div></div>
        <div class="fact"><div class="k">To risk $10, buy</div><div class="v">$${Math.round(d.size_for_10usd_risk).toLocaleString()}</div></div>
        <div class="fact"><div class="k">Hold for at most</div><div class="v">${esc(E.humanDuration(d.hold_minutes))}</div></div>
      </div>
      <div><h3>Why it was picked</h3><ul class="why">${d.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${d.warnings.length ? `<div class="warnings">${d.warnings.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      ${checksBlock(d.integrity)}
      ${bm}
      ${calcBlock(d.symbol, a.price, a.take_profit, a.safety_exit, state.amount, "Profit calculator")}
      <div class="actions">
        ${hourly ? `<span class="small muted">Hourly snapshot. Live prices are unavailable right now.</span>` :
        held ? `<span class="pill calm">You're watching this trade</span>` :
        `<button class="btn primary" type="button" data-take="${esc(d.symbol)}">I bought this: watch it for me</button>`}
      </div>
    </article>`;
  }

  function renderIdeas(res) {
    const m = res.mood, style = state.cfg.styles[res.style];
    const cls = m.label === "Favourable" ? "good" : m.label === "Mixed" ? "calm" : "warn";
    $("mood").className = `banner ${cls}`;
    $("mood").innerHTML = `<b>Market mood: ${m.label}.</b> ${Math.round(m.share_positive * 100)}% of ${m.coins} coins look positive for the ${esc(style.label.split(":")[0])} speed (sell within ${esc(style.hold_text)}). ${m.label === "Unfavourable" ? "Doing nothing is a perfectly good choice right now." : "Scores above 5 are better than break-even after fees."}`;
    dropCharts("idea|");
    $("ideas").innerHTML = `<div style="display:grid;gap:14px">${res.ideas.map((d) => ideaCard(d, false)).join("")}</div>${earlierBlock()}`;
    for (const d of res.ideas) {
      const a = d.anchor;
      const ch = makeChart($(`chart-idea-${d.style}-${d.symbol}`), res.candles[d.symbol], [
        { price: a.price, color: "--calm", dashed: true, title: "Suggested" },
        { price: a.take_profit, color: "--good", title: "Take profit" },
        { price: a.safety_exit, color: "--bad", title: "Safety exit" }], a.at, style.interval);
      if (ch) state.charts[`idea|${d.symbol}|${style.interval}`] = ch;
      const live = res.candles[d.symbol]?.live; if (live) state.live[d.symbol] = { price: live.close, at: Date.now() };
    }
    for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    paintLiveBadges(); refreshEarlierPrices();
  }

  function earlierBlock() {
    const rows = state.earlier.filter((e) => e.style === state.style).slice(0, 15);
    if (!rows.length) return "";
    return `<article class="card"><h3>Earlier suggestions on this device (${esc(state.cfg.styles[state.style].label.split(":")[0])} speed, last 48 hours)</h3>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th>Suggested</th><th class="num">Price then</th><th class="num">Price now</th><th class="num">Change</th><th>Sell-by</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td>${esc(coinName(e.symbol))}</td><td>${esc(localDay(e.at))}</td><td class="num">${price(e.price)}</td><td class="num" data-live="${esc(e.symbol)}">${price(state.live[e.symbol]?.price)}</td><td class="num" data-since="${esc(e.symbol)}" data-ref="${e.price}">–</td><td>${Date.now() > e.exit_by ? "passed" : local(e.exit_by)}</td></tr>`).join("")}
      </tbody></table></div><p class="small muted">Coins that left the top 5. “Change” is the price move since the suggestion, before fees.</p></article>`;
  }
  async function refreshEarlierPrices() {
    const syms = [...new Set(state.earlier.filter((e) => e.style === state.style).slice(0, 15).map((e) => e.symbol))];
    if (!syms.length) return;
    try {
      const all = await getJSON(`${BINANCE}/ticker/price`); const want = new Set(syms);
      for (const p of all) if (want.has(p.symbol)) onTick(p.symbol, "none", { t: 0, open: +p.price, high: +p.price, low: +p.price, close: +p.price });
    } catch { /* ignore */ }
  }

  async function renderHourlyFallback(err) {
    try {
      const pl = await getJSON(REPO + `suggestions/latest_${state.style}.json`);
      const ideas = pl.ideas.map((d) => ({ ...d, exit_by: Date.parse(d.exit_by), chance_beats_market: d.chance_beats_market ?? d.chance_of_profit ?? 0.5 }));
      $("mood").className = "banner warn";
      $("mood").innerHTML = `<b>Live prices are unavailable</b> (${esc(err.message || err)}). Showing the ideas saved at ${esc(pl.generated_at.slice(11, 16))} UTC instead. Prices may have moved since. Binance may be blocked on your network; try another network or try again later.`;
      $("ideas").innerHTML = `<div style="display:grid;gap:14px">${ideas.map((d) => ideaCard(d, true)).join("")}</div>`;
      for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    } catch {
      $("mood").className = "banner bad";
      $("mood").textContent = "Couldn't load ideas right now. Check your internet connection; the page will try again on the next refresh.";
      $("ideas").innerHTML = "";
    }
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.trades", state.trades); renderTradeCount(); }
  function renderTradeCount() { const n = state.trades.length; $("tradeCount").hidden = n === 0; $("tradeCount").textContent = n; }

  async function refreshTrades(rebuildCharts) {
    renderTradeCount();
    const box = $("trades");
    if (!state.trades.length) { dropCharts("trade|"); box.innerHTML = `<div class="banner calm">No trades yet. Tap “I bought this” on an idea, or add one below.</div>`; updateBackup(); return; }
    let prices = {};
    try { const all = await getJSON(`${BINANCE}/ticker/price`); const want = new Set(state.trades.map((t) => t.symbol)); for (const p of all) if (want.has(p.symbol)) prices[p.symbol] = +p.price; }
    catch { prices = Object.fromEntries(state.trades.map((t) => [t.symbol, state.live[t.symbol]?.price])); }
    const needCharts = rebuildCharts || state.trades.some((t) => !Object.keys(state.charts).some((k) => k.startsWith(`trade|${t.id}|`)));
    const openCalc = new Set([...document.querySelectorAll("#trades details[open]")].map((e) => e.dataset.id));
    const amounts = Object.fromEntries([...document.querySelectorAll("#trades .calc .amt")].map((e) => [e.closest("[data-tid]")?.dataset.tid, e.value]));
    const html = [];
    for (const t of state.trades) {
      const px = prices[t.symbol] ?? state.live[t.symbol]?.price;
      if (Number.isFinite(px)) state.live[t.symbol] = { price: px, at: Date.now() };
      const scan = state.lastScan[t.style]; let score = scan?.scores?.[t.symbol];
      if (scan && Number.isFinite(score)) {
        const seen = state.lowStreak[t.id] || { at: 0, n: 0 };
        if (seen.at !== scan.at) state.lowStreak[t.id] = { at: scan.at, n: score < 4.5 ? seen.n + 1 : 0 };
        if (state.lowStreak[t.id].n < 2) score = Math.max(score, 4.5);   // one bad reading could be a planted signal
      }
      const a = E.advise(t, px, Date.now(), score);
      notifyIfChanged(t, a);
      const amt = +(amounts[t.id] ?? t.amount ?? state.amount);
      const pl = outcome(amt, t.entry, px);
      html.push(`<article class="card trade ${a.level}" data-tid="${esc(t.id)}">
        <div class="idea-head"><h2>${esc(coinName(t.symbol))}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div>
        <p>${esc(a.why)}</p>
        <div class="strip">
          <div class="fact"><div class="k">You bought</div><div class="v">${localDay(t.opened)}</div><div class="d muted">at ${price(t.entry)}</div></div>
          <div class="fact"><div class="k">Price now</div><div class="v" data-live="${esc(t.symbol)}">${price(px)}</div><div class="d ${a.pnl >= 0 ? "up" : "down"}">${pct(a.pnl, 2)} after fees · ${usd(pl)} on $${amt.toLocaleString()}</div><div class="livebadge live small muted">Live</div></div>
          <div class="fact"><div class="k">Take profit at</div><div class="v">${price(t.take_profit)}</div><div class="d muted">${pct(a.toTp)} away</div></div>
          <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(t.safety_exit)}</div><div class="d muted">${pct(a.toSl)} away</div></div>
          <div class="fact"><div class="k">Sell by</div><div class="v">${local(t.exit_by)}</div><div class="d muted">${a.leftMin > 0 ? E.humanDuration(a.leftMin) + " left" : "time is up"}</div></div>
        </div>
        <div class="chart" id="chart-trade-${esc(t.id)}"></div>
        <div class="chart-legend"><span><i class="l-base"></i>Your buy price</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span></div>
        <details data-id="${esc(t.id)}" ${openCalc.has(t.id) ? "open" : ""}><summary>Profit calculator</summary>${calcBlock(t.symbol, t.entry, t.take_profit, t.safety_exit, amt, "How much this trade makes or loses")}</details>
        <div class="actions"><button class="btn" type="button" data-sold="${esc(t.id)}">I've sold it: remove</button></div>
      </article>`);
    }
    // keep existing charts alive across refreshes by moving them into the new markup
    const keep = {};
    if (!needCharts) for (const t of state.trades) { const el = $(`chart-trade-${t.id}`); if (el) keep[t.id] = el; }
    box.innerHTML = `<div style="display:grid;gap:14px">${html.join("")}</div><p class="small muted">Prices checked ${local(Date.now())}. Charts move live.</p>`;
    if (needCharts) {
      dropCharts("trade|");
      for (const t of state.trades) {
        const iv = state.cfg ? state.cfg.styles[t.style].interval : "15m";
        try {
          const c = await candles(t.symbol, iv, 120);
          const ch = makeChart($(`chart-trade-${t.id}`), c, [
            { price: t.entry, color: "--calm", dashed: true, title: "Bought" },
            { price: t.take_profit, color: "--good", title: "Take profit" },
            { price: t.safety_exit, color: "--bad", title: "Safety exit" }], t.opened, iv);
          if (ch) state.charts[`trade|${t.id}|${t.symbol}|${iv}`] = ch;
        } catch { /* chart unavailable */ }
      }
    } else {
      for (const [id, el] of Object.entries(keep)) { const slot = $(`chart-trade-${id}`); if (slot) slot.replaceWith(el); }
    }
    for (const el of document.querySelectorAll("#trades .calc")) updateCalc(el);
    paintLiveBadges(); updateBackup(); connectLive();
  }

  function notifyIfChanged(t, a) {
    const prev = state.lastAction[t.id]; state.lastAction[t.id] = a.action;
    if (!prev || prev === a.action || a.action === "Hold") return;
    document.title = `${a.action}: ${coinName(t.symbol)} · Mempool Omega`;
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`${coinName(t.symbol)}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }
  function updateBackup() { try { $("backupOut").value = btoa(unescape(encodeURIComponent(JSON.stringify(state.trades)))); } catch { $("backupOut").value = ""; } }

  // ------------------------------------------------------------------ track record & account
  function parseCSV(text) {
    const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(",");
    return lines.map((l) => { const v = l.split(","); return Object.fromEntries(head.map((h, i) => [h, v[i]])); });
  }
  async function renderRecord() {
    const box = $("record");
    try {
      await loadConfig();
      const b = await getJSON(REPO + "suggestions/scoreboard.json");
      const rows = Object.entries(b.by_style || {}).filter(([, v]) => v.closed);
      let html = "";
      if (!rows.length) html += `<div class="banner calm">No ideas have reached their time limit yet. Check back in a few hours.</div>`;
      else {
        html += `<div class="table-wrap"><table><thead><tr><th>Speed</th><th class="num">Ideas checked</th><th class="num">Ended in profit</th><th class="num">Average per idea</th><th class="num">Random pick average</th><th class="num">$100 in each idea</th></tr></thead><tbody>`;
        for (const [k, v] of rows) {
          const beat = v.avg_return_per_idea > v.random_pick_avg_return;
          html += `<tr><td>${esc(state.cfg.styles[k]?.label || k)}</td><td class="num">${v.closed}</td><td class="num">${Math.round(v.win_rate * 100)}%</td>
            <td class="num ${v.avg_return_per_idea >= 0 ? "up" : "down"}">${pct(v.avg_return_per_idea, 2)}</td>
            <td class="num">${pct(v.random_pick_avg_return, 2)} ${beat ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>'}</td>
            <td class="num ${v.if_100usd_each_total_pnl >= 0 ? "up" : "down"}">${usd(v.if_100usd_each_total_pnl)}</td></tr>`;
        }
        html += `</tbody></table></div><p class="small muted">If a speed doesn't beat “random pick” over a few weeks, its ranking isn't adding value.</p>`;
      }
      try {
        const h = parseCSV(await getText(REPO + "suggestions/history.csv")).filter((r) => r.status === "closed").slice(-25).reverse();
        if (h.length) {
          html += `<h3>Latest checked ideas</h3><div class="table-wrap"><table><thead><tr><th>Suggested (UTC)</th><th>Speed</th><th>Coin</th><th class="num">Score</th><th class="num">Price then</th><th>What happened</th><th class="num">Result</th><th class="num">On $100</th></tr></thead><tbody>`;
          const what = { take_profit: "Hit take profit", safety_exit: "Hit safety exit", time_limit: "Time limit reached" };
          for (const r of h) html += `<tr><td>${esc(r.ts.slice(5, 16))}</td><td>${esc(r.style || "day")}</td><td>${esc(coinName(r.symbol))}</td><td class="num">${esc(r.score)}</td><td class="num">${price(+r.price_at_idea)}</td><td>${esc(what[r.outcome] || r.outcome)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${pct(+r.net_ret, 2)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${usd(100 * +r.net_ret)}</td></tr>`;
          html += `</tbody></table></div>`;
        }
      } catch { /* history optional */ }
      box.innerHTML = html;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the track record right now.</div>`; }
  }

  function sparkline(points) {
    if (points.length < 2) return `<p class="small muted">The balance chart appears after a few hourly runs.</p>`;
    const W = 640, H = 160, P = 28; const ys = points.map((p) => p[1]); let lo = Math.min(...ys), hi = Math.max(...ys);
    if (hi - lo < 1) { lo -= 50; hi += 50; }
    const x = (i) => P + (i / (points.length - 1)) * (W - 2 * P), y = (v) => H - P - ((v - lo) / (hi - lo)) * (H - 2 * P);
    const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
    const last = points[points.length - 1];
    return `<div class="table-wrap"><svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Practice balance over time">
      <line x1="${P}" x2="${W - P}" y1="${y(100000)}" y2="${y(100000)}" stroke="var(--line)" stroke-dasharray="4 4"/>
      <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2"/>
      <circle cx="${x(points.length - 1)}" cy="${y(last[1])}" r="4" fill="var(--accent)"/>
      <text x="${P}" y="14">$${Math.round(hi).toLocaleString()}</text><text x="${P}" y="${H - 6}">$${Math.round(lo).toLocaleString()}</text>
      <text x="${W - P}" y="${H - 6}" text-anchor="end">${esc(new Date(last[0]).toISOString().slice(5, 16).replace("T", " "))} UTC</text>
    </svg></div>`;
  }
  async function renderAccount() {
    const box = $("account");
    try {
      const s = await getJSON(REPO + "status.json");
      const ks = s.kill_switch || {};
      const month = new Date().toISOString().slice(0, 7);
      let pts = [];
      try { pts = parseCSV(await getText(REPO + `ledger/equity_${month}.csv`)).map((r) => [Date.parse(r.ts), +r.equity]).filter((p) => Number.isFinite(p[1])); } catch { /* new month */ }
      const pos = Object.values(s.positions || {});
      box.innerHTML = `<div class="stats">
          <div class="stat"><div class="k">Practice balance</div><div class="v num">$${Math.round(s.equity).toLocaleString()}</div><div class="small ${s.equity >= 100000 ? "up" : "down"}">${pct(s.equity / 100000 - 1, 2)} since start</div></div>
          <div class="stat"><div class="k">Open practice trades</div><div class="v">${pos.length}</div></div>
          <div class="stat"><div class="k">Data trust</div><div class="v">${(s.systemic_trust ?? 0).toFixed(2)}</div><div class="small muted">0–1: how real today's signals look</div></div>
          <div class="stat"><div class="k">Emergency stop</div><div class="v">${ks.active ? "On" : "Ready"}</div><div class="small muted">${ks.active ? esc(ks.reason) : "Closes everything if losses get too big"}</div></div>
        </div>
        ${sparkline(pts)}
        <p class="small muted">Last check ${esc((s.run_at || "").slice(0, 16).replace("T", " "))} UTC. The practice account trades only Bitcoin and Ether, using the full manipulation layer (order-book spoofing, mempool, stablecoin flows, liquidation cascades, funding games, wallet clustering). Days without trades are normal.</p>`;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the practice account right now.</div>`; }
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "record") renderRecord();
    if (name === "account") renderAccount();
    if (name === "trades") refreshTrades(false);
    store.set("omega.tab", name);
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  }
  function selectStyle(key) {
    state.style = key; store.set("omega.style", key);
    for (const b of document.querySelectorAll("#speed button")) b.setAttribute("aria-pressed", String(b.dataset.style === key));
    dropCharts("idea|");
    $("ideas").innerHTML = `<div class="skeleton"></div>`;
    refreshIdeas();
  }

  document.addEventListener("click", (ev) => {
    const take = ev.target.closest("[data-take]");
    if (take) {
      const idea = state.lastScan[state.style]?.ideas.find((d) => d.symbol === take.dataset.take);
      if (idea) {
        const nowPx = state.live[idea.symbol]?.price ?? idea.price_now;
        const card = take.closest("article"); const amt = +card.querySelector(".calc .amt")?.value || state.amount;
        const tr = E.makeTrade({ ...idea, price_now: nowPx }); tr.amount = amt;
        state.trades.push(tr); saveTrades(); connectLive();
        take.outerHTML = `<span class="pill good">Added at ${price(nowPx)}. Open “My trades” to see when to sell.</span>`;
      }
    }
    const sold = ev.target.closest("[data-sold]");
    if (sold) { state.trades = state.trades.filter((t) => t.id !== sold.dataset.sold); saveTrades(); refreshTrades(true); }
  });
  document.addEventListener("input", (ev) => {
    const calc = ev.target.closest(".calc"); if (!calc) return;
    if (ev.target.classList.contains("amt") && calc.closest("#ideas")) { state.amount = +ev.target.value || state.amount; store.set("omega.amount", state.amount); }
    const tid = calc.closest("[data-tid]")?.dataset.tid;
    if (tid && ev.target.classList.contains("amt")) { const t = state.trades.find((x) => x.id === tid); if (t) { t.amount = +ev.target.value || t.amount; saveTrades(); } }
    updateCalc(calc);
  });
  for (const b of document.querySelectorAll("#speed button")) b.addEventListener("click", () => selectStyle(b.dataset.style));
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  $("refresh").addEventListener("change", (e) => { state.refresh = +e.target.value; store.set("omega.refresh", state.refresh); schedule(); paintStatus(); });
  $("refreshNow").addEventListener("click", () => { state.universe = null; refreshIdeas(); });

  $("notifyBtn").addEventListener("click", async () => {
    if (!("Notification" in window)) { $("notifyState").textContent = "This browser doesn't support alerts. The tab title changes instead."; return; }
    const p = await Notification.requestPermission();
    $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off. The tab title will still change when it's time to act.";
  });

  $("manualForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    let coin = $("mCoin").value.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!coin) return;
    if (!coin.endsWith("USDT")) coin += "USDT";
    const entry = +$("mPrice").value, ago = +$("mAgo").value || 0, risk = +$("mRisk").value / 100;
    const style = $("mSpeed").value; const minutes = { quick: 60, short: 240, day: 1440 }[style];
    try { await getJSON(`${BINANCE}/ticker/price?symbol=${coin}`); }
    catch { $("manualMsg").textContent = `Couldn't find ${coin} on Binance. Check the coin name (for example SOL, ETH, DOGE).`; return; }
    const opened = Date.now() - ago * 60000;
    const tr = E.makeTrade({ symbol: coin, style, price_now: entry, risk_unit: risk, hold_minutes: minutes }, entry, opened);
    tr.amount = +$("mAmount").value || state.amount;
    state.trades.push(tr);
    saveTrades(); $("manualMsg").textContent = `Watching ${coinName(coin)}.`; $("manualForm").reset(); refreshTrades(true);
  });
  $("mCoin").addEventListener("change", async () => {
    let coin = $("mCoin").value.trim().toUpperCase(); if (!coin) return; if (!coin.endsWith("USDT")) coin += "USDT";
    try { const p = await getJSON(`${BINANCE}/ticker/price?symbol=${coin}`); if (!$("mPrice").value) $("mPrice").value = (+p.price).toPrecision(6); } catch { /* ignore */ }
  });
  $("copyBackup").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("backupOut").value); $("backupMsg").textContent = "Copied."; }
    catch { $("backupOut").select(); $("backupMsg").textContent = "Select the code and copy it."; }
  });
  $("loadBackup").addEventListener("click", () => {
    try {
      const add = JSON.parse(decodeURIComponent(escape(atob($("backupIn").value.trim()))));
      if (!Array.isArray(add)) throw new Error("bad");
      const ids = new Set(state.trades.map((t) => t.id));
      for (const t of add) if (t && t.symbol && !ids.has(t.id)) state.trades.push(t);
      saveTrades(); refreshTrades(true); $("backupMsg").textContent = `Loaded ${add.length} trade(s).`;
    } catch { $("backupMsg").textContent = "That code didn't work. Copy it again from the other device."; }
  });

  // ------------------------------------------------------------------ start
  $("refresh").value = String(state.refresh);
  for (const b of document.querySelectorAll("#speed button")) b.setAttribute("aria-pressed", String(b.dataset.style === state.style));
  const startTab = (location.hash || "").slice(1) || store.get("omega.tab", "ideas");
  selectTab(["ideas", "trades", "record", "account", "guide"].includes(startTab) ? startTab : "ideas");
  renderTradeCount();
  refreshIdeas();
  setInterval(paintStatus, 1000);
  setInterval(() => { if (state.trades.length && !$("tab-trades").hidden) refreshTrades(false); }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.nextAt && Date.now() > state.nextAt) refreshIdeas(); });
})();
