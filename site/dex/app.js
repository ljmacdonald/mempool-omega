/* Mempool Omega DEX page: runs in your browser.
   Hourly: the repository's snapshot of checked candidates (state/dex/snapshot.json, made by GitHub Actions).
   Every refresh: live prices and pool money (DexScreener), live security re-checks (GoPlus, honeypot.is,
   RugCheck), all-in costs for YOUR amount, then the ranking. Charts: GeckoTerminal candles. */
(function () {
  "use strict";
  const E = window.OmegaEngine, X = window.OmegaDex;
  const REPO = window.OMEGA_REPO || "https://raw.githubusercontent.com/ljmacdonald/mempool-omega/main/state/";
  const GT = "https://api.geckoterminal.com/api/v2", DS = "https://api.dexscreener.com";
  const GRADES = [[6.5, "Strong"], [5.6, "Moderate"], [5.0, "Weak"], [-1, "Avoid - watch only"]];
  const LIVE_CHECK = 8;               // candidates re-checked live each refresh
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
  };
  const state = {
    style: store.get("omega.dex.style", "dex_short"), chain: store.get("omega.dex.chain", "all"),
    profile: store.get("omega.dex.profile", "standard"),
    refresh: store.get("omega.dex.refresh", 300), amount: store.get("omega.dex.amount", 100),
    snap: null, snapAt: 0, live: {}, sec: {}, ideas: [], scanning: false, timer: null, nextAt: 0, lastAt: 0,
    trades: store.get("omega.dex.trades", []), anchors: store.get("omega.dex.sugg", {}), charts: {}, lastAction: {},
  };

  // ------------------------------------------------------------------ formatting
  const price = (x) => (!Number.isFinite(x) ? "–" : x >= 1000 ? `$${x.toLocaleString("en-US", { maximumFractionDigits: 2 })}` :
    x >= 1 ? `$${x.toFixed(x >= 100 ? 2 : 4)}` : `$${x.toPrecision(4)}`);
  const pct = (x, d = 1) => (Number.isFinite(x) ? `${x >= 0 ? "+" : ""}${(x * 100).toFixed(d)}%` : "–");
  const usd = (x) => (Number.isFinite(x) ? `${x >= 0 ? "+" : "−"}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : "–");
  const money = (x) => (Number.isFinite(x) ? `$${Math.round(x).toLocaleString("en-US")}` : "–");
  const local = (ms) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const localDay = (ms) => new Date(ms).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });
  const hhmm = (ms) => new Date(ms).toISOString().slice(11, 16);
  const gradeOf = (s) => GRADES.find(([th]) => s >= th)[1];
  const gradeClass = (g) => (g === "Strong" ? "good" : g === "Moderate" ? "calm" : g === "Weak" ? "warn" : "bad");
  const riskLevel = (ru, st) => { const x = (ru - st.min) / (st.max - st.min); return x < 0.15 ? "Low" : x < 0.35 ? "Medium" : x < 0.6 ? "High" : "Very high"; };
  const RISK_RANGE = { dex_short: { min: 0.02, max: 0.15 }, dex_day: { min: 0.04, max: 0.30 } };
  const riskClass = (r) => (r === "Low" ? "good" : r === "Medium" ? "calm" : r === "High" ? "warn" : "bad");
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const key = (t) => `${t.chain}:${t.token}`;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function getJSON(url, timeout = 20000) {
    const ctl = new AbortController(); const tm = setTimeout(() => ctl.abort(), timeout);
    try { const r = await fetch(url, { signal: ctl.signal, cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return await r.json(); }
    finally { clearTimeout(tm); }
  }
  async function getText(url) { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text(); }
  const cache = {};
  async function cached(k, ttl, fn) { const c = cache[k]; if (c && Date.now() - c.at < ttl) return c.v; const v = await fn(); cache[k] = { at: Date.now(), v }; return v; }
  // GeckoTerminal allows 30 calls a minute per visitor: space them out
  let gtNext = 0;
  async function gt(path) { const wait = gtNext - Date.now(); gtNext = Math.max(Date.now(), gtNext) + 2200; if (wait > 0) await sleep(wait); return getJSON(GT + path); }

  // ------------------------------------------------------------------ data
  async function loadSnapshot(force) {
    if (!force && state.snap && Date.now() - state.snapAt < 10 * 60000) return state.snap;
    state.snap = await getJSON(REPO + "dex/snapshot.json", 30000); state.snapAt = Date.now();
    return state.snap;
  }
  // Live price and pool money from DexScreener (30 tokens per call, one call per network)
  async function liveMarket(tokens) {
    const byChain = {};
    for (const t of tokens) (byChain[t.chain] = byChain[t.chain] || []).push(t);
    await Promise.all(Object.entries(byChain).map(async ([chain, ts]) => {
      const ds = state.snap.chains[chain].ds;
      for (let i = 0; i < ts.length; i += 30) {
        const chunk = ts.slice(i, i + 30);
        try {
          const pairs = await getJSON(`${DS}/tokens/v1/${ds}/${chunk.map((t) => t.token).join(",")}`);
          for (const t of chunk) {
            const mine = (pairs || []).filter((p) => norm(chain, (p.baseToken || {}).address) === t.token);
            const pr = mine.find((p) => norm(chain, p.pairAddress) === t.pool);
            if (!pr) {           // DexScreener doesn't list this pool: price from its deepest pool, money from GeckoTerminal below
              const any = mine.sort((a, b) => ((b.liquidity || {}).usd || 0) - ((a.liquidity || {}).usd || 0))[0];
              if (any && +any.priceUsd > 0) state.live[key(t)] = { ...(state.live[key(t)] || {}), price: +any.priceUsd, at: Date.now() };
              continue;
            }
            const qp = t.quote_price > 0 ? t.quote_price : NaN;
            const liq = pr.liquidity && pr.liquidity.quote && Number.isFinite(qp) ? 2 * pr.liquidity.quote * qp : NaN;
            state.live[key(t)] = { price: +pr.priceUsd, liq, at: Date.now(), tx_h24: pr.txns && pr.txns.h24, vol_h24: pr.volume && pr.volume.h24 };
          }
        } catch { /* keep the snapshot values */ }
      }
      // pools DexScreener doesn't list: GeckoTerminal batch (30 per request), pool money scaled from the hourly value
      const miss = ts.filter((t) => !Number.isFinite((state.live[key(t)] || {}).liq) && t.reserve_usd > 0);
      for (let i = 0; i < miss.length; i += 30) {
        const chunk = miss.slice(i, i + 30);
        try {
          const d = await cached(`gtm:${chain}:${chunk.map((t) => t.pool).join(",")}`, 50000, () => gt(`/networks/${state.snap.chains[chain].gt}/pools/multi/${chunk.map((t) => t.pool).join(",")}`));
          for (const p of d.data || []) {
            const a = p.attributes; const t = chunk.find((x) => x.pool === norm(chain, a.address)); if (!t) continue;
            const lv = state.live[key(t)] || {};
            state.live[key(t)] = { ...lv, price: Number.isFinite(lv.price) ? lv.price : +a.base_token_price_usd, liq: t.liq_real * (+a.reserve_in_usd / t.reserve_usd), at: Date.now() };
          }
        } catch { /* keep the snapshot values */ }
      }
    }));
  }
  const norm = (chain, a) => (!a ? "" : chain === "solana" ? a : a.toLowerCase());

  // Live security re-check: worst case of GoPlus + honeypot.is (Ethereum, BNB Chain) or RugCheck (Solana).
  async function liveSecurity(tokens) {
    const byChain = {};
    for (const t of tokens) (byChain[t.chain] = byChain[t.chain] || []).push(t);
    const nowS = Date.now() / 1000;
    await Promise.all(Object.entries(byChain).map(async ([chain, ts]) => {
      const c = state.snap.chains[chain];
      // the free GoPlus service answers one token per request
      const url = chain === "solana" ? "https://api.gopluslabs.io/api/v1/solana/token_security" : `https://api.gopluslabs.io/api/v1/token_security/${c.goplus}`;
      const gp = {};
      for (const t of ts) {
        try {
          const r = await cached(`gp:${chain}:${t.token}`, 120000, () => getJSON(`${url}?contract_addresses=${t.token}`));
          for (const [k, v] of Object.entries(r.result || {})) gp[norm(chain, k)] = v;
        } catch { /* missing -> fails closed */ }
      }
      await Promise.all(ts.map(async (t) => {
        if (!gp[t.token]) { state.sec[key(t)] = { facts: null, at: Date.now() }; return; }
        let f = chain === "solana" ? X.fromGoplusSol(gp[t.token]) : X.fromGoplusEvm(gp[t.token], t.pools.map((p) => p.pool), nowS);
        try {
          if (chain === "solana") f = X.addRugcheck(f, await cached(`rc:${t.token}`, 180000, () => getJSON(`https://api.rugcheck.xyz/v1/tokens/${t.token}/report`)));
          else if (c.honeypot) f = X.addHoneypotIs(f, await cached(`hp:${t.token}`, 180000, async () => {
            const r = await fetch(`https://api.honeypot.is/v2/IsHoneypot?address=${t.token}&chainID=${c.honeypot}`, { cache: "no-store" });
            if (r.status === 404) return { _unsupported: true };      // pool type it can't simulate: stricter seller rules
            if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json();
          }));
        } catch { /* that checker is down: the facts stay incomplete and assess() fails closed */ }
        state.sec[key(t)] = { facts: f, at: Date.now() };
      }));
    }));
  }

  function deviceSeed() {
    let s0 = store.get("omega.seed", null);
    if (!Number.isInteger(s0)) { const a = new Uint32Array(1); crypto.getRandomValues(a); s0 = a[0]; store.set("omega.seed", s0); }
    return ((Math.imul(s0, 2654435761) >>> 0) ^ Math.floor(Date.now() / 3600000)) >>> 0;
  }

  // Market facts for the checks, with live pool money folded into the hourly liquidity history
  function marketFacts(t) {
    const lv = state.live[key(t)] || {};
    const liq = Number.isFinite(lv.liq) ? lv.liq : t.liq_real;
    const delta = t.liq_real > 0 ? liq / t.liq_real - 1 : 0;
    const comb = (x) => ((x === null || x === undefined) ? (delta < 0 ? delta : null) : (1 + x) * (1 + delta) - 1);
    const tx = t.tx_h24 || {};
    return { liq_real: liq, age_days: t.age_days, active_days: t.active_days, buyers_h24: tx.buyers || 0, sellers_h24: tx.sellers || 0,
      buys_h24: tx.buys || 0, sells_h24: tx.sells || 0, vol_h24: t.vol_h24 || 0, liq_change_6h: comb(t.liq_change_6h),
      liq_change_24h: comb(t.liq_change_24h), liq_change_72h: t.liq_change_72h, boosted: !!t.boosted, copycat: !!t.copycat,
      impersonator: false, rules_changed: !!t.rules_changed };
  }

  // Full evaluation of one token for the chosen speed and amount
  function evaluate(t, styleKey, amount, th) {
    const s = t.styles && t.styles[styleKey]; const model = state.snap.models[styleKey];
    if (!s || !model) return null;
    const ch = state.snap.chains[t.chain]; const lv = state.live[key(t)] || {};
    const px = Number.isFinite(lv.price) && lv.price > 0 ? lv.price : t.close;
    const sec = state.sec[key(t)];
    const facts = sec && sec.facts ? sec.facts : t.facts;
    const m = marketFacts(t);
    const a = X.assess(facts, m, th, t.chain);
    const cost = X.dexCost(amount, px, m.liq_real, t.pool_fee, facts.buy_tax || 0, facts.sell_tax || 0, ch.gas_usd, ch.mev, ch.sniper);
    const prob = ((state.snap.adaptive || {}).probation || {})[styleKey] || {};
    const r = X.expectedR(s.p, model.win_r, model.loss_r, cost.round_trip, s.risk_unit) - s.flag_penalty - a.penalty - (prob.active ? prob.penalty || 0.1 : 0);
    const sc = E.scoreFromR(r);
    return { t, s, px, facts, m, a, cost, r, score: sc, grade: gradeOf(sc), liveChecked: !!(sec && sec.facts), probation: prob.active ? prob : null };
  }

  // ------------------------------------------------------------------ scan
  async function scan() {
    const snap = await loadSnapshot(false);
    const amount = state.amount; const th = X.thresholds(deviceSeed(), state.profile);
    const ak = state.profile === "risky" ? "assess_risky" : "assess";
    const pool = snap.tokens.filter((t) => t[ak] && t[ak].verdict === "pass" && (state.chain === "all" || t.chain === state.chain));
    await liveMarket(pool.concat(tradeTokens()));
    // pre-rank, then re-check the best few live (security can change between hourly runs)
    let ev = pool.map((t) => evaluate(t, state.style, amount, th)).filter(Boolean).sort((x, y) => y.score - x.score);
    setStatus(`Re-checking the top ${Math.min(LIVE_CHECK, ev.length)} tokens for scams, live…`);
    await liveSecurity(ev.slice(0, LIVE_CHECK).map((e) => e.t));
    ev = pool.map((t) => evaluate(t, state.style, amount, th)).filter(Boolean);
    const passing = ev.filter((e) => e.a.verdict === "pass").sort((x, y) => y.score - x.score);
    const failedLive = ev.filter((e) => e.a.verdict === "reject");
    state.ideas = passing.slice(0, 5).map((e, i) => ({ ...e, rank: i + 1 }));
    state.mood = { n: passing.length, positive: passing.filter((e) => e.r > 0).length };
    state.failedLive = failedLive; state.lastAt = Date.now();
    anchor();
  }

  function tradeTokens() {
    if (!state.snap) return [];
    return state.trades.map((tr) => state.snap.tokens.find((t) => t.chain === tr.chain && t.token === tr.token) ||
      { chain: tr.chain, token: tr.token, pool: tr.pool, quote_price: tr.quote_price, pools: [{ pool: tr.pool }], liq_real: tr.liq_at_entry });
  }

  // A suggestion's price and exits stay fixed from the first time it enters the top 5 on this device
  function anchor() {
    const now = Date.now(); const keep = {};
    for (const d of state.ideas) {
      const k = `${state.style}:${key(d.t)}`; let a = state.anchors[k];
      const hold = state.snap.styles[state.style].hold_minutes;
      if (!a || now > a.exit_by) a = { at: now, price: d.px, take_profit: d.px * (1 + d.s.take_profit_pct), safety_exit: d.px * (1 + d.s.safety_exit_pct), exit_by: now + hold * 60000 };
      keep[k] = a; d.anchor = a;
    }
    for (const [k, a] of Object.entries(state.anchors)) if (!k.startsWith(`${state.style}:`)) keep[k] = a;
    state.anchors = keep; store.set("omega.dex.sugg", keep);
  }

  async function refreshIdeas() {
    if (state.scanning) return;
    state.scanning = true; $("refreshNow").disabled = true;
    setStatus("Loading checked tokens and live prices…");
    try {
      await scan();
      renderIdeas();
      const age = Math.round((Date.now() - Date.parse(state.snap.generated_at)) / 60000);
      setStatus(`Updated ${local(state.lastAt)} · ${state.mood.n} tokens passed every check · hourly list from ${age} min ago`);
    } catch (e) {
      $("mood").className = "banner bad";
      $("mood").textContent = `Couldn't load the DEX data right now (${e.message || e}). The page will try again on the next refresh.`;
      setStatus("Couldn't load data");
    } finally { state.scanning = false; $("refreshNow").disabled = false; schedule(); }
  }
  function schedule() {
    clearTimeout(state.timer);
    if (state.refresh > 0) { state.nextAt = Date.now() + state.refresh * 1000; state.timer = setTimeout(refreshIdeas, state.refresh * 1000); } else state.nextAt = 0;
  }
  function setStatus(text) { $("status").dataset.base = text; paintStatus(); }
  function paintStatus() {
    const base = $("status").dataset.base || ""; let extra = "";
    if (state.nextAt && !state.scanning) { const s = Math.max(0, Math.round((state.nextAt - Date.now()) / 1000)); extra = ` · next in ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }
    $("status").textContent = base + extra; $("clock").textContent = `UTC ${new Date().toISOString().slice(11, 16)}`;
  }

  // ------------------------------------------------------------------ charts (GeckoTerminal 15-minute candles)
  async function candles15(t) {
    const net = state.snap.chains[t.chain].gt;
    const d = await cached(`c15:${t.chain}:${t.pool}`, 60000, () => gt(`/networks/${net}/pools/${t.pool}/ohlcv/minute?aggregate=15&limit=192&currency=usd`));
    return ((d.data || {}).attributes || {}).ohlcv_list.map((r) => ({ time: r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4] })).sort((a, b) => a.time - b.time);
  }
  function precisionFor(p) { return p >= 100 ? 2 : p >= 1 ? 4 : Math.min(10, Math.max(4, 3 - Math.floor(Math.log10(p)))); }
  async function drawChart(el, t, lines, markerMs) {
    if (!el) return null;
    if (!window.LightweightCharts) { el.innerHTML = `<p class="small muted" style="padding:12px">The chart library couldn't load on this network.</p>`; return null; }
    let bars;
    for (let attempt = 0; attempt < 3 && !bars; attempt++) {
      try { bars = await candles15(t); } catch { if (attempt < 2) await sleep(15000 * (attempt + 1)); }
    }
    if (!bars) { el.innerHTML = `<p class="small muted" style="padding:12px">Chart data is busy right now; it will load on the next refresh.</p>`; return null; }
    if (!bars.length) return null;
    const chart = LightweightCharts.createChart(el, { autoSize: true,
      layout: { background: { color: cssVar("--surface") }, textColor: cssVar("--muted"), fontFamily: cssVar("--f-num") },
      grid: { vertLines: { color: cssVar("--sunken") }, horzLines: { color: cssVar("--sunken") } },
      rightPriceScale: { borderColor: cssVar("--line") }, timeScale: { borderColor: cssVar("--line"), timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }, handleScroll: false, handleScale: false });
    const last = bars[bars.length - 1].close; const prec = precisionFor(last);
    const series = chart.addCandlestickSeries({ upColor: cssVar("--good"), downColor: cssVar("--bad"), borderVisible: false, wickUpColor: cssVar("--good"), wickDownColor: cssVar("--bad"),
      priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    series.setData(bars);
    for (const l of lines) series.createPriceLine({ price: l.price, color: cssVar(l.color), lineWidth: 2, lineStyle: l.dashed ? 2 : 0, axisLabelVisible: true, title: l.title });
    if (markerMs) { const mt = Math.floor(markerMs / 900000) * 900; if (mt >= bars[0].time) series.setMarkers([{ time: Math.min(mt, bars[bars.length - 1].time), position: "belowBar", color: cssVar("--calm"), shape: "arrowUp", text: "Suggested" }]); }
    chart.timeScale().fitContent();
    return { chart, series, t, lastTime: bars[bars.length - 1].time };
  }
  function dropCharts(prefix) { for (const k of Object.keys(state.charts)) if (k.startsWith(prefix)) { try { state.charts[k].chart.remove(); } catch { /* gone */ } delete state.charts[k]; } }
  async function tickCharts() {   // move the last candle with the live price (DexScreener), no extra chart calls
    for (const ch of Object.values(state.charts)) {
      const lv = state.live[key(ch.t)]; if (!lv || !Number.isFinite(lv.price)) continue;
      const time = Math.floor(Date.now() / 900000) * 900;
      try { ch.series.update({ time: Math.max(time, ch.lastTime), open: ch.lastOpen ?? lv.price, high: Math.max(ch.hi ?? lv.price, lv.price), low: Math.min(ch.lo ?? lv.price, lv.price), close: lv.price }); } catch { /* chart gone */ }
      if (time > ch.lastTime) { ch.lastTime = time; ch.lastOpen = lv.price; ch.hi = lv.price; ch.lo = lv.price; } else { ch.hi = Math.max(ch.hi ?? lv.price, lv.price); ch.lo = Math.min(ch.lo ?? lv.price, lv.price); }
    }
  }
  async function pollPrices() {
    if (!state.snap) return;
    const toks = state.ideas.map((d) => d.t).concat(tradeTokens());
    if (!toks.length) return;
    await liveMarket(toks);
    for (const t of toks) {
      const lv = state.live[key(t)]; if (!lv) continue;
      for (const el of document.querySelectorAll(`[data-live="${key(t)}"]`)) el.textContent = price(lv.price);
      for (const el of document.querySelectorAll(`[data-since="${key(t)}"]`)) { const ch = lv.price / +el.dataset.ref - 1; el.textContent = `${pct(ch, 2)} since suggested`; el.className = `d ${ch >= 0 ? "up" : "down"}`; }
    }
    for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    tickCharts();
  }

  // ------------------------------------------------------------------ calculator (all costs)
  function costsFor(el) {
    const d = state.ideas.find((x) => key(x.t) === el.dataset.k); if (!d) return null;
    const amt = +el.querySelector(".amt").value || 0;
    const ch = state.snap.chains[d.t.chain]; const lv = state.live[key(d.t)] || {}; const px = Number.isFinite(lv.price) ? lv.price : d.px;
    const args = [amt, px, d.m.liq_real, d.t.pool_fee, d.facts.buy_tax || 0, d.facts.sell_tax || 0, ch.gas_usd];
    return { d, amt, px, a: d.anchor, c: X.dexCost(...args, ch.mev, ch.sniper), safe: X.dexCost(...args, 0, ch.sniper), ch };
  }
  function updateCalc(el) {
    const r = costsFor(el); if (!r) return;
    const { d, amt, px, a, c, safe } = r; const tgt = +el.querySelector(".tgt").value;
    const tp = px * (1 + d.s.take_profit_pct), sl = px * (1 + d.s.safety_exit_pct);
    const row = (label, x, sub = "") => { const o = c.at(x); return `<tr><td>${label}${sub}</td><td class="num">${price(x)}</td><td class="num ${o.net >= 0 ? "up" : "down"}">${usd(o.net)}</td><td class="num muted">${pct(o.net / amt, 1)}</td></tr>`; };
    let html = `<tr><td colspan="4" class="muted">If you buy now at ${price(px)} with $${amt.toLocaleString()}: you'd get about <b class="num">${c.coins.toPrecision(5)}</b> ${esc(d.t.base_symbol)} after every buying cost.</td></tr>`;
    html += row("If it reaches the take profit", tp) + row("If it hits the safety exit", sl);
    html += row("If you sold right away", px, ` <span class="small muted">(the cost of a round trip)</span>`);
    if (tgt > 0) html += row("If you sell at your price", tgt);
    if (a && Math.abs(a.price / px - 1) > 0.002) html += `<tr><td colspan="4" class="small muted">Exits are set from the price now. When it was first suggested at ${localDay(a.at)} the price was ${price(a.price)}.</td></tr>`;
    el.querySelector(".calc-out").innerHTML = html;
    const k = c.at(tp).costs; const s = safe.at(tp);
    el.querySelector(".cost-out").innerHTML = `<tr><td>Pool fees (buy + sell)</td><td class="num">$${k.fee.toFixed(2)}</td></tr>
      <tr><td>Price impact (your trade moves the price)</td><td class="num">$${k.impact.toFixed(2)}</td></tr>
      <tr><td>Token tax (buy + sell)</td><td class="num">$${k.tax.toFixed(2)}</td></tr>
      <tr><td>Front-running bots (sandwich / MEV)</td><td class="num">$${k.mev.toFixed(2)}</td></tr>
      <tr><td>Snipers buying the idea before you</td><td class="num">$${k.sniper.toFixed(2)}</td></tr>
      <tr><td>Network fees (2 swaps)</td><td class="num">$${k.gas.toFixed(2)}</td></tr>
      <tr><td><b>Break-even price</b> (you get your $${amt.toLocaleString()} back)</td><td class="num"><b>${price(c.break_even)}</b> (${pct(c.break_even / px - 1)})</td></tr>
      <tr><td>Money out at take profit <b>with front-running protection</b></td><td class="num up">${usd(s.net)}</td></tr>`;
  }
  function calcBlock(d) {
    const k = key(d.t);
    return `<div class="calc" data-k="${esc(k)}">
      <h3>Real profit calculator: the money you'd actually take out</h3>
      <div class="row">
        <label class="inline">I put in $<input class="amt" type="number" min="1" step="any" value="${state.amount}"></label>
        <label class="inline">and sell at $<input class="tgt" type="number" min="0" step="any" placeholder="${(d.px * (1 + d.s.take_profit_pct)).toPrecision(5)}"></label>
      </div>
      <div class="table-wrap"><table><tbody class="calc-out"></tbody></table></div>
      <details><summary>Every cost, at the take profit</summary><div class="table-wrap"><table class="costs"><tbody class="cost-out"></tbody></table></div></details>
      <p class="small muted">After the pool fee, price impact, token tax, front-running bots, snipers and network fees, for your amount. Protect yourself with ${esc(state.snap.chains[d.t.chain].protect)}.</p>
    </div>`;
  }

  // ------------------------------------------------------------------ rendering
  function checksBlock(d) {
    const icon = (ok) => (ok === true ? `<span class="ok">✓</span>` : ok === false ? `<span class="bad">✗</span>` : `<span class="na">–</span>`);
    const items = d.a.checks;
    const passed = items.filter((c) => c.ok === true).length, known = items.filter((c) => c.ok !== null).length;
    const when = d.liveChecked ? `re-checked live at ${local(state.sec[key(d.t)].at)}` : `checked at ${hhmm(Date.parse(state.snap.generated_at))} UTC (live re-check not run for this one)`;
    return `<details class="checks"><summary>Scam and rug-pull checks: <span class="pill ${d.a.penalty === 0 ? "good" : d.a.penalty < 0.1 ? "calm" : "warn"}">passed every hard rule · ${passed} of ${known} extra checks clean</span></summary>
      <ul>${items.map((c) => `<li>${icon(c.ok)}<span>${esc(c.text)}</span></li>`).join("")}</ul>
      <p class="small muted">Security ${when}. Sources: GoPlus${d.t.chain === "solana" ? ", RugCheck" : state.snap.chains[d.t.chain].honeypot ? ", honeypot.is" : ""}, DexScreener, GeckoTerminal. Hard rules (any one rejects the token) are listed under “How it works”.</p></details>`;
  }
  function venues(d) {
    const ch = state.snap.chains[d.t.chain]; const amt = state.amount; const tp = d.px * (1 + d.s.take_profit_pct);
    const rows = (d.t.pools || []).filter((p) => (p.reserve_usd || 0) >= 100000).map((p) => {
      const liq = p.pool === d.t.pool ? d.m.liq_real : (p.reserve_usd || 0) * 0.5;
      const c = X.dexCost(amt, d.px, liq, p.fee, d.facts.buy_tax || 0, d.facts.sell_tax || 0, ch.gas_usd, ch.mev, ch.sniper);
      return { p, net: c.at(tp).net, be: c.break_even };
    }).sort((x, y) => y.net - x.net);
    const exp = ch.explorer ? ch.explorer.replace("{}", d.t.token) : null;
    return `<details class="venues"><summary>Where to buy it cheapest, and the real token address</summary><div class="venues-out">
      ${rows.length ? `<div class="banner ${rows[0].net >= 0 ? "good" : "warn"}"><b>Best pool for $${amt.toLocaleString()}: ${esc(rows[0].p.dex)} (${esc(rows[0].p.quote_symbol || "")}).</b> At the take profit you'd take out about <b>${usd(rows[0].net)}</b> after every cost.</div>
      <div class="table-wrap"><table><thead><tr><th>Pool</th><th class="num">Money out at take profit</th><th class="num">Break-even</th><th class="num">Pool fee</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td>${esc(r.p.dex)} · ${esc(r.p.quote_symbol || "")}</td><td class="num ${r.net >= 0 ? "up" : "down"}">${usd(r.net)}</td><td class="num">${price(r.be)}</td><td class="num">${(r.p.fee * 100).toFixed(2)}%</td></tr>`).join("")}
      </tbody></table></div>` : ""}
      <p><b>Token address</b> (always check it, fakes copy names): <span class="addr">${esc(d.t.token)}</span> <button class="btn" type="button" data-copy="${esc(d.t.token)}">Copy</button></p>
      <p class="small"><a href="${esc(d.t.ds_url)}" target="_blank" rel="noopener">Open on DexScreener</a>${exp ? ` · <a href="${esc(exp)}" target="_blank" rel="noopener">Block explorer</a>` : ""}</p>
      <p class="small muted">On ${esc(ch.name)}, use ${esc(ch.protect)}. Without protection, bots can take about ${(ch.mev * 100).toFixed(1)}% of each swap. This app never connects to a wallet.</p></div></details>`;
  }
  function ideaCard(d) {
    const a = d.anchor; const st = state.snap.styles[state.style]; const ch = state.snap.chains[d.t.chain];
    const since = d.px / a.price - 1; const rl = riskLevel(d.s.risk_unit, RISK_RANGE[state.style]);
    const held = state.trades.some((t) => t.chain === d.t.chain && t.token === d.t.token);
    const already = d.px >= a.take_profit ? `<div class="banner good">Since it was suggested, the price already reached the take-profit level.</div>` :
      d.px <= a.safety_exit ? `<div class="banner bad">Since it was suggested, the price already fell to the safety exit. Don't buy it now.</div>` : "";
    const riskyOnly = state.profile === "risky" && d.t.assess && d.t.assess.verdict !== "pass";
    const warns = (riskyOnly ? [`Higher-risk only: ${d.t.assess.hard.map((h) => h.text).join(" ")}`] : []).concat(d.s.warnings).concat(d.probation ? [`This speed is on probation: its last ${d.probation.n} ideas did worse than random picks, so scores are lowered.`] : []);
    return `<article class="card">
      <div class="idea-head">
        <h2><span class="rank">${d.rank}.</span>${esc(d.t.base_symbol)} <span class="pill chainpill">${esc(ch.name)}</span> <span class="pill calm">${esc(d.t.dex)}</span>${riskyOnly ? ' <span class="pill bad">Higher risk</span>' : ""}</h2>
        <div class="score"><b>${(Math.round(d.score * 10) / 10).toFixed(1)}</b><span class="muted">/ 10</span>
          <span class="pill ${gradeClass(d.grade)}">${esc(d.grade)}</span><span class="pill ${riskClass(rl)}">Risk: ${rl}</span></div>
      </div>
      <p class="small muted">${esc(d.t.base_name || "")} · paired with ${esc(d.t.quote_symbol)} · ${money(d.m.liq_real)} real money in the pool · ${Math.round(d.t.age_days)} days old</p>
      <div class="strip">
        <div class="fact"><div class="k">Suggested</div><div class="v">${localDay(a.at)}</div><div class="d muted">at ${price(a.price)}</div></div>
        <div class="fact"><div class="k">Price now</div><div class="v" data-live="${esc(key(d.t))}">${price(d.px)}</div><div class="d ${since >= 0 ? "up" : "down"}" data-since="${esc(key(d.t))}" data-ref="${a.price}">${pct(since, 2)} since suggested</div><div class="live small muted">Live (30 s)</div></div>
        <div class="fact"><div class="k">Take profit at</div><div class="v">${price(a.take_profit)}</div><div class="d up">${pct(a.take_profit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(a.safety_exit)}</div><div class="d down">${pct(a.safety_exit / a.price - 1)}</div></div>
        <div class="fact"><div class="k">Sell by</div><div class="v">${local(a.exit_by)}</div><div class="d muted">${hhmm(a.exit_by)} UTC</div></div>
      </div>
      ${already}
      <div class="chart" id="chart-${esc(key(d.t)).replace(/[^a-zA-Z0-9-]/g, "_")}"></div>
      <div class="chart-legend"><span><i class="l-base"></i>Price when suggested</span><span><i class="l-tp"></i>Take profit</span><span><i class="l-sl"></i>Safety exit</span><span>15-minute candles, moving live</span></div>
      <div class="facts">
        <div class="fact"><div class="k">Beats the market</div><div class="v">${Math.round(d.s.p * 100)}%</div><div class="d muted">chance</div></div>
        <div class="fact"><div class="k">All costs, round trip</div><div class="v">${(d.cost.round_trip * 100).toFixed(1)}%</div><div class="d muted">on $${state.amount.toLocaleString()}</div></div>
        <div class="fact"><div class="k">Break-even</div><div class="v">${pct(d.cost.break_even / d.px - 1)}</div><div class="d muted">rise needed</div></div>
        <div class="fact"><div class="k">Hold for at most</div><div class="v">${esc(st.hold_text)}</div></div>
      </div>
      <div><h3>Why it was picked</h3><ul class="why">${d.s.why.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></div>
      ${warns.length ? `<div class="warnings">${warns.map((w) => `<div class="banner warn">⚠ ${esc(w)}</div>`).join("")}</div>` : ""}
      ${checksBlock(d)}
      ${calcBlock(d)}
      ${venues(d)}
      <div class="actions">${held ? `<span class="pill calm">You're watching this trade</span>` : `<button class="btn primary" type="button" data-take="${esc(key(d.t))}">I bought this: watch it for me</button>`}</div>
    </article>`;
  }
  function renderIdeas() {
    const st = state.snap.styles[state.style]; const m = state.mood; const share = m.n ? m.positive / m.n : 0;
    const label = share > 0.6 ? "Favourable" : share > 0.35 ? "Mixed" : "Unfavourable";
    const chName = state.chain === "all" ? "all four networks" : state.snap.chains[state.chain].name;
    $("mood").className = `banner ${m.n === 0 ? "warn" : label === "Favourable" ? "good" : label === "Mixed" ? "calm" : "warn"}`;
    const riskyNote = state.profile === "risky" ? `<br><b>Higher-risk mode is on:</b> tokens as young as 5 days and pools as small as $100,000 are included. Every scam test still applies, but young, small tokens collapse far more often. Use small amounts.` : "";
    $("mood").innerHTML = (m.n === 0 ? `<b>No token on ${esc(chName)} passes every safety rule right now.</b> That's the system working: it would rather show nothing than a likely scam.${state.chain === "robinhood" ? " Robinhood Chain is new, so most of its tokens are younger than 14 days or hold less than $500,000." : ""} See the rejected list below.`
      : `<b>Market mood: ${label}.</b> ${m.positive} of ${m.n} checked tokens on ${esc(chName)} look positive after all costs for $${state.amount.toLocaleString()} (sell within ${esc(st.hold_text)}). ${label === "Unfavourable" ? "Doing nothing is a perfectly good choice right now." : "Scores above 5 beat break-even after every cost."}`) + riskyNote;
    if (state.profile === "risky") $("mood").className = "banner warn";
    dropCharts("idea|");
    $("ideas").innerHTML = `<div style="display:grid;gap:14px">${state.ideas.map(ideaCard).join("")}</div>`;
    for (const el of document.querySelectorAll("#ideas .calc")) updateCalc(el);
    for (const d of state.ideas) {
      const a = d.anchor;
      drawChart($(`chart-${key(d.t).replace(/[^a-zA-Z0-9-]/g, "_")}`), d.t, [
        { price: a.price, color: "--calm", dashed: true, title: "Suggested" }, { price: a.take_profit, color: "--good", title: "Take profit" },
        { price: a.safety_exit, color: "--bad", title: "Safety exit" }], a.at).then((c) => { if (c) state.charts[`idea|${key(d.t)}`] = c; });
    }
    renderRejected();
  }
  function renderRejected() {
    const snap = state.snap;
    const live = (state.failedLive || []).map((e) => ({ chain: e.t.chain, symbol: e.t.base_symbol, reasons: e.a.hard.map((h) => h.text), live: true }));
    const rows = live.concat((snap.rejected || []).filter((r) => !(state.profile === "risky" && r.risky_ok)))
      .filter((r) => state.chain === "all" || r.chain === state.chain);
    if (!rows.length) { $("rejected").innerHTML = ""; return; }
    $("rejected").innerHTML = `<details class="card rejected"><summary>Tokens checked and rejected this hour (${rows.length}), and why</summary>
      <ul>${rows.slice(0, 60).map((r) => `<li><b>${esc(r.symbol)}</b> <span class="small muted">${esc(snap.chains[r.chain]?.name || r.chain)}${r.live ? " · failed the live re-check just now" : ""}</span>: ${esc((r.reasons || []).join(" "))}</li>`).join("")}</ul></details>`;
  }

  // ------------------------------------------------------------------ trades
  function saveTrades() { store.set("omega.dex.trades", state.trades); const n = state.trades.length; $("tradeCount").hidden = n === 0; $("tradeCount").textContent = n; }
  async function refreshTrades(rebuild) {
    saveTrades();
    const box = $("trades");
    if (!state.trades.length) { dropCharts("trade|"); box.innerHTML = `<div class="banner calm">No DEX trades yet. Tap “I bought this” on an idea.</div>`; return; }
    try { await loadSnapshot(false); } catch { /* use what we have */ }
    const toks = tradeTokens();
    await liveMarket(toks);
    if (!state.tradeSecAt || Date.now() - state.tradeSecAt > 120000) { state.tradeSecAt = Date.now(); try { await liveSecurity(toks); } catch { /* next time */ } }
    const th = X.thresholds(deviceSeed());
    const html = [];
    for (const tr of state.trades) {
      const t = toks.find((x) => x.chain === tr.chain && x.token === tr.token);
      const lv = state.live[key(tr)] || {}; const px = Number.isFinite(lv.price) ? lv.price : (t && t.close) || NaN;
      // rug signals since you bought: pool money falling, security turning bad
      const liqNow = Number.isFinite(lv.liq) ? lv.liq : null;
      const liqDrop = liqNow && tr.liq_at_entry ? liqNow / tr.liq_at_entry - 1 : 0;
      const sec = state.sec[key(tr)];
      let rug = null;
      if (liqDrop < -0.2) rug = `Money in the pool fell ${Math.round(-liqDrop * 100)}% since you bought. That's how rug pulls start.`;
      if (sec && sec.facts && t && t.liq_real) {
        const a = X.assess(sec.facts, { ...marketFacts(t) }, th, tr.chain);
        const bad = a.hard.filter((h) => ["honeypot", "tax", "tax_modifiable", "owner_powers", "authorities", "balance_change", "liq_pull", "upgradeable", "hidden_owner", "rugged"].includes(h.key));
        if (bad.length) rug = bad.map((h) => h.text).join(" ");
      }
      let adv;
      if (rug) adv = { action: "EXIT NOW: scam warning", level: "bad", why: rug + " Sell now; waiting can mean you can't sell at all.", pnl: 0 };
      else adv = E.advise(tr, px, Date.now(), null);
      notify(tr, adv);
      const ch = state.snap?.chains[tr.chain];
      const c = ch && Number.isFinite(px) ? X.dexCost(tr.amount, tr.entry, liqNow || tr.liq_at_entry, tr.pool_fee, tr.buy_tax || 0, tr.sell_tax || 0, ch.gas_usd, ch.mev, ch.sniper) : null;
      const net = c ? c.at(px).net : NaN;
      html.push(`<article class="card trade ${adv.level === "bad" ? "bad" : adv.level}" data-tid="${esc(tr.id)}">
        <div class="idea-head"><h2>${esc(tr.symbol)} <span class="pill chainpill">${esc(ch?.name || tr.chain)}</span></h2><span class="verdict ${adv.level === "bad" ? "bad" : adv.level}">${esc(adv.action)}</span></div>
        <p>${esc(adv.why)}</p>
        <div class="strip">
          <div class="fact"><div class="k">You bought</div><div class="v">${localDay(tr.opened)}</div><div class="d muted">at ${price(tr.entry)}</div></div>
          <div class="fact"><div class="k">Price now</div><div class="v">${price(px)}</div><div class="d ${net >= 0 ? "up" : "down"}">${usd(net)} on $${tr.amount.toLocaleString()} after all costs</div></div>
          <div class="fact"><div class="k">Take profit at</div><div class="v">${price(tr.take_profit)}</div></div>
          <div class="fact"><div class="k">Safety exit at</div><div class="v">${price(tr.safety_exit)}</div></div>
          <div class="fact"><div class="k">Pool money</div><div class="v">${money(liqNow ?? tr.liq_at_entry)}</div><div class="d ${liqDrop < -0.05 ? "down" : "muted"}">${pct(liqDrop)} since you bought</div></div>
          <div class="fact"><div class="k">Sell by</div><div class="v">${local(tr.exit_by)}</div></div>
        </div>
        <div class="chart" id="chart-trade-${esc(tr.id).replace(/[^a-zA-Z0-9-]/g, "_")}"></div>
        <p class="small muted">Security last re-checked ${sec ? local(sec.at) : "–"}. Token: <span class="addr">${esc(tr.token)}</span></p>
        <div class="actions"><button class="btn" type="button" data-sold="${esc(tr.id)}">I've sold it: remove</button></div>
      </article>`);
    }
    const keep = {};
    if (!rebuild) for (const tr of state.trades) { const el = $(`chart-trade-${tr.id.replace(/[^a-zA-Z0-9-]/g, "_")}`); if (el && el.childElementCount) keep[tr.id] = el; }
    box.innerHTML = `<div style="display:grid;gap:14px">${html.join("")}</div><p class="small muted">Prices checked ${local(Date.now())}.</p>`;
    for (const tr of state.trades) {
      const id = `chart-trade-${tr.id.replace(/[^a-zA-Z0-9-]/g, "_")}`;
      if (keep[tr.id]) { $(id).replaceWith(keep[tr.id]); continue; }
      dropCharts(`trade|${tr.id}`);
      drawChart($(id), { chain: tr.chain, token: tr.token, pool: tr.pool }, [
        { price: tr.entry, color: "--calm", dashed: true, title: "Bought" }, { price: tr.take_profit, color: "--good", title: "Take profit" },
        { price: tr.safety_exit, color: "--bad", title: "Safety exit" }], tr.opened).then((c) => { if (c) state.charts[`trade|${tr.id}`] = c; });
    }
  }
  function notify(tr, a) {
    const prev = state.lastAction[tr.id]; state.lastAction[tr.id] = a.action;
    if (!prev || prev === a.action || a.action === "Hold") return;
    document.title = `${a.action}: ${tr.symbol} · Mempool Omega DEX`;
    try { if ("Notification" in window && Notification.permission === "granted") new Notification(`${tr.symbol}: ${a.action}`, { body: a.why }); } catch { /* not supported */ }
  }

  // ------------------------------------------------------------------ track record
  function parseCSV(text) { const lines = text.trim().split(/\r?\n/); const head = lines.shift().split(","); return lines.map((l) => { const v = l.split(","); return Object.fromEntries(head.map((h, i) => [h, v[i]])); }); }
  async function renderMovers() {
    const box = $("movers");
    try {
      const snap = await loadSnapshot(false);
      const rows = (snap.movers || []).filter((r) => state.chain === "all" || r.chain === state.chain);
      if (!rows.length) { box.innerHTML = `<div class="banner calm">No price changes recorded yet for this network.</div>`; return; }
      const pill = (s) => (s === "standard" ? '<span class="pill good">Passes every rule</span>' : s === "risky" ? '<span class="pill warn">Higher risk only</span>' : '<span class="pill bad">Rejected</span>');
      box.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Token</th><th>Network</th><th class="num">24 h change</th><th class="num">Biggest pool</th><th style="min-width:18em">Safety and why</th></tr></thead><tbody>
        ${rows.map((r) => `<tr><td><b>${esc(r.symbol)}</b><div class="small muted">${esc(r.dex)}</div></td><td>${esc(snap.chains[r.chain]?.name || r.chain)}</td><td class="num ${r.chg_h24 >= 0 ? "up" : "down"}">${pct(r.chg_h24)}</td><td class="num">${money(r.reserve_usd)}</td>
          <td class="small" style="white-space:normal">${pill(r.status)} ${esc((r.reasons || []).slice(0, 2).join(" ")) || (r.status === "rejected" ? "" : "Can be suggested if it scores well enough after costs.")}</td></tr>`).join("")}
        </tbody></table></div><p class="small muted">From the hourly scan at ${hhmm(Date.parse(snap.generated_at))} UTC. Information only, not suggestions. A big jump is not a reason to buy: on DEXes it's often the pump before the dump.</p>`;
    } catch { box.innerHTML = `<div class="banner warn">Couldn't load the biggest movers right now.</div>`; }
  }

  async function renderRecord() {
    const box = $("record");
    let html = "";
    for (const [name, file, histFile] of [["Standard", "dex/scoreboard.json", "dex/history.csv"], ["Higher risk", "dex/scoreboard_risky.json", "dex/history_risky.csv"]]) {
      html += `<h2>${name}</h2>` + await recordBlock(file, histFile);
    }
    box.innerHTML = html;
  }
  async function recordBlock(file, histFile) {
    try {
      const b = await getJSON(REPO + file);
      const rows = Object.entries(b.by_style || {}).filter(([, v]) => v.closed);
      let html = rows.length ? "" : `<div class="banner calm">No DEX ideas have reached their time limit yet. Check back in a few hours.</div>`;
      if (rows.length) {
        html += `<div class="table-wrap"><table><thead><tr><th>Speed</th><th class="num">Ideas checked</th><th class="num">Ended in profit</th><th class="num">Average per idea</th><th class="num">Random pick average</th><th class="num">$100 in each idea</th></tr></thead><tbody>`;
        for (const [k, v] of rows) {
          html += `<tr><td>${esc(state.snap?.styles[k]?.label || k)}</td><td class="num">${v.closed}</td><td class="num">${Math.round(v.win_rate * 100)}%</td><td class="num ${v.avg_return_per_idea >= 0 ? "up" : "down"}">${pct(v.avg_return_per_idea, 2)}</td>
            <td class="num">${pct(v.random_pick_avg_return, 2)} ${v.avg_return_per_idea > v.random_pick_avg_return ? '<span class="pill good">beating random</span>' : '<span class="pill warn">not beating random</span>'}</td><td class="num ${v.if_100usd_each_total_pnl >= 0 ? "up" : "down"}">${usd(v.if_100usd_each_total_pnl)}</td></tr>`;
        }
        html += `</tbody></table></div>`;
      }
      try {
        const h = parseCSV(await getText(REPO + histFile)).filter((r) => r.status === "closed").slice(-15).reverse();
        const what = { take_profit: "Hit take profit", safety_exit: "Hit safety exit", time_limit: "Time limit reached" };
        if (h.length) html += `<h3>Latest checked ideas</h3><div class="table-wrap"><table><thead><tr><th>Suggested (UTC)</th><th>Network</th><th class="num">Score</th><th>What happened</th><th class="num">Result after all costs</th></tr></thead><tbody>
          ${h.map((r) => `<tr><td>${esc(r.ts.slice(5, 16))}</td><td>${esc(r.symbol.split(":")[0])}</td><td class="num">${esc(r.score)}</td><td>${esc(what[r.outcome] || r.outcome)}</td><td class="num ${+r.net_ret >= 0 ? "up" : "down"}">${pct(+r.net_ret, 2)}</td></tr>`).join("")}</tbody></table></div>`;
      } catch { /* optional */ }
      return html;
    } catch { return `<div class="banner calm">This track record starts after the first hourly run.</div>`; }
  }

  // ------------------------------------------------------------------ wiring
  function selectTab(name) {
    for (const b of document.querySelectorAll("nav.tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const p of document.querySelectorAll("section.panel")) p.hidden = p.id !== `tab-${name}`;
    if (name === "record") renderRecord();
    if (name === "movers") renderMovers();
    if (name === "trades") refreshTrades(true);
    store.set("omega.dex.tab", name);
    if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
  }
  function press(sel, attr, val) { for (const b of document.querySelectorAll(sel)) b.setAttribute("aria-pressed", String(b.dataset[attr] === val)); }
  document.addEventListener("click", async (ev) => {
    const take = ev.target.closest("[data-take]");
    if (take) {
      const d = state.ideas.find((x) => key(x.t) === take.dataset.take);
      if (d) {
        const amt = +take.closest("article").querySelector(".calc .amt")?.value || state.amount;
        const tr = E.makeTrade({ symbol: key(d.t), style: state.style, price_now: d.px, risk_unit: d.s.risk_unit, take_profit_pct: d.s.take_profit_pct,
          safety_exit_pct: d.s.safety_exit_pct, hold_minutes: state.snap.styles[state.style].hold_minutes });
        Object.assign(tr, { chain: d.t.chain, token: d.t.token, pool: d.t.pool, symbol: d.t.base_symbol, amount: amt, liq_at_entry: d.m.liq_real,
          pool_fee: d.t.pool_fee, buy_tax: d.facts.buy_tax || 0, sell_tax: d.facts.sell_tax || 0, quote_price: d.t.quote_price });
        state.trades.push(tr); saveTrades();
        take.outerHTML = `<span class="pill good">Added at ${price(d.px)}. Open “My DEX trades” to see when to sell.</span>`;
      }
    }
    const sold = ev.target.closest("[data-sold]");
    if (sold) { state.trades = state.trades.filter((t) => t.id !== sold.dataset.sold); refreshTrades(true); }
    const copy = ev.target.closest("[data-copy]");
    if (copy) { try { await navigator.clipboard.writeText(copy.dataset.copy); copy.textContent = "Copied"; } catch { copy.textContent = "Select and copy"; } }
  });
  document.addEventListener("input", (ev) => { const c = ev.target.closest(".calc"); if (c) updateCalc(c); });
  for (const b of document.querySelectorAll("#speed button")) b.addEventListener("click", () => { state.style = b.dataset.style; store.set("omega.dex.style", state.style); press("#speed button", "style", state.style); refreshIdeas(); });
  for (const b of document.querySelectorAll("#chains button")) b.addEventListener("click", () => { state.chain = b.dataset.chain; store.set("omega.dex.chain", state.chain); press("#chains button", "chain", state.chain); refreshIdeas(); if (!$("tab-movers").hidden) renderMovers(); });
  for (const b of document.querySelectorAll("nav.tabs button")) b.addEventListener("click", () => selectTab(b.dataset.tab));
  for (const b of document.querySelectorAll("#profile button")) b.addEventListener("click", () => { state.profile = b.dataset.profile; store.set("omega.dex.profile", state.profile); press("#profile button", "profile", state.profile); refreshIdeas(); });
  $("refresh").addEventListener("change", (e) => { state.refresh = +e.target.value; store.set("omega.dex.refresh", state.refresh); schedule(); paintStatus(); });
  $("refreshNow").addEventListener("click", () => { state.snapAt = 0; refreshIdeas(); });
  $("amount").addEventListener("change", (e) => { state.amount = Math.max(1, +e.target.value || 100); store.set("omega.dex.amount", state.amount); refreshIdeas(); });
  $("notifyBtn").addEventListener("click", async () => {
    if (!("Notification" in window)) { $("notifyState").textContent = "This browser doesn't support alerts. The tab title changes instead."; return; }
    const p = await Notification.requestPermission();
    $("notifyState").textContent = p === "granted" ? "Alerts are on while this page is open." : "Alerts are off. The tab title will still change when it's time to act.";
  });
  window.addEventListener("omega-theme", () => { if (state.snap && state.ideas.length) renderIdeas(); if (state.trades.length && !$("tab-trades").hidden) refreshTrades(true); });

  // ------------------------------------------------------------------ start
  $("refresh").value = String(state.refresh); $("amount").value = String(state.amount);
  press("#speed button", "style", state.style); press("#chains button", "chain", state.chain); press("#profile button", "profile", state.profile);
  const startTab = (location.hash || "").slice(1) || store.get("omega.dex.tab", "ideas");
  selectTab(["ideas", "movers", "trades", "record", "guide"].includes(startTab) ? startTab : "ideas");
  saveTrades();
  refreshIdeas();
  setInterval(paintStatus, 1000);
  setInterval(() => { if (!document.hidden) pollPrices(); }, 30000);
  setInterval(() => { if (state.trades.length && !$("tab-trades").hidden && !document.hidden) refreshTrades(false); }, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && state.nextAt && Date.now() > state.nextAt) refreshIdeas(); });
})();
