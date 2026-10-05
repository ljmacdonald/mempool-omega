/* Mempool Omega web app: runs entirely in your browser.
   Live prices and candles: Binance public market data (REST + WebSocket). Cross-checks: OKX and Gate.io.
   Models, track record and practice account: this project's public repository (updated by GitHub Actions). */
(function () {
  "use strict";
  const E = window.OmegaEngine, Q = window.OmegaQuality;
  const BINANCE = "https://data-api.binance.vision/api/v3";
  const WS = "wss://data-stream.binance.vision/stream?streams=";
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  // Two pages share this app: "main" (the ~150 most-traded coins) and "small" (the next tier, coins that often swing 20%+)
  const MODE = window.OMEGA_MODE || (location.pathname.includes("/small/") ? "small" : "main");
  const SMALL = MODE === "small";
  const PFX = SMALL ? "omega.small." : "omega.";
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
    anchors: store.get(PFX + "sugg", {}), earlier: store.get(PFX + "sugg.earlier", []), tickers: null, swing: store.get("omega.swing", {}),
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
  async function getJSONRevalidate(url, timeout = 30000) {   // reuse the browser's copy when the file hasn't changed
    const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeout);
    try { const r = await fetch(url, { signal: ctl.signal, cache: "no-cache" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
    finally { clearTimeout(t); }
  }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function loadConfig() {
    if (!state.cfg) state.cfg = await getJSON(REPO + "web/config.json");
    if (!Object.keys(state.bigMovers).length) { try { state.bigMovers = await getJSON(REPO + "reports/scanner_bigmovers.json"); } catch { /* optional */ } }
  }
  async function loadModel(style) {
    if (!state.models[style]) state.models[style] = await getJSONRevalidate(REPO + `web/model_${style}.json`);
    return state.models[style];
  }
  async function loadUniverse() {
    if (state.universe && Date.now() - state.universeAt < 10 * 60000) return state.universe;
    const tickers = await getJSON(`${BINANCE}/ticker/24hr`);
    state.tickers = tickers;
    state.universe = SMALL ? E.selectSmallUniverse(tickers, state.cfg.universe) : E.selectUniverse(tickers, state.cfg.universe);
    state.universeAt = Date.now();
    return state.universe;
  }
  // Small coins: how often each one rose 20%+ within a week over ~90 days (daily candles, remembered for a day)
  async function swingStats(symbols) {
    const day = new Date().toISOString().slice(0, 10);
    if (state.swing.day !== day) state.swing = { day, stats: {} };
    for (const s of symbols) {        // the nightly server file already has most of them
      const bm = state.bigMovers[s];
      if (!(s in state.swing.stats) && bm && Number.isFinite(bm.up_pct)) state.swing.stats[s] = { up_pct: bm.up_pct, down_pct: bm.down_pct };
    }
    const todo = symbols.filter((s) => !(s in state.swing.stats));
    const got = await pool(todo, 16, async (s) => {
      const rows = await getJSON(`${BINANCE}/klines?symbol=${s}&interval=1d&limit=97`);
      const d = { close: [], high: [], low: [] };
      for (const r of rows.slice(0, -1).slice(-96)) { d.close.push(+r[4]); d.high.push(+r[2]); d.low.push(+r[3]); }
      return E.bigMoverStats(d);
    });
    todo.forEach((s, k) => { if (got[k]) state.swing.stats[s] = { up_pct: got[k].up_pct, down_pct: got[k].down_pct }; });
    store.set("omega.swing", state.swing);
    return state.swing.stats;
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
  // After the first load only the newest candles are downloaded and added to what the page already has.
  const candleStore = {};
  async function candlesCached(symbol, interval, n) {
    const k = `${symbol}|${interval}`; const old = candleStore[k]; const step = INTERVAL_MS[interval];
    if (old && old.t.length >= n && Date.now() - old.t[old.t.length - 1] < step * 20) {
      const fresh = await candles(symbol, interval, 4);
      const lastT = old.t[old.t.length - 1];
      if (!fresh.t.length || fresh.t[0] <= lastT + step) {          // no gap: append the new closed candles
        const c = {}; for (const f of Object.keys(old)) if (Array.isArray(old[f])) c[f] = old[f].slice();
        fresh.t.forEach((tt, i) => { if (tt > lastT) for (const f of Object.keys(c)) c[f].push(fresh[f][i]); });
        for (const f of Object.keys(c)) c[f] = c[f].slice(-n);
        c.live = fresh.live; candleStore[k] = c; return c;
      }
    }
    const c = await candles(symbol, interval, n); candleStore[k] = c; return c;
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
      out[s] = E.combineChecks(checks, (state.adaptive || {}).check_penalties);
    });
    return out;
  }
  async function loadAdaptive() {
    if (state.adaptive && Date.now() - state.adaptiveAt < 3600000) return state.adaptive;
    const [ad, board] = await Promise.all([getJSON(REPO + "web/adaptive.json").catch(() => null),
      getJSON(REPO + `suggestions/${SMALL ? "small_" : ""}scoreboard.json`).catch(() => null)]);
    state.quality = (board && board.quality) || null;          // grade check + jumpiness adjustment from the track record
    state.adaptive = ad || state.quality ? { ...(ad || {}), vol: state.quality ? state.quality.vol : null } : null;
    state.adaptiveAt = Date.now();
    return state.adaptive;
  }

  // ------------------------------------------------------------------ scanning
  async function scan(styleKey, onPreview) {
    await loadConfig();
    const style = state.cfg.styles[styleKey];
    const [model, uni0] = await Promise.all([loadModel(styleKey), loadUniverse()]);
    let uni = uni0, movers = state.bigMovers;
    if (SMALL) {     // keep only small coins that often swing 20%+ within a week
      setStatus(`Checking how often ${uni0.length} smaller coins swing 20%+ in a week…`);
      movers = await swingStats(uni0.map((u) => u.symbol));
      uni = uni0.filter((u) => (movers[u.symbol]?.up_pct ?? 0) >= state.cfg.universe.small_min_swing);
    }
    const topN = SMALL ? state.cfg.universe.small_top_n : 5;
    const syms = uni.map((u) => u.symbol);
    if (!syms.includes("BTCUSDT")) syms.push("BTCUSDT");
    const data = await pool(syms, 16, (s) => candlesCached(s, style.interval, style.live_bars));
    const bySym = Object.fromEntries(syms.map((s, i) => [s, data[i]]));
    const coins = uni.map((u) => ({ symbol: u.symbol, quoteVolume: u.quoteVolume, candles: bySym[u.symbol] })).filter((c) => c.candles);
    if (coins.length < (SMALL ? 3 : 10)) throw new Error("not enough market data came back");
    const nCheck = SMALL ? 15 : 10;
    const res = E.rankCoins(coins, bySym.BTCUSDT, model, style, state.cfg, movers, nCheck, await loadAdaptive());
    if (onPreview) {     // show the ranking now; the fake-signal checks finish in the background and update it
      const pre = { ...res, ideas: E.applyProbation(res.ideas.map((d) => ({ ...d, checksPending: true })), state.adaptive, styleKey, state.cfg.grades).slice(0, topN),
        candles: bySym, at: Date.now(), style: styleKey, preview: true };
      onPreview(pre);
    }
    setStatus(`Showing the ranking · running fake-signal checks on the best ${nCheck} (about 15 seconds)…`);
    let checks = {};
    try { checks = await integrityFor(res.ideas, bySym, uni); } catch { /* checks unavailable */ }
    res.ideas = E.applyProbation(E.applyIntegrity(res.ideas, checks, nCheck, state.cfg.grades), state.adaptive, styleKey, state.cfg.grades).slice(0, topN);
    res.swingFiltered = uni0.length - uni.length;
    res.probation = ((state.adaptive || {}).probation || {})[styleKey] || null;
    res.candles = bySym; res.at = Date.now(); res.style = styleKey;
    state.lastScan[styleKey] = res;
    return res;
  }

  // First open: show the server's hourly list at once (clearly labelled) while live prices load
  async function quickHourly(style) {
    try {
      const [pl] = await Promise.all([getJSON(REPO + `suggestions/${SMALL ? "small_" : ""}latest_${style}.json`), loadAdaptive()]);
      if (state.lastScan[style] || state.style !== style || !pl.ideas?.length) return;
      const ideas = pl.ideas.map((d) => ({ ...d, exit_by: Date.parse(d.exit_by), chance_beats_market: d.chance_beats_market ?? 0.5 }));
      $("mood").className = "banner calm";
      $("mood").innerHTML = `<b>Showing the hourly list from ${esc(pl.generated_at.slice(11, 16))} UTC</b> while live prices load. It updates in a few seconds.`;
      $("ideas").innerHTML = `${Q.ideasBanner(state.quality)}<div style="display:grid;gap:14px">${ideas.map((d) => ideaCard(d, true, true)).join("")}</div>`;
      for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    } catch { /* no hourly list yet: the live one is coming */ }
  }

  async function refreshIdeas() {
    if (state.scanning) return;
    if (!state.lastScan[state.style]) quickHourly(state.style);
    state.scanning = true; $("refreshNow").disabled = true;
    setStatus(SMALL ? "Fetching live prices for the smaller coins…" : "Fetching live prices and re-ranking about 150 coins…");
    try {
      const res = await scan(state.style, (pre) => {
        for (const d of pre.ideas) {      // preview: show existing anchors, don't create or move any yet
          d.anchor = state.anchors[`${pre.style}:${d.symbol}`] || { at: Date.now(), price: d.price_now, take_profit: d.take_profit,
            safety_exit: d.safety_exit, exit_by: Date.now() + d.hold_minutes * 60000, risk_unit: d.risk_unit, score: d.score };
        }
        state.lastScan[pre.style] = pre; renderIdeas(pre); connectLive();
      });
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
    state.anchors = keep; store.set(PFX + "sugg", keep); store.set(PFX + "sugg.earlier", state.earlier);
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
  // a price that stops being confirmed (connection lost, coin stopped updating) is blanked out rather than left on screen
  function clearStale() {
    for (const el of document.querySelectorAll("[data-live]")) if (!E.freshPrice(state.live[el.dataset.live]) && el.textContent !== "–") {
      el.textContent = "–";
      for (const s2 of document.querySelectorAll(`[data-since="${el.dataset.live}"]`)) { s2.textContent = "waiting for the live price"; s2.className = "d muted"; }
      for (const c of document.querySelectorAll(`.calc[data-sym="${el.dataset.live}"]`)) updateCalc(c);
    }
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
      <p class="small muted">Quick estimate with Binance's 0.1% fee each way. For the exact money you'd take out after every fee, slippage, withdrawal, gas and front-running risk, open “Where to buy” below. Prices move; these are what-ifs, not promises.</p>
    </div>`;
  }
  function updateCalc(el) {
    const amt = +el.querySelector(".amt").value || 0; const entry = +el.dataset.entry, tp = +el.dataset.tp, sl = +el.dataset.sl;
    const tgt = +el.querySelector(".tgt").value; const now = E.freshPrice(state.live[el.dataset.sym]);
    const qty = amt * (1 - FEE) / entry;
    const row = (label, px, extra = "") => { const v = outcome(amt, entry, px); return `<tr><td>${label}${extra}</td><td class="num">${price(px)}</td><td class="num ${v >= 0 ? "up" : "down"}">${usd(v)}</td><td class="num muted">${pct(v / amt, 1)}</td></tr>`; };
    let html = `<tr><td colspan="4" class="muted">You'd get about <b class="num">${qty.toPrecision(5)}</b> ${esc(coinName(el.dataset.sym))} for $${amt.toLocaleString()} at ${price(entry)}.</td></tr>`;
    html += row("If it reaches the take profit", tp);
    html += row("If it hits the safety exit", sl);
    html += now ? row("If you sold right away", now, ` <span class="small muted">(live price)</span>`)
      : `<tr><td>If you sold right away</td><td colspan="3" class="muted">shown once the live price is confirmed</td></tr>`;
    if (tgt > 0) html += row("If you sell at your price", tgt);
    el.querySelector(".calc-out").innerHTML = html;
  }

  // ------------------------------------------------------------------ where to buy (all-in costs)
  const VENUE_SYM = {
    binance: (b) => `${b}USDT`, okx: (b) => `${b}-USDT`, bitget: (b) => `${b}USDT`, gate: (b) => `${b}_USDT`,
    htx: (b) => `${b.toLowerCase()}usdt`, kraken: (b) => `${b === "BTC" ? "XBT" : b}USD`, coinbase: (b) => `${b}-USD`,
  };
  const VENUE_LINK = {
    binance: (b) => `https://www.binance.com/en/trade/${b}_USDT`, okx: (b) => `https://www.okx.com/trade-spot/${b.toLowerCase()}-usdt`,
    bitget: (b) => `https://www.bitget.com/spot/${b}USDT`, gate: (b) => `https://www.gate.io/trade/${b}_USDT`,
    htx: (b) => `https://www.htx.com/trade/${b.toLowerCase()}_usdt`, kraken: (b) => `https://pro.kraken.com/app/trade/${b.toLowerCase()}-usd`,
    coinbase: (b) => `https://www.coinbase.com/advanced-trade/spot/${b}-USD`,
  };
  const num2 = (rows) => rows.map((r) => [+r[0], +r[1]]).filter(([p, q]) => p > 0 && q > 0);
  const BOOK = {
    binance: async (s) => { const d = await getJSON(`${BINANCE}/depth?symbol=${s}&limit=100`); return { bids: num2(d.bids), asks: num2(d.asks) }; },
    okx: async (s) => { const d = (await getJSON(`https://www.okx.com/api/v5/market/books?instId=${s}&sz=100`)).data[0]; return { bids: num2(d.bids), asks: num2(d.asks) }; },
    bitget: async (s) => { const d = (await getJSON(`https://api.bitget.com/api/v2/spot/market/orderbook?symbol=${s}&limit=100`)).data; return { bids: num2(d.bids), asks: num2(d.asks) }; },
    gate: async (s) => { const d = await getJSON(`https://api.gateio.ws/api/v4/spot/order_book?currency_pair=${s}&limit=100`); return { bids: num2(d.bids), asks: num2(d.asks) }; },
    htx: async (s) => { const d = (await getJSON(`https://api.huobi.pro/market/depth?symbol=${s}&type=step0`)).tick; return { bids: num2(d.bids).slice(0, 100), asks: num2(d.asks).slice(0, 100) }; },
    kraken: async (s) => { const r = (await getJSON(`https://api.kraken.com/0/public/Depth?pair=${s}&count=100`)).result; const d = r[Object.keys(r)[0]]; return { bids: num2(d.bids), asks: num2(d.asks) }; },
    coinbase: async (s) => { const d = await getJSON(`https://api.exchange.coinbase.com/products/${s}/book?level=2`); return { bids: num2(d.bids).slice(0, 200), asks: num2(d.asks).slice(0, 200) }; },
  };
  const RPC = { ethereum: "https://ethereum-rpc.publicnode.com", bsc: "https://bsc-rpc.publicnode.com", base: "https://base-rpc.publicnode.com", arbitrum: "https://arbitrum-one-rpc.publicnode.com" };
  const MAJOR_QUOTES = new Set(["USDT", "USDC", "WETH", "ETH", "WBNB", "BNB", "SOL", "WSOL", "DAI", "USD1", "FDUSD", "USDE"]);
  const cache = {};
  async function cached(key, ttl, fn) { const c = cache[key]; if (c && Date.now() - c.at < ttl) return c.v; const v = await fn(); cache[key] = { at: Date.now(), v }; return v; }
  async function gasUsd(chain) {
    const c = E.CHAINS[chain]; if (c.gasUsd !== undefined) return c.gasUsd;
    return cached(`gas:${chain}`, 60000, async () => {
      const r = await fetch(RPC[chain], { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "eth_gasPrice", params: [] }) });
      const wei = parseInt((await r.json()).result, 16);
      const px = +(await getJSON(`${BINANCE}/ticker/price?symbol=${c.native}USDT`)).price;
      return wei * c.gasUnits / 1e18 * px;
    });
  }
  async function dexPools(base) {
    return cached(`dex:${base}`, 120000, async () => {
      const d = await getJSON(`https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(base)}`);
      const seen = new Set(); const out = [];
      for (const p of (d.pairs || []).filter((p) => p.baseToken?.symbol?.toUpperCase() === base && E.CHAINS[p.chainId] && MAJOR_QUOTES.has((p.quoteToken?.symbol || "").toUpperCase()) && (p.liquidity?.usd || 0) >= 100000 && +p.priceUsd > 0)
        .sort((a, b) => b.liquidity.usd - a.liquidity.usd)) {
        const k = `${p.chainId}:${p.dexId}`; if (seen.has(k)) continue; seen.add(k); out.push(p); if (out.length >= 3) break;
      }
      return out;
    });
  }

  async function compareVenues(idea, amount) {
    const base = coinName(idea.symbol); const a = idea.anchor || idea;
    const refMid = state.live[idea.symbol]?.price ?? idea.price_now;
    const tasks = Object.keys(E.VENUES).map(async (v) => {
      try {
        const book = await cached(`book:${v}:${base}`, 30000, () => BOOK[v](VENUE_SYM[v](base)));
        if (!book.asks.length || !book.bids.length) return null;
        const f = E.VENUES[v]; const r = E.cexNet({ amount, asks: book.asks, bids: book.bids, taker: f.taker, withdraw_usd: f.withdraw_usd, refMid });
        if (Math.abs((book.asks[0][0] + book.bids[0][0]) / 2 / refMid - 1) > 0.2) return null;   // a different token with the same ticker
        return { kind: "CEX", name: f.name, link: VENUE_LINK[v](base), fee: f.taker, r, net: r.netAt(a.take_profit), loss: r.netAt(a.safety_exit),
          note: `${(f.taker * 100).toFixed(2)}% taker fee each way · ${usd(-f.withdraw_usd).replace("−", "")} to withdraw` + (r.filled ? "" : " · order book too thin for this amount") };
      } catch { return null; }
    });
    let pools = [];
    try { pools = await dexPools(base); } catch { /* DexScreener unavailable */ }
    const dexTasks = pools.map(async (p) => {
      try {
        const ch = E.CHAINS[p.chainId]; const g = await gasUsd(p.chainId);
        const fee = E.DEX_FEE[p.dexId] ?? 0.003;
        const args = { amount, priceUsd: +p.priceUsd, liqUsd: p.liquidity.usd, swapFee: fee, gasUsd: g, refMid };
        const r = E.dexNet({ ...args, mev: ch.mev }); const safe = E.dexNet({ ...args, mev: 0 });
        if (Math.abs(+p.priceUsd / refMid - 1) > 0.2) return null;
        const addr = p.baseToken.address;
        return { kind: "DEX", name: `${p.dexId} on ${ch.name}`, link: p.url, fee, r, net: r.netAt(a.take_profit), loss: r.netAt(a.safety_exit), safeNet: safe.netAt(a.take_profit),
          note: `${(fee * 100).toFixed(2)}% swap fee · ~${usd(-g).replace("−", "")} gas per swap · pool ${usd(-p.liquidity.usd).replace("−", "").replace(/\.\d+$/, "")} · token ${addr.slice(0, 6)}…${addr.slice(-4)} (check it's the real one) · front-running/sandwich bots could take ~${usd(-(safe.netAt(a.take_profit) - r.netAt(a.take_profit))).replace("−", "")}; use ${ch.protect} to avoid it` };
      } catch { return null; }
    });
    const rows = (await Promise.all(tasks.concat(dexTasks))).filter(Boolean).sort((x, y) => y.net - x.net);
    return { rows, amount, base };
  }

  function venueTable(cmp) {
    if (!cmp.rows.length) return `<p class="small muted">Couldn't load prices from other exchanges right now.</p>`;
    const best = cmp.rows.find((r) => r.r.filled !== false) || cmp.rows[0];
    return `<div class="banner ${best.net >= 0 ? "good" : "warn"}"><b>Best place for $${cmp.amount.toLocaleString()}: ${esc(best.name)}.</b> If it reaches the take profit you'd take out about <b>${usd(best.net)}</b> after every fee (trading fees, slippage, ${best.kind === "DEX" ? "gas and front-running risk" : "withdrawal"}). If it hits the safety exit: ${usd(best.loss)}.</div>
      <div class="table-wrap"><table><thead><tr><th>Where</th><th class="num">Money out at take profit</th><th class="num">At safety exit</th><th class="num">Break-even price</th><th>Costs included</th></tr></thead><tbody>
      ${cmp.rows.map((r) => `<tr><td><a href="${esc(r.link)}" target="_blank" rel="noopener">${esc(r.name)}</a> <span class="pill ${r.kind === "DEX" ? "warn" : "calm"}">${r.kind}</span></td>
        <td class="num ${r.net >= 0 ? "up" : "down"}">${usd(r.net)}</td><td class="num down">${usd(r.loss)}</td><td class="num">${price(r.r.breakEven)}</td>
        <td class="small" style="white-space:normal;min-width:16em">${esc(r.note)}${r.safeNet !== undefined ? ` (with protection: ${usd(r.safeNet)})` : ""}</td></tr>`).join("")}
      </tbody></table></div>
      <p class="small muted">Live order books from each exchange: your amount is "walked" through real orders to get the price you'd actually pay and receive. Fees are standard entry-level rates (yours may be lower with VIP tiers or fee-token discounts). Withdrawal = typical fee to move stablecoins off the exchange on a cheap network; turning them into bank money can add more. Using limit orders instead of market orders can cut fees and slippage, but they may not fill. DEX rows only list pools with at least $100,000 in them. Fake tokens often copy popular names, so check the token address on the exchange or a block explorer before buying.</p>`;
  }

  async function fillVenues(details) {
    const card = details.closest("article"); const sym = details.dataset.sym;
    const idea = state.lastScan[state.style]?.ideas.find((d) => d.symbol === sym); if (!idea) return;
    const amount = +card.querySelector(".calc .amt")?.value || state.amount;
    const box = details.querySelector(".venues-out"); box.innerHTML = `<p class="small muted">Checking ${Object.keys(E.VENUES).length} exchanges and decentralised exchanges for $${amount.toLocaleString()}…</p>`;
    const cmp = await compareVenues(idea, amount); box.innerHTML = venueTable(cmp); box.dataset.amount = amount;
  }

  // ------------------------------------------------------------------ rendering: ideas
  function checksBlock(it, pending) {
    if (pending) return `<p class="small muted">⏳ Fake-signal checks are running for this coin (order book read 3 times at random moments). The score updates when they finish.</p>`;
    if (!it) return `<p class="small muted">Fake-signal checks couldn't run for this coin right now.</p>`;
    const icon = (ok) => (ok === true ? `<span class="ok">✓</span>` : ok === false ? `<span class="bad">✗</span>` : `<span class="na">–</span>`);
    const label = it.checked ? `${it.passed} of ${it.checked} checks passed` : "Checks unavailable";
    const cls = it.penalty === 0 ? "good" : it.penalty < 0.1 ? "calm" : "warn";
    return `<details class="checks"><summary>Fake-signal check: <span class="pill ${cls}">${label}</span></summary>
      <ul>${it.checks.map((c) => `<li>${icon(c.ok)}<span>${esc(c.text)}</span></li>`).join("")}</ul>
      <p class="small muted">Checks: spoofing (fake orders that vanish), thin order book, fake back-and-forth trading, whether other exchanges (OKX, Gate.io) confirm the price, and whale-sized trades. Failed checks lower the score.</p></details>`;
  }

  function ideaCard(d, hourly, loading) {
    const a = d.anchor || { at: Date.parse(d.suggested_at || "") || Date.now(), price: d.price_now, take_profit: d.take_profit, safety_exit: d.safety_exit, exit_by: d.exit_by };
    const held = state.trades.some((t) => t.symbol === d.symbol && t.style === d.style);
    const nowPx = E.freshPrice(state.live[d.symbol]);      // live only: an old price is never shown as "now"
    const since = nowPx ? nowPx / a.price - 1 : NaN;
    const already = !nowPx ? "" : nowPx >= a.take_profit ? `<div class="banner good">Since it was suggested, the price already reached the take-profit level.</div>` :
      nowPx <= a.safety_exit ? `<div class="banner bad">Since it was suggested, the price already fell to the safety exit. Don't buy it now.</div>` : "";
    const bm = Number.isFinite(d.week_up20_pct) ? `<p class="small muted">Last 90 days: it rose 20%+ within a week ${Math.round(d.week_up20_pct * 100)}% of the time and fell 20%+ ${Math.round(d.week_down20_pct * 100)}% of the time.</p>` : "";
    const chartId = `chart-idea-${d.style}-${d.symbol}`;
    return `<article class="card">
      <div class="idea-head">
        <h2><span class="rank">${d.rank}.</span>${esc(d.coin)}</h2>
        <div class="score"><b>${d.score.toFixed(1)}</b><span class="muted">/ 10</span>
          <span class="pill ${Q.gradeClassFor(d.grade, state.quality)}">${esc(Q.gradeWord(d.grade, state.quality, d.rank))}</span>
          <span class="pill ${riskClass(d.risk_level)}">Risk: ${esc(d.risk_level)}</span></div>
      </div>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${localDay(a.at)}</div><div class="d muted">at ${price(a.price)}</div></div>
        <div class="fact"><div class="k">Price now</div><div class="v" data-live="${esc(d.symbol)}">${price(nowPx)}</div><div class="d ${!nowPx ? "muted" : since >= 0 ? "up" : "down"}" data-since="${esc(d.symbol)}" data-ref="${a.price}">${nowPx ? `${pct(since, 2)} since suggested` : "waiting for the live price"}</div>${hourly ? "" : '<div class="livebadge live small muted">Live</div>'}</div>
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
      ${checksBlock(d.integrity, d.checksPending)}
      ${bm}
      ${calcBlock(d.symbol, a.price, a.take_profit, a.safety_exit, state.amount, "Profit calculator")}
      ${hourly ? "" : `<details class="venues" data-sym="${esc(d.symbol)}"><summary>Where to buy it cheapest: actual money you'd take out after all fees</summary><div class="venues-out"></div></details>`}
      <div class="actions">
        ${hourly ? `<span class="small muted">${loading ? "Hourly snapshot. Live prices are loading…" : "Hourly snapshot. Live prices are unavailable right now."}</span>` :
        held ? `<span class="pill calm">You're watching this trade</span>` :
        `<button class="btn primary" type="button" data-take="${esc(d.symbol)}">I bought this: watch it for me</button>`}
      </div>
    </article>`;
  }

  function renderIdeas(res) {
    const m = res.mood, style = state.cfg.styles[res.style];
    const cls = m.label === "Favourable" ? "good" : m.label === "Mixed" ? "calm" : "warn";
    $("mood").className = `banner ${cls}`;
    const pb = res.probation && res.probation.active ? `<br><b>On probation:</b> this speed's last ${res.probation.n} ideas did ${pct(res.probation.avg_vs_random, 2)} vs picking coins at random, so the system has lowered its scores until it does better.` : "";
    const smallNote = SMALL ? ` These are smaller coins that rose 20%+ within a week at least ${Math.round(state.cfg.universe.small_min_swing * 100)}% of the time over the last 90 days (${m.coins} qualified now). <b>They swing both ways: expect bigger losses too.</b>` : "";
    $("mood").innerHTML = `<b>Market mood: ${m.label}.</b> ${Math.round(m.share_positive * 100)}% of ${m.coins} coins look positive for the ${esc(style.label.split(":")[0])} speed (sell within ${esc(style.hold_text)}). ${m.label === "Unfavourable" ? "Doing nothing is a perfectly good choice right now." : "Scores above 5 are better than break-even after fees."}${smallNote}${pb}`;
    dropCharts("idea|");
    // prices fetched by this scan are live: record them before drawing the cards
    for (const d of res.ideas) { const live = res.candles[d.symbol]?.live; if (live) state.live[d.symbol] = { price: live.close, at: res.at || Date.now() }; }
    $("ideas").innerHTML = `${Q.ideasBanner(state.quality)}<div style="display:grid;gap:14px">${res.ideas.map((d) => ideaCard(d, false)).join("")}</div>${earlierBlock()}`;
    for (const d of res.ideas) {
      const a = d.anchor;
      const ch = makeChart($(`chart-idea-${d.style}-${d.symbol}`), res.candles[d.symbol], [
        { price: a.price, color: "--calm", dashed: true, title: "Suggested" },
        { price: a.take_profit, color: "--good", title: "Take profit" },
        { price: a.safety_exit, color: "--bad", title: "Safety exit" }], a.at, style.interval);
      if (ch) state.charts[`idea|${d.symbol}|${style.interval}`] = ch;
    }
    for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    paintLiveBadges(); refreshEarlierPrices();
  }

  function earlierBlock() {
    const rows = state.earlier.filter((e) => e.style === state.style).slice(0, 15);
    if (!rows.length) return "";
    return `<article class="card"><h3>Earlier suggestions on this device (${esc(state.cfg.styles[state.style].label.split(":")[0])} speed, last 48 hours)</h3>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th>Suggested</th><th class="num">Price then</th><th class="num">Price now</th><th class="num">Change</th><th>Sell-by</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td>${esc(coinName(e.symbol))}</td><td>${esc(localDay(e.at))}</td><td class="num">${price(e.price)}</td><td class="num" data-live="${esc(e.symbol)}">${price(E.freshPrice(state.live[e.symbol]))}</td><td class="num" data-since="${esc(e.symbol)}" data-ref="${e.price}">–</td><td>${Date.now() > e.exit_by ? "passed" : local(e.exit_by)}</td></tr>`).join("")}
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
      const pl = await getJSON(REPO + `suggestions/${SMALL ? "small_" : ""}latest_${state.style}.json`);
      const ideas = pl.ideas.map((d) => ({ ...d, exit_by: Date.parse(d.exit_by), chance_beats_market: d.chance_beats_market ?? d.chance_of_profit ?? 0.5 }));
      $("mood").className = "banner warn";
      $("mood").innerHTML = `<b>Live prices are unavailable</b> (${esc(err.message || err)}). Showing the ideas saved at ${esc(pl.generated_at.slice(11, 16))} UTC instead. Prices may have moved since. Binance may be blocked on your network; try another network or try again later.`;
      $("ideas").innerHTML = `${Q.ideasBanner(state.quality)}<div style="display:grid;gap:14px">${ideas.map((d) => ideaCard(d, true)).join("")}</div>`;
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
    catch { prices = Object.fromEntries(state.trades.map((t) => [t.symbol, E.freshPrice(state.live[t.symbol])])); }
    const needCharts = rebuildCharts || state.trades.some((t) => !Object.keys(state.charts).some((k) => k.startsWith(`trade|${t.id}|`)));
    const openCalc = new Set([...document.querySelectorAll("#trades details[open]")].map((e) => e.dataset.id));
    const amounts = Object.fromEntries([...document.querySelectorAll("#trades .calc .amt")].map((e) => [e.closest("[data-tid]")?.dataset.tid, e.value]));
    const html = [];
    for (const t of state.trades) {
      const px = prices[t.symbol] ?? E.freshPrice(state.live[t.symbol]);
      if (prices[t.symbol]) state.live[t.symbol] = { price: px, at: Date.now() };
      const scan = state.lastScan[t.style]; let score = scan?.scores?.[t.symbol];
      if (scan && Number.isFinite(score)) {
        const seen = state.lowStreak[t.id] || { at: 0, n: 0 };
        if (seen.at !== scan.at) state.lowStreak[t.id] = { at: scan.at, n: score < 4.5 ? seen.n + 1 : 0 };
        if (state.lowStreak[t.id].n < 2) score = Math.max(score, 4.5);   // one bad reading could be a planted signal
      }
      const a = Number.isFinite(px) ? E.advise(t, px, Date.now(), score) : E.adviseNoPrice(t, Date.now());
      notifyIfChanged(t, a);
      const amt = +(amounts[t.id] ?? t.amount ?? state.amount);
      const pl = outcome(amt, t.entry, px);
      html.push(`<article class="card trade ${a.level}" data-tid="${esc(t.id)}">
        <div class="idea-head"><h2>${esc(coinName(t.symbol))}</h2><span class="verdict ${a.level}">${esc(a.action)}</span></div>
        <p>${esc(a.why)}</p>
        <div class="strip">
          <div class="fact"><div class="k">You bought</div><div class="v">${localDay(t.opened)}</div><div class="d muted">at ${price(t.entry)}</div></div>
          <div class="fact"><div class="k">Price now</div><div class="v" data-live="${esc(t.symbol)}">${price(px)}</div><div class="d ${a.stale ? "muted" : a.pnl >= 0 ? "up" : "down"}">${a.stale ? "waiting for the live price" : `${pct(a.pnl, 2)} after fees · ${usd(pl)} on $${amt.toLocaleString()}`}</div><div class="livebadge live small muted">Live</div></div>
          <div class="fact"><div class="k">Take profit at</div><div class="v">${price(t.take_profit)}</div><div class="d muted">${a.stale ? "" : `${pct(a.toTp)} away`}</div></div>
          <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(t.safety_exit)}</div><div class="d muted">${a.stale ? "" : `${pct(a.toSl)} away`}</div></div>
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
    if (!prev || prev === a.action || a.action === "Hold" || a.action === "Waiting for a live price") return;
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
      const b = await getJSON(REPO + `suggestions/${SMALL ? "small_" : ""}scoreboard.json`);
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
      html += Q.gradeTable(b.quality);
      try {
        const h = parseCSV(await getText(REPO + `suggestions/${SMALL ? "small_" : ""}history.csv`)).filter((r) => r.status === "closed").slice(-25).reverse();
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

  async function renderImprove() {
    const box = $("improve");
    try {
      const [log, ad] = await Promise.all([getJSON(REPO + "reports/improvements.json"), getJSON(REPO + "web/adaptive.json").catch(() => ({}))]);
      if (!log.length) { box.innerHTML = `<div class="banner calm">The first nightly self-review hasn't run yet.</div>`; return; }
      const prob = Object.entries(ad.probation || {});
      const pen = ad.check_penalties || {};
      box.innerHTML = `<div class="stats">${prob.map(([k, v]) => `<div class="stat"><div class="k">${esc(state.cfg?.styles[k]?.label.split(":")[0] || k)} speed</div><div class="v">${v.active ? "Probation" : "Normal"}</div><div class="small muted">${v.n ? `last ${v.n} ideas: ${pct(v.avg_vs_random, 2)} vs random` : "not enough checked ideas yet"}</div></div>`).join("")}</div>
        ${Object.keys(pen).length ? `<h3>Current weight of each fake-signal check (learned from results)</h3><div class="table-wrap"><table><tbody>${Object.entries(pen).map(([k, v]) => `<tr><td>${esc(k.replace("_", " "))}</td><td class="num">${(+v).toFixed(2)}</td></tr>`).join("")}</tbody></table></div>` : ""}
        <h3>What the system changed about itself</h3>
        ${log.slice(0, 14).map((e) => `<article class="card"><b>${esc(e.date)} UTC</b><ul class="why">${e.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul></article>`).join("")}`;
    } catch { box.innerHTML = `<div class="banner calm">The first nightly self-review hasn't run yet. It runs every night at about 02:17 UTC.</div>`; }
  }

  // ------------------------------------------------------------------ biggest movers (why each is or isn't suggested)
  const money = (x) => (x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${(x / 1e6).toFixed(1)}M` : `$${Math.round(x / 1e3)}k`);
  async function renderMovers() {
    const box = $("movers");
    try {
      await loadConfig();
      if (!state.tickers || Date.now() - state.universeAt > 120000) { state.universe = null; await loadUniverse(); }
      const ucfg = state.cfg.universe; const tk = state.tickers;
      const main = new Set(E.selectUniverse(tk, ucfg).map((r) => r.symbol));
      const small = new Set(E.selectSmallUniverse(tk, ucfg).map((r) => r.symbol));
      const excluded = new Set([...ucfg.exclude_stablecoins, ...ucfg.exclude_other, ...ucfg.exclude_stock_tokens]);
      const rows = tk.filter((x) => x.symbol.endsWith("USDT") && +x.quoteVolume >= 100000 && /^[A-Z0-9]{2,15}$/.test(x.symbol.slice(0, -4)) &&
        !excluded.has(x.symbol.slice(0, -4)) && !/(UP|DOWN|BULL|BEAR)$/.test(x.symbol.slice(0, -4)))
        .sort((a, b) => +b.priceChangePercent - +a.priceChangePercent).slice(0, 20);
      const res = state.lastScan[state.style];
      const all = Object.fromEntries((res?.all || []).map((r) => [r.symbol, r]));
      const top = new Set((res?.ideas || []).map((d) => d.symbol));
      const why = (s, qv) => {
        if (top.has(s)) return `<span class="pill good">In the current top ${res.ideas.length}</span>`;
        const a = all[s];
        if (a) return `Score ${a.score.toFixed(1)}/10, #${a.place} of ${res.all.length} for this speed. ${esc(a.warnings[0] || "Other coins scored higher.")}`;
        if (SMALL && main.has(s)) return `Big enough for the <a href="./">Exchange coins</a> page: look there.`;
        if (!SMALL && small.has(s)) return `A smaller coin: covered by the <a href="small/">Small coins</a> page.`;
        if (SMALL && small.has(s)) { const sw = state.swing.stats?.[s]; return sw ? `Swung 20%+ within a week only ${Math.round(sw.up_pct * 100)}% of the time (the page needs ${Math.round(ucfg.small_min_swing * 100)}%).` : "Not checked yet: refresh the ideas first."; }
        if (SMALL) return `Not scanned: only ${money(qv)} traded a day. Coins this thin are easy to push around.`;
        return `Not scanned here: only ${money(qv)} traded a day. ${qv >= ucfg.small_min_quote_volume_usd ? 'See the <a href="small/">Small coins</a> page.' : "Coins this thin are easy to push around."}`;
      };
      box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Coin</th><th class="num">24 h change</th><th class="num">Price</th><th class="num">Traded (24 h)</th><th style="min-width:18em">Why it is or isn't suggested</th></tr></thead><tbody>
        ${rows.map((x) => `<tr><td><b>${esc(coinName(x.symbol))}</b></td><td class="num ${+x.priceChangePercent >= 0 ? "up" : "down"}">${pct(+x.priceChangePercent / 100)}</td><td class="num">${price(+x.lastPrice)}</td><td class="num">${money(+x.quoteVolume)}</td><td class="small" style="white-space:normal">${why(x.symbol, +x.quoteVolume)}</td></tr>`).join("")}
        </tbody></table></div>
        <p class="small muted">Biggest 24-hour gainers on Binance. Information only, not suggestions. A coin that already jumped is often <b>marked down</b>: buying after a spike usually means buying near the top, and pumps are a favourite trap. ${res ? `Scores are for the ${esc(state.cfg.styles[state.style].label.split(":")[0])} speed from your last refresh at ${local(res.at)}.` : "Refresh the ideas to see scores."}</p>`;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the biggest movers right now.</div>`; }
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
    if (name === "improve") renderImprove();
    if (name === "movers") renderMovers();
    if (name === "trades") refreshTrades(false);
    store.set("omega.tab", name);
    if (location.hash !== `#${name}`) history.replaceState(null, "", `${location.pathname}${location.search}#${name}`);   // page has <base> on Small coins
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
        const nowPx = E.freshPrice(state.live[idea.symbol]);
        if (!nowPx) { take.textContent = "Waiting for the live price… tap again in a few seconds"; return; }
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
    if (ev.target.classList.contains("amt") && calc.closest("#ideas")) {
      state.amount = +ev.target.value || state.amount; store.set("omega.amount", state.amount);
      const v = calc.closest("article")?.querySelector("details.venues[open]");
      if (v) { clearTimeout(v._t); v._t = setTimeout(() => fillVenues(v), 600); }
    }
    const tid = calc.closest("[data-tid]")?.dataset.tid;
    if (tid && ev.target.classList.contains("amt")) { const t = state.trades.find((x) => x.id === tid); if (t) { t.amount = +ev.target.value || t.amount; saveTrades(); } }
    updateCalc(calc);
  });
  // day/night switch: charts take their colours when drawn, so redraw them
  window.addEventListener("omega-theme", () => {
    const r = state.lastScan[state.style];
    if (r) renderIdeas(r);
    if (state.trades.length) refreshTrades(true);
  });
  document.addEventListener("toggle", (ev) => { const d = ev.target; if (d.matches && d.matches("details.venues") && d.open) fillVenues(d); }, true);
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
  document.documentElement.dataset.mode = MODE;
  for (const a of document.querySelectorAll("nav.sites a")) { if (a.dataset.site === MODE) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current"); }
  if (SMALL) {
    document.title = "Mempool Omega · Small coins";
    $("pageTitle").textContent = "Mempool Omega · Small coins";
    $("pageSub").textContent = "Top 10 smaller coins that often swing 20–50% · bigger moves both ways · practice and education only, not financial advice";
    $("mood").textContent = "Checking smaller coins…";
    document.querySelector('nav.tabs button[data-tab="ideas"]').textContent = "Top 10 ideas";
  }
  $("refresh").value = String(state.refresh);
  for (const b of document.querySelectorAll("#speed button")) b.setAttribute("aria-pressed", String(b.dataset.style === state.style));
  const startTab = (location.hash || "").slice(1) || store.get("omega.tab", "ideas");
  selectTab(["ideas", "movers", "trades", "record", "improve", "account", "guide"].includes(startTab) ? startTab : "ideas");
  renderTradeCount();
  refreshIdeas();
  setInterval(paintStatus, 1000);
  setInterval(clearStale, 5000);
  setInterval(() => { if (!document.hidden && !$("tab-ideas").hidden) refreshEarlierPrices(); }, 30000);
  setInterval(() => { if (state.trades.length && !$("tab-trades").hidden) refreshTrades(false); }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.nextAt && Date.now() > state.nextAt) refreshIdeas(); });
})();
